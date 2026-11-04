import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import sync_ssh_assets


class SshSyncTests(unittest.TestCase):
    def test_inventory_and_payload_use_ssh_without_transferring_roms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            label = b"label"
            digest = hashlib.sha256(label).hexdigest()
            (root / "artwork.pack").write_bytes(label)
            index = {
                "schema_version": 1, "version": "test", "objects": {
                    digest: {"sha256": digest, "pack": "artwork.pack", "offset": 0,
                             "bytes": len(label), "attribution": {"creator": "Test"}}
                }, "games": [{"id": "tetris", "platform": "gb", "title": "Tetris",
                               "regions": ["US"], "hashes": {}, "model": "gb",
                               "surfaces": {"artwork.front": digest}}],
            }
            (root / "index.json").write_text(json.dumps(index))
            inventory = {"schema_version": 1, "games": [{
                "rom": "Roms/GB/Tetris (USA).gb", "platform": "gb", "title": "Tetris (USA)",
                "digests": [],
            }]}
            fake_remote = root / "remote"
            fake_remote.mkdir()
            fake_ssh = root / "ssh"
            fake_ssh.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *--scan-library*) cat \"$FAKE_INVENTORY\" ;;\n"
                "  *) mkdir -p \"$FAKE_REMOTE\"; tar -xf - -C \"$FAKE_REMOTE\" ;;\n"
                "esac\n")
            fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IXUSR)
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory))
            previous = {key: os.environ.get(key) for key in ("FAKE_INVENTORY", "FAKE_REMOTE")}
            os.environ["FAKE_INVENTORY"] = str(inventory_path)
            os.environ["FAKE_REMOTE"] = str(fake_remote)
            try:
                manifest, missing = sync_ssh_assets.synchronise(
                    "brick", str(root / "index.json"), ssh=str(fake_ssh), cache=root / "cache")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["game_id"], "tetris")
            self.assertEqual((fake_remote / "library.json").is_file(), True)
            installed = next((fake_remote / "objects").rglob("*.png"))
            self.assertEqual(installed.read_bytes(), label)


if __name__ == "__main__":
    unittest.main()
