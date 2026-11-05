import binascii
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import sync_library_assets as syncer


class LibrarySyncTests(unittest.TestCase):
    def fixture(self, root, games):
        label = root / "label.png"
        Image.new("RGBA", (8, 6), (20, 40, 80, 255)).save(label)
        data = label.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        model_data = b"device-ready-model"
        model_digest = hashlib.sha256(model_data).hexdigest()
        pack = root / "pack"
        pack.mkdir()
        (pack / "artwork-000.pack").write_bytes(data)
        (pack / "models.pack").write_bytes(model_data)
        model_ids = sorted({game["model"] for game in games} |
                           set(syncer.DEFAULT_MODELS.values()))
        model_index = {"schema_version": 1, "version": "test-1", "models": [
            {"id": model_id, "sha256": model_digest, "pack": "models.pack",
             "offset": 0, "bytes": len(model_data)} for model_id in model_ids
        ]}
        (pack / "models-index.json").write_text(json.dumps(model_index))
        index = {
            "schema_version": 1, "version": "test-1",
            "models_index": "models-index.json",
            "objects": {digest: {"sha256": digest, "pack": "artwork-000.pack",
                                   "offset": 0, "bytes": len(data), "attribution": {"creator": "Example"}}},
            "games": games,
        }
        (pack / "artwork-index.json").write_text(json.dumps(index))
        return pack / "artwork-index.json", digest

    def test_hash_match_installs_only_required_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Roms/GBA").mkdir(parents=True)
            rom = b"a-rom"
            (root / "Roms/GBA/anything.gba").write_bytes(rom)
            index, digest = self.fixture(root, [{
                "id": "game", "platform": "gba", "title": "Game", "aliases": [],
                "hashes": {"sha1": [hashlib.sha1(rom).hexdigest()]}, "model": "gba",
                "surfaces": {"artwork.front": "pending"},
            }])
            document = json.loads(index.read_text())
            document["games"][0]["surfaces"]["artwork.front"] = digest
            index.write_text(json.dumps(document))
            manifest, missing = syncer.sync(root, index, cache=root / "cache")
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["match"], "sha1")
            installed = root / "Assets/Brick" / manifest["games"][0]["surfaces"]["artwork.front"]
            self.assertEqual(installed.read_bytes(), (root / "label.png").read_bytes())
            texture = root / "Assets/Brick" / manifest["games"][0]["textures"]["artwork.front"]
            self.assertEqual(texture.read_bytes()[:8], b"BRTX\x08\x00\x06\x00")
            self.assertEqual((root / "Assets/Brick/models/gba.glb").read_bytes(),
                             b"device-ready-model")

    def test_zip_rom_matches_headerless_nes_crc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Roms/FC").mkdir(parents=True)
            payload = b"payload"
            rom = b"NES\x1a" + bytes(12) + payload
            with zipfile.ZipFile(root / "Roms/FC/game.zip", "w") as archive:
                archive.writestr("Game.nes", rom)
            crc = f"{binascii.crc32(payload) & 0xffffffff:08x}"
            index, digest = self.fixture(root, [{
                "id": "game", "platform": "nes", "title": "Game", "hashes": {"crc32": [crc]},
                "model": "nes", "surfaces": {"artwork.front": "pending"},
            }])
            document = json.loads(index.read_text())
            document["games"][0]["surfaces"]["artwork.front"] = digest
            index.write_text(json.dumps(document))
            manifest, missing = syncer.sync(root, index, cache=root / "cache")
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["match"], "crc32")

    def test_exact_name_fallback_is_unambiguous_and_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Roms/PS").mkdir(parents=True)
            (root / "Roms/PS/Example Game (USA) (Disc 1).chd").write_bytes(b"chd")
            index, digest = self.fixture(root, [{
                "id": "game", "platform": "psx", "title": "Example Game", "aliases": [],
                "hashes": {}, "model": "psx", "surfaces": {"artwork.disc": "pending"},
            }])
            document = json.loads(index.read_text())
            document["games"][0]["surfaces"]["artwork.disc"] = digest
            index.write_text(json.dumps(document))
            manifest, missing = syncer.sync(root, index, cache=root / "cache", dry_run=True)
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["match"], "name")
            self.assertFalse((root / "Assets").exists())

    def test_unknown_game_is_reported_without_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Roms/GB").mkdir(parents=True)
            (root / "Roms/GB/Unknown.gb").write_bytes(b"unknown")
            index, _ = self.fixture(root, [])
            manifest, missing = syncer.sync(root, index, cache=root / "cache")
            self.assertEqual(len(missing), 1)
            self.assertEqual(manifest["games"][0]["match"], "missing")
            self.assertEqual(manifest["games"][0]["model"], "gb")
            self.assertEqual(manifest["games"][0]["surfaces"], {})
            texture_path = manifest["games"][0]["textures"]["artwork.front"]
            texture = (root / "Assets/Brick" / texture_path).read_bytes()
            self.assertEqual(texture[:4], b"BRTX")
            self.assertEqual((root / "Assets/Brick/models/gb.glb").read_bytes(),
                             b"device-ready-model")

    def test_generated_labels_are_deterministic_and_platform_shaped(self):
        first = syncer.runtime_texture(syncer.generated_label("Unknown Game", "gba"))
        second = syncer.runtime_texture(syncer.generated_label("Unknown Game", "gba"))
        other = syncer.runtime_texture(syncer.generated_label("Other Game", "gba"))
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        width, height = int.from_bytes(first[4:6], "little"), int.from_bytes(first[6:8], "little")
        self.assertGreater(width, height)

    def test_multi_region_and_language_tags_do_not_change_title(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Roms/GB").mkdir(parents=True)
            (root / "Roms/GB/Pokemon - Red Version (USA, Europe) (En).gb").write_bytes(b"rom")
            index, digest = self.fixture(root, [{
                "id": "red", "platform": "gb", "title": "Pokemon Red Version",
                "regions": ["US"], "hashes": {}, "model": "gb",
                "surfaces": {"artwork.front": "pending"},
            }])
            document = json.loads(index.read_text())
            document["games"][0]["surfaces"]["artwork.front"] = digest
            index.write_text(json.dumps(document))
            manifest, missing = syncer.sync(root, index, cache=root / "cache")
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["game_id"], "red")

    def test_device_inventory_uses_hash_without_reading_rom_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rom = b"remote-rom"
            index, digest = self.fixture(root, [{
                "id": "game", "platform": "gb", "title": "Different Filename",
                "hashes": {"sha256": [hashlib.sha256(rom).hexdigest()]},
                "model": "gb", "surfaces": {"artwork.front": "pending"},
            }])
            document = json.loads(index.read_text())
            document["games"][0]["surfaces"]["artwork.front"] = digest
            index.write_text(json.dumps(document))
            inventory = {"schema_version": 1, "games": [{
                "rom": "Roms/GB/remote.gb", "platform": "gb", "title": "remote",
                "hashes": {"sha256": hashlib.sha256(rom).hexdigest(),
                           "sha1": hashlib.sha1(rom).hexdigest(),
                           "crc32": f"{binascii.crc32(rom) & 0xffffffff:08x}", "size": len(rom)},
            }]}
            manifest, missing = syncer.sync(root, index, cache=root / "cache", inventory=inventory)
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["match"], "sha256")

    def test_filename_region_selects_one_physical_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Roms/GBC").mkdir(parents=True)
            (root / "Roms/GBC/Pokemon Gold (Japan).gbc").write_bytes(b"unknown-dump")
            index, digest = self.fixture(root, [
                {"id": "gold-us", "platform": "gbc", "title": "Pokemon Gold", "regions": ["US"],
                 "hashes": {}, "model": "gold", "surfaces": {"artwork.front": "pending"}},
                {"id": "gold-jp", "platform": "gbc", "title": "Pokemon Gold", "regions": ["JP"],
                 "hashes": {}, "model": "gold-jp", "surfaces": {"artwork.front": "pending"}},
            ])
            document = json.loads(index.read_text())
            for game in document["games"]:
                game["surfaces"]["artwork.front"] = digest
            index.write_text(json.dumps(document))
            manifest, missing = syncer.sync(root, index, cache=root / "cache")
            self.assertFalse(missing)
            self.assertEqual(manifest["games"][0]["game_id"], "gold-jp")


if __name__ == "__main__":
    unittest.main()
