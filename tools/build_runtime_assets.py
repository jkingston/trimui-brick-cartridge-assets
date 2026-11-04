#!/usr/bin/env python3
"""Build screen-sized runtime GLBs from the full-resolution asset library."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.parse

from PIL import Image

from cartridge import ROOT, read_model
from compress_assets import FLAGS, source_files

TOPOLOGY_SENSITIVE = {'crystal'}


def texture_classes(document):
    classes = {}

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith('Texture') and isinstance(child, dict) and 'index' in child:
                    source = document['textures'][child['index']]['source']
                    kind = ('normal' if key == 'normalTexture' else
                            'color' if key in {'baseColorTexture', 'emissiveTexture',
                                               'specularColorTexture', 'sheenColorTexture'} else
                            'attribute')
                    classes.setdefault(source, set()).add(kind)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document.get('materials', []))
    return classes


def resize_textures(model, limit):
    document = json.loads(model.read_text())
    classes = texture_classes(document)
    report = []
    for index, descriptor in enumerate(document.get('images', [])):
        uri = urllib.parse.unquote(urllib.parse.urlsplit(descriptor['uri']).path)
        path = model.parent / uri
        with Image.open(path) as source:
            source.load()
            before = source.size
            image = source.copy()
            if max(image.size) > limit:
                image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                clean = Image.frombytes(image.mode, image.size, image.tobytes())
                clean.save(path, 'PNG', compress_level=9)
            report.append({'image': descriptor.get('name', Path(uri).stem),
                           'classes': sorted(classes.get(index, {'unused'})),
                           'source_size': list(before), 'runtime_size': list(image.size)})
    return report


def embedded_images(document):
    uri = document['buffers'][0]['uri']
    binary = base64.b64decode(uri.split(',', 1)[1], validate=True)
    result = []
    for descriptor in document.get('images', []):
        view = document['bufferViews'][descriptor['bufferView']]
        start = view.get('byteOffset', 0)
        data = binary[start:start + view['byteLength']]
        with Image.open(io.BytesIO(data)) as image:
            result.append({'width': image.width, 'height': image.height})
    return result


def triangle_count(document):
    total = 0
    for mesh in document.get('meshes', []):
        for primitive in mesh.get('primitives', []):
            if primitive.get('mode', 4) == 4:
                total += document['accessors'][primitive['indices']]['count'] // 3
    return total


def build(name, source_root, output_root, command, ratio, error, texture_limit, strategy):
    source = source_root / name / 'model.gltf'
    target_dir = output_root / name
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_root) as temporary:
        temporary = Path(temporary)
        for path in source_files(source):
            destination = temporary / path.relative_to(source.parent.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        model = temporary / 'model.gltf'
        textures = resize_textures(model, texture_limit)
        target = temporary / 'model.glb'
        permissive = strategy == 'permissive' or (strategy == 'adaptive' and name not in TOPOLOGY_SENSITIVE)
        flags = [*FLAGS, *(['-sp'] if permissive else ['-slb']),
                 '-si', str(ratio), '-se', str(error)]
        subprocess.run([*command, '-i', str(model), '-o', str(target), *flags], check=True)
        document = read_model(target)
        original = read_model(source)
        material_names = lambda value: {item.get('name') for item in value.get('materials', [])}
        if material_names(document) != material_names(original):
            raise ValueError(f'{name}: runtime build changed named material slots')
        triangles = triangle_count(document)
        if triangles == 0:
            raise ValueError(f'{name}: runtime build contains no triangles')
        report = {
            'id': name,
            'profile': 'device',
            'bytes': target.stat().st_size,
            'triangles': triangles,
            'texture_limit': texture_limit,
            'simplification': 'permissive' if permissive else 'locked-borders',
            'images': embedded_images(document),
            'texture_sources': textures,
            'flags': flags,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'material_slots_preserved': True,
        }
        target.replace(target_dir / 'model.glb')
        (target_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--input', type=Path, default=ROOT / 'assets')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/runtime-options/device')
    parser.add_argument('--gltfpack-command', nargs='+', default=['gltfpack'])
    parser.add_argument('--ratio', type=float, default=0.15)
    parser.add_argument('--error', type=float, default=0.0025)
    parser.add_argument('--texture-limit', type=int, default=512)
    parser.add_argument('--strategy', choices=('adaptive', 'locked', 'permissive'),
                        default='adaptive', help='mesh-boundary simplification policy')
    args = parser.parse_args()
    if not 0 < args.ratio <= 1 or not 0 <= args.error <= 1 or args.texture_limit < 1:
        parser.error('ratio, error, and texture limit are outside their valid ranges')
    models = args.models or [row['id'] for row in
                             json.loads((ROOT / 'sources/catalogue.json').read_text())['models']]
    args.output.mkdir(parents=True, exist_ok=True)
    for name in models:
        result = build(name, args.input, args.output, args.gltfpack_command,
                       args.ratio, args.error, args.texture_limit, args.strategy)
        print(f"{name}: {result['triangles']} triangles, {result['bytes']} bytes")


if __name__ == '__main__':
    main()
