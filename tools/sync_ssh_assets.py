#!/usr/bin/env python3
"""Synchronise curated artwork to a Brick over its USB SSH connection."""

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile

from sync_library_assets import sync

DEFAULT_TARGET = "root@192.168.7.2"
DEFAULT_INDEX = ("https://github.com/jkingston/trimui-brick-cartridge-assets/"
                 "releases/download/v0.2.0/artwork-index.json")


def command(ssh, options, target, remote):
    return [ssh, *sum((["-o", option] for option in options), []), target, remote]


def synchronise(target, index, remote_root="/data", cache=None, dry_run=False,
                ssh="ssh", ssh_options=(), models_index=None):
    scan = subprocess.run(command(ssh, ssh_options, target,
                                  f"brick-shell --scan-library {shlex.quote(remote_root)}"),
                          check=True, stdout=subprocess.PIPE, stderr=None)
    inventory = json.loads(scan.stdout)
    with tempfile.TemporaryDirectory(prefix="brick-assets-") as temporary:
        staging = Path(temporary)
        manifest, missing = sync(staging, index, output=staging / "Brick", cache=cache,
                                 dry_run=dry_run, prune=True, inventory=inventory,
                                 models_index_source=models_index)
        if dry_run:
            return manifest, missing
        archive = staging / "brick-assets.tar"
        with tarfile.open(archive, "w") as output:
            for path in sorted((staging / "Brick").rglob("*")):
                if path.is_file():
                    info = output.gettarinfo(str(path), arcname=path.relative_to(staging / "Brick").as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as contents:
                        output.addfile(info, contents)
        root = shlex.quote(remote_root.rstrip("/"))
        install = (
            "set -eu; "
            f"base={root}; "
            "rm -rf \"$base/Assets/Brick.new\"; "
            "mkdir -p \"$base/Assets/Brick.new\"; "
            "tar -xf - -C \"$base/Assets/Brick.new\"; "
            "rm -rf \"$base/Assets/Brick.old\"; "
            "if [ -e \"$base/Assets/Brick\" ]; then mv \"$base/Assets/Brick\" \"$base/Assets/Brick.old\"; fi; "
            "mv \"$base/Assets/Brick.new\" \"$base/Assets/Brick\"; "
            "rm -rf \"$base/Assets/Brick.old\""
        )
        with archive.open("rb") as payload:
            subprocess.run(command(ssh, ssh_options, target, install), stdin=payload, check=True)
        return manifest, missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default=DEFAULT_TARGET,
                        help=f"USB SSH host (default: {DEFAULT_TARGET})")
    parser.add_argument("--index", default=DEFAULT_INDEX,
                        help="Artwork release index")
    parser.add_argument("--remote-root", default="/data")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--models-index")
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--ssh-option", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest, missing = synchronise(args.target, args.index, args.remote_root,
                                        args.cache, args.dry_run, args.ssh, args.ssh_option,
                                        args.models_index)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")
    matched = sum("surfaces" in game for game in manifest["games"])
    print(f"Scanned {len(manifest['games'])} games: {matched} matched, {len(missing)} missing")
    for row in missing:
        print(f"missing\t{row['platform']}\t{row['rom']}\t{row['match']}")
    return 2 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
