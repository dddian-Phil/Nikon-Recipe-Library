"""Verify paired restoration and failure recovery with disposable source trees."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from restore_v1 import restore


class VersionRestoreTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / 'app/ui.css'
        self.web = self.root / 'local/worktrees/static-deploy/app/ui.css'
        for file in (self.local, self.web):
            file.parent.mkdir(parents=True)
            file.write_bytes(b'new design')
        self.plans = [('main/app/ui.css', self.local, b'v1 local'), ('web/app/ui.css', self.web, b'v1 web')]

    def test_both_trees_are_backed_up_before_restoration(self):
        photo = self.root / 'photos/original.jpg'
        photo.parent.mkdir()
        photo.write_bytes(b'original photograph')
        records = self.root / 'local/personal-records.json'
        records.write_bytes(b'personal records')
        backup = restore(self.root, self.plans)
        with zipfile.ZipFile(backup) as saved:
            self.assertEqual(saved.read('main/app/ui.css'), b'new design')
            self.assertEqual(saved.read('web/app/ui.css'), b'new design')
        self.assertEqual(self.local.read_bytes(), b'v1 local')
        self.assertEqual(self.web.read_bytes(), b'v1 web')
        self.assertEqual(photo.read_bytes(), b'original photograph')
        self.assertEqual(records.read_bytes(), b'personal records')

    def test_failed_backup_changes_neither_tree(self):
        with patch('restore_v1.zipfile.ZipFile.testzip', return_value='bad file'):
            with self.assertRaisesRegex(ValueError, 'backup failed'):
                restore(self.root, self.plans)
        self.assertEqual(self.local.read_bytes(), b'new design')
        self.assertEqual(self.web.read_bytes(), b'new design')

    def test_second_tree_failure_rolls_back_the_first(self):
        original_replace = Path.replace
        def replace(path, target):
            if target == self.web:
                raise OSError('Synthetic disk failure')
            return original_replace(path, target)
        with patch.object(Path, 'replace', replace):
            with self.assertRaises(OSError):
                restore(self.root, self.plans)
        self.assertEqual(self.local.read_bytes(), b'new design')
        self.assertEqual(self.web.read_bytes(), b'new design')


if __name__ == '__main__':
    unittest.main()
