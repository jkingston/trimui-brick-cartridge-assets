"""Exercise the glTF postprocessor without requiring Blender in the test runner."""
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'export_assets', Path(__file__).resolve().parents[1] / 'tools/export_assets.py')
export_assets = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'bpy': types.ModuleType('bpy'),
                             'mathutils': types.SimpleNamespace(Matrix=None)}):
    spec.loader.exec_module(export_assets)


class CompactTests(unittest.TestCase):
    def fixture(self, directory, artwork=False, zero_normal=False):
        data = bytearray()
        views, accessors = [], []
        def add(values, width, kind='VEC3', component=5126):
            while len(data) % 4:
                data.append(0)
            offset = len(data)
            code = 'f' if component == 5126 else 'H'
            for value in values:
                data.extend(struct.pack('<' + code * width, *value))
            views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)-offset})
            accessors.append({'bufferView': len(views)-1, 'componentType': component,
                              'count': len(values), 'type': kind})
            return len(accessors)-1
        attrs = {
            'POSITION': add([(0, 0, 0), (1, 0, 0), (0, 1, 0)], 3),
            'NORMAL': add([(0, 0, 0 if zero_normal else 1)] * 3, 3),
            'TANGENT': add([(0, 0, 0, 1)] * 3, 4, 'VEC4'),
            'TEXCOORD_2': add([(0, 0), (1, 0), (0, 1)], 2, 'VEC2'),
        }
        if artwork:
            attrs['TEXCOORD_0'] = add([(.1, .2), (.8, .2), (.1, .9)], 2, 'VEC2')
        index = add([(i,) for i in (0, 1, 2, 0, 0, 1)], 1, 'SCALAR', 5123)
        material = {'normalTexture': {'index': 0, 'texCoord': 2}}
        if artwork:
            material['pbrMetallicRoughness'] = {'baseColorTexture': {'index': 0}}
        document = {'asset': {'version': '2.0'}, 'buffers': [{'uri': 'model.bin', 'byteLength': len(data)}],
                    'bufferViews': views, 'accessors': accessors, 'materials': [material],
                    'textures': [{'source': 0}], 'images': [{'uri': 'label.png'}],
                    'meshes': [{'primitives': [{'attributes': attrs, 'indices': index, 'material': 0}]}]}
        path = Path(directory) / 'model.gltf'
        path.write_text(json.dumps(document))
        path.with_suffix('.bin').write_bytes(data)
        return path

    def test_uv_reindex_and_zero_tangent_repair_preserve_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            report = export_assets.compact_gltf(path)
            gltf = json.loads(path.read_text())
            primitive = gltf['meshes'][0]['primitives'][0]
            self.assertIn('TEXCOORD_0', primitive['attributes'])
            self.assertNotIn('TEXCOORD_2', primitive['attributes'])
            self.assertEqual(gltf['materials'][0]['normalTexture']['texCoord'], 0)
            self.assertEqual(report['zero_area_triangles_removed'], 1)
            self.assertEqual(report['degenerate_tangent_frames_repaired'], 3)
            self.assertEqual(gltf['accessors'][primitive['indices']]['count'], 3)
            accessor = gltf['accessors'][primitive['attributes']['TANGENT']]
            view = gltf['bufferViews'][accessor['bufferView']]
            binary = path.with_suffix('.bin').read_bytes()
            tangent = struct.unpack_from('<ffff', binary, view['byteOffset'])
            self.assertAlmostEqual(math.sqrt(sum(x*x for x in tangent[:3])), 1)
            self.assertAlmostEqual(tangent[2], 0)

    def test_artwork_and_normal_uvs_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, artwork=True)
            export_assets.compact_gltf(path)
            gltf = json.loads(path.read_text())
            material = gltf['materials'][0]
            self.assertEqual(material['normalTexture']['texCoord'], 1)
            self.assertEqual(material['pbrMetallicRoughness']['baseColorTexture']['texCoord'], 0)
            attrs = gltf['meshes'][0]['primitives'][0]['attributes']
            self.assertEqual({a for a in attrs if a.startswith('TEXCOORD')}, {'TEXCOORD_0', 'TEXCOORD_1'})

    def test_zero_normals_require_geometry_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Zero vertex normal'):
                export_assets.compact_gltf(self.fixture(directory, zero_normal=True))

@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Run atlas tests with Blender bundled NumPy')
class AtlasFilterTests(unittest.TestCase):
    def image(self, pixels):
        import numpy as np
        class Pixels:
            def __init__(self, values): self.values = np.array(values, dtype=np.float32).reshape(-1)
            def foreach_get(self, target): target[:] = self.values
            def foreach_set(self, values): self.values = np.array(values)
        class Image:
            name = 'surface-base-color'
            size = (2, 2)
            def scale(self, *size): self.size = size
            def update(self): pass
        image = Image()
        image.pixels = Pixels(pixels)
        return image

    def test_unbaked_black_does_not_darken_filtered_colour(self):
        image = self.image([[.1, .2, .3, .5], [0, 0, 0, 0],
                            [0, 0, 0, 0], [0, 0, 0, 0]])
        export_assets.resize_runtime_image(image, 1)
        for actual, expected in zip(image.pixels.values, [.2, .4, .6, 1]):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_real_black_sample_survives_gutter_dilation(self):
        image = self.image([[0, 0, 0, 1], [0, 0, 0, 0],
                            [0, 0, 0, 0], [0, 0, 0, 0]])
        export_assets.resize_runtime_image(image, 2)
        self.assertEqual(image.pixels.values.reshape(-1, 4).tolist(), [[0, 0, 0, 1]] * 4)


if __name__ == '__main__':
    unittest.main()
