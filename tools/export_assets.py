#!/usr/bin/env python3
"""Build metre-scale glTF assets with replaceable artwork and baked normal maps.
Run with Blender: blender -b -t 3 --python-exit-code 1 --python tools/export_assets.py -- --models gb psx
"""
import argparse
import hashlib
import json
import math
import re
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'tools/export_profiles.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'assets')
    parser.add_argument('--reports', type=Path, default=ROOT / 'build/export-reports')
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--skip-current', action='store_true')
    parser.add_argument('--image-python', type=Path, help='Python with project Pillow dependencies')
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


def slug(value):
    return re.sub(r'[^a-z0-9._-]+', '-', value.lower()).strip('-')


def uv_digest(obj):
    return {layer.name: hashlib.sha256(b''.join(
        __import__('struct').pack('<ff', *item.uv) for item in layer.data)).hexdigest()
        for layer in obj.data.uv_layers}


def select(objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def evaluated_meshes(objects):
    """Evaluate fonts and modifiers once, retaining world pose and UV coordinates."""
    graph = bpy.context.evaluated_depsgraph_get()
    result = []
    for obj in objects:
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(graph),
                                               preserve_all_data_layers=True, depsgraph=graph)
        replacement = bpy.data.objects.new(obj.name + '.runtime', mesh)
        bpy.context.collection.objects.link(replacement)
        replacement.matrix_world = obj.matrix_world.copy()
        replacement['source_object'] = obj.name
        bpy.data.objects.remove(obj, do_unlink=True)
        result.append(replacement)
    return result


def prepare_normals(objects, output, resolution):
    """Bake procedural normals to a dedicated atlas, preserving existing artwork UVs."""
    original_uvs = {o.name: uv_digest(o) for o in objects}
    materials = {m for obj in objects for m in obj.data.materials if m}
    procedural = set()
    for material in materials:
        if not material.use_nodes:
            continue
        bsdf = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf and bsdf.inputs['Normal'].is_linked:
            if bsdf.inputs['Normal'].links[0].from_node.type == 'BUMP':
                procedural.add(material)
            else:
                raise ValueError(f'Unsupported normal network: {material.name}')
    channel_names = ['Base Color', 'Metallic', 'Roughness', 'Transmission Weight']
    has_procedural_channel = any(
        n.type == 'BSDF_PRINCIPLED' and any(n.inputs[name].is_linked and
        n.inputs[name].links[0].from_node.type != 'TEX_IMAGE' for name in channel_names)
        for m in materials if m.use_nodes for n in m.node_tree.nodes)
    if not procedural and not has_procedural_channel:
        return {'baked_materials': [], 'baked_channels': {}, 'artwork_uv_preserved': True}
    # Implicit image vectors follow the active render UV. Pin them before adding a bake UV.
    for material in materials:
        if not material.use_nodes:
            continue
        users = [o for o in objects if material in o.data.materials[:]]
        names = {o.data.uv_layers.active.name for o in users if o.data.uv_layers.active}
        images = [n for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'
                  and not n.inputs['Vector'].is_linked]
        if images and len(names) != 1:
            raise ValueError(f'Ambiguous implicit artwork UV for {material.name}: {names}')
        for node in images:
            uv = material.node_tree.nodes.new('ShaderNodeUVMap')
            uv.uv_map = next(iter(names))
            material.node_tree.links.new(uv.outputs['UV'], node.inputs['Vector'])
    for obj in objects:
        if not obj.data.uv_layers:
            obj.data.uv_layers.new(name='UVMap')
        layer = obj.data.uv_layers.new(name='RuntimeNormal')
        obj.data.uv_layers.active = layer
        layer.active_render = True
    select(objects)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.012,
                            area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    # Multi-object packing operates across objects, avoiding overlapping per-object atlases.
    for obj in objects:
        now = uv_digest(obj)
        for name, digest in original_uvs[obj.name].items():
            if now[name] != digest:
                raise ValueError(f'Artwork UV changed while unwrapping: {obj.name}/{name}')
    image = bpy.data.images.new('surface-normal', resolution, resolution, alpha=True, float_buffer=True)
    image.generated_color = (0, 0, 0, 0)
    image.colorspace_settings.name = 'Non-Color'
    for material in materials:
        if not material.use_nodes:
            raise ValueError(f'Non-node material needs explicit conversion: {material.name}')
        node = material.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = image
        material.node_tree.nodes.active = node
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 8
    scene.render.threads = 3
    scene.render.threads_mode = 'FIXED'
    # Only surfaces consuming this atlas need baking; untouched artwork and hardware
    # otherwise repeat a full-atlas bake for no runtime benefit.
    normal_objects = [o for o in objects if any(m in procedural for m in o.data.materials)]
    if normal_objects:
        select(normal_objects)
        bpy.ops.object.bake(type='NORMAL', normal_space='TANGENT', margin=8, use_clear=False)
    image.filepath_raw = str(output / 'surface-normal.png')
    image.file_format = 'PNG'
    image.save()
    for material in procedural:
        nodes = material.node_tree.nodes
        bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
        image_node = next(n for n in nodes if n.type == 'TEX_IMAGE' and n.image == image)
        uv = nodes.new('ShaderNodeUVMap')
        uv.uv_map = 'RuntimeNormal'
        normal = nodes.new('ShaderNodeNormalMap')
        normal.uv_map = 'RuntimeNormal'
        material.node_tree.links.new(uv.outputs['UV'], image_node.inputs['Vector'])
        material.node_tree.links.new(image_node.outputs['Color'], normal.inputs['Color'])
        material.node_tree.links.new(normal.outputs['Normal'], bsdf.inputs['Normal'])
    channels = {}
    for socket_name, color_space in [('Base Color', 'sRGB'), ('Metallic', 'Non-Color'),
                                      ('Roughness', 'Non-Color'), ('Transmission Weight', 'Non-Color')]:
        targets = {}
        for material in materials:
            bsdf = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if not bsdf:
                raise ValueError(f'Unsupported surface shader: {material.name}')
            socket = bsdf.inputs[socket_name]
            if socket.is_linked and socket.links[0].from_node.type != 'TEX_IMAGE':
                targets[material] = socket.links[0].from_socket
        if not targets:
            continue
        channel_image = bpy.data.images.new('surface-' + slug(socket_name), resolution, resolution, alpha=True, float_buffer=True)
        channel_image.generated_color = (0, 0, 0, 0)
        channel_image.colorspace_settings.name = color_space
        saved_outputs = {}
        for material in materials:
            nodes = material.node_tree.nodes
            node = nodes.new('ShaderNodeTexImage')
            node.image = channel_image
            nodes.active = node
        for material, source in targets.items():
            nodes = material.node_tree.nodes
            out = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
            saved_outputs[material] = out.inputs['Surface'].links[0].from_socket
            emission = nodes.new('ShaderNodeEmission')
            material.node_tree.links.new(source, emission.inputs['Color'])
            material.node_tree.links.new(emission.outputs[0], out.inputs['Surface'])
        select([o for o in objects if any(m in targets for m in o.data.materials)])
        bpy.ops.object.bake(type='EMIT', margin=8, use_clear=False)
        channel_image.filepath_raw = str(output / (channel_image.name + '.png'))
        channel_image.file_format = 'PNG'
        channel_image.save()
        for material, original in saved_outputs.items():
            nodes = material.node_tree.nodes
            out = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
            material.node_tree.links.new(original, out.inputs['Surface'])
            bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
            tex = next(n for n in nodes if n.type == 'TEX_IMAGE' and n.image == channel_image)
            uv = nodes.new('ShaderNodeUVMap')
            uv.uv_map = 'RuntimeNormal'
            material.node_tree.links.new(uv.outputs['UV'], tex.inputs['Vector'])
            material.node_tree.links.new(tex.outputs['Color'], bsdf.inputs[socket_name])
        channels[socket_name] = sorted(m.name for m in targets)
    for material in materials:
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                for socket in node.inputs:
                    if socket.is_linked and socket.links[0].from_node.type not in {'TEX_IMAGE', 'NORMAL_MAP'}:
                        source = socket.links[0].from_node
                        # Binary cutouts are physical holes, not translucent PCB/paper.
                        # Blender's glTF exporter recognizes this exact alpha-clip network.
                        if (socket.name == 'Alpha' and source.type == 'MATH' and
                                source.operation == 'GREATER_THAN' and
                                source.inputs[0].is_linked and
                                source.inputs[0].links[0].from_node.type == 'TEX_IMAGE' and
                                not source.inputs[1].is_linked):
                            continue
                        raise ValueError(f'Unconverted procedural input: {material.name}/{socket.name}')
    return {'baked_materials': sorted(m.name for m in procedural),
            'baked_channels': channels, 'artwork_uv_preserved': True,
            'resolution': resolution, 'normal_uv': 'RuntimeNormal'}


def bake_occlusion(objects, output, resolution, distance, material_names):
    """Bake short-range mould contact shading into glTF's independent AO channel."""
    materials = {m for o in objects for m in o.data.materials if m and m.use_nodes}
    targets = {m for m in materials if m.name in material_names}
    image = bpy.data.images.new('surface-occlusion', resolution, resolution, alpha=True, float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'
    saved = {}
    for material in materials:
        nodes, links = material.node_tree.nodes, material.node_tree.links
        tex = nodes.new('ShaderNodeTexImage'); tex.image = image; nodes.active = tex
        if material not in targets:
            continue
        out = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
        saved[material] = out.inputs['Surface'].links[0].from_socket
        ao = nodes.new('ShaderNodeAmbientOcclusion'); ao.inputs['Distance'].default_value = distance; ao.samples = 32
        emission = nodes.new('ShaderNodeEmission')
        links.new(ao.outputs['AO'], emission.inputs['Color']); links.new(emission.outputs[0], out.inputs['Surface'])
    select([o for o in objects if any(m in targets for m in o.data.materials)])
    bpy.ops.object.bake(type='EMIT', margin=8, use_clear=False)
    image.filepath_raw = str(output / 'surface-occlusion.png'); image.file_format = 'PNG'; image.save()
    for material, original in saved.items():
        nodes, links = material.node_tree.nodes, material.node_tree.links
        out = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
        links.new(original, out.inputs['Surface'])
        tex = next(n for n in nodes if n.type == 'TEX_IMAGE' and n.image == image)
        uv = nodes.new('ShaderNodeUVMap'); uv.uv_map = 'RuntimeNormal'; links.new(uv.outputs['UV'], tex.inputs['Vector'])
        group = bpy.data.node_groups.new('glTF Material Output', 'ShaderNodeTree')
        group.interface.new_socket(name='Occlusion', in_out='INPUT', socket_type='NodeSocketFloat')
        node = nodes.new('ShaderNodeGroup'); node.node_tree = group; links.new(tex.outputs['Color'], node.inputs['Occlusion'])
    return {'distance_source_units': distance, 'materials': sorted(m.name for m in targets)}


def convert_volume(materials, profile, metres, display_scale):
    report = []
    for material in materials:
        if not material.use_nodes:
            continue
        volumes = [n for n in material.node_tree.nodes if n.type == 'VOLUME_ABSORPTION']
        if not volumes:
            continue
        config = profile.get('volume', {}).get(material.name)
        if not config or len(volumes) != 1 or config['density_space'] != 'authored_world':
            raise ValueError(f'Volume conversion must be explicit: {material.name}')
        density = volumes[0].inputs['Density']
        if density.is_linked:
            raise ValueError('Texture-driven density conversion is not implemented')
        old = density.default_value
        density.default_value *= display_scale / metres
        group = bpy.data.node_groups.new('glTF Material Output', 'ShaderNodeTree')
        group.interface.new_socket(name='Thickness', in_out='INPUT', socket_type='NodeSocketFloat')
        node = material.node_tree.nodes.new('ShaderNodeGroup')
        node.node_tree = group
        node.inputs['Thickness'].default_value = config['thickness_mm'] * 0.001
        report.append({'material': material.name, 'source_density': old,
                       'density_per_metre': density.default_value,
                       'thickness_metres': node.inputs['Thickness'].default_value,
                       'thickness_status': config['thickness_status']})
    return report


def group_materials(objects, slots):
    """Merge compatible surfaces after baking; keep independent semantic material slots."""
    select(objects)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='MATERIAL')
    bpy.ops.object.mode_set(mode='OBJECT')
    separated = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    groups = {}
    for obj in separated:
        used = {p.material_index for p in obj.data.polygons}
        if len(used) != 1:
            raise ValueError(f'Material split failed: {obj.name}')
        material = obj.data.materials[next(iter(used))]
        groups.setdefault(material, []).append(obj)
    result = []
    for material, members in sorted(groups.items(), key=lambda item: item[0].name):
        source_name = material.name
        slot = slots.get(source_name, 'surface.' + slug(source_name))
        select(members)
        if len(members) > 1:
            bpy.ops.object.join()
        obj = bpy.context.view_layer.objects.active
        obj.name = slot
        obj['material_slot'] = slot
        obj['source_material'] = source_name
        material.name = slot
        obj.data.materials.clear()
        obj.data.materials.append(material)
        for polygon in obj.data.polygons:
            polygon.material_index = 0
        # Apply triangulation before tangent calculation; never decimate authored silhouette.
        modifier = obj.modifiers.new('Runtime triangulation', 'TRIANGULATE')
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        result.append(obj)
    return result


def compact_gltf(path):
    """Drop unused channels and exactly weld equal vertices; preserve every triangle."""
    import struct
    gltf = json.loads(path.read_text())
    if gltf.get('skins') or gltf.get('animations'):
        raise ValueError('Static asset compaction does not support skinning or animation')
    buffers = [(path.parent / b['uri']).read_bytes() for b in gltf['buffers']]
    sizes = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
    components = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}
    old_accessors = gltf['accessors']
    old_views = gltf['bufferViews']
    result = bytearray()
    accessors, views = [], []

    def read(index):
        accessor = old_accessors[index]
        if 'sparse' in accessor:
            raise ValueError('Sparse accessors are not supported')
        view = old_views[accessor['bufferView']]
        size = sizes[accessor['componentType']] * components[accessor['type']]
        stride = view.get('byteStride', size)
        offset = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
        data = buffers[view['buffer']]
        return [data[offset+i*stride:offset+i*stride+size] for i in range(accessor['count'])]

    def append(values, template, target):
        while len(result) % 4:
            result.append(0)
        offset = len(result)
        result.extend(b''.join(values))
        views.append({'buffer': 0, 'byteOffset': offset,
                      'byteLength': len(result)-offset, 'target': target})
        accessor = {k: v for k, v in template.items()
                    if k not in {'bufferView', 'byteOffset', 'count'}}
        accessor.update(bufferView=len(views)-1, count=len(values))
        accessors.append(accessor)
        return len(accessors)-1

    def coordinates(material):
        used = set()
        def visit(value):
            if isinstance(value, dict):
                for name, item in value.items():
                    if name.endswith('Texture') and isinstance(item, dict) and 'index' in item:
                        used.add(item.get('texCoord', 0))
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        visit(material)
        return used

    uv_maps = {i: {old: new for new, old in enumerate(sorted(coordinates(m)))}
               for i, m in enumerate(gltf['materials'])}
    tangent_repairs = normal_repairs = removed_zero_area_triangles = 0
    before_vertices = after_vertices = 0
    for mesh in gltf['meshes']:
        for primitive in mesh['primitives']:
            if primitive.get('mode', 4) != 4 or primitive.get('targets'):
                raise ValueError('Only static triangle primitives supported')
            material = gltf['materials'][primitive['material']]
            uv_map = uv_maps[primitive['material']]
            used_uv = set(uv_map)
            attributes = {name: index for name, index in primitive['attributes'].items()
                          if (not name.startswith('TEXCOORD_') or int(name[9:]) in used_uv)
                          and (name != 'TANGENT' or 'normalTexture' in material)}
            fields = {name: read(index) for name, index in attributes.items()}
            # Some authored degenerate UV corners yield zero Blender tangents.
            # Preserve position/UV data; provide a valid orthogonal frame at those corners.
            if 'NORMAL' in fields:
                normals = []
                for data in fields['NORMAL']:
                    n = struct.unpack('<fff', data)
                    length = math.sqrt(sum(v*v for v in n))
                    if length < 1e-12:
                        raise ValueError('Zero vertex normal requires geometry repair')
                    if abs(length-1) > 1e-6:
                        normal_repairs += 1
                    normals.append(tuple(v/length for v in n))
                fields['NORMAL'] = [struct.pack('<fff', *n) for n in normals]
                if 'TANGENT' in fields:
                    tangents = []
                    for data, n in zip(fields['TANGENT'], normals):
                        t = struct.unpack('<ffff', data)
                        projection = sum(t[i]*n[i] for i in range(3))
                        xyz = [t[i]-projection*n[i] for i in range(3)]
                        length = math.sqrt(sum(v*v for v in xyz))
                        if length < 1e-12:
                            axis = min(range(3), key=lambda i: abs(n[i]))
                            xyz = [(1.0 if i == axis else 0.0)-n[axis]*n[i] for i in range(3)]
                            length = math.sqrt(sum(v*v for v in xyz))
                            tangent_repairs += 1
                        tangents.append(struct.pack('<ffff', *(v/length for v in xyz),
                                                    -1.0 if t[3] < 0 else 1.0))
                    fields['TANGENT'] = tangents
            index_accessor = old_accessors[primitive['indices']]
            code = {5121: 'B', 5123: 'H', 5125: 'I'}[index_accessor['componentType']]
            indices = [struct.unpack('<'+code, b)[0] for b in read(primitive['indices'])]
            positions = [struct.unpack('<fff', data) for data in fields['POSITION']]
            surviving = []
            for start in range(0, len(indices), 3):
                triangle = indices[start:start+3]
                a, b, c = (positions[i] for i in triangle)
                ab = [b[i]-a[i] for i in range(3)]
                ac = [c[i]-a[i] for i in range(3)]
                cross = [ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2],
                         ab[0]*ac[1]-ab[1]*ac[0]]
                if all(value == 0.0 for value in cross):
                    removed_zero_area_triangles += 1
                else:
                    surviving.extend(triangle)
            indices = surviving
            if not indices:
                raise ValueError('Entire primitive has zero-area geometry')
            keys = list(zip(*(fields[name] for name in fields)))
            before_vertices += len(keys)
            unique, mapping, representatives = {}, {}, []
            for old_index in indices:
                key = keys[old_index]
                if key not in unique:
                    unique[key] = len(unique)
                    representatives.append(old_index)
                mapping[old_index] = unique[key]
            remapped = [mapping[i] for i in indices]
            # Exact corner attribute identity: no tolerance or silhouette modification.
            assert all(keys[old] == keys[representatives[new]] for old, new in zip(indices, remapped))
            after_vertices += len(representatives)
            primitive['attributes'] = {('TEXCOORD_'+str(uv_map[int(name[9:])])
                                        if name.startswith('TEXCOORD_') else name):
                                      append([fields[name][i] for i in representatives],
                                                    old_accessors[index], 34962)
                                       for name, index in attributes.items()}
            short = len(representatives) <= 65535
            primitive['indices'] = append([struct.pack('<H' if short else '<I', i) for i in remapped],
                                          {'componentType': 5123 if short else 5125, 'type': 'SCALAR'}, 34963)
    def remap_uvs(value, mapping):
        if isinstance(value, dict):
            for name, item in value.items():
                if name.endswith('Texture') and isinstance(item, dict) and 'index' in item:
                    old = item.get('texCoord', 0)
                    item['texCoord'] = mapping[old]
                remap_uvs(item, mapping)
        elif isinstance(value, list):
            for item in value:
                remap_uvs(item, mapping)
    for index, material in enumerate(gltf['materials']):
        remap_uvs(material, uv_maps[index])
    gltf['accessors'], gltf['bufferViews'] = accessors, views
    binary = path.with_suffix('.bin')
    binary.write_bytes(result)
    gltf['buffers'] = [{'byteLength': len(result), 'uri': binary.name}]
    # Identical texture descriptors need only one runtime texture object.
    unique, remap, textures = {}, {}, []
    for index, texture in enumerate(gltf.get('textures', [])):
        key = json.dumps(texture, sort_keys=True)
        if key not in unique:
            unique[key] = len(textures)
            textures.append(texture)
        remap[index] = unique[key]
    def remap_textures(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name.endswith('Texture') and isinstance(item, dict) and 'index' in item:
                    item['index'] = remap[item['index']]
                remap_textures(item)
        elif isinstance(value, list):
            for item in value:
                remap_textures(item)
    for material in gltf['materials']:
        remap_textures(material)
    gltf['textures'] = textures
    path.write_text(json.dumps(gltf, separators=(',', ':')) + '\n')
    return {'vertices_before': before_vertices, 'vertices_after': after_vertices,
            'binary_bytes': len(result), 'position_and_uv_corner_values_preserved': True,
            'degenerate_tangent_frames_repaired': tangent_repairs,
            'normal_vectors_renormalized': normal_repairs,
            'zero_area_triangles_removed': removed_zero_area_triangles}


def resize_runtime_image(image, maximum):
    """Coverage-weighted filtering and dilation keep unbaked atlas gutters out of colour."""
    width, height = image.size
    factor = min(1, maximum / max(width, height))
    target = (max(1, round(width * factor)), max(1, round(height * factor)))
    if image.name.startswith('surface-') and width % target[0] == 0 and height % target[1] == 0:
        import numpy as np
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, 4)
        # Cycles float bake pixels are already linear and premultiplied by
        # coverage, including dilated edges; multiplying again darkens seams.
        pixels = pixels.reshape(target[1], height // target[1], target[0], width // target[0], 4).mean(axis=(1, 3))
        coverage = pixels[:, :, 3:4]
        pixels[:, :, :3] = np.divide(pixels[:, :, :3], coverage,
                                    out=np.zeros_like(pixels[:, :, :3]), where=coverage > 0)
        valid = coverage[:, :, 0] > 0
        if not valid.any():
            raise ValueError(f'Empty bake: {image.name}')
        # Expand only uncovered pixels. Do not infer coverage from colour: black
        # metallic values and dark artwork can be legitimate baked samples.
        iteration = 0
        while not valid.all():
            # Eight local dilation passes protect bilinear/trilinear sampling.
            # Exponentially expanding passes fill only distant unused gutters.
            step = 1 << max(0, iteration - 8)
            iteration += 1
            total = np.zeros_like(pixels[:, :, :3])
            count = np.zeros(valid.shape, dtype=np.float32)
            for axis, delta in ((0, step), (0, -step), (1, step), (1, -step)):
                mask = np.roll(valid, delta, axis=axis)
                if axis == 0 and delta > 0: mask[:delta, :] = False
                if axis == 0 and delta < 0: mask[delta:, :] = False
                if axis == 1 and delta > 0: mask[:, :delta] = False
                if axis == 1 and delta < 0: mask[:, delta:] = False
                total += np.roll(pixels[:, :, :3], delta, axis=axis) * mask[:, :, None]
                count += mask
            fill = ~valid & (count > 0)
            pixels[fill, :3] = total[fill] / count[fill, None]
            valid |= fill
        if image.name.startswith('surface-normal'):
            vectors = pixels[:, :, :3] * 2 - 1
            length = np.linalg.norm(vectors, axis=2, keepdims=True)
            vectors = np.divide(vectors, length, out=np.zeros_like(vectors), where=length > 1e-8)
            pixels[:, :, :3] = (vectors + 1) / 2
        pixels[:, :, 3] = 1
        image.scale(*target)
        image.pixels.foreach_set(np.ascontiguousarray(pixels).reshape(-1))
        image.update()
    elif target != (width, height):
        image.scale(*target)


def replace_profile_meshes(key, profile):
    """Use reviewed glyph tessellation only when the detailed source mesh matches."""
    config = profile.get('mesh_replacements')
    if not config:
        return []
    import gzip
    path = ROOT / config['path']
    if hashlib.sha256(path.read_bytes()).hexdigest() != config['sha256']:
        raise ValueError('Geometry replacement bundle hash mismatch')
    bundle = json.loads(gzip.decompress(path.read_bytes()))
    if bundle['schema_version'] != 1:
        raise ValueError('Unsupported geometry replacement schema')
    report = []
    for name, item in bundle['models'][key].items():
        obj = bpy.data.objects[name]
        old = obj.data
        source = {'vertices': [list(v.co) for v in old.vertices],
                  'faces': [list(f.vertices) for f in old.polygons],
                  'smooth': [f.use_smooth for f in old.polygons],
                  'material_indices': [f.material_index for f in old.polygons],
                  'uv_layers': {uv.name: [list(a.uv) for a in uv.data] for uv in old.uv_layers}}
        digest = hashlib.sha256(json.dumps(source, separators=(',', ':')).encode()).hexdigest()
        if digest != item['source_sha256']:
            raise ValueError(f'Geometry replacement source mismatch: {name}')
        replacement = item['replacement']
        mesh = bpy.data.meshes.new(name + '.runtime-tessellation')
        mesh.from_pydata(replacement['vertices'], [], replacement['faces'])
        for material in old.materials:
            mesh.materials.append(material)
        for face, smooth, material in zip(mesh.polygons, replacement['smooth'], replacement['material_indices'], strict=True):
            face.use_smooth, face.material_index = smooth, material
        for uv_name, coordinates in replacement['uv_layers'].items():
            uv = mesh.uv_layers.new(name=uv_name)
            for loop, value in zip(uv.data, coordinates, strict=True):
                loop.uv = value
        if mesh.validate():
            raise ValueError(f'Invalid replacement geometry: {name}')
        old.calc_loop_triangles()
        mesh.calc_loop_triangles()
        report.append({'object': name, 'source_mesh_sha256': digest,
                       'triangles_before': len(old.loop_triangles),
                       'triangles_after': len(mesh.loop_triangles),
                       'object_transform_and_materials_preserved': True})
        obj.data = mesh
        if old.users == 0:
            bpy.data.meshes.remove(old)
    return report


def reduce_profile_geometry(profile):
    """Apply reviewed display-scale tessellation, retaining raw authoring sources."""
    result = []
    for item in profile.get('torus_tessellation', []):
        obj = bpy.data.objects[item['object']]
        if obj.type != 'MESH' or len(obj.data.materials) != 1:
            raise ValueError(f'Unexpected torus source: {obj.name}')
        matrix, parent = obj.matrix_local.copy(), obj.parent
        material = obj.data.materials[0]
        obj.data.calc_loop_triangles()
        before = len(obj.data.loop_triangles)
        name = obj.name
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.ops.mesh.primitive_torus_add(major_radius=item['radius_mm'],
            minor_radius=item['tube_mm'], major_segments=item['major_segments'],
            minor_segments=item['minor_segments'])
        obj = bpy.context.object
        obj.name, obj.parent, obj.matrix_local = name, parent, matrix
        obj.data.materials.append(material)
        for face in obj.data.polygons:
            face.use_smooth = True
        obj.data.calc_loop_triangles()
        result.append({'object': name, 'triangles_before': before,
                       'triangles_after': len(obj.data.loop_triangles),
                       'maximum_sagitta_mm': item['radius_mm'] * (1 - math.cos(math.pi / item['major_segments']))})
    return result


def build(key, profile, manifest, args):
    signature = build_signature(profile, manifest)
    source = ROOT / profile['source']
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = args.output / key
    args.output.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)
    staging = args.reports / 'staging'
    staging.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix=key + '-', dir=staging))
    bpy.ops.wm.open_mainfile(filepath=str(source))
    geometry_reductions = replace_profile_meshes(key, profile)
    geometry_reductions.extend(reduce_profile_geometry(profile))
    root = bpy.data.objects[profile['display_root']]
    scales = tuple(root.matrix_world.to_scale())
    if max(scales) - min(scales) > 1e-6:
        raise ValueError('Display root must have uniform scale')
    root_matrix = root.matrix_world.copy()
    root_inverse = root_matrix.inverted()
    objects = [o for o in bpy.data.objects if o.type in {'MESH', 'CURVE', 'FONT'}
               and o.name not in profile.get('exclude', [])]
    objects = evaluated_meshes(objects)
    # Bake and export use the same triangulation/tangent basis.
    select(objects)
    for obj in objects:
        bpy.context.view_layer.objects.active = obj
        modifier = obj.modifiers.new('Bake triangulation', 'TRIANGULATE')
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    bake = prepare_normals(objects, out, profile.get('bake_resolution', manifest['normal_resolution']))
    if profile.get('contact_occlusion_mm'):
        bake['contact_occlusion'] = bake_occlusion(objects, out, profile.get('bake_resolution', manifest['normal_resolution']), profile['contact_occlusion_mm'] * scales[0], bake['baked_materials'])
    metres = manifest['source_unit_metres']
    for obj in objects:
        obj.matrix_world = Matrix.Scale(metres, 4) @ root_inverse @ obj.matrix_world
    select(objects)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()
    points = [obj.matrix_world @ v.co for obj in objects for v in obj.data.vertices]
    bounds = [[min(p[i] for p in points), max(p[i] for p in points)] for i in range(3)]
    width = bounds[0][1] - bounds[0][0]
    if abs(width * 1000 - profile['expected_width_mm']) > 0.5:
        raise ValueError(f'Physical width mismatch: {key}: {width*1000} mm')
    materials = {m for o in objects for m in o.data.materials if m}
    volume = convert_volume(materials, profile, metres, scales[0])
    slots = profile.get('material_slots', {})
    missing = set(slots) - {m.name for m in materials}
    if missing:
        raise ValueError(f'Unknown material slots: {missing}')
    objects = group_materials(objects, slots)
    image_report = []
    for image in bpy.data.images:
        if image.source not in {'FILE', 'GENERATED'} or not image.size[0]:
            continue
        old = list(image.size)
        resize_runtime_image(image, profile.get('image_max', manifest['image_max']))
        image_report.append({'name': image.name, 'source_size': old, 'size': list(image.size)})
    select(objects)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.ops.export_scene.gltf(filepath=str(out / 'model.gltf'), export_format='GLTF_SEPARATE',
        export_texture_dir='textures', use_selection=True, export_apply=True,
        export_extras=True, export_cameras=False, export_lights=False,
        export_animations=False, export_yup=True, export_normals=True,
        export_texcoords=True, export_tangents=True, export_image_format='AUTO')
    image_report_path = args.reports / (key + '-textures.json')
    python = args.image_python or ROOT / '.venv/bin/python'
    if python.exists():
        command = [str(python)]
    elif shutil.which('uv'):
        command = [shutil.which('uv'), 'run', '--project', str(ROOT), 'python']
    else:
        raise ValueError('Run uv sync or provide --image-python with Pillow installed')
    subprocess.run(command + [str(ROOT / 'tools/normalize_textures.py'),
                   str(out / 'model.gltf'), '--report', str(image_report_path)], check=True)
    compaction = compact_gltf(out / 'model.gltf')
    for temporary_map in out.glob('surface-*.png'):
        temporary_map.unlink()
    gltf = json.loads((out / 'model.gltf').read_text())
    primitives = [p for mesh in gltf['meshes'] for p in mesh['primitives']]
    normals = [m for m in gltf['materials'] if 'normalTexture' in m]
    if bake['baked_materials'] and not normals:
        raise ValueError('Normal bake missing from glTF')
    if volume and 'KHR_materials_volume' not in gltf.get('extensionsUsed', []):
        raise ValueError('Volume conversion missing from glTF')
    report = {'model': key, 'source_sha256': source_hash,
              'build_signature': signature,
              'geometry_reductions': geometry_reductions,
              'source': profile['source'], 'bounds_metres_blender_z_up': bounds,
              'removed_display_scale': scales[0], 'bake': bake, 'volume': volume,
              'images': image_report, 'primitives': len(primitives),
              'triangles': sum(gltf['accessors'][p['indices']]['count']//3 for p in primitives),
              'vertices': sum(gltf['accessors'][p['attributes']['POSITION']]['count'] for p in primitives),
              'extensions': gltf.get('extensionsUsed', []),
              'normal_mapped_materials': len(normals), 'compaction': compaction,
              'limitations': ['Target device rendering/performance require separate validation.',
                              'Source geometry retained except explicitly reported profile reductions.',
                              'Procedural microdetail is filtered to runtime texture resolution.',
                              'Textures use PNG/JPEG; GPU texture compression not yet applied.']}
    if volume:
        report['limitations'].append('Volume thickness is nominal constant, not a baked thickness field.')
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise ValueError('Source changed during export; rebuild required')
    previous = staging / (key + '-previous')
    if previous.exists():
        shutil.rmtree(previous)
    if destination.exists():
        destination.rename(previous)
    out.rename(destination)
    if previous.exists():
        shutil.rmtree(previous)
    (args.reports / (key + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    return report


def build_signature(profile, manifest):
    settings = {k: v for k, v in manifest.items() if k != 'models'}
    settings['profile'] = profile
    return hashlib.sha256(Path(__file__).read_bytes() +
                          (ROOT / 'tools/normalize_textures.py').read_bytes() +
                          json.dumps(settings, sort_keys=True).encode()).hexdigest()


def main():
    args = arguments()
    manifest = json.loads(args.manifest.read_text())
    import traceback
    results = {}
    args.reports.mkdir(parents=True, exist_ok=True)
    for key in args.models or list(manifest['models']):
        try:
            if key not in manifest['models']:
                raise ValueError(f'Unknown export profile: {key}')
            profile = manifest['models'][key]
            existing = args.reports / (key + '.json')
            source_hash = hashlib.sha256((ROOT / profile['source']).read_bytes()).hexdigest()
            if args.skip_current and existing.exists() and (args.output/key/'model.gltf').exists():
                previous = json.loads(existing.read_text())
                if previous.get('source_sha256') == source_hash and previous.get('build_signature') == build_signature(profile, manifest):
                    results[key] = {'status': 'current'}
                    continue
            print('EXPORT_START', key, flush=True)
            build(key, profile, manifest, args)
            results[key] = {'status': 'exported'}
        except Exception as exc:
            results[key] = {'status': 'failed', 'error': str(exc), 'traceback': traceback.format_exc()}
            print('EXPORT_FAILED', key, str(exc), flush=True)
        (args.reports / 'batch-status.json').write_text(json.dumps(results, indent=2) + '\n')
    batch_name = hashlib.sha256(','.join(results).encode()).hexdigest()[:12]
    (args.reports / ('batch-' + batch_name + '.json')).write_text(json.dumps(results, indent=2) + '\n')
    if any(result['status'] == 'failed' for result in results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
