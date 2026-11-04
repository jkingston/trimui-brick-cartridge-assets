import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('share_assets', Path(__file__).resolve().parents[1] / 'tools/share_assets.py')
sharing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sharing)


class SharingTests(unittest.TestCase):
    def fixture(self, root, name, colour):
        folder = root / name
        folder.mkdir()
        vertices = struct.pack('<8f', 1, 2, 3, 99, 4, 5, 6, 98)
        unique = struct.pack('<2f', colour, colour + 1)
        (folder / 'data.bin').write_bytes(vertices + unique)
        (folder / 'label.png').write_bytes(b'\x89PNG\r\n\x1a\nlabel')
        doc = {'asset': {'version': '2.0'},
               'buffers': [{'uri': 'data.bin', 'byteLength': 40}],
               'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 32, 'byteStride': 16, 'target': 34962},
                               {'buffer': 0, 'byteOffset': 32, 'byteLength': 8}],
               'accessors': [{'bufferView': 0, 'componentType': 5126, 'type': 'VEC3', 'count': 2}],
               'images': [{'uri': 'label.png'}],
               'materials': [{'name': name, 'pbrMetallicRoughness': {'baseColorFactor': [colour, 0, 0, 1]}}],
               'nodes': [{'translation': [1, 2, 3]}]}
        path = folder / 'model.gltf'
        path.write_text(json.dumps(doc))
        return path

    def test_shared_bytes_preserve_interleaved_accessor_and_independent_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [self.fixture(root, 'red', .2), self.fixture(root, 'blue', .5)]
            report = sharing.pack(paths, root / 'packed')
            self.assertEqual(report['geometry_bytes_saved'], 32)
            self.assertEqual(report['shared_chunks'], 1)
            for path in paths:
                before, old_chunks = sharing.read(path)
                target = root / 'packed' / (path.parent.name + '.gltf')
                after, new_chunks = sharing.read(target)
                for field in ['accessors', 'materials', 'nodes']:
                    self.assertEqual(before[field], after[field])
                self.assertEqual(old_chunks, new_chunks)
                def decoded(chunks):
                    return [struct.unpack_from('<3f', chunks[0], i * 16) for i in range(2)]
                self.assertEqual(decoded(old_chunks), decoded(new_chunks))
                self.assertEqual(decoded(new_chunks), [(1, 2, 3), (4, 5, 6)])
                self.assertEqual(sharing.resource(target, after['images'][0]['uri']), b'\x89PNG\r\n\x1a\nlabel')
            self.assertEqual(len(list((root / 'packed/shared').glob('*.png'))), 1)
            with self.assertRaisesRegex(ValueError, 'exists'):
                sharing.pack(paths, root / 'packed')

    def test_rejects_compression_escape_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.fixture(root, 'red', .2)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                sharing.pack([path, path], root / 'out')
            doc = json.loads(path.read_text())
            doc['extensionsUsed'] = ['EXT_meshopt_compression']
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, 'uncompressed'):
                sharing.pack([path], root / 'out')
            del doc['extensionsUsed']
            doc['buffers'][0]['uri'] = '../secret.bin'
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, 'escapes'):
                sharing.pack([path], root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_sparse_accessor_and_padding_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.fixture(root, 'sparse', .2)
            doc = json.loads(path.read_text())
            raw = (path.parent / 'data.bin').read_bytes() + b'\x01\0\0\0' + struct.pack('<3f', 7, 8, 9)
            (path.parent / 'data.bin').write_bytes(raw)
            doc['buffers'][0]['byteLength'] = len(raw)
            doc['bufferViews'] += [{'buffer': 0, 'byteOffset': 40, 'byteLength': 1},
                                   {'buffer': 0, 'byteOffset': 44, 'byteLength': 12}]
            doc['accessors'].append({'componentType': 5126, 'type': 'VEC3', 'count': 2,
                'sparse': {'count': 1, 'indices': {'bufferView': 2, 'componentType': 5121},
                           'values': {'bufferView': 3}}})
            path.write_text(json.dumps(doc))
            sharing.pack([path], root / 'packed')
            after, chunks = sharing.read(root / 'packed/sparse.gltf')
            self.assertEqual(doc['accessors'], after['accessors'])
            decoded = [(0, 0, 0), (0, 0, 0)]
            decoded[chunks[2][0]] = struct.unpack('<3f', chunks[3])
            self.assertEqual(decoded, [(0, 0, 0), (7, 8, 9)])
            self.assertEqual(after['bufferViews'][3]['byteOffset'] % 4, 0)

    def test_failure_does_not_publish_partial_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.fixture(root, 'red', .2)
            (path.parent / 'label.png').unlink()
            with self.assertRaises(FileNotFoundError):
                sharing.pack([path], root / 'out')
            self.assertFalse((root / 'out').exists())
            self.assertFalse(list(root.glob('.shared-assets-*')))


if __name__ == '__main__':
    unittest.main()
