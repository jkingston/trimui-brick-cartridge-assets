#!/usr/bin/env python3
"""Normalize exported glTF images without applying colour transforms to data maps."""
import argparse
import io
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageChops, ImageCms


def normalize(path):
    path = Path(path)
    gltf = json.loads(path.read_text())
    colour, data = set(), set()
    def visit(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name.endswith('Texture') and isinstance(item, dict) and 'index' in item:
                    texture = gltf['textures'][item['index']]
                    target = colour if name in {'baseColorTexture', 'emissiveTexture',
                                                 'specularColorTexture', 'sheenColorTexture'} else data
                    target.add(texture['source'])
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(gltf.get('materials', []))
    if colour & data:
        raise ValueError('An image is used as both colour and data; split it explicitly')
    report = []
    for index, descriptor in enumerate(gltf.get('images', [])):
        source = path.parent / descriptor['uri']
        with Image.open(source) as original:
            original.load()
            before = original.convert('RGBA' if 'A' in original.getbands() or 'transparency' in original.info else 'RGB')
            image = before
            icc = original.info.get('icc_profile')
            operation = 'preserve authored pixels; strip metadata'
            if icc and index in colour:
                profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                converted = ImageCms.profileToProfile(before.convert('RGB'), profile,
                    ImageCms.createProfile('sRGB'), outputMode='RGB')
                if before.mode == 'RGBA':
                    converted.putalpha(before.getchannel('A'))
                image = converted
                operation = 'embedded ICC to sRGB'
            elif 'chromaticity' in original.info and index in colour:
                # Blender's selected source image node already interprets these pixels as sRGB.
                # cHRM alone lacks a transfer function: do not guess a different source profile.
                operation = 'preserve authored sRGB interpretation; discard unprofiled cHRM'
            diff = ImageChops.difference(before, image)
            max_delta = max(high for low, high in diff.getextrema())
            clean = Image.frombytes(image.mode, image.size, image.tobytes())
            output = source.with_suffix('.png')
            clean.save(output, 'PNG', compress_level=9)
            if output != source:
                source.unlink()
            descriptor['uri'] = output.relative_to(path.parent).as_posix()
            descriptor['mimeType'] = 'image/png'
            report.append({'image': descriptor['uri'], 'operation': operation,
                           'data_texture': index in data, 'maximum_channel_change': max_delta,
                           'size': list(image.size),
                           'normalized_sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
    path.write_text(json.dumps(gltf, separators=(',', ':')) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = normalize(args.model)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
    else:
        print(json.dumps(result, indent=2))
