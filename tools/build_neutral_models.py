#!/usr/bin/env python3
"""Remove game-specific artwork from runtime GLBs while retaining UV bindings."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import struct

from PIL import Image

from cartridge import ROOT, prune_artwork_resources, read_model, resource_bytes
from gltf_resources import compact_buffers


def neutralize(document, model):
    """Replace game artwork with tiny neutral images while preserving UV contracts."""
    slots = []
    for material in document.get("materials", []):
        name = material.get("name", "")
        if not name.startswith("artwork."):
            continue
        slots.append(name)
        pbr = material.setdefault("pbrMetallicRoughness", {})
        texture_info = pbr.get("baseColorTexture")
        if texture_info is None:
            raise ValueError(f"Artwork material has no texture/UV binding: {name}")
        old_texture = document["textures"][texture_info["index"]]
        old_image = document["images"][old_texture["source"]]
        with Image.open(io.BytesIO(resource_bytes(document, old_image, model))) as source:
            width, height = source.size
        neutral = Image.new("RGBA", (width, height), (184, 184, 174, 255))
        encoded = io.BytesIO()
        neutral.save(encoded, "PNG", optimize=True)
        document.setdefault("images", []).append({
            "uri": "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode(),
        })
        replacement = {"source": len(document["images"]) - 1}
        if "sampler" in old_texture:
            replacement["sampler"] = old_texture["sampler"]
        document.setdefault("textures", []).append(replacement)
        texture_info["index"] = len(document["textures"]) - 1
        pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
        material.pop("alphaMode", None)
        material.pop("alphaCutoff", None)
    if not slots:
        raise ValueError("Runtime model has no artwork material slots")
    prune_artwork_resources(document)
    compact_buffers(document)
    return sorted(slots)


def write_glb(document, path):
    buffers = document.get("buffers", [])
    # meshopt GLBs may retain a second, URI-less logical fallback buffer. The
    # first buffer remains the sole physical GLB BIN chunk.
    if not buffers or not buffers[0].get("uri", "").startswith("data:application/octet-stream;base64,"):
        raise ValueError("Neutral runtime GLB requires an embedded primary binary buffer")
    if any(buffer.get("uri") for buffer in buffers[1:]):
        raise ValueError("Neutral runtime GLB cannot contain external buffers")
    binary = base64.b64decode(buffers[0].pop("uri").split(",", 1)[1], validate=True)
    buffers[0]["byteLength"] = len(binary)
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    data = (struct.pack("<4sII", b"glTF", 2, length) +
            struct.pack("<I4s", len(encoded), b"JSON") + encoded +
            struct.pack("<I4s", len(binary), b"BIN\0") + binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def build(source, output, version="0.1.0"):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("Output directory must not exist")
    models = []
    for model in sorted(source.glob("*/model.glb")):
        model_id = model.parent.name
        document = read_model(model)
        slots = neutralize(document, model)
        target = output / "models" / f"{model_id}.glb"
        data = write_glb(document, target)
        models.append({"id": model_id, "path": target.relative_to(output).as_posix(),
                       "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                       "artwork_slots": slots})
    if not models:
        raise ValueError("No runtime model.glb files found")
    index = {"schema_version": 1, "version": version, "models": models}
    (output / "models-index.json").write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    sums = []
    for path in sorted(p for p in output.rglob("*") if p.is_file()):
        sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return {"version": version, "models": len(models),
            "bytes": sum(row["bytes"] for row in models)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "build/runtime-options/device")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.source, args.output, args.version), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
