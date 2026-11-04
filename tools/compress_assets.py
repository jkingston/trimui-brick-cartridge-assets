#!/usr/bin/env python3
"""Create meshopt-compressed assets while retaining named artwork/material slots."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.parse

from cartridge import ROOT, read_model

FLAGS = ['-cc', '-vp', '16', '-vt', '16', '-vn', '12', '-km', '-kn', '-ke']


def source_files(model):
    document = read_model(model)
    files = {model.resolve()}
    for resource in document.get('buffers', []) + document.get('images', []):
        uri = resource.get('uri', '')
        if uri and not uri.startswith('data:'):
            parsed = urllib.parse.urlsplit(uri)
            if parsed.scheme or parsed.netloc:
                raise ValueError('Compression requires local resources')
            files.add((model.parent / urllib.parse.unquote(parsed.path)).resolve())
    return sorted(files)


def compress(model, output, command, reports):
    model = Path(model)
    files = source_files(model)
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(model.parent.resolve())).encode())
        digest.update(path.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        temporary = Path(temporary)
        target, report = temporary/'model.glb', temporary/'report.json'
        subprocess.run([*command, '-i', str(model), '-o', str(target), *FLAGS, '-r', str(report)], check=True)
        original, packed = read_model(model), read_model(target)
        names = lambda d: {m.get('name') for m in d.get('materials', [])}
        if names(original) != names(packed):
            raise ValueError('Compression changed named material slots')
        source_bytes = sum(p.stat().st_size for p in files)
        result = {'model': model.parent.name, 'source_sha256': digest.hexdigest(),
                  'source_bytes': source_bytes, 'compressed_bytes': target.stat().st_size,
                  'flags': FLAGS, 'required_extensions': packed.get('extensionsRequired', []),
                  'material_slots_preserved': True,
                  'simplification': False, 'gltfpack': json.loads(report.read_text())}
        target.replace(output)
        (reports/(model.parent.name+'.json')).write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--input', type=Path, default=ROOT/'assets')
    parser.add_argument('--output', type=Path, default=ROOT/'assets-compressed')
    parser.add_argument('--reports', type=Path, default=ROOT/'build/compression-reports')
    parser.add_argument('--gltfpack-command', nargs='+', default=['gltfpack'])
    args = parser.parse_args()
    models = args.models or [r['id'] for r in json.loads((ROOT/'sources/catalogue.json').read_text())['models']]
    for name in models:
        result = compress(args.input/name/'model.gltf', args.output/name/'model.glb', args.gltfpack_command, args.reports)
        print(f"{name}: {result['source_bytes']} -> {result['compressed_bytes']} bytes")


if __name__ == '__main__':
    main()
