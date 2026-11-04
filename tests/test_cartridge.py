import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

spec = importlib.util.spec_from_file_location('cartridge', Path(__file__).resolve().parents[1] / 'tools/cartridge.py')
cartridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cartridge)


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = cartridge.read_json(cartridge.ROOT / 'catalogue/games.json')

    def test_region_is_required_when_shell_differs(self):
        with self.assertRaisesRegex(ValueError, 'Region required'):
            cartridge.resolve(self.catalogue, 'snes')
        self.assertEqual(cartridge.resolve(self.catalogue, 'snes', region='EU')['model'], 'snes-pal')
        self.assertEqual(cartridge.resolve(self.catalogue, 'snes', region='JP')['model'], 'snes-jp')

    def test_pokemon_regional_edition_cannot_be_guessed(self):
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            cartridge.resolve(self.catalogue, 'gbc', 'Pokémon Gold')
        self.assertEqual(cartridge.resolve(self.catalogue, 'gbc', 'Pokémon Gold', 'JP')['model'], 'gold-jp')

    def test_explicit_edition_cannot_silently_override_region(self):
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            cartridge.resolve(self.catalogue, 'gbc', region='US', edition='gold-jp')

    def test_unknown_game_is_explicitly_generic(self):
        self.assertEqual(cartridge.resolve(self.catalogue, 'gba', 'Unknown title')['selection'], 'generic')

    def test_editions_are_unique_and_explicitly_selectable(self):
        seen = set()
        for row in self.catalogue['editions']:
            result = cartridge.resolve(self.catalogue, row['platform'], edition=row['id'])
            self.assertEqual(result['id'], row['id'])
            for title in row['titles']:
                for region in row['regions']:
                    key = (row['platform'], title.casefold(), region, row['model'])
                    self.assertNotIn(key, seen, 'Duplicate physical edition mapping')
                    seen.add(key)

    def test_same_title_region_can_require_mould_revision(self):
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            cartridge.resolve(self.catalogue, 'snes', 'Super Mario World', 'US')
        result = cartridge.resolve(self.catalogue, 'snes', edition='snes-us-locking')
        self.assertEqual(result['model'], 'snes-us-locking')

    def test_every_model_reference_exists(self):
        available = {x['id'] for x in cartridge.read_json(cartridge.ROOT / 'sources/catalogue.json')['models']}
        for row in self.catalogue['editions']:
            self.assertIn(row['model'], available)
        for regional in self.catalogue['defaults'].values():
            for model in regional.values():
                self.assertIn(model, available)


class CompositionTests(unittest.TestCase):
    def test_compaction_removes_embedded_old_image_but_preserves_meshopt(self):
        from gltf_resources import compact_buffers
        import base64
        raw=b'OLDIMAGE'+b'compressed!'+b'padding!!'+b'NEWIMAGE'
        document={'buffers':[{'byteLength':len(raw),'uri':'data:application/octet-stream;base64,'+base64.b64encode(raw).decode()}, {'byteLength':60}],
            'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':8},
                {'buffer':1,'byteOffset':0,'byteLength':60,'extensions':{'EXT_meshopt_compression':{'buffer':0,'byteOffset':8,'byteLength':11,'count':5,'byteStride':12,'mode':'ATTRIBUTES'}}},
                {'buffer':0,'byteOffset':28,'byteLength':8}],
            'accessors':[{'bufferView':1}], 'images':[{'bufferView':2}]}
        compact_buffers(document)
        self.assertEqual(document['accessors'][0]['bufferView'],0)
        self.assertEqual(document['images'][0]['bufferView'],1)
        self.assertEqual(len(document['bufferViews']),2)
        result=base64.b64decode(document['buffers'][0]['uri'].split(',')[1])
        self.assertNotIn(b'OLDIMAGE',result)
        extension=document['bufferViews'][0]['extensions']['EXT_meshopt_compression']
        self.assertEqual(result[extension['byteOffset']:extension['byteOffset']+extension['byteLength']],b'compressed!')
        image=document['bufferViews'][1]
        self.assertEqual(image['byteOffset']%4,0)
        self.assertEqual(result[image['byteOffset']:image['byteOffset']+image['byteLength']],b'NEWIMAGE')

    def test_wrong_aspect_rejected_and_contain_preserves_shape(self):
        from PIL import Image, ImageDraw
        import io
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            Image.new('RGB',(40,20),'white').save(p/'original.png')
            image=Image.new('RGBA',(20,20),(255,255,255,255))
            ImageDraw.Draw(image).rectangle((4,4,15,15),fill=(255,0,0,255))
            image.save(p/'new.png')
            document={'images':[{'uri':'original.png'}],'textures':[{'source':0}]}
            with self.assertRaisesRegex(ValueError,'Artwork aspect'):
                cartridge.fit_artwork(document,{'index':0},p/'new.png',p/'model.gltf')
            data,alpha=cartridge.fit_artwork(document,{'index':0},p/'new.png',p/'model.gltf','contain')
            self.assertTrue(alpha)
            with Image.open(io.BytesIO(data)) as result:
                self.assertEqual(result.size,(40,20))
                red=[(x,y) for y in range(20) for x in range(40) if result.getpixel((x,y))==(255,0,0,255)]
                self.assertEqual(max(x for x,y in red)-min(x for x,y in red),11)
                self.assertEqual(max(y for x,y in red)-min(y for x,y in red),11)
                self.assertEqual(result.getpixel((0,10))[3],0)

    def test_pruning_preserves_shared_material_and_extension_textures(self):
        document = {'materials': [
            {'pbrMetallicRoughness': {'baseColorTexture': {'index': 2}}},
            {'normalTexture': {'index': 0}, 'extensions': {'KHR_materials_clearcoat': {'clearcoatTexture': {'index': 2}}}}],
            'textures': [{'source': 0}, {'source': 1}, {'source': 2}],
            'images': [{'uri': 'keep-normal'}, {'uri': 'discard-old-label'}, {'uri': 'keep-label'}]}
        cartridge.prune_artwork_resources(document)
        self.assertEqual([i['uri'] for i in document['images']], ['keep-normal', 'keep-label'])
        self.assertEqual(document['materials'][0]['pbrMetallicRoughness']['baseColorTexture']['index'], 1)
        self.assertEqual(document['materials'][1]['extensions']['KHR_materials_clearcoat']['clearcoatTexture']['index'], 1)
        self.assertEqual(document['textures'][1]['source'], 1)

    def test_perspective_rectification_preserves_four_corners(self):
        from artwork_geometry import perspective_coefficients
        quad = [12, 8, 88, 15, 96, 76, 5, 70]
        coefficients = perspective_coefficients(quad, [80, 60], [100, 80])
        a,b,c,d,e,f,g,h = coefficients
        for (u,v), (x,y) in zip([(0,0),(80,0),(80,60),(0,60)], zip(quad[::2],quad[1::2])):
            self.assertAlmostEqual((a*u+b*v+c)/(g*u+h*v+1),x)
            self.assertAlmostEqual((d*u+e*v+f)/(g*u+h*v+1),y)
        for invalid in [[0,0,80,60,80,0,0,60], [0,0,0,0,80,60,0,60], [-1,0,80,0,80,60,0,60]]:
            with self.assertRaises(ValueError):
                perspective_coefficients(invalid,[80,60],[100,80])
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            Image.new('RGB',(100,80),'red').save(p/'source.png')
            cartridge.prepare_artwork(p/'source.png',p/'rectified.png',quad=quad,size=[80,60])
            with Image.open(p/'rectified.png') as result:
                self.assertEqual(result.size,(80,60))
                self.assertEqual(result.getpixel((40,30)),(255,0,0,255))
            with self.assertRaisesRegex(ValueError, 'Choose crop'):
                cartridge.prepare_artwork(p/'source.png',p/'bad.png',crop=[0,0,20,20],quad=quad,size=[80,60])

    def test_shell_recolour_preserves_geometry_and_unrelated_surfaces(self):
        import copy
        document = {'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
                    'materials': [
                        {'name': 'shell', 'pbrMetallicRoughness': {'baseColorFactor': [.4, .4, .4, 1], 'roughnessFactor': .3}, 'normalTexture': {'index': 0}},
                        {'name': 'metal', 'pbrMetallicRoughness': {'baseColorFactor': [.7, .6, .5, 1]}},
                        {'name': 'artwork.front', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 1}}}]}
        before = copy.deepcopy(document)
        variant = {'binding': 'base-colour-override', 'base_color_factors': {'shell': [.5, .01, .02, 1]}}
        cartridge.apply_material_variant(document, variant)
        self.assertEqual(document['meshes'], before['meshes'])
        self.assertEqual(document['materials'][1:], before['materials'][1:])
        self.assertEqual(document['materials'][0]['normalTexture'], before['materials'][0]['normalTexture'])
        self.assertEqual(document['materials'][0]['pbrMetallicRoughness']['roughnessFactor'], .3)
        self.assertEqual(document['materials'][0]['pbrMetallicRoughness']['baseColorFactor'], [.5, .01, .02, 1])
        for factors in [{'artwork.front': [1, 0, 0, 1]}, {'missing': [1, 0, 0, 1]}, {'shell': [float('nan'), 0, 0, 1]}]:
            with self.assertRaises(ValueError):
                cartridge.apply_material_variant(copy.deepcopy(before), dict(variant, base_color_factors=factors))

    def test_automatic_artwork_uses_verified_recipe_and_preserves_disc_material(self):
        import hashlib
        from unittest.mock import patch
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p/'catalogue').mkdir()
            (p/'assets/disc').mkdir(parents=True)
            cache = p/'cache'
            cache.mkdir()
            Image.new('RGB', (12, 12), 'red').save(p/'scan.png')
            data = (p/'scan.png').read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            (cache/(digest+'.png')).write_bytes(data)
            games = {'defaults': {'psx': {'default': 'disc'}}, 'editions': [
                {'id': 'verified', 'titles': ['Verified'], 'platform': 'psx', 'regions': ['US'],
                 'model': 'disc', 'artwork_recipe': 'verified'}]}
            (p/'catalogue/games.json').write_text(json.dumps(games))
            (p/'catalogue/models.json').write_text(json.dumps({'models': [{'id': 'disc'}]}))
            recipe = {'platform': 'psx', 'model': 'disc', 'region': 'US', 'slot': 'artwork.disc',
                      'url': 'https://example.com/disc.png', 'sha256': digest}
            (p/'catalogue/artwork.json').write_text(json.dumps({'recipes': {'verified': recipe}}))
            backing = {'name': 'optical.back', 'pbrMetallicRoughness': {'roughnessFactor': .1}}
            document = {'asset': {'version': '2.0'}, 'materials': [
                {'name': 'artwork.disc', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}, backing],
                'images': [{'uri': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC'}], 'textures': [{'source': 0}]}
            (p/'assets/disc/model.gltf').write_text(json.dumps(document))
            with patch.object(cartridge.urllib.request, 'urlopen', side_effect=AssertionError('Cache miss')):
                cartridge.assemble('psx', p/'library/disc.gltf', edition='verified', root=p, cache=cache)
            self.assertEqual(cartridge.read_json(p/'library/disc.gltf')['materials'][1], backing)
            self.assertEqual(cartridge.read_json(p/'library/disc.gltf.json')['artwork_recipe'], 'verified')
            with self.assertRaisesRegex(ValueError, 'No verified artwork'):
                cartridge.assemble('psx', p/'library/unknown.gltf', title='Unknown', root=p)
            recipe['region'] = 'JP'
            (p/'catalogue/artwork.json').write_text(json.dumps({'recipes': {'verified': recipe}}))
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                cartridge.assemble('psx', p/'library/wrong.gltf', edition='verified', root=p)

    def test_shared_image_is_not_replaced_for_other_materials(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            # Valid PNG: binding also verifies its aspect ratio.
            (p/'label.png').write_bytes(__import__('base64').b64decode('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC'))
            uv = {'index': 0, 'texCoord': 1, 'extensions': {'KHR_texture_transform': {'scale': [0.5, 1]}}}
            document = {'asset': {'version': '2.0'}, 'materials': [
                {'name': 'label.front', 'pbrMetallicRoughness': {'baseColorTexture': uv}},
                {'name': 'pcb', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}],
                'images': [{'uri': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC'}],
                'textures': [{'source': 0, 'sampler': 0}], 'samplers': [{}]}
            (p/'model.gltf').write_text(json.dumps(document))
            cartridge.compose(p/'model.gltf', 'label.front', p/'label.png', p/'out.gltf')
            result = cartridge.read_json(p/'out.gltf')
            label = result['materials'][0]['pbrMetallicRoughness']['baseColorTexture']
            self.assertEqual(label['index'], 1)
            self.assertEqual(label['texCoord'], 1)
            self.assertEqual(label['extensions'], uv['extensions'])
            self.assertEqual(result['materials'][1]['pbrMetallicRoughness']['baseColorTexture']['index'], 0)
            self.assertEqual(result['textures'][1]['sampler'], 0)

    def test_front_and_top_replacement_leave_shared_rear_binding_intact(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            for name, colour in [('front', 'red'), ('top', 'blue')]:
                Image.new('RGB', (8, 8), colour).save(p/(name+'.png'))
            document = {'asset': {'version': '2.0'}, 'materials': [
                {'name': 'artwork.'+name, 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0, 'texCoord': uv}}}
                for name, uv in [('front', 0), ('top', 1), ('rear', 0)]],
                'images': [{'uri': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC'}], 'textures': [{'source': 0}]}
            (p/'model.gltf').write_text(json.dumps(document))
            cartridge.compose_surfaces(p/'model.gltf', {'artwork.front': p/'front.png', 'artwork.top': p/'top.png'}, p/'game.gltf')
            result = cartridge.read_json(p/'game.gltf')
            bindings = [m['pbrMetallicRoughness']['baseColorTexture'] for m in result['materials']]
            self.assertEqual([x['index'] for x in bindings], [1, 2, 0])
            self.assertEqual([x['texCoord'] for x in bindings], [0, 1, 0])

    def test_generic_assembly_hides_other_game_secondary_artwork(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p/'catalogue').mkdir()
            (p/'assets/model').mkdir(parents=True)
            (p/'catalogue/games.json').write_text(json.dumps({'defaults': {'test': {'default': 'model'}}, 'editions': []}))
            (p/'catalogue/models.json').write_text(json.dumps({'models': [{'id': 'model'}]}))
            document = {'asset': {'version': '2.0'}, 'materials': [
                {'name': 'artwork.'+name, 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}
                for name in ['front', 'top']],
                'images': [{'uri': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC'}], 'textures': [{'source': 0}]}
            (p/'assets/model/model.gltf').write_text(json.dumps(document))
            Image.new('RGB', (8, 8), 'red').save(p/'front.png')
            cartridge.assemble('test', p/'library/game.gltf', p/'front.png', title='Unknown game', root=p)
            result = cartridge.read_json(p/'library/game.gltf')
            top = result['materials'][1]
            self.assertEqual(top['alphaMode'], 'MASK')
            self.assertEqual(top['pbrMetallicRoughness']['baseColorFactor'][3], 0)
            details = cartridge.read_json(p/'library/game.gltf.json')
            self.assertEqual(details['hidden_secondary_artwork'], ['artwork.top'])
            # Supplying that surface restores normal visible material behavior.
            cartridge.assemble('test', p/'library/game.gltf', p/'front.png', title='Unknown game', root=p, surfaces={'artwork.top': p/'front.png'})
            result = cartridge.read_json(p/'library/game.gltf')
            self.assertNotIn('alphaMode', result['materials'][1])

    def test_recipe_secondary_surfaces_and_explicit_override(self):
        from PIL import Image
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p/'catalogue').mkdir()
            (p/'assets/model').mkdir(parents=True)
            Image.new('RGB', (8, 8), 'red').save(p/'label.png')
            document = {'asset': {'version': '2.0'}, 'materials': [
                {'name': 'artwork.'+name, 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}
                for name in ['front', 'top']], 'images': [{'uri': 'label.png'}], 'textures': [{'source': 0}]}
            (p/'assets/model/model.gltf').write_text(json.dumps(document))
            (p/'assets/model/label.png').write_bytes((p/'label.png').read_bytes())
            recipe = {'model': 'model', 'platform': 'test', 'region': 'US', 'slot': 'artwork.front',
                      'url': 'https://example.com/front.png', 'surfaces': {'artwork.top': {'url': 'https://example.com/top.png'}}}
            (p/'catalogue/artwork.json').write_text(json.dumps({'recipes': {'test': recipe}}))
            (p/'catalogue/games.json').write_text(json.dumps({'editions': [{'id': 'test', 'platform': 'test', 'regions': ['US'], 'model': 'model', 'artwork_recipe': 'test'}]}))
            (p/'catalogue/models.json').write_text(json.dumps({'models': [{'id': 'model'}]}))
            with patch.object(cartridge, 'acquire_artwork', return_value=p/'label.png') as acquire:
                cartridge.assemble('test', p/'game.gltf', edition='test', root=p)
                self.assertEqual(acquire.call_count, 2)
                metadata = cartridge.read_json(p/'game.gltf.json')
                self.assertEqual(set(metadata['artwork_sources']), {'artwork.front', 'artwork.top'})
                acquire.reset_mock()
                cartridge.assemble('test', p/'game.gltf', edition='test', root=p, surfaces={'artwork.top': p/'label.png'})
                self.assertEqual(acquire.call_count, 1)
                metadata = cartridge.read_json(p/'game.gltf.json')
                self.assertEqual(set(metadata['artwork_sources']), {'artwork.front'})
                self.assertIn('artwork.top', metadata['surface_sha256'])

    def test_webp_source_is_prepared_as_portable_png(self):
        from PIL import Image
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            Image.new('RGB', (8, 8), 'blue').save(p/'scan.webp')
            data = (p/'scan.webp').read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            cached = p/(digest+'.webp')
            cached.write_bytes(data)
            self.assertEqual(cartridge.fetch_artwork('https://example.com/scan.webp', p, digest), cached)
            cartridge.prepare_artwork(cached, p/'label.png')
            self.assertEqual(cartridge.image_type((p/'label.png').read_bytes())[0], 'image/png')
            with self.assertRaises(ValueError):
                cartridge.image_type(data)

    def test_crop_preserves_proportions_and_rejects_out_of_bounds(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            Image.new('RGB', (100, 80), 'red').save(p/'scan.png')
            cartridge.prepare_artwork(p/'scan.png', p/'label.png', (10, 20, 90, 70))
            with Image.open(p/'label.png') as result:
                self.assertEqual(result.size, (80, 50))
            with self.assertRaisesRegex(ValueError, 'outside'):
                cartridge.prepare_artwork(p/'scan.png', p/'bad.png', (0, 0, 101, 80))

    def test_prepare_converts_and_removes_embedded_colour_profile(self):
        from PIL import Image, ImageCms
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            Image.new('RGB', (8, 8), (80, 120, 160)).save(p/'source.png', icc_profile=profile)
            cartridge.prepare_artwork(p/'source.png', p/'prepared.png')
            with Image.open(p/'prepared.png') as image:
                self.assertNotIn('icc_profile', image.info)
                self.assertEqual(image.getpixel((0, 0)), (80, 120, 160, 255))

    def test_shared_buffers_are_written_once_for_multiple_games(self):
        import base64
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            raw = b'example mesh buffer'
            uri = 'data:application/octet-stream;base64,' + base64.b64encode(raw).decode()
            for name in ['first', 'second']:
                document = {'buffers': [{'byteLength': len(raw), 'uri': uri}]}
                cartridge.externalize(document, p/(name+'.gltf'), p/'assets')
                self.assertTrue(document['buffers'][0]['uri'].startswith('assets/'))
            self.assertEqual(len(list((p/'assets').iterdir())), 1)
            self.assertEqual(next((p/'assets').iterdir()).read_bytes(), raw)

    def test_truncated_glb_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'broken.glb'
            p.write_bytes(b'glTF')
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                cartridge.read_model(p)

    def test_download_rejects_non_https_before_network(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            cartridge.fetch_artwork('http://example.com/image.png', '/unused')


if __name__ == '__main__':
    unittest.main()
