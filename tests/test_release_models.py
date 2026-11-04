import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release_models


class ReleaseModelTests(unittest.TestCase):
    def test_pack_is_sorted_and_range_addressable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "release"
            (source / "models").mkdir(parents=True)
            rows = []
            for model_id, data in (("z", b"last"), ("a", b"first")):
                path = source / "models" / f"{model_id}.glb"
                path.write_bytes(data)
                rows.append({"id": model_id, "path": f"models/{model_id}.glb",
                             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "artwork_slots": ["artwork.front"]})
            (source / "models-index.json").write_text(json.dumps({"models": rows}))
            release_models.build(source, output, "test")
            index = json.loads((output / "models-index.json").read_text())
            self.assertEqual([row["id"] for row in index["models"]], ["a", "z"])
            pack = (output / "models.pack").read_bytes()
            for row in index["models"]:
                data = pack[row["offset"]:row["offset"] + row["bytes"]]
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])


if __name__ == "__main__":
    unittest.main()
