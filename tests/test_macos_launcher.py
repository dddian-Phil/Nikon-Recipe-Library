"""Exercise the Mac entry points with an isolated, empty photo library."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from launch import Launcher


@unittest.skipUnless(sys.platform == 'darwin', 'macOS entry points')
class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='Mac library 测试 ', dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / 'app', self.root / 'app',
                        ignore=shutil.ignore_patterns('__pycache__', 'viewer.html'))
        (self.root / 'local').mkdir()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        (self.root / 'local/runtime.json').write_text(json.dumps({'port': port}), encoding='utf-8')
        (self.root / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
        for name in ('open-library.command', 'update-photos.command', 'stop-library.command'):
            shutil.copy2(ROOT / name, self.root / name)
        self.launcher = Launcher(self.root)
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        path = self.root / 'local/runtime-process.json'
        if path.exists():
            try:
                os.kill(json.loads(path.read_text())['pid'], signal.SIGTERM)
            except ProcessLookupError:
                pass

    def command(self, name, *args):
        return subprocess.run(['/bin/bash', str(self.root / name), *args],
            cwd='/private/tmp', capture_output=True, text=True, timeout=35)

    def test_open_reuses_service_rebuilds_viewer_updates_and_stops_from_other_directory(self):
        with patch('launch.webbrowser.open', side_effect=AssertionError('Browser must remain closed')):
            result = self.command('open-library.command', '--no-browser')
            self.assertEqual(result.returncode, 0, result.stderr +
                (self.root / 'local/runtime.log').read_text(encoding='utf-8'))
        state = self.launcher.health()
        self.assertIsNotNone(state)
        pid = state['pid']
        viewer = self.root / 'app/viewer.html'
        viewer.write_text('stale generated page', encoding='utf-8')
        result = self.command('open-library.command', '--no-browser')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launcher.health()['pid'], pid)
        self.assertIn('id="browse-controls"', viewer.read_text(encoding='utf-8'))
        result = self.command('update-photos.command')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['photos'], 0)
        result = self.command('stop-library.command')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.launcher.health())
        self.assertFalse(self.launcher.process_path.exists())

    def test_foreign_workspace_never_rebuilds_starts_or_opens(self):
        with patch.object(self.launcher, 'request', return_value={'service': 'nikon-local-library', 'workspace': 'other'}), \
                patch('launch.subprocess.run') as run, patch('launch.subprocess.Popen') as spawn, \
                patch('launch.webbrowser.open') as browser:
            with self.assertRaisesRegex(ValueError, '另一个'):
                self.launcher.open()
        run.assert_not_called()
        spawn.assert_not_called()
        browser.assert_not_called()

    def test_failed_generation_does_not_open_browser(self):
        with patch.object(self.launcher, 'health', return_value={'pid': 1}), \
                patch('launch.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'builder')), \
                patch('launch.webbrowser.open') as browser:
            with self.assertRaises(subprocess.CalledProcessError):
                self.launcher.open()
        browser.assert_not_called()

    def test_failed_start_has_diagnostics_and_no_saved_process(self):
        (self.root / 'app/library_server.py').write_text('raise RuntimeError("synthetic startup failure")\n')
        result = self.command('open-library.command', '--no-browser')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('synthetic startup failure', (self.root / 'local/runtime.log').read_text())
        self.assertFalse(self.launcher.process_path.exists())

    def test_stop_rejects_stale_pid_without_sending_signal(self):
        self.launcher.process_path.write_text(json.dumps({'pid': 41, 'port': self.launcher.port,
            'workspace': self.launcher.fingerprint}))
        with patch.object(self.launcher, 'health', return_value={'pid': 42}), patch('launch.os.kill') as kill:
            with self.assertRaisesRegex(ValueError, '并非'):
                self.launcher.stop()
        kill.assert_not_called()

    def test_configure_replaces_windows_interpreter_and_preserves_port(self):
        self.launcher.config['python'] = r'D:\miniconda3\python.exe'
        self.launcher.configure()
        config = json.loads((self.root / 'local/runtime.json').read_text())
        self.assertEqual(config['python'], sys.executable)
        self.assertEqual(config['port'], self.launcher.port)

    def test_prepare_only_publication_uses_project_python_and_rebuilt_worktree(self):
        script = self.root / 'local/worktrees/static-deploy/app/publish_site.py'
        script.parent.mkdir(parents=True)
        script.write_text('# test publisher')
        with patch('launch.subprocess.run') as run:
            self.launcher.sync(prepare_only=True)
        run.assert_called_once_with([sys.executable, '-u', '-B', str(script), '--workspace', str(self.root),
            '--prepare-only'], cwd=script.parents[1], check=True)


if __name__ == '__main__':
    unittest.main()
