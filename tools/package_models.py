#!/usr/bin/env python3
"""Create a reproducible gzip-compressed tar archive of neutral runtime models."""

import argparse
import gzip
import hashlib
from pathlib import Path
import tarfile


def package(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("Output archive already exists")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files or not (source / "models-index.json").is_file():
        raise ValueError("Source is not a neutral model release")
    suffix = ".tar.gz"
    if not output.name.endswith(suffix):
        raise ValueError("Output archive must end in .tar.gz")
    archive_root = output.name.removesuffix(suffix)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    relative = path.relative_to(source).as_posix()
                    info = archive.gettarinfo(str(path), arcname=f"{archive_root}/{relative}")
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    info.mode = 0o644
                    info.pax_headers = {}
                    with path.open("rb") as contents:
                        archive.addfile(info, contents)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        print(package(args.source, args.output))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
