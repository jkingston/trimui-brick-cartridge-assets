import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_neutral_models
from cartridge import read_model


class NeutralModelTests(unittest.TestCase):
    def test_neutralize_removes_only_artwork_image_and_retains_uvs(self):
        document = {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 8, "uri": "data:application/octet-stream;base64," + base64.b64encode(b"GEOMETRY").decode()}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}],
            "accessors": [{"bufferView": 0}],
            "images": [{"uri": "data:image/png;base64,eA=="}],
            "textures": [{"source": 0}],
            "materials": [
                {"name": "artwork.front", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                {"name": "shell", "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 0}, "material": 0}]}],
        }
        slots = build_neutral_models.neutralize(document)
        self.assertEqual(slots, ["artwork.front"])
        self.assertEqual(document["images"], [])
        self.assertEqual(document["textures"], [])
        self.assertIn("TEXCOORD_0", document["meshes"][0]["primitives"][0]["attributes"])
        self.assertNotIn("baseColorTexture", document["materials"][0]["pbrMetallicRoughness"])

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
