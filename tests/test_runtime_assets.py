import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_runtime_assets


class RuntimeAssetTests(unittest.TestCase):
    def test_texture_limit_preserves_aspect_and_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'textures').mkdir()
            Image.new('RGB', (1024, 256), 'red').save(root / 'textures/label.png')
            Image.new('RGB', (1024, 1024), (128, 128, 255)).save(root / 'textures/normal.png')
            document = {
                'textures': [{'source': 0}, {'source': 1}],
                'images': [{'name': 'label', 'uri': 'textures/label.png'},
                           {'name': 'normal', 'uri': 'textures/normal.png'}],
                'materials': [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}},
                               'normalTexture': {'index': 1}}],
            }
            model = root / 'model.gltf'
            model.write_text(json.dumps(document))
            report = build_runtime_assets.resize_textures(model, 512)
            with Image.open(root / 'textures/label.png') as label:
                self.assertEqual(label.size, (512, 128))
            with Image.open(root / 'textures/normal.png') as normal:
                self.assertEqual(normal.size, (512, 512))
            self.assertEqual(report[0]['classes'], ['color'])
            self.assertEqual(report[1]['classes'], ['normal'])

    def test_triangle_count_uses_index_accessors(self):
        document = {
            'accessors': [{'count': 12}, {'count': 6}],
            'meshes': [{'primitives': [{'indices': 0}, {'indices': 1}]}],
        }
        self.assertEqual(build_runtime_assets.triangle_count(document), 6)


if __name__ == '__main__':
    unittest.main()
