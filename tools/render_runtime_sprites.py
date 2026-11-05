#!/usr/bin/env python3
"""Pre-render installed glTF models into transparent framebuffer sprites."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from cartridge import ROOT, compose_surfaces, read_model
from sync_library_assets import atomic_write, generated_label, runtime_texture


def render(asset_root, maximum=256):
    asset_root = Path(asset_root).resolve()
    manifest_path = asset_root / "library.json"
    manifest = json.loads(manifest_path.read_text())
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="runtime-sprites-", dir=build))
    try:
        jobs, rows = [], []
        for index, row in enumerate(manifest["games"]):
            model_id = row.get("model")
            if not model_id:
                continue
            model = asset_root / "models" / f"{model_id}.glb"
            document = read_model(model)
            primary = [material["name"] for material in document.get("materials", [])
                       if material.get("name") in ("artwork.front", "artwork.disc")]
            if len(primary) != 1:
                raise ValueError(f"Model has no unambiguous artwork surface: {model_id}")
            replacements = {slot: asset_root / path
                            for slot, path in row.get("surfaces", {}).items()}
            fit = "strict"
            if primary[0] not in replacements:
                label = work / f"label-{index}.png"
                label.write_bytes(generated_label(row["title"], row["platform"]))
                replacements[primary[0]] = label
                fit = "contain"
            composed = work / f"game-{index}.gltf"
            compose_surfaces(model, replacements, composed, work / "shared",
                             artwork_fit=fit)
            output = work / f"game-{index}.png"
            jobs.append({
                "id": f"game-{index}",
                "asset": "../" + composed.relative_to(ROOT).as_posix(),
                "output": str(output),
            })
            rows.append((row, output))
        jobs_path = work / "jobs.json"
        jobs_path.write_text(json.dumps(jobs))
        subprocess.run(["node", str(ROOT / "preview/capture.mjs"), str(jobs_path)],
                       cwd=ROOT, check=True)
        for row, image in rows:
            data = runtime_texture(image.read_bytes(), maximum)
            digest = hashlib.sha256(data).hexdigest()
            relative = f"textures/sprites/{digest}.brtx"
            atomic_write(asset_root / relative, data)
            row.setdefault("textures", {})["sprite.hero"] = relative
        atomic_write(manifest_path,
                     (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
        return len(rows)
    finally:
        shutil.rmtree(work, ignore_errors=True)
