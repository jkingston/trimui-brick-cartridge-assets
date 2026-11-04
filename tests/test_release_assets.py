import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release_assets


class ReleaseAssetTests(unittest.TestCase):
    def test_runtime_image_is_bounded_deterministic_and_metadata_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.png"
            image = Image.new("RGB", (900, 450), "red")
            image.save(path, pnginfo=__import__("PIL.PngImagePlugin").PngImagePlugin.PngInfo())
            first, size = release_assets.runtime_image(path, 512)
            second, second_size = release_assets.runtime_image(path, 512)
            self.assertEqual(size, (512, 256))
            self.assertEqual(size, second_size)
            self.assertEqual(first, second)
            with Image.open(__import__("io").BytesIO(first)) as result:
                self.assertEqual(result.mode, "RGB")

    def test_shards_are_sorted_bounded_and_range_addressable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "release"
            objects = {}
            for data in (b"ccc", b"a" * 4, b"bb"):
                digest = hashlib.sha256(data).hexdigest()
                objects[digest] = {"sha256": digest, "bytes": len(data), "data": data}
            release_assets.write_shards(objects, root, maximum=5)
            self.assertGreaterEqual(len(list(root.glob("*.pack"))), 2)
            for digest, descriptor in objects.items():
                with (root / descriptor["pack"]).open("rb") as pack:
                    pack.seek(descriptor["offset"])
                    self.assertEqual(hashlib.sha256(pack.read(descriptor["bytes"])).hexdigest(), digest)

    def test_release_rows_reject_duplicate_editions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "catalogue").mkdir()
            edition = {"id": "same", "platform": "gb", "titles": ["One"],
                       "regions": ["US"], "model": "gb", "artwork_recipe": "label"}
            (root / "catalogue/games.json").write_text(json.dumps({"editions": [edition, edition]}))
            with self.assertRaisesRegex(ValueError, "Duplicate edition"):
                release_assets.release_rows(root, {"label": {"artwork.front": "0" * 64}})


if __name__ == "__main__":
    unittest.main()
