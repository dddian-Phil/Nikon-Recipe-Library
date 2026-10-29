"""Verify that only reduced, metadata-free photo copies can reach Pages."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import build_static as static
import public_photos as public
import publish_site as publish
from test_static_build import fixture, IMAGE, RID, CID

PID = 'a' * 24


class PublicPhotoTests(unittest.TestCase):
    def setUp(self):
        base = static.ROOT / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'private'
        self.target = self.root / 'public-photos'
        self.original = self.workspace / 'photos/PRIVATE_FOLDER/sample.JPG'
        self.original.parent.mkdir(parents=True)
        self.local = self.workspace / 'local'
        self.local.mkdir()
        self.write_source('red')
        self.records = {'version': 1, 'updatedAt': '2026-10-30T12:01:00+08:00',
            'recipeFavorites': [RID], 'photoFavorites': [PID], 'photoAssignments': {PID: RID},
            'privateHistory': 'PRIVATE_SENTINEL'}
        self.save_index()

    def write_source(self, color):
        exif = Image.Exif()
        exif[315] = 'PRIVATE_SENTINEL'
        exif[274] = 6
        Image.new('RGB', (1800, 1200), color).save(self.original, exif=exif)
        digest = hashlib.sha256(self.original.read_bytes()).hexdigest()
        version = {'path': 'PRIVATE_FOLDER/sample.JPG', 'sha256': digest, 'kind': 'JPG'}
        self.index = {'updatedAt': '2026-10-30T12:00:00+08:00',
            'files': {version['path']: version}, 'scanReport': {'copying': 0, 'errors': []},
            'captures': [{'id': PID, 'name': 'sample', 'files': [version],
                'autoRecipeId': None, 'camera': 'Nikon', 'cameraSerial': 'PRIVATE_SENTINEL',
                'issues': [{'file': 'PRIVATE_FOLDER/sample.JPG', 'detail': 'PRIVATE_SENTINEL'}]}]}

    def save_index(self):
        (self.local / 'photo-index.json').write_bytes(static.json_bytes(self.index))
        (self.local / 'personal-records.json').write_bytes(static.json_bytes(self.records))

    def export(self):
        return public.export_photos(self.workspace, self.target, {RID})

    def catalog(self):
        payload = fixture()
        root = self.root / 'public-catalog'
        static.write_tree(root, {'catalog.json': static.json_bytes(payload),
            'details/' + RID + '.json': static.json_bytes(payload['recipes'][0]),
            static.sample_path(RID): IMAGE, static.portrait_path(CID): IMAGE})
        return root

    def test_export_preserves_originals_and_publishes_only_resolved_read_only_state(self):
        original = self.original.read_bytes()
        records = (self.local / 'personal-records.json').read_bytes()
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            result = self.export()
        self.assertEqual(result['photos'], 1)
        self.assertEqual(self.original.read_bytes(), original)
        self.assertEqual((self.local / 'personal-records.json').read_bytes(), records)
        payload, files = public.snapshot_files(self.target, {RID})
        photo = payload['captures'][0]
        self.assertEqual(photo['recipeId'], RID)
        self.assertEqual(photo['association'], 'manual')
        self.assertTrue(photo['favorite'])
        self.assertEqual(payload['recipeFavorites'], [RID])
        for body in files.values():
            self.assertNotIn(b'PRIVATE_SENTINEL', body)
            self.assertNotIn(b'PRIVATE_FOLDER', body)
        for field, info, limit in [('thumbnail', 'thumbnailInfo', 480), ('preview', 'imageInfo', 1280)]:
            body = files[photo[field].removeprefix('photos/')]
            with Image.open(io.BytesIO(body)) as picture:
                self.assertLessEqual(max(picture.size), limit)
                self.assertGreater(picture.height, picture.width)  # EXIF orientation applied.
                self.assertFalse(picture.getexif())
            self.assertNotEqual(body, original)

    def test_edited_and_deleted_photos_replace_old_public_assets(self):
        self.export()
        catalog = self.catalog()
        output = self.root / 'dist'
        static.build(catalog, output)
        old_files = public.tree_files(self.target) - {'library.json'}
        self.write_source('blue')
        self.save_index()
        self.export()
        self.assertFalse(old_files & (public.tree_files(self.target) - {'library.json'}))
        static.build(catalog, output)
        self.assertFalse(any((output / 'photos' / path).exists() for path in old_files))
        self.original.unlink()
        self.index.update(files={}, captures=[])
        self.save_index()
        self.export()
        result = static.build(catalog, output)
        self.assertEqual(result['photos'], 0)
        self.assertEqual(public.tree_files(self.target), {'library.json'})

    def test_stale_index_or_partial_copy_cannot_publish(self):
        self.original.write_bytes(self.original.read_bytes() + b'edited')
        with self.assertRaisesRegex(ValueError, 'edited since'):
            self.export()
        self.assertFalse(self.target.exists())
        self.index['scanReport']['copying'] = 1
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'scan is incomplete'):
            self.export()

    def test_new_photo_requires_scan_and_escape_is_rejected(self):
        extra = self.original.with_name('new.JPG')
        extra.write_bytes(self.original.read_bytes())
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.export()
        extra.unlink()
        self.index['captures'][0]['files'][0]['path'] = '../../private.jpg'
        self.save_index()
        with self.assertRaises(ValueError):
            self.export()

    def test_export_is_deterministic_and_unexpected_files_survive_rejection(self):
        self.export()
        original = {p: (self.target / p).read_bytes() for p in public.tree_files(self.target)}
        self.export()
        self.assertEqual(original, {p: (self.target / p).read_bytes() for p in public.tree_files(self.target)})
        unexpected = self.target / 'original.JPG'
        unexpected.write_bytes(b'PRIVATE_SENTINEL')
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(unexpected.read_bytes(), b'PRIVATE_SENTINEL')

    def test_public_snapshot_rejects_private_fields_bad_references_and_image_tampering(self):
        self.export()
        payload = static.read_json(self.target / 'library.json')
        mutations = [lambda p: p.update(records={}),
            lambda p: p['captures'][0].update(files=[]),
            lambda p: p['captures'][0].update(recipeId='UNKNOWN'),
            lambda p: p['captures'][0].update(preview='../../photos/original.jpg'),
            lambda p: p['captures'][0]['imageInfo'].update(width=6000)]
        for mutate in mutations:
            altered = copy.deepcopy(payload)
            mutate(altered)
            with self.assertRaises(ValueError):
                public.validate_library(altered, {RID})
        image = self.target / payload['captures'][0]['preview'].removeprefix('photos/')
        image.write_bytes(self.original.read_bytes())
        with self.assertRaises(ValueError):
            public.snapshot_files(self.target, {RID})

    def test_metadata_is_rejected_even_with_matching_hash(self):
        self.export()
        payload, files = public.snapshot_files(self.target, {RID})
        photo = payload['captures'][0]
        stream = io.BytesIO()
        exif = Image.Exif()
        exif[315] = 'PRIVATE_SENTINEL'
        Image.new('RGB', (100, 100)).save(stream, format='JPEG', exif=exif)
        body = stream.getvalue()
        info = photo['imageInfo']
        info.update(width=100, height=100, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        relative = public.image_path(PID, 'display', info['sha256'])
        photo['preview'] = 'photos/' + relative
        files[relative] = body
        with self.assertRaisesRegex(ValueError, 'must not contain'):
            public.validate_images(payload, files)

    def test_nikon_mpo_jpg_publishes_primary_image_only(self):
        stream = io.BytesIO()
        Image.new('RGB', (1800, 1200), 'red').save(stream, format='MPO', save_all=True,
            append_images=[Image.new('RGB', (300, 200), 'blue')])
        for _, body, info in public.reduced_jpegs(stream.getvalue()):
            with Image.open(io.BytesIO(body)) as picture:
                self.assertEqual(picture.format, 'JPEG')
                self.assertLessEqual(max(picture.size), 1280)
                self.assertNotIn('mp', picture.info)

    def test_publication_lock_prevents_overlapping_cli_and_web_jobs(self):
        with patch.object(publish, 'ROOT', self.root):
            with publish.publication_lock():
                with self.assertRaisesRegex(ValueError, 'already running'):
                    with publish.publication_lock():
                        self.fail('Overlapping publication was allowed')
            with publish.publication_lock():
                pass


class PublicationGitTests(unittest.TestCase):
    def test_dead_local_git_proxy_is_bypassed_for_this_push_only(self):
        with patch.object(publish, 'command', return_value='https://github.com/example/library.git') as run, \
             patch.object(publish.subprocess, 'run', return_value=SimpleNamespace(stdout='http://127.0.0.1:10808')), \
             patch.object(publish.socket, 'create_connection', side_effect=ConnectionRefusedError):
            publish.push_snapshot()
        run.assert_called_with('git', '-c', 'http.proxy=', 'push', 'origin',
                               'HEAD:refs/heads/' + publish.BRANCH, capture=False)

    def test_remote_proxy_configuration_is_preserved(self):
        with patch.object(publish, 'command', return_value='https://github.com/example/library.git') as run, \
             patch.object(publish.subprocess, 'run', return_value=SimpleNamespace(stdout='https://proxy.example:8080')), \
             patch.object(publish.socket, 'create_connection') as connect:
            publish.push_snapshot()
        connect.assert_not_called()
        run.assert_called_with('git', 'push', 'origin', 'HEAD:refs/heads/' + publish.BRANCH, capture=False)

    def test_only_public_snapshot_changes_are_allowed(self):
        for status in (' M data/public-photos/library.json\0?? data/public-photos/images/a.jpg\0',
                       '?? data/public-catalog/details/a.json\0'):
            with patch.object(publish, 'command', side_effect=[publish.BRANCH, '', status]):
                publish.check_deployment()
        for status in (' M app/app.js\0', '?? photos/private.jpg\0', '?? local/records.json\0',
                       ' R data/public-photos/new.jpg\0photos/private.jpg\0'):
            with patch.object(publish, 'command', side_effect=[publish.BRANCH, '', status]), self.assertRaises(ValueError):
                publish.check_deployment()

    def test_wrong_branch_and_staged_work_are_not_published(self):
        with patch.object(publish, 'command', return_value='main'), self.assertRaises(ValueError):
            publish.check_deployment()
        with patch.object(publish, 'command', side_effect=[publish.BRANCH, 'app/app.js']), self.assertRaises(ValueError):
            publish.check_deployment()


if __name__ == '__main__':
    unittest.main()
