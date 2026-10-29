"""Isolated sync tests: all network responses and personal data are synthetic."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import catalog_sync as sync
from collect_nikon import main as rebuild

RID = '01JSDSNV8Z3TXW116M4RJXF6F4'
NEW = '01JSDSNV8Z3TXW116M4RJXF6F5'
AUTHOR = '01M3NKSAABCNX5KW9M8A0TDKQM'
HOST = 'https://front.ir.imagingcloud.nikon.com.cn/'


def picture(color='green'):
    output = io.BytesIO()
    Image.new('RGB', (16, 12), color).save(output, format='JPEG')
    return output.getvalue()


def detail(rid=RID, **changes):
    return {'id': rid, 'name': 'Film ' + rid[-2:], 'pictureControlName': 'Film_' + rid[-2:],
            'pictureControl': {'baseName': 'FLEXIBLE COLOR', 'saturation': '+20'},
            'exif': {'model': 'TEST CAMERA'}, 'publicDate': '2026-10-24T10:00:00+08:00',
            'recipeDetail': 'Original introduction', 'thumbnail': HOST + rid + '.jpg', **changes}


class FakeOfficial:
    def __init__(self, details=None):
        self.details = details or [detail()]
        self.calls = []
        self.images = {}
        self.failure = None
        self.started = None
        self.release = None
        self.catalog_override = None

    def catalog(self):
        return {'recipe': [{'id': d['id'], 'name': d['name']} for d in self.details]}

    def creators(self):
        return {'creators': [{'id': AUTHOR, 'name': 'Example Creator', 'profile': 'Public profile',
                              'thumbnail': HOST + 'portrait.png',
                              'recipes': [{'path': '/recipe/' + d['id']} for d in self.details]}]}

    def __call__(self, url, data=None, **kwargs):
        self.calls.append(url)
        if self.failure:
            raise self.failure
        if url == sync.LIST:
            if self.started:
                self.started.set()
                self.release.wait(5)
            result = self.catalog_override if self.catalog_override is not None else self.catalog()
        elif url == sync.IR + '/info/creator':
            result = self.creators()
        elif '/publicrecipe/' in url:
            rid = url.split('/')[-1].split('?')[0]
            result = next(d for d in self.details if d['id'] == rid)
        else:
            return self.images.get(url.split('?')[0], picture())
        return json.dumps(result).encode()


class ConditionalOfficial(FakeOfficial):
    def __init__(self):
        super().__init__()
        self.image_bytes = 0

    def __call__(self, url, data=None, **kwargs):
        body = super().__call__(url, data, **kwargs)
        if url == sync.LIST or url == sync.IR + '/info/creator' or '/publicrecipe/' in url:
            return body
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        kwargs['response_info']['etag'] = etag
        if kwargs.get('conditional', {}).get('If-None-Match') == etag:
            return None
        self.image_bytes += len(body)
        return body


def seed(workspace, official):
    root = workspace / 'data/catalog'
    sync.write_json(root / 'raw/catalog.json', official.catalog())
    sync.write_json(root / 'raw/creators.json', official.creators())
    sync.write_json(root / 'data/recipes.json', [])
    sync.write_json(root / 'data/collection_report.json', {})
    for d in official.details:
        sync.write_json(root / 'raw/details' / (d['id'] + '.json'), d)
        image = root / 'recipes' / d['id'].lower() / 'sample.jpg'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(picture())
    portrait = root / 'assets/creators' / (AUTHOR + '.png')
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(picture())
    assert not rebuild(['--offline'], root=root, quiet=True)['failures']
    return root


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class CatalogSyncTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / 'local/test-runs'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        assert Path(self.temporary.name).resolve().is_relative_to(test_root.resolve())
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.official = FakeOfficial()
        self.root = seed(self.workspace, self.official)
        for name, body in [('photos/private.jpg', b'private source photo'),
                           ('local/personal-records.json', b'{"recipeFavorites":["keep"],"photoAssignments":{"photo":"keep"}}'),
                           ('local/personal-records.previous.json', b'previous personal records')]:
            p = self.workspace / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
        self.personal = {name: (self.workspace / name).read_bytes() for name in
                         ('photos/private.jpg', 'local/personal-records.json', 'local/personal-records.previous.json')}
        self.manager = sync.CatalogSync(self.workspace, transport=self.official)

    def run_sync(self, success=True):
        self.assertTrue(self.manager.start())
        self.manager.thread.join(15)
        self.assertFalse(self.manager.thread.is_alive())
        status = self.manager.status()
        self.assertEqual(status['state'], 'succeeded' if success else 'failed', status)
        for name, body in self.personal.items():
            self.assertEqual((self.workspace / name).read_bytes(), body)
        return status

    def test_reading_and_construction_never_contact_official(self):
        self.manager.payload()
        self.manager.status()
        self.assertEqual(self.official.calls, [])

    def test_added_and_changed_recipes(self):
        self.official.details = [detail(recipeDetail='Changed introduction'), detail(NEW)]
        result = self.run_sync()['result']
        self.assertEqual(result['newRecipeIds'], [NEW])
        self.assertEqual(result['changedRecipeIds'], [RID])
        self.assertEqual(len(self.manager.payload()['recipes']), 2)

    def test_image_only_change_is_detected(self):
        self.official.images[HOST + RID + '.jpg'] = picture('blue')
        self.assertEqual(self.run_sync()['result']['changedRecipeIds'], [RID])

    def test_unchanged_images_are_revalidated_without_downloading(self):
        self.official = ConditionalOfficial()
        self.manager.transport = self.official
        self.assertEqual(self.run_sync()['result']['imageDownloads'], 2)
        self.official.image_bytes = 0
        self.official.details = [detail(thumbnail=HOST + RID + '.jpg?Signature=rotated')]
        result = self.run_sync()['result']
        self.assertEqual(result['imageReused'], 2)
        self.assertEqual(result['imageDownloads'], 0)
        self.assertEqual(self.official.image_bytes, 0)
        self.assertEqual(result['changedRecipeIds'], [])

    def test_conditional_cache_detects_image_only_edits(self):
        self.official = ConditionalOfficial()
        self.manager.transport = self.official
        self.run_sync()
        self.official.images[HOST + RID + '.jpg'] = picture('blue')
        result = self.run_sync()['result']
        self.assertEqual(result['changedRecipeIds'], [RID])
        self.assertEqual(result['imageDownloads'], 1)
        self.assertEqual(result['imageReused'], 1)

    def test_corrupt_local_image_is_downloaded_instead_of_reused(self):
        self.official = ConditionalOfficial()
        self.manager.transport = self.official
        self.run_sync()
        (self.root / 'recipes' / RID.lower() / 'sample.jpg').write_bytes(b'corrupt')
        result = self.run_sync()['result']
        self.assertEqual(result['imageDownloads'], 1)
        self.assertEqual(result['imageReused'], 1)
        self.manager.validate(self.root)

    def test_asset_identity_ignores_only_access_signatures(self):
        self.assertEqual(sync.asset_identity(HOST + 'a.jpg?Signature=old&width=400'),
                         sync.asset_identity(HOST + 'a.jpg?Signature=new&width=400'))
        self.assertNotEqual(sync.asset_identity(HOST + 'a.jpg?width=400'),
                            sync.asset_identity(HOST + 'a.jpg?width=800'))

    def test_rotating_signed_urls_do_not_mark_recipe_changed(self):
        self.official.details = [detail(thumbnail=HOST + RID + '.jpg?signature=rotated')]
        result = self.run_sync()['result']
        self.assertEqual(result['newRecipeIds'], [])
        self.assertEqual(result['changedRecipeIds'], [])

    def test_missing_official_recipe_is_retained(self):
        self.official.details = [detail(NEW)]
        result = self.run_sync()['result']
        self.assertEqual(result['retainedCount'], 1)
        self.assertEqual({r['id'] for r in self.manager.payload()['recipes']}, {RID, NEW})

    def test_download_and_validation_failures_preserve_exact_catalog(self):
        before = hashes(self.root)
        cases = ['timeout', 'empty', 'duplicate', 'path', 'bad_image', 'truncated_jpeg', 'bad_detail', 'bad_url']
        for case in cases:
            with self.subTest(case=case):
                official = FakeOfficial()
                if case == 'timeout':
                    official.failure = TimeoutError('Synthetic timeout')
                elif case == 'empty':
                    official.catalog_override = {'recipe': []}
                elif case == 'duplicate':
                    official.catalog_override = {'recipe': [detail(), detail()]}
                elif case == 'path':
                    official.catalog_override = {'recipe': [{'id': '../photos/private', 'name': 'Unsafe'}]}
                elif case == 'bad_image':
                    official.images[HOST + RID + '.jpg'] = b'not an image'
                elif case == 'truncated_jpeg':
                    official.images[HOST + RID + '.jpg'] = picture()[:-20]
                elif case == 'bad_detail':
                    official.details = [detail(pictureControl='not a dictionary')]
                elif case == 'bad_url':
                    official.details = [detail(thumbnail='http://127.0.0.1/private')]
                self.official = official
                self.manager = sync.CatalogSync(self.workspace, transport=official)
                self.run_sync(False)
                self.assertEqual(hashes(self.root), before)
                self.assertFalse(self.manager.journal.exists())

    def test_promotion_rename_failure_rolls_back(self):
        before = hashes(self.root)
        rename = Path.rename
        def fail_stage(path, target):
            if path.name.startswith('.catalog-stage-') and Path(target) == self.root:
                raise PermissionError('Synthetic locked directory')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_stage):
            self.run_sync(False)
        self.assertEqual(hashes(self.root), before)

    def test_reload_failure_rolls_back_files_and_memory(self):
        before = hashes(self.root)
        calls = []
        def reload():
            calls.append(json.loads((self.root / 'data/recipes.json').read_text(encoding='utf-8'))[0]['description'])
            if len(calls) == 1:
                raise RuntimeError('Synthetic reload failure')
        self.manager.on_reload = reload
        self.official.details = [detail(recipeDetail='Changed')]
        self.run_sync(False)
        self.assertEqual(hashes(self.root), before)
        self.assertEqual(calls, ['Changed', 'Original introduction'])

    def test_commit_journal_failure_rolls_back(self):
        before = hashes(self.root)
        write_json = sync.write_json
        def fail_commit(path, value):
            if path == self.manager.journal and value.get('phase') == 'committed':
                raise OSError('Synthetic disk write failure')
            return write_json(path, value)
        with patch.object(sync, 'write_json', fail_commit):
            self.run_sync(False)
        self.assertEqual(hashes(self.root), before)

    def test_overlapping_manual_jobs_are_rejected(self):
        self.official.started = threading.Event()
        self.official.release = threading.Event()
        self.assertTrue(self.manager.start())
        self.assertTrue(self.official.started.wait(3))
        self.assertFalse(self.manager.start())
        self.official.release.set()
        self.manager.thread.join(15)
        self.assertEqual(self.manager.status()['state'], 'succeeded')

    def test_start_status_write_failure_releases_job_and_allows_retry(self):
        with patch.object(sync, 'write_json', side_effect=OSError('Synthetic full disk')):
            with self.assertRaises(OSError):
                self.manager.start()
        self.assertFalse(self.manager.status()['running'])
        self.run_sync()

    def test_manifest_hash_failure_is_rejected(self):
        assets_path = self.root / 'data/assets.json'
        assets = json.loads(assets_path.read_text(encoding='utf-8'))
        assets[0]['sha256'] = '0' * 64
        sync.write_json(assets_path, assets)
        with self.assertRaisesRegex(ValueError, '哈希'):
            self.manager.validate(self.root)

    def test_interrupted_swap_is_restored_without_network(self):
        before = hashes(self.root)
        backup = self.root.parent / ('.catalog-backup-' + 'a' * 32)
        stage = self.root.parent / ('.catalog-stage-' + 'b' * 32)
        self.root.rename(backup)
        self.root.mkdir()
        (self.root / 'partial.json').write_text('{}')
        sync.write_json(self.manager.journal, {'phase': 'prepared', 'stage': stage.name,
                                             'backup': backup.name, 'hadCatalog': True})
        sync.CatalogSync(self.workspace, transport=self.official)
        self.assertEqual(hashes(self.root), before)
        self.assertEqual(self.official.calls, [])

    def test_committed_transaction_survives_restart(self):
        before = hashes(self.root)
        backup = self.root.parent / ('.catalog-backup-' + 'a' * 32)
        backup.mkdir()
        (backup / 'old.txt').write_text('old')
        sync.write_json(self.manager.journal, {'phase': 'committed', 'stage': '.catalog-stage-' + 'b' * 32,
                                             'backup': backup.name, 'hadCatalog': True})
        sync.CatalogSync(self.workspace, transport=self.official)
        self.assertEqual(hashes(self.root), before)
        self.assertFalse(backup.exists())

    def test_finished_commit_with_interrupted_status_is_reconciled(self):
        self.run_sync()
        metadata = self.manager.payload()['syncMetadata']
        sync.write_json(self.manager.status_path, {'state': 'running', 'running': True,
                                                 'jobId': metadata['jobId']})
        restarted = sync.CatalogSync(self.workspace, transport=self.official)
        self.assertEqual(restarted.status()['state'], 'succeeded')
        self.assertEqual(restarted.status()['result']['revision'], metadata['revision'])

    def test_recovery_rejects_outside_paths(self):
        sync.write_json(self.manager.journal, {'phase': 'prepared', 'stage': '../photos',
                                             'backup': '.catalog-backup-' + 'a' * 32, 'hadCatalog': True})
        with self.assertRaisesRegex(ValueError, '路径'):
            sync.CatalogSync(self.workspace, transport=self.official)
        self.assertTrue((self.workspace / 'photos/private.jpg').exists())

    def test_missing_offline_asset_never_downloads(self):
        (self.root / 'recipes' / RID.lower() / 'sample.jpg').unlink()
        with patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
            report = rebuild(['--offline'], root=self.root, quiet=True)
        self.assertTrue(report['failures'])

    def test_online_collector_cli_is_disabled(self):
        with patch('collect_nikon.get_json') as network, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                rebuild([], root=self.root, quiet=True)
        self.assertEqual(error.exception.code, 2)
        network.assert_not_called()

    def test_empty_install_can_sync_from_web_job(self):
        empty = self.workspace / 'empty-install'
        self.manager = sync.CatalogSync(empty, transport=self.official)
        self.assertTrue(self.manager.start())
        self.manager.thread.join(15)
        self.assertEqual(self.manager.status()['state'], 'succeeded', self.manager.status())
        self.assertEqual(self.manager.payload()['syncMetadata']['newRecipeIds'], [RID])

    def test_empty_install_can_render_without_network(self):
        from build_viewer import build
        empty = self.workspace / 'empty-viewer'
        with patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
            result = build(root=empty / 'data/catalog', output=empty / 'app/viewer.html')
        self.assertEqual(result['recipes'], 0)
        page = Path(result['viewer']).read_text(encoding='utf-8')
        self.assertIn('id="sync-catalog"', page)
        self.assertNotIn('__PAYLOAD__', page)


if __name__ == '__main__':
    unittest.main()
