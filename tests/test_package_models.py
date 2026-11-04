import hashlib
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import package_models


class PackageModelTests(unittest.TestCase):
    def test_archive_is_reproducible_and_has_buildroot_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "models"
            (source / "models").mkdir(parents=True)
            (source / "models-index.json").write_text("{}\n")
            (source / "models" / "gb.glb").write_bytes(b"model")
            first = root / "brick-models-1.tar.gz"
            second = root / "other" / "brick-models-1.tar.gz"
            package_models.package(source, first)
            package_models.package(source, second)
            self.assertEqual(hashlib.sha256(first.read_bytes()).digest(),
                             hashlib.sha256(second.read_bytes()).digest())
            with tarfile.open(first) as archive:
                self.assertEqual(archive.getnames(), [
                    "brick-models-1/models/gb.glb",
                    "brick-models-1/models-index.json",
                ])


if __name__ == "__main__":
    unittest.main()
