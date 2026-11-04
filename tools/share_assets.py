#!/usr/bin/env python3
"""Pack raw portable glTFs with exact shared chunks; never infer mesh equivalence.

Usage: python tools/share_assets.py --output build/shared assets/*/model.gltf
The output must not exist. Flat model JSONs and their shared resources can be
moved together and remain compatible with cartridge.py's local-resource checks.
"""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import unquote, urlsplit


def resource(path, uri):
    if uri.startswith('data:'):
        header, data = uri.split(',', 1)
        if not header.endswith(';base64'):
            raise ValueError('Only base64 data URIs are supported')
        return base64.b64decode(data, validate=True)
    parsed = urlsplit(uri)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Resources must be local files or base64 data URIs')
    target = (path.parent / unquote(parsed.path)).resolve()
    if not target.is_relative_to(path.parent.resolve()):
        raise ValueError('Resource escapes model directory')
    return target.read_bytes()


def read(path):
    document = json.loads(path.read_text())
    if document.get('asset', {}).get('version') != '2.0':
        raise ValueError('Expected glTF 2.0 JSON')
    if any(x in document.get('extensionsUsed', []) for x in
           ['EXT_meshopt_compression', 'KHR_mesh_quantization', 'KHR_draco_mesh_compression']):
        raise ValueError('Use raw uncompressed glTF input')
    buffers = []
    for b in document.get('buffers', []):
        if b.get('extensions'):
            raise ValueError('Buffer extensions are unsupported')
        data = resource(path, b['uri'])
        if len(data) < b['byteLength']:
            raise ValueError('Truncated buffer')
        buffers.append(data[:b['byteLength']])
    chunks = []
    for view in document.get('bufferViews', []):
        if view.get('extensions'):
            raise ValueError('Buffer view extensions are unsupported')
        data = buffers[view['buffer']]
        start, size = view.get('byteOffset', 0), view['byteLength']
        if start < 0 or size <= 0 or start + size > len(data):
            raise ValueError('Buffer view outside buffer')
        chunks.append(data[start:start + size])
    return document, chunks


def pack(paths, output):
    paths, output = [Path(p).resolve() for p in paths], Path(output)
    if not paths:
        raise ValueError('At least one input is required')
    if output.exists():
        raise ValueError('Output already exists')
    models, owners = {}, {}
    for path in paths:
        name = path.parent.name if path.stem == 'model' else path.stem
        if name in models or name == 'shared':
            raise ValueError('Duplicate or reserved model id: ' + name)
        doc, chunks = read(path)
        models[name] = (path, doc, chunks)
        for chunk in chunks:
            owners.setdefault(hashlib.sha256(chunk).hexdigest(), set()).add(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.shared-assets-', dir=output.parent))
    try:
        (staging / 'shared').mkdir()
        input_bytes = 0
        for name, (path, original, chunks) in models.items():
            doc, unique, buffers, shared_indices = copy.deepcopy(original), bytearray(), [], {}
            def store(raw, suffix):
                relative = 'shared/' + hashlib.sha256(raw).hexdigest() + suffix
                target = staging / relative
                if target.exists() and target.read_bytes() != raw:
                    raise ValueError('Hash collision')
                if not target.exists():
                    target.write_bytes(raw)
                return relative
            for view, chunk in zip(doc.get('bufferViews', []), chunks):
                digest = hashlib.sha256(chunk).hexdigest()
                input_bytes += len(chunk)
                if len(owners[digest]) > 1:
                    if digest not in shared_indices:
                        shared_indices[digest] = len(buffers)
                        buffers.append({'uri': store(chunk, '.bin'), 'byteLength': len(chunk)})
                    view['buffer'], view['byteOffset'] = shared_indices[digest], 0
                else:
                    unique.extend(b'\0' * (-len(unique) % 4))
                    view['buffer'], view['byteOffset'] = -1, len(unique)
                    unique.extend(chunk)
            if unique:
                for view in doc['bufferViews']:
                    if view['buffer'] == -1:
                        view['buffer'] = len(buffers)
                (staging / (name + '.bin')).write_bytes(unique)
                buffers.append({'uri': name + '.bin', 'byteLength': len(unique)})
            doc['buffers'] = buffers
            for image in doc.get('images', []):
                if 'uri' in image:
                    raw = resource(path, image['uri'])
                    suffix = '.png' if raw.startswith(b'\x89PNG') else '.jpg' if raw.startswith(b'\xff\xd8') else '.webp' if raw[:4] == b'RIFF' else '.image'
                    image['uri'] = store(raw, suffix)
            target = staging / (name + '.gltf')
            target.write_text(json.dumps(doc, separators=(',', ':')) + '\n')
            # Unchanged accessors + byte-identical views also preserves sparse,
            # interleaved and matrix accessor interpretation without reindexing.
            checked, checked_chunks = read(target)
            if checked_chunks != chunks or checked.get('accessors') != original.get('accessors'):
                raise ValueError('Accessor storage changed')
        geometry_bytes = sum(p.stat().st_size for p in staging.rglob('*.bin'))
        report = {'models': len(models), 'input_view_bytes': input_bytes,
                  'packed_geometry_bytes': geometry_bytes,
                  'geometry_bytes_saved': input_bytes - geometry_bytes,
                  'shared_chunks': sum(len(v) > 1 for v in owners.values()),
                  'all_accessor_storage_verified': True}
        (staging / 'sharing-report.json').write_text(json.dumps(report, indent=2) + '\n')
        staging.rename(output)
        return report
    except BaseException:
        shutil.rmtree(staging)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('models', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps(pack(args.models, args.output), indent=2))
