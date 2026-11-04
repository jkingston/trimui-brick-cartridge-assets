import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageCms

spec = importlib.util.spec_from_file_location('normalize_textures',
    Path(__file__).resolve().parents[1] / 'tools/normalize_textures.py')
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)


class TextureTests(unittest.TestCase):
    def fixture(self, directory, colour=True, data=False):
        path = Path(directory)
        image = Image.new('RGBA', (2, 2), (24, 80, 190, 77))
        image.save(path/'texture.png', icc_profile=ImageCms.ImageCmsProfile(
            ImageCms.createProfile('sRGB')).tobytes())
        material = {}
        if colour:
            material['pbrMetallicRoughness'] = {'baseColorTexture': {'index': 0}}
        if data:
            material['normalTexture'] = {'index': 0}
        model = path/'model.gltf'
        model.write_text(json.dumps({'materials': [material], 'textures': [{'source': 0}],
                                     'images': [{'uri': 'texture.png'}]}))
        return model

    def test_srgb_profile_conversion_retains_alpha_and_strips_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.fixture(directory)
            report = normalizer.normalize(model)
            image = Image.open(Path(directory)/'texture.png')
            self.assertEqual(image.getpixel((0, 0)), (24, 80, 190, 77))
            self.assertFalse(image.info)
            self.assertEqual(report[0]['operation'], 'embedded ICC to sRGB')

    def test_data_maps_are_not_colour_transformed(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.fixture(directory, colour=False, data=True)
            report = normalizer.normalize(model)
            self.assertTrue(report[0]['data_texture'])
            self.assertEqual(report[0]['maximum_channel_change'], 0)

    def test_ambiguous_colour_and_data_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'both colour and data'):
                normalizer.normalize(self.fixture(directory, colour=True, data=True))


if __name__ == '__main__':
    unittest.main()
