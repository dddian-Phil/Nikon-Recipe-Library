"""Photo parsing and association tests run only in generated local test folders."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import photo_library as photos


def metadata(name='Example Film'):
    return {'camera': 'TEST CAMERA', 'takenAt': '2026:10:24 10:00:00', 'subsecond': '',
            'lens': 'TEST LENS', 'cameraSerial': 'TEST', 'shutterCount': 10,
            'pictureControlName': name, 'pictureControlBase': 'FLEXIBLE COLOR',
            'pictureControlVersion': '0310', 'metadataIssue': ''}


class PhotoTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        assert Path(temporary.name).resolve().is_relative_to(base.resolve())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        local = self.root / 'local'
        catalog = self.root / 'data/catalog'
        (catalog / 'data').mkdir(parents=True)
        self.recipes = [{'id': 'recipe-one', 'pictureControlName': 'Example Film'},
                        {'id': 'recipe-two', 'pictureControlName': 'Other Film'}]
        (catalog / 'data/recipes.json').write_text(json.dumps(self.recipes), encoding='utf-8')
        self.patcher = patch.multiple(photos, WORKSPACE=self.root, INTERNAL=local,
                                     PHOTO_ROOT=self.root / 'photos', CATALOG_ROOT=catalog,
                                     INDEX_PATH=local / 'photo-index.json',
                                     RECORDS_PATH=local / 'personal-records.json', PREVIEWS=local / 'previews')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.library = photos.PhotoLibrary()
        self.records_before = photos.RECORDS_PATH.read_bytes()

    def image(self, name='sample.jpg', body=None):
        p = photos.PHOTO_ROOT / name
        if body is None:
            Image.new('RGB', (20, 20), 'green').save(p)
        else:
            p.write_bytes(body)
        os.utime(p, (100, 100))
        return p

    def scan(self):
        self.assertTrue(self.library.scan())
        self.assertEqual(self.library.scan_error, '')
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), self.records_before)
        return self.library.state()

    def test_corrupt_jpeg_reports_reason_filename_and_action(self):
        self.image('broken.jpg', b'not a JPEG')
        state = self.scan()
        problem = state['captures'][0]['issues'][0]
        self.assertEqual(problem['file'], 'broken.jpg')
        self.assertEqual(problem['code'], 'invalid_image')
        self.assertTrue(problem['message'])
        self.assertTrue(problem['action'])
        self.assertIn('UnidentifiedImageError', problem['detail'])
        self.assertEqual(self.scan()['scanReport']['issues'], state['scanReport']['issues'])

    def test_missing_picture_control_is_a_warning_not_read_failure(self):
        self.image()
        state = self.scan()
        problem = state['captures'][0]['issues'][0]
        self.assertEqual(problem['code'], 'missing_picture_control')
        self.assertEqual(problem['severity'], 'warning')
        self.assertEqual(state['scanReport']['errors'], [])

    def test_truncated_jpeg_pixel_data_explains_corruption(self):
        p = self.image()
        p.write_bytes(p.read_bytes()[:-20])
        os.utime(p, (100, 100))
        state = self.scan()
        issues = state['captures'][0]['issues']
        self.assertEqual(issues[0]['code'], 'invalid_image')
        self.assertIn('损坏', issues[0]['message'])

    def test_unsupported_control_version_explains_the_version(self):
        path = self.image()
        with patch.object(photos, 'maker_tags', return_value={0x23: b'9999' + b'\0' * 44}):
            result = photos.read_metadata(path)
        self.assertEqual(result['metadataIssueCode'], 'unsupported_picture_control')
        self.assertIn('9999', result['metadataIssue'])

    def test_truncated_control_block_is_reported(self):
        path = self.image()
        with patch.object(photos, 'maker_tags', return_value={0x23: b'0310short'}):
            result = photos.read_metadata(path)
        self.assertEqual(result['metadataIssueCode'], 'incomplete_picture_control')

    def test_missing_nef_preview_has_specific_recovery_action(self):
        self.image('sample.nef', b'raw test data')
        with patch.object(photos, 'read_metadata', return_value=metadata()), patch.object(
                photos, 'create_preview', side_effect=ValueError('未找到可用的 RAW 内嵌 JPEG 预览；可放入同组 JPG。')):
            state = self.scan()
        problem = state['captures'][0]['issues'][0]
        self.assertEqual(problem['code'], 'missing_raw_preview')
        self.assertIn('JPG', problem['action'])

    def test_empty_file_is_reported_as_pending(self):
        self.image('copying.jpg', b'')
        state = self.scan()
        self.assertEqual(state['summary']['photos'], 0)
        self.assertEqual(state['scanReport']['issues'][0]['code'], 'copying')

    def test_file_being_replaced_does_not_remove_existing_capture(self):
        p = self.image()
        with patch.object(photos, 'read_metadata', return_value=metadata()):
            before = self.scan()
            p.write_bytes(b'')
            after = self.scan()
        self.assertEqual(after['captures'][0]['id'], before['captures'][0]['id'])
        self.assertEqual(after['scanReport']['copying'], 1)

    def test_permission_failure_identifies_the_affected_file(self):
        p = self.image()
        original = Path.stat
        def protected(path, *args, **kwargs):
            if path == p:
                raise PermissionError('Synthetic read denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', protected):
            state = self.scan()
        issue = state['scanReport']['issues'][0]
        self.assertEqual(issue['code'], 'permission_denied')
        self.assertEqual(issue['file'], 'sample.jpg')

    def test_ambiguous_truncated_name_keeps_candidates_without_guessing(self):
        prefix = 'A' * 20
        self.library.recipes = [{'id': 'recipe-one', 'pictureControlName': prefix + ' One'},
                                {'id': 'recipe-two', 'pictureControlName': prefix + ' Two'}]
        self.image()
        with patch.object(photos, 'read_metadata', return_value=metadata(prefix)):
            capture = self.scan()['captures'][0]
        self.assertIsNone(capture['autoRecipeId'])
        self.assertEqual(set(capture['candidateRecipeIds']), {'recipe-one', 'recipe-two'})

    def test_reload_and_scan_preserve_manual_assignment_and_favorites(self):
        self.image()
        with patch.object(photos, 'read_metadata', return_value=metadata()):
            capture = self.scan()['captures'][0]
            self.library.edit('toggle_recipe', 'recipe-one')
            self.library.edit('toggle_photo', capture['id'])
            self.library.edit('assign_photo', capture['id'], 'recipe-two')
            self.records_before = photos.RECORDS_PATH.read_bytes()
            previous_backup = photos.RECORDS_PATH.with_suffix('.previous.json').read_bytes()
            self.library.reload_catalog()
            state = self.scan()
        self.assertEqual(state['captures'][0]['recipeId'], 'recipe-two')
        self.assertTrue(state['captures'][0]['favorite'])
        self.assertEqual(state['records']['recipeFavorites'], ['recipe-one'])
        self.assertEqual(photos.RECORDS_PATH.with_suffix('.previous.json').read_bytes(), previous_backup)

    def test_scanning_never_modifies_source_bytes(self):
        p = self.image()
        before = hashlib.sha256(p.read_bytes()).digest()
        self.scan()
        self.assertEqual(hashlib.sha256(p.read_bytes()).digest(), before)


if __name__ == '__main__':
    unittest.main()
