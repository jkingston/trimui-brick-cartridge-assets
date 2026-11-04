#!/usr/bin/env python3
"""Install exactly the curated artwork required by a local ROM library."""

import argparse
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


SYSTEMS = {
    "nes": ({"fc", "nes"}, {".nes", ".unf", ".unif"}),
    "gb": ({"gb"}, {".gb"}),
    "gbc": ({"gbc"}, {".gb", ".gbc"}),
    "gba": ({"gba", "mgba"}, {".gba"}),
    "mastersystem": ({"ms", "sms", "mastersystem"}, {".sms"}),
    "gamegear": ({"gg", "gamegear"}, {".gg"}),
    "megadrive": ({"md", "genesis", "megadrive"}, {".md", ".gen", ".bin", ".smd"}),
    "snes": ({"sfc", "snes"}, {".sfc", ".smc"}),
    "psx": ({"ps", "ps1", "psx"}, {".chd", ".cue", ".m3u", ".pbp", ".ccd"}),
    "neogeo": ({"neogeo", "ng"}, {".zip"}),
    "ngpc": ({"ngp", "ngpc"}, {".ngp", ".ngc"}),
}
ARCHIVES = {".zip", ".7z"}
REGION_WORDS = ("usa|europe|japan|world|canada|australia|korea|asia|en|fr|de|es|it|pt|nl|sv|no|da|"
                "unl|proto|beta|demo|rev(?:ision)?[^)]*|v(?:ersion)?[^)]*|disc[^)]*|disk[^)]*|side[^)]*")
NAME_TAG = re.compile(rf"\s*[\[(](?:{REGION_WORDS})[\])]", re.I)
NON_WORD = re.compile(r"[^a-z0-9]+")
REGIONS = {"usa": "US", "us": "US", "canada": "CA", "europe": "EU", "eu": "EU",
           "japan": "JP", "jp": "JP", "australia": "AU", "au": "AU"}


def normal_name(value):
    value = NAME_TAG.sub("", value.casefold().replace("&", " and "))
    return NON_WORD.sub(" ", value).strip()


def name_region(value):
    for contents in re.findall(r"[\[(]([^])]*)[\])]", value.casefold()):
        for token in NON_WORD.sub(" ", contents).split():
            if token in REGIONS:
                return REGIONS[token]
    return None


def folder_tag(name):
    match = re.search(r"\(([^()]*)\)\s*$", name)
    return (match.group(1) if match else name).casefold()


def platform_for_folder(name):
    tokens = set(NON_WORD.sub(" ", folder_tag(name)).split())
    matches = [platform for platform, (aliases, _) in SYSTEMS.items() if tokens & aliases]
    return matches[0] if len(matches) == 1 else None


def normalized_payloads(platform, suffix, data):
    variants = [data]
    if platform == "nes" and suffix == ".nes" and data.startswith(b"NES\x1a") and len(data) >= 16:
        trainer = 512 if data[6] & 0x04 else 0
        if len(data) > 16 + trainer:
            variants.append(data[16 + trainer:])
    if platform == "snes" and len(data) > 512 and len(data) % 1024 == 512:
        variants.append(data[512:])
    return variants


def digests(platform, suffix, data):
    result = []
    for payload in normalized_payloads(platform, suffix, data):
        row = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "sha1": hashlib.sha1(payload).hexdigest(),
            "crc32": f"{binascii.crc32(payload) & 0xffffffff:08x}",
            "size": len(payload),
        }
        if row not in result:
            result.append(row)
    return result


def archive_payload(path, platform):
    if path.suffix.casefold() == ".7z":
        return None, None, "extract 7z archives before synchronising"
    try:
        with zipfile.ZipFile(path) as archive:
            allowed = SYSTEMS[platform][1]
            names = [name for name in archive.namelist()
                     if not name.endswith("/") and Path(name).suffix.casefold() in allowed]
            if len(names) != 1:
                return None, None, f"ZIP contains {len(names)} matching ROM files"
            return archive.read(names[0]), Path(names[0]).suffix.casefold(), None
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        return None, None, f"cannot read ZIP: {error}"


def scan_library(root):
    rom_root = next((path for path in root.iterdir()
                     if path.is_dir() and path.name.casefold() == "roms"), None)
    if rom_root is None:
        raise ValueError(f"No Roms directory under {root}")
    for system_dir in sorted((p for p in rom_root.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()):
        platform = platform_for_folder(system_dir.name)
        if not platform:
            continue
        allowed = SYSTEMS[platform][1] | ARCHIVES
        for path in sorted(system_dir.rglob("*"), key=lambda p: str(p).casefold()):
            if (not path.is_file() or
                    any(part.startswith(".") for part in path.relative_to(rom_root).parts)):
                continue
            suffix = path.suffix.casefold()
            if suffix not in allowed or path.name.casefold() == "neogeo.zip":
                continue
            if suffix in ARCHIVES:
                data, inner_suffix, note = archive_payload(path, platform)
            elif platform == "psx":
                data, inner_suffix, note = None, suffix, "disc image matched by exact catalogue name"
            else:
                try:
                    data, inner_suffix, note = path.read_bytes(), suffix, None
                except OSError as error:
                    data, inner_suffix, note = None, suffix, str(error)
            yield {
                "path": path.relative_to(root).as_posix(),
                "platform": platform,
                "title": path.stem,
                "name_key": normal_name(path.stem),
                "region": name_region(path.stem),
                "digests": digests(platform, inner_suffix, data) if data is not None else [],
                "scan_note": note,
            }


def request_bytes(url, start=None, length=None, maximum=80 * 1024 * 1024):
    headers = {"User-Agent": "brick-library-assets/0.1"}
    if start is not None:
        headers["Range"] = f"bytes={start}-{start + length - 1}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        if urllib.parse.urlsplit(response.url).scheme != "https":
            raise ValueError("Download redirected away from HTTPS")
        data = response.read(maximum + 1)
        status = response.status
    if len(data) > maximum:
        raise ValueError("Download exceeds size limit")
    if start is not None:
        if status == 206 and len(data) == length:
            return data
        if status == 200 and len(data) >= start + length:
            return data[start:start + length]
        raise ValueError("Server did not return the requested artwork range")
    return data


def load_index(source):
    parsed = urllib.parse.urlsplit(str(source))
    if parsed.scheme:
        if parsed.scheme != "https":
            raise ValueError("Remote index must use HTTPS")
        data, base = request_bytes(str(source), maximum=32 * 1024 * 1024), str(source)
    else:
        path = Path(source).resolve()
        data, base = path.read_bytes(), path.as_uri()
    document = json.loads(data)
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported artwork index schema")
    if not isinstance(document.get("games"), list) or not isinstance(document.get("objects"), dict):
        raise ValueError("Artwork index requires games and objects")
    return document, base


def build_lookup(index):
    hashes, names = {}, {}
    for game in index["games"]:
        platform = game["platform"]
        for kind, values in game.get("hashes", {}).items():
            for value in values:
                key = (platform, kind, str(value).casefold())
                if key in hashes and hashes[key]["id"] != game["id"]:
                    raise ValueError(f"Duplicate hash mapping: {key}")
                hashes[key] = game
        for value in [game.get("title", ""), *game.get("aliases", [])]:
            names.setdefault((platform, normal_name(value)), []).append(game)
    return hashes, names


def match_game(rom, hashes, names):
    for digest in rom["digests"]:
        for kind in ("sha256", "sha1", "crc32"):
            game = hashes.get((rom["platform"], kind, digest[kind]))
            if game:
                return game, kind
    candidates = names.get((rom["platform"], rom["name_key"]), [])
    if rom.get("region"):
        regional = [game for game in candidates if rom["region"] in game.get("regions", [])]
        if regional:
            candidates = regional
    return (candidates[0], "name") if len(candidates) == 1 else (None, "ambiguous" if candidates else "missing")


def object_bytes(descriptor, index_base, cache):
    digest = descriptor["sha256"].casefold()
    cached = cache / digest
    if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == digest:
        return cached.read_bytes()
    location = urllib.parse.urljoin(index_base, descriptor["pack"])
    parsed = urllib.parse.urlsplit(location)
    if parsed.scheme == "file":
        with Path(urllib.request.url2pathname(parsed.path)).open("rb") as source:
            source.seek(descriptor["offset"])
            data = source.read(descriptor["bytes"])
    elif parsed.scheme == "https":
        data = request_bytes(location, descriptor["offset"], descriptor["bytes"])
    else:
        raise ValueError("Artwork pack must be local or HTTPS")
    if len(data) != descriptor["bytes"] or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Artwork checksum mismatch: {digest}")
    cache.mkdir(parents=True, exist_ok=True)
    temporary = cache / (digest + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, cached)
    return data


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sync-", delete=False) as output:
        output.write(data)
        temporary = Path(output.name)
    os.replace(temporary, path)


def inventory_rows(document):
    if document.get("schema_version") != 1 or not isinstance(document.get("games"), list):
        raise ValueError("Unsupported library inventory schema")
    for game in document["games"]:
        source_digests = game.get("digests")
        if source_digests is None:
            source_digests = [game.get("hashes", {})]
        digests = [{key: value for key, value in source.items()
                    if key in {"sha256", "sha1", "crc32", "size"} and value is not None}
                   for source in source_digests]
        digests = [digest for digest in digests if digest]
        yield {
            "path": game["rom"], "platform": game["platform"], "title": game["title"],
            "name_key": normal_name(game["title"]), "region": name_region(game["title"]),
            "digests": digests,
            "scan_note": "container matched by exact catalogue name" if not digests else None,
        }


def sync(root, index_source, output=None, cache=None, dry_run=False, prune=False, inventory=None):
    root = Path(root).resolve()
    output = Path(output).resolve() if output else root / "Assets" / "Brick"
    cache = Path(cache).expanduser().resolve() if cache else Path.home() / ".cache/brick-cartridge-assets"
    index, base = load_index(index_source)
    hashes, names = build_lookup(index)
    rows, required, missing = [], set(), []
    scanned = inventory_rows(inventory) if inventory is not None else scan_library(root)
    for rom in scanned:
        game, method = match_game(rom, hashes, names)
        row = {"rom": rom["path"], "platform": rom["platform"], "match": method}
        if game:
            row.update({"game_id": game["id"], "title": game["title"],
                        "model": game["model"], "surfaces": game["surfaces"]})
            required.update(game["surfaces"].values())
        else:
            row.update({"title": rom["title"], "note": rom["scan_note"]})
            missing.append(row)
        rows.append(row)
    object_paths = {}
    for digest in sorted(required):
        descriptor = index["objects"].get(digest)
        if not descriptor or descriptor.get("sha256") != digest:
            raise ValueError(f"Missing object descriptor: {digest}")
        relative = f"objects/{digest[:2]}/{digest}.png"
        object_paths[digest] = relative
        if not dry_run:
            atomic_write(output / relative, object_bytes(descriptor, base, cache))
    for row in rows:
        if "surfaces" in row:
            row["surfaces"] = {slot: object_paths[digest] for slot, digest in row["surfaces"].items()}
    manifest = {
        "schema_version": 1,
        "asset_index_version": index.get("version"),
        "games": rows,
        "attribution": {digest: index["objects"][digest].get("attribution", {})
                        for digest in sorted(required)},
    }
    if not dry_run:
        atomic_write(output / "library.json",
                     (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
        if prune:
            keep = {output / value for value in object_paths.values()}
            objects = output / "objects"
            if objects.is_dir():
                for path in objects.rglob("*"):
                    if path.is_file() and path not in keep:
                        path.unlink()
                for path in sorted((p for p in objects.rglob("*") if p.is_dir()), reverse=True):
                    try:
                        path.rmdir()
                    except OSError:
                        pass
    return manifest, missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", help="SD/data root containing Roms/")
    parser.add_argument("--index", required=True, help="Local path or HTTPS artwork-index.json")
    parser.add_argument("--output", help="Destination (default: LIBRARY/Assets/Brick)")
    parser.add_argument("--cache", help="Content-addressed download cache")
    parser.add_argument("--inventory", type=Path,
                        help="Device-generated inventory JSON instead of scanning LIBRARY/Roms")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args(argv)
    try:
        inventory = json.loads(args.inventory.read_text()) if args.inventory else None
        manifest, missing = sync(args.library, args.index, args.output, args.cache,
                                 args.dry_run, args.prune, inventory)
    except (OSError, ValueError, urllib.error.URLError) as error:
        parser.exit(1, str(error) + "\n")
    matched = sum("surfaces" in row for row in manifest["games"])
    print(f"Scanned {len(manifest['games'])} games: {matched} matched, {len(missing)} missing")
    for row in missing:
        print(f"missing\t{row['platform']}\t{row['rom']}\t{row['match']}")
    return 2 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
