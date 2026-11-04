#!/usr/bin/env python3
"""Resolve a physical game edition and prepare label artwork for a glTF model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text())


def resolve(catalogue, platform, title=None, region=None, edition=None):
    """Exact edition wins. Never infer a physical edition from a fuzzy title match."""
    if edition:
        matches = [r for r in catalogue['editions'] if r['id'] == edition and r['platform'] == platform]
    else:
        key = (title or '').casefold().strip()
        matches = [r for r in catalogue['editions'] if r['platform'] == platform
                   and key in [t.casefold() for t in r['titles']]
                   and (region is None or region in r['regions'])]
    if len(matches) > 1:
        raise ValueError('Ambiguous physical edition: ' + ', '.join(r['id'] for r in matches))
    if matches:
        if region is not None and region not in matches[0]['regions']:
            raise ValueError('Requested region conflicts with physical edition: ' + matches[0]['id'])
        return dict(matches[0], selection='exact')
    if edition:
        raise ValueError('Unknown edition for platform: ' + edition)
    defaults = catalogue['defaults'].get(platform)
    if not defaults:
        raise ValueError('Unsupported platform: ' + platform)
    model = defaults.get(region, defaults.get('default'))
    if not model:
        raise ValueError('Region required for ' + platform)
    return {'model': model, 'platform': platform, 'selection': 'generic',
            'accuracy_note': 'Generic platform shell; this release has not been physically verified.'}


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    raise ValueError('Artwork must be PNG or JPEG for portable glTF use')


def source_image_type(data):
    """Accept WebP scans for preparation; runtime artwork remains PNG/JPEG."""
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    return image_type(data)


def fetch_artwork(url, cache, expected_sha256=None, max_bytes=32 * 1024 * 1024):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('Artwork URLs must use HTTPS')
    if expected_sha256:
        for suffix in ('.png', '.jpg', '.webp'):
            candidate = Path(cache) / (expected_sha256 + suffix)
            if candidate.is_file():
                cached = candidate.read_bytes()
                if hashlib.sha256(cached).hexdigest() == expected_sha256:
                    source_image_type(cached)
                    return candidate
    request = urllib.request.Request(url, headers={'User-Agent': 'cartridge-assets/0.1'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if urllib.parse.urlsplit(response.url).scheme != 'https':
            raise ValueError('Artwork redirected away from HTTPS')
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('Artwork exceeds size limit')
    mime, suffix = source_image_type(data)
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError('Artwork checksum mismatch')
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (digest + suffix)
    target.write_bytes(data)
    (cache / (digest + '.json')).write_text(json.dumps({'url': url, 'sha256': digest, 'mime_type': mime}, indent=2) + '\n')
    return target


def read_model(path):
    """Read glTF JSON or a glTF 2 binary, retaining its embedded binary buffer."""
    import base64
    data = Path(path).read_bytes()
    if data[:4] != b'glTF':
        return json.loads(data)
    if len(data) < 20:
        raise ValueError('Truncated GLB header')
    magic, version, length = struct.unpack_from('<4sII', data)
    if version != 2 or length != len(data):
        raise ValueError('Invalid GLB version or length')
    offset, document, binary = 12, None, None
    while offset < length:
        if offset + 8 > length:
            raise ValueError('Truncated GLB chunk')
        size, kind = struct.unpack_from('<I4s', data, offset)
        offset += 8
        if size % 4 or offset + size > length:
            raise ValueError('Invalid GLB chunk size')
        chunk = data[offset:offset + size]
        offset += size
        if kind == b'JSON':
            if document is not None:
                raise ValueError('Duplicate GLB JSON chunk')
            document = json.loads(chunk)
        elif kind == b'BIN\x00':
            if binary is not None:
                raise ValueError('Duplicate GLB binary chunk')
            binary = chunk
    if document is None:
        raise ValueError('GLB has no JSON chunk')
    if binary is not None:
        buffers = document.get('buffers', [])
        if not buffers or 'uri' in buffers[0] or buffers[0]['byteLength'] > len(binary):
            raise ValueError('Invalid embedded GLB buffer')
        buffers[0]['uri'] = 'data:application/octet-stream;base64,' + base64.b64encode(binary).decode('ascii')
    return document


def compose(model, slot, artwork, output, shared_assets=None, artwork_fit='strict'):
    """Replace a single named surface without changing other image bindings."""
    return compose_surfaces(model, {slot: artwork}, output, shared_assets, artwork_fit=artwork_fit)


def apply_material_variant(document, variant):
    """Recolour explicitly named untextured surfaces without duplicating geometry."""
    import math
    if variant.get('binding') != 'base-colour-override':
        raise ValueError('Unsupported material variant binding')
    for name, colour in variant['base_color_factors'].items():
        matches = [m for m in document.get('materials', []) if m.get('name') == name]
        if name.startswith('artwork.') or len(matches) != 1:
            raise ValueError('Expected one non-artwork material named ' + name)
        if len(colour) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in colour):
            raise ValueError('Material colours must be four finite linear values in [0, 1]')
        pbr = matches[0].setdefault('pbrMetallicRoughness', {})
        if 'baseColorTexture' in pbr:
            raise ValueError('Colour overrides require an untextured base colour: ' + name)
        pbr['baseColorFactor'] = list(colour)


def resource_bytes(document, resource, model):
    import base64
    if 'bufferView' in resource:
        view = document['bufferViews'][resource['bufferView']]
        data = resource_bytes(document, document['buffers'][view['buffer']], model)
        offset = view.get('byteOffset', 0)
        return data[offset:offset + view['byteLength']]
    uri = resource.get('uri', '')
    if uri.startswith('data:'):
        header, encoded = uri.split(',', 1)
        if not header.endswith(';base64'):
            raise ValueError('Only base64 embedded resources are supported')
        return base64.b64decode(encoded, validate=True)
    parsed = urllib.parse.urlsplit(uri)
    if not uri or parsed.scheme or parsed.netloc:
        raise ValueError('Expected a local model resource')
    parent = Path(model).resolve().parent
    path = (parent / urllib.parse.unquote(parsed.path)).resolve()
    if not path.is_relative_to(parent):
        raise ValueError('Model resource escapes model directory')
    return path.read_bytes()


def fit_artwork(document, texture_info, artwork, model, mode='strict'):
    """Keep replacement aspect compatible with the reference texture's UV contract."""
    from PIL import Image, ImageOps, ImageCms
    import io
    if mode not in ('strict', 'contain', 'cover'):
        raise ValueError('Artwork fit must be strict, contain or cover')
    texture = document['textures'][texture_info['index']]
    reference = document['images'][texture['source']]
    with Image.open(io.BytesIO(resource_bytes(document, reference, model))) as original:
        ratio = original.width / original.height
    with Image.open(artwork) as source:
        source = ImageOps.exif_transpose(source)
        profile = source.info.get('icc_profile')
        if profile:
            source = ImageCms.profileToProfile(source.convert('RGBA'), ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile('sRGB'), outputMode='RGBA')
        else:
            source = source.convert('RGBA')
        mismatch = abs((source.width/source.height)/ratio - 1)
        if mismatch > .02 and mode == 'strict':
            raise ValueError(f'Artwork aspect differs from label UV contract by {mismatch:.1%}; rectify with prepare --quad/--size, or explicitly choose --fit contain/cover')
        if mismatch > .02:
            width = max(source.width, round(source.height * ratio))
            target = (width, max(1, round(width / ratio)))
            if max(target) > 8192:
                raise ValueError('Fitted artwork exceeds 8192 pixels')
            if mode == 'contain':
                source = ImageOps.pad(source, target, method=Image.Resampling.LANCZOS, color=(0,0,0,0))
            else:
                source = ImageOps.fit(source, target, method=Image.Resampling.LANCZOS)
        has_alpha = source.getchannel('A').getextrema()[0] < 255
        source.info.clear()
        output = io.BytesIO()
        source.save(output, format='PNG')
        return output.getvalue(), has_alpha


def prune_artwork_resources(document):
    """Remove unused textures/images after rebinding, retaining shared users."""
    bindings = []
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith('Texture') and isinstance(child, dict) and 'index' in child:
                    bindings.append(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(document.get('materials', []))
    # Unknown document extensions could reference images outside materials.
    if document.get('extensions'):
        return
    textures = document.get('textures', [])
    used = sorted({binding['index'] for binding in bindings})
    remap = {old: new for new, old in enumerate(used)}
    for binding in bindings:
        binding['index'] = remap[binding['index']]
    document['textures'] = [textures[index] for index in used]
    sources = []
    for texture in document['textures']:
        if 'source' in texture:
            sources.append(texture)
        for extension in texture.get('extensions', {}).values():
            if 'source' in extension:
                sources.append(extension)
    images = document.get('images', [])
    used_images = sorted({source['source'] for source in sources})
    remap_images = {old: new for new, old in enumerate(used_images)}
    for source in sources:
        source['source'] = remap_images[source['source']]
    document['images'] = [images[index] for index in used_images]


def compose_surfaces(model, replacements, output, shared_assets=None, hide_surfaces=(), material_variant=None, artwork_fit='strict'):
    """Bind one or more release-specific surfaces while preserving each UV set."""
    import base64
    document = read_model(model)
    if material_variant is not None:
        apply_material_variant(document, material_variant)
    bindings = []
    for material in document.get('materials', []):
        if material.get('name') in hide_surfaces:
            if material['name'] in replacements:
                raise ValueError('Cannot replace and hide the same surface')
            pbr = material.setdefault('pbrMetallicRoughness', {})
            factor = list(pbr.get('baseColorFactor', [1, 1, 1, 1]))
            factor[3] = 0
            pbr['baseColorFactor'] = factor
            material['alphaMode'] = 'MASK'
            material['alphaCutoff'] = 0.5
    for slot, artwork in replacements.items():
        matches = [m for m in document.get('materials', []) if m.get('name') == slot]
        if len(matches) != 1:
            raise ValueError('Expected exactly one material named ' + slot)
        texture_info = matches[0].get('pbrMetallicRoughness', {}).get('baseColorTexture')
        if texture_info is None:
            raise ValueError('Material has no base-colour texture/UV binding: ' + slot)
        data, has_alpha = fit_artwork(document, texture_info, artwork, model, artwork_fit)
        if has_alpha and matches[0].get('alphaMode', 'OPAQUE') == 'OPAQUE':
            matches[0]['alphaMode'] = 'MASK'
            matches[0]['alphaCutoff'] = .5
        bindings.append((texture_info, data, 'image/png'))
    # Embed existing relative resources so moving the assembled glTF stays safe.
    parent = Path(model).resolve().parent
    for resource in document.get('buffers', []) + document.get('images', []):
        uri = resource.get('uri', '')
        if not uri or uri.startswith('data:'):
            continue
        parsed = urllib.parse.urlsplit(uri)
        if parsed.scheme or parsed.netloc:
            raise ValueError('Model resources must be local files or data URIs')
        path = (parent / urllib.parse.unquote(parsed.path)).resolve()
        if not path.is_relative_to(parent):
            raise ValueError('Model resource escapes model directory')
        raw = path.read_bytes()
        kind = resource.get('mimeType')
        if not kind:
            kind = image_type(raw)[0] if resource in document.get('images', []) else 'application/octet-stream'
        resource['uri'] = f'data:{kind};base64,' + base64.b64encode(raw).decode('ascii')
    for texture_info, data, mime in bindings:
        images = document.setdefault('images', [])
        images.append({'uri': f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')})
        old = document['textures'][texture_info['index']]
        texture = {'source': len(images) - 1}
        if 'sampler' in old:
            texture['sampler'] = old['sampler']
        document['textures'].append(texture)
        texture_info['index'] = len(document['textures']) - 1
    prune_artwork_resources(document)
    from gltf_resources import compact_buffers
    compact_buffers(document)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if shared_assets is not None:
        externalize(document, output, Path(shared_assets))
    output.write_text(json.dumps(document, separators=(',', ':')) + '\n')
    return output


def externalize(document, output, directory):
    """Store identical buffers/images once across an arbitrary game collection."""
    import base64
    directory.mkdir(parents=True, exist_ok=True)
    for resource in document.get('buffers', []) + document.get('images', []):
        uri = resource.get('uri', '')
        if not uri.startswith('data:'):
            continue
        header, encoded = uri.split(',', 1)
        if not header.endswith(';base64'):
            raise ValueError('Only base64 data resources are supported')
        raw = base64.b64decode(encoded, validate=True)
        mime = header[5:-7]
        suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}.get(mime, '.bin')
        target = directory / (hashlib.sha256(raw).hexdigest() + suffix)
        if not target.exists():
            target.write_bytes(raw)
        elif target.read_bytes() != raw:
            raise ValueError('Shared asset cache content does not match its hash')
        resource['uri'] = urllib.parse.quote(os.path.relpath(target.resolve(), output.parent.resolve()).replace(os.sep, '/'), safe='/')


def prepare_artwork(source, output, crop=None, quad=None, size=None):
    """Extract a label from a front-on scan without stretching its proportions."""
    from PIL import Image, ImageOps, ImageCms
    from artwork_geometry import perspective_coefficients
    import io
    if crop is not None and quad is not None:
        raise ValueError('Choose crop or quad, not both')
    if (quad is None) != (size is None):
        raise ValueError('Perspective preparation requires both quad and size')
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        profile = image.info.get('icc_profile')
        if profile:
            image = ImageCms.profileToProfile(
                image.convert('RGBA'), ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile('sRGB'), outputMode='RGBA')
        else:
            image = image.convert('RGBA')
        # glTF colour textures are sRGB; discard metadata after conversion.
        image.info.clear()
        if crop:
            left, top, right, bottom = crop
            if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                raise ValueError('Crop lies outside source image')
            image = image.crop(crop)
        if quad is not None:
            coefficients = perspective_coefficients(quad, size, image.size)
            image = image.transform(tuple(size), Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format='PNG')
    return output


def acquire_artwork(recipe, output, cache):
    """Fetch and prepare a verified release-specific recipe, retaining provenance."""
    source = fetch_artwork(recipe['url'], cache, recipe.get('sha256'))
    target = prepare_artwork(source, output, recipe.get('crop'), recipe.get('quad'), recipe.get('size'))
    provenance = dict(recipe)
    provenance['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    provenance['output_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + '.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return target


def assemble(platform, output, artwork=None, title=None, region=None, edition=None, root=ROOT, surfaces=None, asset_format="portable", cache=None, artwork_fit="strict"):
    """Resolve a game and bind prepared label art to its reusable runtime model."""
    root = Path(root)
    selection = resolve(read_json(root / 'catalogue/games.json'), platform, title, region, edition)
    model_id = selection['model']
    model_catalogue = read_json(root / 'catalogue/models.json')
    variant_id = selection.get('material_variant')
    variant = None
    if variant_id:
        variant = model_catalogue['materials'][variant_id]
        if variant['source_model'] != model_id:
            raise ValueError('Material variant conflicts with selected model')
    if asset_format not in ('portable', 'compressed'):
        raise ValueError('Unsupported asset format')
    model = root / ('assets-compressed' if asset_format == 'compressed' else 'assets') / model_id / ('model.glb' if asset_format == 'compressed' else 'model.gltf')
    if not model.is_file():
        raise ValueError('Runtime model has not been built: ' + model_id)
    document = read_model(model)
    slots = [m['name'] for m in document.get('materials', [])
             if m.get('name') in ('artwork.front', 'artwork.disc')]
    if len(slots) != 1:
        raise ValueError('Runtime model needs one unambiguous primary artwork slot')
    output = Path(output)
    recipe_id = None
    recipe_surfaces = {}
    provenance = {}
    if artwork is None:
        recipe_id = selection.get('artwork_recipe')
        if not recipe_id:
            raise ValueError('No verified artwork recipe for this edition; supply --artwork')
        recipe = read_json(root / 'catalogue/artwork.json')['recipes'][recipe_id]
        if (recipe['model'] != model_id or recipe['platform'] != platform
                or recipe['region'] not in selection['regions'] or recipe['slot'] != slots[0]):
            raise ValueError('Artwork recipe conflicts with selected physical edition')
        cache = Path(cache) if cache is not None else root / '.cache/artwork'
        artwork = acquire_artwork(recipe, cache / 'prepared' / (recipe_id + '.png'), cache)
        provenance[slots[0]] = {key: value for key, value in recipe.items() if key != 'surfaces'}
        for slot, surface in recipe.get('surfaces', {}).items():
            if not slot.startswith('artwork.') or slot == slots[0]:
                raise ValueError('Invalid secondary artwork slot: ' + slot)
            if slot not in (surfaces or {}):
                recipe_surfaces[slot] = acquire_artwork(surface, cache / 'prepared' / (recipe_id + '-' + slot + '.png'), cache)
                provenance[slot] = surface
    replacements = {slots[0]: artwork, **recipe_surfaces}
    for slot, path in (surfaces or {}).items():
        if slot in replacements:
            raise ValueError('Primary artwork slot supplied twice: ' + slot)
        if not slot.startswith('artwork.'):
            raise ValueError('Additional surfaces must use artwork.* slots')
        replacements[slot] = path
    hidden = []
    if selection['selection'] == 'generic':
        hidden = [m['name'] for m in document.get('materials', [])
                  if m.get('name', '').startswith('artwork.') and m['name'] not in replacements]
    compose_surfaces(model, replacements, output, output.parent / 'shared', hide_surfaces=hidden, material_variant=variant, artwork_fit=artwork_fit)
    choices = dict(next(r for r in model_catalogue['models'] if r['id'] == model_id))
    if variant_id:
        choices['material'] = variant_id
    details = {'schema_version': 1, 'title': title, 'region': region,
               'selection': selection, 'choices': choices,
               'artwork_sha256': hashlib.sha256(Path(artwork).read_bytes()).hexdigest(),
               'material_slot': slots[0], 'runtime_model': model_id, 'asset_format': asset_format,
               'artwork_recipe': recipe_id,
               'artwork_sources': provenance,
               'material_variant': variant_id,
               'artwork_fit': artwork_fit,
               'hidden_secondary_artwork': hidden,
               'surface_sha256': {slot: hashlib.sha256(Path(path).read_bytes()).hexdigest() for slot, path in replacements.items()},
               'note': 'Exact editions retain verified secondary artwork; generic selections hide unreplaced artwork surfaces. Board imagery remains from the selected reference model.'}
    output.with_suffix(output.suffix + '.json').write_text(json.dumps(details, indent=2) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    g = sub.add_parser('assemble')
    g.add_argument('--format', choices=['portable', 'compressed'], default='portable')
    g.add_argument('--fit', choices=['strict', 'contain', 'cover'], default='strict')
    g.add_argument('platform'); g.add_argument('--title'); g.add_argument('--region'); g.add_argument('--edition')
    g.add_argument('--surface', action='append', default=[], metavar='SLOT=PATH');
    g.add_argument('--artwork', type=Path); g.add_argument('--output', type=Path, required=True)
    g.add_argument('--cache', type=Path, default=ROOT / '.cache/artwork')
    r = sub.add_parser('resolve')
    r.add_argument('platform'); r.add_argument('--title'); r.add_argument('--region'); r.add_argument('--edition')
    f = sub.add_parser('fetch')
    f.add_argument('url'); f.add_argument('--cache', type=Path, default=ROOT / '.cache/artwork'); f.add_argument('--sha256')
    art = sub.add_parser('artwork')
    art.add_argument('recipe'); art.add_argument('--output', type=Path, required=True)
    art.add_argument('--cache', type=Path, default=ROOT / '.cache/artwork')
    a = sub.add_parser('prepare')
    a.add_argument('source', type=Path); a.add_argument('--output', type=Path, required=True)
    a.add_argument('--crop', type=int, nargs=4, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    a.add_argument('--quad', type=float, nargs=8, metavar=('TLX','TLY','TRX','TRY','BRX','BRY','BLX','BLY'))
    a.add_argument('--size', type=int, nargs=2, metavar=('WIDTH','HEIGHT'))
    c = sub.add_parser('compose')
    c.add_argument('model', type=Path); c.add_argument('--slot', required=True); c.add_argument('--artwork', type=Path, required=True); c.add_argument('--output', type=Path, required=True); c.add_argument('--shared-assets', type=Path)
    c.add_argument('--fit', choices=['strict', 'contain', 'cover'], default='strict')
    args = parser.parse_args()
    try:
        if args.command == 'assemble':
            surfaces = {}
            for value in args.surface:
                slot, separator, path = value.partition('=')
                if not separator or not path or slot in surfaces:
                    raise ValueError('Expected unique SLOT=PATH surface assignments')
                surfaces[slot] = Path(path)
            print(assemble(args.platform, args.output, args.artwork, args.title, args.region, args.edition, surfaces=surfaces, asset_format=args.format, cache=args.cache, artwork_fit=args.fit))
        elif args.command == 'resolve':
            print(json.dumps(resolve(read_json(ROOT / 'catalogue/games.json'), args.platform, args.title, args.region, args.edition), indent=2))
        elif args.command == 'fetch':
            print(fetch_artwork(args.url, args.cache, args.sha256))
        elif args.command == 'artwork':
            recipes = read_json(ROOT / 'catalogue/artwork.json')['recipes']
            if args.recipe not in recipes:
                raise ValueError('Unknown artwork recipe: ' + args.recipe)
            print(acquire_artwork(recipes[args.recipe], args.output, args.cache))
        elif args.command == 'prepare':
            print(prepare_artwork(args.source, args.output, args.crop, args.quad, args.size))
        else:
            print(compose(args.model, args.slot, args.artwork, args.output, args.shared_assets, args.fit))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
