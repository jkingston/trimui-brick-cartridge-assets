import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_neutral_models
from cartridge import read_model


class NeutralModelTests(unittest.TestCase):
    def test_neutralize_replaces_artwork_and_retains_uvs(self):
        image = io.BytesIO()
        Image.new("RGBA", (8, 6), (255, 0, 0, 255)).save(image, "PNG")
        document = {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 8, "uri": "data:application/octet-stream;base64," + base64.b64encode(b"GEOMETRY").decode()}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}],
            "accessors": [{"bufferView": 0}],
            "images": [{"uri": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()}],
            "textures": [{"source": 0}],
            "materials": [
                {"name": "artwork.front", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                {"name": "shell", "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 0}, "material": 0}]}],
        }
        slots = build_neutral_models.neutralize(document, Path("model.glb"))
        self.assertEqual(slots, ["artwork.front"])
        self.assertEqual(len(document["images"]), 1)
        self.assertEqual(len(document["textures"]), 1)
        self.assertIn("TEXCOORD_0", document["meshes"][0]["primitives"][0]["attributes"])
        self.assertIn("baseColorTexture", document["materials"][0]["pbrMetallicRoughness"])
        encoded = document["images"][0]["uri"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as result:
            self.assertEqual(result.size, (8, 6))

    def test_glb_writer_round_trips(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.glb"
            document = {"asset": {"version": "2.0"}, "buffers": [{
                "byteLength": 4,
                "uri": "data:application/octet-stream;base64," + base64.b64encode(b"data").decode(),
            }]}
            build_neutral_models.write_glb(document, path)
            result = read_model(path)
            self.assertEqual(result["asset"]["version"], "2.0")
            self.assertTrue(result["buffers"][0]["uri"].endswith("ZGF0YQ=="))


if __name__ == "__main__":
    unittest.main()
