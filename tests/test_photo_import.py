"""Import generated JPEGs into an isolated library, never the user's photos."""
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from photo_import import import_photo, MAX_FILE_BYTES


def jpeg(color='green'):
    output = io.BytesIO()
    exif = Image.Exif()
    exif[272] = 'NIKON TEST CAMERA'
    Image.new('RGB', (32, 24), color).save(output, format='JPEG', exif=exif)
    return output.getvalue()


def jpeg_with_previews():
    """A generated JPEG with an MPF directory and two embedded preview images."""
    output = io.BytesIO()
    exif = Image.Exif()
    exif[272] = 'NIKON TEST CAMERA'
    image = Image.new('RGB', (96, 64), 'purple')
    previews = [Image.new('RGB', (24, 16), 'blue'), Image.new('RGB', (48, 32), 'red')]
    image.save(output, format='MPO', save_all=True, append_images=previews, exif=exif)
    return output.getvalue()


class PhotoImportTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'photos'
        self.root.mkdir()

    def add(self, name='实拍 照片.JPEG', body=None):
        body = jpeg() if body is None else body
        return import_photo(self.root, name, io.BytesIO(body), len(body))

    def test_import_preserves_exact_bytes_and_exif(self):
        body = jpeg()
        result = self.add(body=body)
        self.assertEqual(result, {'status': 'added', 'path': '实拍 照片.JPEG'})
        self.assertEqual((self.root / result['path']).read_bytes(), body)
        with Image.open(self.root / result['path']) as image:
            self.assertEqual(image.getexif()[272], 'NIKON TEST CAMERA')
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_duplicate_content_in_subfolder_is_not_copied(self):
        nested = self.root / '以前的拍摄'
        nested.mkdir()
        (nested / 'old.jpg').write_bytes(jpeg())
        result = self.add('new.jpeg')
        self.assertEqual(result, {'status': 'duplicate', 'path': '以前的拍摄/old.jpg'})
        self.assertFalse((self.root / 'new.jpeg').exists())
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_jpeg_with_mpf_previews_preserves_all_frames_and_exif(self):
        body = jpeg_with_previews()
        result = self.add('camera.JPG', body)
        self.assertEqual(result, {'status': 'added', 'path': 'camera.JPG'})
        self.assertEqual((self.root / result['path']).read_bytes(), body)
        with Image.open(self.root / result['path']) as image:
            self.assertEqual(image.format, 'MPO')
            self.assertEqual(image.n_frames, 3)
            self.assertEqual(image.getexif()[272], 'NIKON TEST CAMERA')
            for frame, size in enumerate([(96, 64), (24, 16), (48, 32)]):
                image.seek(frame)
                image.load()
                self.assertEqual(image.size, size)
        self.assertEqual(self.add('copy.jpg', body)['status'], 'duplicate')

    def test_truncated_mpf_preview_is_rejected_even_when_main_image_loads(self):
        body = jpeg_with_previews()[:-30]
        with Image.open(io.BytesIO(body)) as image:
            image.load()
        with self.assertRaisesRegex(ValueError, '未能完整读取'):
            self.add(body=body)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrong_format_has_specific_message(self):
        output = io.BytesIO()
        Image.new('RGB', (20, 20)).save(output, format='PNG')
        with self.assertRaisesRegex(ValueError, '文件内容是 PNG'):
            self.add(body=output.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_same_name_different_content_never_overwrites(self):
        original = self.add('sample.jpg')
        newer = self.add('sample.jpg', jpeg('blue'))
        newest = self.add('sample.jpg', jpeg('red'))
        self.assertEqual(newer['path'], 'sample (2).jpg')
        self.assertEqual(newest['path'], 'sample (3).jpg')
        self.assertEqual((self.root / original['path']).read_bytes(), jpeg())

    def test_invalid_names_and_non_jpeg_extensions_are_rejected(self):
        for name in ('../escape.jpg', 'folder/image.jpg', r'folder\image.jpg',
                     '.hidden.jpg', 'bad\x00.jpg', 'image.jpg:stream', 'x' * 221 + '.jpg',
                     'raw.NEF', 'image.png', 'image.heic', 'image.jpg.'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.add(name)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_and_mislabeled_images_are_rejected_without_leftovers(self):
        output = io.BytesIO()
        Image.new('RGB', (20, 20)).save(output, format='PNG')
        for body in (b'not a photo', jpeg()[:100], output.getvalue()):
            with self.subTest(length=len(body)), self.assertRaises(ValueError):
                self.add(body=body)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_transfer_and_read_failure_leave_no_file(self):
        with self.assertRaises(ValueError):
            import_photo(self.root, 'partial.jpg', io.BytesIO(jpeg()), len(jpeg()) + 10)
        with self.assertRaises(OSError):
            class BrokenSource:
                def read(self, size):
                    raise OSError('Disconnected')
            import_photo(self.root, 'broken.jpg', BrokenSource(), 100)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_and_oversized_files_are_rejected_before_reading(self):
        for size in (0, -1, MAX_FILE_BYTES + 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                import_photo(self.root, 'photo.jpg', None, size)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_collision_cannot_write_outside_library(self):
        outside = self.root.parent / 'private.jpg'
        outside.write_bytes(b'private')
        (self.root / 'sample.jpg').symlink_to(outside)
        result = self.add('sample.jpg')
        self.assertEqual(result['path'], 'sample (2).jpg')
        self.assertEqual(outside.read_bytes(), b'private')

    def test_concurrent_duplicate_uploads_save_only_one_copy(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.add, ['same.jpg', 'same.jpg']))
        self.assertEqual(sorted(result['status'] for result in results), ['added', 'duplicate'])
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_failed_save_cleans_staging_file(self):
        with patch('photo_import.os.link', side_effect=PermissionError('Read-only folder')):
            with self.assertRaises(PermissionError):
                self.add()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
