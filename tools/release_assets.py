#!/usr/bin/env python3
"""Build deterministic, range-addressable runtime artwork release files."""

import argparse
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import shutil

from PIL import Image, ImageOps

from cartridge import ROOT, acquire_artwork, read_json

SCHEMA_VERSION = 1
DEFAULT_LIMIT = 512
DEFAULT_SHARD_SIZE = 64 * 1024 * 1024
LIBRARY_PLATFORMS = {
    "nes", "gb", "gbc", "gba", "mastersystem", "gamegear", "megadrive",
    "snes", "psx", "neogeo", "ngpc",
}
PLATFORM_ALIASES = {"famicom": "nes", "mark-iii": "mastersystem"}


def canonical_json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def runtime_image(source, limit=DEFAULT_LIMIT):
    """Return a metadata-free, screen-sized PNG without enlarging the source."""
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        alpha = image.getchannel("A").getextrema()[0] < 255
        if not alpha:
            image = image.convert("RGB")
        result = io.BytesIO()
        image.save(result, "PNG", optimize=True, compress_level=9)
        return result.getvalue(), image.size


def provenance(recipe, surface=None):
    source = surface if surface is not None else recipe
    result = {
        key: source[key] for key in
        ("url", "reference", "author", "license", "source_type", "note", "sha256")
        if key in source
    }
    result["changes"] = "Cropped or perspective-rectified as catalogued, converted to sRGB, resized without enlargement, and encoded as PNG."
    result["release_title"] = recipe.get("title")
    result["release_region"] = recipe.get("region")
    result["release_code"] = recipe.get("release_code")
    return {key: value for key, value in result.items() if value is not None}


def collect_artwork(root, cache, work, limit=DEFAULT_LIMIT):
    recipes = read_json(root / "catalogue/artwork.json")["recipes"]
    objects = {}
    release_surfaces = {}
    for recipe_id, recipe in sorted(recipes.items()):
        surfaces = {recipe["slot"]: recipe}
        surfaces.update(recipe.get("surfaces", {}))
        release_surfaces[recipe_id] = {}
        for slot, surface in sorted(surfaces.items()):
            stem = recipe_id if slot == recipe["slot"] else f"{recipe_id}-{slot}"
            prepared = acquire_artwork(surface, work / "prepared" / f"{stem}.png", cache)
            data, size = runtime_image(prepared, limit)
            digest = hashlib.sha256(data).hexdigest()
            objects.setdefault(digest, {
                "sha256": digest,
                "bytes": len(data),
                "width": size[0],
                "height": size[1],
                "mime_type": "image/png",
                "data": data,
                "attribution": provenance(recipe, None if surface is recipe else surface),
            })
            release_surfaces[recipe_id][slot] = digest
    return objects, release_surfaces


def release_rows(root, release_surfaces):
    games = read_json(root / "catalogue/games.json")
    rows = []
    seen = set()
    for edition in games["editions"]:
        recipe = edition.get("artwork_recipe")
        if not recipe or recipe not in release_surfaces:
            continue
        release_id = edition["id"]
        if release_id in seen:
            raise ValueError(f"Duplicate edition ID: {release_id}")
        seen.add(release_id)
        platform = PLATFORM_ALIASES.get(edition["platform"], edition["platform"])
        if platform not in LIBRARY_PLATFORMS:
            raise ValueError(f"Unsupported library platform: {edition['platform']}")
        rows.append({
            "id": release_id,
            "platform": platform,
            "title": edition["titles"][0],
            "aliases": edition["titles"][1:],
            "regions": edition.get("regions", []),
            "release_code": edition.get("release_code"),
            "model": edition["model"],
            "surfaces": release_surfaces[recipe],
            "hashes": edition.get("hashes", {}),
        })
    return sorted(rows, key=lambda row: (row["platform"], row["id"]))


def write_shards(objects, output, maximum=DEFAULT_SHARD_SIZE):
    output.mkdir(parents=True, exist_ok=False)
    shard_number, offset, handle = -1, 0, None
    try:
        for digest, descriptor in sorted(objects.items()):
            data = descriptor.pop("data")
            if len(data) > maximum:
                raise ValueError(f"Artwork object exceeds shard size: {digest}")
            if handle is None or (offset and offset + len(data) > maximum):
                if handle is not None:
                    handle.close()
                shard_number += 1
                name = f"artwork-{shard_number:03d}.pack"
                handle = (output / name).open("wb")
                offset = 0
            descriptor["pack"] = handle.name.rsplit("/", 1)[-1]
            descriptor["offset"] = offset
            handle.write(data)
            offset += len(data)
    finally:
        if handle is not None:
            handle.close()


def build(root, output, cache=None, version="0.1.0", limit=DEFAULT_LIMIT,
          shard_size=DEFAULT_SHARD_SIZE):
    root, output = Path(root).resolve(), Path(output).resolve()
    cache = Path(cache).resolve() if cache else root / ".cache/artwork"
    work = output.parent / (output.name + ".work")
    if output.exists() or work.exists():
        raise ValueError("Output and temporary work directories must not exist")
    work.mkdir(parents=True)
    try:
        objects, surfaces = collect_artwork(root, cache, work, limit)
        write_shards(objects, output, shard_size)
        index = {
            "schema_version": SCHEMA_VERSION,
            "version": version,
            "texture_profile": {"format": "png", "maximum_dimension": limit,
                                "colour_space": "srgb"},
            "games": release_rows(root, surfaces),
            "objects": objects,
        }
        (output / "artwork-index.json").write_bytes(canonical_json(index))
        sums = []
        for path in sorted(output.iterdir()):
            sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        (output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        summary = {
            "version": version,
            "games": len(index["games"]),
            "objects": len(objects),
            "packs": len(list(output.glob("artwork-*.pack"))),
            "payload_bytes": sum(item["bytes"] for item in objects.values()),
        }
        (output / "release.json").write_bytes(canonical_json(summary))
        return summary
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--texture-limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE)
    args = parser.parse_args(argv)
    try:
        summary = build(args.root, args.output, args.cache, args.version,
                        args.texture_limit, args.shard_size)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
