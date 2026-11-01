"""Website publication is explicit, single-job, persistent and verified."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from website_sync import EVENT_PREFIX, SITE_URL, WebsiteSync


def event(phase, **values):
    return EVENT_PREFIX + json.dumps({'phase': phase, 'message': phase, **values}) + '\n'


class Process:
    def __init__(self, output, code=0):
        self.stdout = io.StringIO(output)
        self.code = code

    def wait(self):
        return self.code


class WebsiteSyncTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / 'local/worktrees/static-deploy/app/publish_site.py'
        self.script.parent.mkdir(parents=True)
        self.script.write_text('# synthetic publisher', encoding='utf-8')

    def run_job(self, output, code=0):
        calls = []
        def factory(args, **kwargs):
            calls.append((args, kwargs))
            return Process(output, code)
        manager = WebsiteSync(self.root, process_factory=factory)
        self.assertEqual(manager.status()['state'], 'idle')
        self.assertEqual(calls, [])
        self.assertTrue(manager.start())
        manager.thread.join(3)
        self.assertFalse(manager.thread.is_alive())
        self.assertEqual(calls[0][0][-1], '--events')
        self.assertNotIn('shell', calls[0][1])
        return manager

    def test_only_confirmed_online_revision_is_success_and_survives_reload(self):
        manager = self.run_job(event('exporting') + event('verified', revision='a' * 40, url=SITE_URL))
        self.assertEqual(manager.status()['state'], 'succeeded')
        self.assertEqual(manager.status()['revision'], 'a' * 40)
        restored = WebsiteSync(self.root, process_factory=lambda *a, **k: self.fail('Unexpected publication'))
        self.assertEqual(restored.status()['revision'], 'a' * 40)
        self.assertFalse(restored.status()['running'])

    def test_successful_process_without_online_confirmation_is_failure(self):
        manager = self.run_job(event('pushing') + 'Git push succeeded\n')
        self.assertEqual(manager.status()['state'], 'failed')

    def test_failed_process_cannot_claim_success(self):
        manager = self.run_job(event('verified', revision='a' * 40, url=SITE_URL), 1)
        self.assertEqual(manager.status()['state'], 'failed')

    def test_friendly_failure_is_shown_without_raw_logs(self):
        manager = self.run_job('private diagnostic\n' + event('failed', message='请重新登录 GitHub。'), 1)
        self.assertEqual(manager.status()['error'], '请重新登录 GitHub。')
        self.assertNotIn('private diagnostic', json.dumps(manager.status()))

    def test_duplicate_click_does_not_start_second_process(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def factory(*args, **kwargs):
            calls.append(args)
            started.set()
            release.wait(3)
            return Process(event('verified', revision='a' * 40, url=SITE_URL))
        manager = WebsiteSync(self.root, process_factory=factory)
        try:
            self.assertTrue(manager.start())
            self.assertTrue(started.wait(2))
            self.assertFalse(manager.start())
        finally:
            release.set()
            manager.thread.join(3)
        self.assertEqual(len(calls), 1)

    def test_interrupted_job_is_not_restarted_on_page_or_server_open(self):
        path = self.root / 'local/website-sync.json'
        path.write_text(json.dumps({'running': True, 'state': 'running'}), encoding='utf-8')
        manager = WebsiteSync(self.root, process_factory=lambda *a, **k: self.fail('Unexpected publication'))
        self.assertEqual(manager.status()['state'], 'failed')
        self.assertFalse(manager.status()['running'])

    def test_missing_deployment_is_reported_without_hanging(self):
        self.script.unlink()
        manager = WebsiteSync(self.root)
        manager.start()
        manager.thread.join(3)
        self.assertEqual(manager.status()['state'], 'failed')
        self.assertIn('未找到', manager.status()['error'])


if __name__ == '__main__':
    unittest.main()
