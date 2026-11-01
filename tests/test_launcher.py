"""Exercise the Windows launcher without opening a browser or a real service."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


POWERSHELL = shutil.which('powershell.exe')
WORKSPACE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell is required')
class LauncherTests(unittest.TestCase):
    def setUp(self):
        base = WORKSPACE / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.program = self.root / 'app'
        self.program.mkdir()
        local = self.root / 'local'
        local.mkdir()
        (local / 'runtime.json').write_text(
            json.dumps({'python': sys.executable, 'port': 8765}), encoding='utf-8')
        for name in ('launch.ps1', 'build_viewer.py', 'collect_nikon.py',
                     'viewer_template.html', 'ui.css'):
            shutil.copyfile(WORKSPACE / 'app' / name, self.program / name)
        self.viewer = self.program / 'viewer.html'
        self.viewer.write_text('old generated page', encoding='utf-8')

    def launch(self, workspace=None):
        fingerprint = workspace or hashlib.sha256(str(self.root).encode()).hexdigest()
        script_path = str(self.program / 'launch.ps1').replace("'", "''")
        command = """
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    if ($Uri -ne 'http://127.0.0.1:8765/api/health') {
        throw 'Unexpected request'
    }
    return [PSCustomObject]@{
        service = 'nikon-local-library'
        workspace = '%s'
    }
}
function Start-Process {
    param($FilePath)
    if ($FilePath -ne 'http://127.0.0.1:8765/') {
        throw 'The existing service must not be restarted'
    }
    if ((Get-Content -LiteralPath '%s' -Raw) -eq 'old generated page') {
        throw 'Browser opened before rebuilding the viewer'
    }
    Write-Host 'Browser opened'
}
& '%s' -Action open
""" % (fingerprint, str(self.viewer).replace("'", "''"), script_path)
        encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        return subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-EncodedCommand', encoded],
            capture_output=True, timeout=30)

    def test_running_service_rebuilds_stale_viewer_before_opening_browser(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'Browser opened', result.stdout)
        html = self.viewer.read_text(encoding='utf-8')
        self.assertIn((self.program / 'ui.css').read_text(encoding='utf-8'), html)
        self.assertIn('id="browse-controls"', html)

    def test_wrong_workspace_does_not_rebuild_or_open_browser(self):
        result = self.launch(workspace='another-workspace')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b'Browser opened', result.stdout)
        self.assertEqual(self.viewer.read_text(encoding='utf-8'), 'old generated page')

    def test_generation_failure_does_not_open_browser(self):
        (self.program / 'build_viewer.py').write_text('raise SystemExit(1)\n', encoding='utf-8')
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b'Browser opened', result.stdout)


if __name__ == '__main__':
    unittest.main()
