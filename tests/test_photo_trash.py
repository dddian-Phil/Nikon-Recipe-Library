"""Deletion and undo only operate on generated photos in isolated workspaces."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import photo_library as photos
from photo_trash import delete_photo, restore_photo
from test_photo_import import jpeg


class PhotoTrashTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        local = self.root / 'local'
        patcher = patch.multiple(photos, WORKSPACE=self.root, INTERNAL=local,
                                 PHOTO_ROOT=self.root / 'photos', CATALOG_ROOT=self.root / 'catalog',
                                 INDEX_PATH=local / 'photo-index.json', RECORDS_PATH=local / 'personal-records.json',
                                 PREVIEWS=local / 'previews')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.library = photos.PhotoLibrary()
        self.add_photo('拍摄/照片.jpg')
        self.capture = self.library.state()['captures'][0]

    def add_photo(self, name, body=None):
        path = photos.PHOTO_ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg() if body is None else body)
        os.utime(path, (100, 100))
        self.library.scan()
        return path

    def delete(self, capture=None):
        capture = capture or self.capture
        return delete_photo(self.library, capture['id'], capture['files'])

    def test_delete_and_undo_preserve_originals_favorites_and_assignments(self):
        self.library.edit('toggle_photo', self.capture['id'])
        self.library.edit('assign_photo', self.capture['id'], None)
        records = photos.RECORDS_PATH.read_bytes()
        unrelated = self.add_photo('另一张.jpeg', jpeg('blue'))
        result = self.delete()
        self.assertEqual(result['state']['summary']['photos'], 1)
        self.assertFalse((photos.PHOTO_ROOT / '拍摄/照片.jpg').exists())
        saved = photos.INTERNAL / 'deleted-photos' / result['token'] / 'files/拍摄/照片.jpg'
        self.assertEqual(saved.read_bytes(), jpeg())
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), records)
        self.assertEqual(unrelated.read_bytes(), jpeg('blue'))
        self.library.scan()
        self.assertEqual(self.library.state()['summary']['photos'], 1)
        restored = restore_photo(self.library, result['token'])
        self.assertEqual(restored['state']['summary']['photos'], 2)
        capture = next(item for item in restored['state']['captures'] if item['id'] == self.capture['id'])
        self.assertTrue(capture['favorite'])
        self.assertEqual(capture['association'], 'manual')
        self.assertEqual((photos.PHOTO_ROOT / '拍摄/照片.jpg').read_bytes(), jpeg())
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), records)

    def test_all_copies_of_capture_are_deleted_and_restored_together(self):
        self.add_photo('副本.jpeg')
        capture = self.library.state()['captures'][0]
        self.assertEqual(len(capture['files']), 2)
        result = self.delete(capture)
        self.assertEqual(result['files'], 2)
        self.assertEqual(result['state']['summary']['photos'], 0)
        self.library.scan()
        self.assertEqual(self.library.state()['summary']['photos'], 0)
        self.assertEqual(restore_photo(self.library, result['token'])['state']['summary']['files'], 2)

    def test_unknown_or_stale_confirmation_removes_nothing(self):
        with self.assertRaises(ValueError):
            delete_photo(self.library, 'unknown', self.capture['files'])
        self.add_photo('new-copy.jpg')
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.delete()
        self.assertEqual(self.library.state()['summary']['files'], 2)

    def test_modified_or_missing_file_is_not_deleted(self):
        path = photos.PHOTO_ROOT / '拍摄/照片.jpg'
        path.write_bytes(jpeg('blue'))
        with self.assertRaisesRegex(ValueError, '内容已变化'):
            self.delete()
        self.assertEqual(path.read_bytes(), jpeg('blue'))
        path.unlink()
        with self.assertRaisesRegex(ValueError, '文件已移动'):
            self.delete()

    def test_symlink_and_traversal_cannot_touch_external_files(self):
        path = photos.PHOTO_ROOT / '拍摄/照片.jpg'
        path.unlink()
        outside = self.root / 'private.jpg'
        outside.write_bytes(jpeg())
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.delete()
        self.assertEqual(outside.read_bytes(), jpeg())
        for token in ('../private', 'x' * 32, None):
            with self.assertRaises(ValueError):
                restore_photo(self.library, token)

    def test_scan_in_progress_blocks_deletion(self):
        with self.library.scan_lock, self.assertRaisesRegex(ValueError, '正在扫描'):
            self.delete()
        self.assertTrue((photos.PHOTO_ROOT / '拍摄/照片.jpg').is_file())

    def test_manifest_or_copy_failure_preserves_all_originals(self):
        self.add_photo('second.jpg')
        capture = self.library.state()['captures'][0]
        with patch('photo_trash.photos.atomic_json', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                self.delete(capture)
        link = os.link
        count = 0
        def fail_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise PermissionError('Cannot archive second file')
            return link(*args)
        with patch('photo_trash.os.link', side_effect=fail_second), self.assertRaises(PermissionError):
            self.delete(capture)
        self.assertEqual((photos.PHOTO_ROOT / '拍摄/照片.jpg').read_bytes(), jpeg())
        self.assertEqual((photos.PHOTO_ROOT / 'second.jpg').read_bytes(), jpeg())
        self.assertEqual(self.library.state()['summary']['files'], 2)

    def test_scan_failure_rolls_back_moves_and_index(self):
        before = self.library.index
        with patch.object(self.library, '_scan', side_effect=OSError('Cannot update index')):
            with self.assertRaises(OSError):
                self.delete()
        self.assertEqual((photos.PHOTO_ROOT / '拍摄/照片.jpg').read_bytes(), jpeg())
        self.assertIs(self.library.index, before)
        self.assertEqual(photos.read_json(photos.INDEX_PATH, {})['captures'], before['captures'])

    def test_restore_refuses_collision_and_retains_recovery_copy(self):
        result = self.delete()
        path = self.add_photo('拍摄/照片.jpg', jpeg('blue'))
        with self.assertRaisesRegex(ValueError, '同名文件'):
            restore_photo(self.library, result['token'])
        self.assertEqual(path.read_bytes(), jpeg('blue'))
        archived = photos.INTERNAL / 'deleted-photos' / result['token'] / 'files/拍摄/照片.jpg'
        self.assertEqual(archived.read_bytes(), jpeg())

    def test_restore_after_restart_and_repeated_operations(self):
        result = self.delete()
        with self.assertRaises(ValueError):
            self.delete()
        self.library = photos.PhotoLibrary()
        restore_photo(self.library, result['token'])
        with self.assertRaises(ValueError):
            restore_photo(self.library, result['token'])
        self.assertEqual((photos.PHOTO_ROOT / '拍摄/照片.jpg').read_bytes(), jpeg())

    def test_invalid_restore_manifest_cannot_escape_trash_or_photo_root(self):
        result = self.delete()
        folder = photos.INTERNAL / 'deleted-photos' / result['token']
        manifest = json.loads((folder / 'manifest.json').read_text())
        manifest['files'][0]['path'] = '../../../private.jpg'
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            restore_photo(self.library, result['token'])

    def test_damaged_photo_can_still_be_removed_and_recovered(self):
        self.add_photo('damaged.jpg', b'incomplete JPEG')
        damaged = next(item for item in self.library.state()['captures'] if item['name'] == 'damaged')
        result = self.delete(damaged)
        restore_photo(self.library, result['token'])
        self.assertEqual((photos.PHOTO_ROOT / 'damaged.jpg').read_bytes(), b'incomplete JPEG')


if __name__ == '__main__':
    unittest.main()
