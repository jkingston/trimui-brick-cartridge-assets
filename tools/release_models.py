#!/usr/bin/env python3
"""Build a deterministic range-addressable pack of neutral runtime models."""

import argparse
import hashlib
import json
from pathlib import Path


def build(source, output, version):
    source, output = Path(source), Path(output)
    original = json.loads((source / "models-index.json").read_text())
    output.mkdir(parents=True, exist_ok=False)
    offset, rows = 0, []
    with (output / "models.pack").open("wb") as pack:
        for row in sorted(original["models"], key=lambda item: item["id"]):
            path = source / row["path"]
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != row["sha256"] or len(data) != row["bytes"]:
                raise ValueError(f"Model index mismatch: {row['id']}")
            rows.append({**row, "pack": "models.pack", "offset": offset})
            pack.write(data)
            offset += len(data)
    document = {"schema_version": 1, "version": version, "models": rows}
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (output / "models-index.json").write_bytes(encoded)
    sums = []
    for path in sorted(output.iterdir()):
        sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output / "models-SHA256SUMS").write_text("\n".join(sums) + "\n")
    return {"models": len(rows), "bytes": offset}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.source, args.output, args.version), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
