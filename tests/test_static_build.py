"""Publication boundary tests use synthetic public data and no network or Pillow."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import build_static as static

RID = 'PUBLICRECIPE0001'
CID = 'PUBLICAUTHOR0001'
IMAGE = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5WQAAAAASUVORK5CYII=')


def fixture():
    info = {'bytes': len(IMAGE), 'sha256': hashlib.sha256(IMAGE).hexdigest(),
            'width': 1, 'height': 1, 'format': 'PNG'}
    recipe = {k: '' for k in static.RECIPE_TEXT}
    recipe.update(id=RID, name='公开方案', authors=['公开作者'], authorIds=[CID],
        publicDate='2026-10-24', pictureControl={'baseName': 'STANDARD'},
        exif={'model': 'TEST CAMERA'}, image='catalog/' + static.sample_path(RID),
        imageInfo=info, source=static.source_url(RID))
    return {'mode': 'static', 'recipes': [recipe], 'creators': [{'id': CID,
        'name': '公开作者', 'position': '', 'profile': '公开简介',
        'localPortrait': 'catalog/' + static.portrait_path(CID), 'imageInfo': info}],
        'labels': static.LABELS, 'values': static.VALUES,
        'report': {'finishedAt': '2026-10-24', 'recipeCount': 1, 'authorCount': 1,
                   'sampleCount': 1, 'portraitCount': 1},
        'syncMetadata': {'revision': 'test-public', 'syncedAt': '2026-10-24', 'newRecipeIds': [RID]}}


class StaticBuildTests(unittest.TestCase):
    def setUp(self):
        base = static.ROOT / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshot = self.root / 'public-catalog'
        self.output = self.root / 'dist'
        self.payload = fixture()
        self.save_snapshot()

    def save_snapshot(self):
        recipe = self.payload['recipes'][0]
        files = {'catalog.json': static.json_bytes(self.payload),
                 'details/' + RID + '.json': static.json_bytes(recipe),
                 static.sample_path(RID): IMAGE, static.portrait_path(CID): IMAGE}
        static.write_tree(self.snapshot, files)

    def test_build_is_offline_and_never_copies_private_siblings(self):
        private = self.root / 'personal-records.json'
        private.write_text('PRIVATE_SENTINEL', encoding='utf-8')
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            result = static.build(self.snapshot, self.output)
        self.assertEqual(result['recipes'], 1)
        self.assertFalse(any('PRIVATE_SENTINEL' in p.read_text(encoding='utf-8', errors='ignore')
                             for p in self.output.rglob('*') if p.is_file()))
        manifest = json.loads((self.output / 'publish-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(static.tree_files(self.output), {i['path'] for i in manifest['files']} | {'publish-manifest.json'})
        for item in manifest['files']:
            body = (self.output / item['path']).read_bytes()
            self.assertEqual(item['sha256'], hashlib.sha256(body).hexdigest())
        html = (self.output / 'index.html').read_text(encoding='utf-8')
        self.assertIn('data-mode="static"', html)
        self.assertIn('src="./app.js"', html)
        self.assertNotIn('href="/api/', html)
        self.assertIn('./catalog/tables/recipes.csv', html)

    def test_rejects_unexpected_snapshot_and_output_files(self):
        for target in (self.snapshot, self.output):
            with self.subTest(target=target.name):
                target.mkdir(exist_ok=True)
                bad = target / 'personal-records.json'
                bad.write_text('private', encoding='utf-8')
                with self.assertRaises(ValueError):
                    static.build(self.snapshot, self.output)
                self.assertEqual(bad.read_text(encoding='utf-8'), 'private')
                bad.unlink()

    def test_rejects_modified_sample_portrait_and_detail(self):
        for relative in (static.sample_path(RID), static.portrait_path(CID), 'details/' + RID + '.json'):
            with self.subTest(relative=relative):
                file = self.snapshot / relative
                original = file.read_bytes()
                file.write_bytes(b'private replacement')
                with self.assertRaises(ValueError):
                    static.build(self.snapshot, self.output)
                file.write_bytes(original)

    def test_rejects_extra_fields_and_invalid_references(self):
        mutations = [lambda p: p.update(records={}),
            lambda p: p['recipes'][0].update(photoAssignments={}),
            lambda p: p['recipes'][0]['exif'].update(cameraSerial='PRIVATE'),
            lambda p: p['recipes'][0].update(image='../../photos/private.jpg'),
            lambda p: p['recipes'][0].update(authorIds=['UNKNOWN_AUTHOR']),
            lambda p: p['syncMetadata']['newRecipeIds'].append('UNKNOWN_RECIPE'),
            lambda p: p['creators'][0].update(localPortrait='https://example.com/a.png')]
        for mutate in mutations:
            payload = copy.deepcopy(self.payload)
            mutate(payload)
            with self.subTest(payload=repr(mutate)), self.assertRaises(ValueError):
                static.validate_payload(payload)

    def test_rejects_signed_urls_tokens_and_local_paths_in_public_text(self):
        for value in ('https://example.com/a.jpg?Signature=private',
                      'https://example.com/a?token=private', 'Bearer private',
                      'C:\\Users\\Private\\photo.jpg', 'file:///private.jpg',
                      'ghp_' + 'x' * 30):
            with self.subTest(value=value), self.assertRaises(ValueError):
                static.text(value)

    def test_rejects_path_escape_and_duplicate_ids(self):
        with self.assertRaises(ValueError):
            static.confined(self.snapshot, '../personal-records.json')
        payload = copy.deepcopy(self.payload)
        payload['recipes'].append(copy.deepcopy(payload['recipes'][0]))
        with self.assertRaises(ValueError):
            static.validate_payload(payload)

    def test_export_strips_raw_urls_and_unknown_private_fields(self):
        source = self.root / 'catalog'
        recipe = copy.deepcopy(self.payload['recipes'][0])
        recipe['image'] = static.sample_path(RID)
        recipe['accessToken'] = 'PRIVATE_SENTINEL'
        creator = copy.deepcopy(self.payload['creators'][0])
        creator['localPortrait'] = static.portrait_path(CID)
        creator['thumbnail'] = 'https://official.example/image?Signature=PRIVATE_SENTINEL'
        assets = [{'path': relative, **recipe['imageInfo']} for relative in
                  (static.sample_path(RID), static.portrait_path(CID))]
        static.write_tree(source, {'data/recipes.json': static.json_bytes([recipe]),
            'data/creators.json': static.json_bytes([creator]),
            'data/assets.json': static.json_bytes(assets),
            'data/collection_report.json': static.json_bytes(self.payload['report']),
            'local/personal-records.json': b'PRIVATE_SENTINEL',
            static.sample_path(RID): IMAGE, static.portrait_path(CID): IMAGE})
        validator = SimpleNamespace(CatalogSync=SimpleNamespace(validate=lambda _: None))
        target = self.root / 'export'
        with patch.dict(sys.modules, {'catalog_sync': validator}), patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            result = static.export_catalog(source, target)
        self.assertEqual(result['recipes'], 1)
        static.snapshot_files(target)
        self.assertNotIn('PRIVATE_SENTINEL', (target / 'catalog.json').read_text(encoding='utf-8'))
        self.assertFalse((target / 'local').exists())

    def test_csv_neutralizes_formula_cells(self):
        output = static.csv_bytes(['name'], [['=FORMULA'], ['+123'], ['@foo'], ['-1'], ['safe']]).decode('utf-8-sig')
        self.assertIn("'=FORMULA", output)
        self.assertIn("'+123", output)
        self.assertIn("'@foo", output)
        self.assertIn("'-1", output)


if __name__ == '__main__':
    unittest.main()
