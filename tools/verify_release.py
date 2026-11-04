#!/usr/bin/env python3
"""Verify every checksum, range and reference in an artwork release directory."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image
import io


def verify(root):
    root = Path(root)
    index = json.loads((root / "artwork-index.json").read_text())
    if index.get("schema_version") != 1:
        raise ValueError("Unsupported artwork index schema")
    ranges, referenced = {}, set()
    for digest, descriptor in index["objects"].items():
        if digest != descriptor.get("sha256") or len(digest) != 64:
            raise ValueError(f"Invalid object identity: {digest}")
        pack = root / descriptor["pack"]
        if not pack.is_file():
            raise ValueError(f"Missing pack: {pack.name}")
        start, size = descriptor["offset"], descriptor["bytes"]
        if start < 0 or size <= 0 or start + size > pack.stat().st_size:
            raise ValueError(f"Invalid object range: {digest}")
        ranges.setdefault(pack.name, []).append((start, start + size, digest))
        with pack.open("rb") as source:
            source.seek(start)
            data = source.read(size)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Object checksum mismatch: {digest}")
        with Image.open(io.BytesIO(data)) as image:
            if image.size != (descriptor["width"], descriptor["height"]):
                raise ValueError(f"Object dimensions mismatch: {digest}")
            image.verify()
    ids = set()
    for game in index["games"]:
        if game["id"] in ids:
            raise ValueError(f"Duplicate game ID: {game['id']}")
        ids.add(game["id"])
        for digest in game["surfaces"].values():
            if digest not in index["objects"]:
                raise ValueError(f"Missing object for {game['id']}: {digest}")
            referenced.add(digest)
    orphaned = set(index["objects"]) - referenced
    if orphaned:
        raise ValueError(f"Unreferenced artwork objects: {len(orphaned)}")
    for pack, entries in ranges.items():
        entries.sort()
        position = 0
        for start, end, digest in entries:
            if start != position:
                raise ValueError(f"Gap or overlap in {pack} before {digest}")
            position = end
        if position != (root / pack).stat().st_size:
            raise ValueError(f"Trailing bytes in {pack}")
    expected = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    for name, digest in expected.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Release checksum mismatch: {name}")
    return {"games": len(ids), "objects": len(index["objects"]),
            "packs": len(ranges), "payload_bytes": sum(x["bytes"] for x in index["objects"].values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.release), indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
