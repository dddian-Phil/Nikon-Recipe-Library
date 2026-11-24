"""Exercise the real local HTTP handler against a synthetic official service."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import library_server as server
import photo_library as photos
from catalog_sync import CatalogSync
from test_catalog_sync import FakeOfficial, NEW, detail, seed
from test_photo_import import jpeg, jpeg_with_previews


class ServerTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        assert Path(temporary.name).resolve().is_relative_to(base.resolve())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        local = self.root / 'local'
        self.official = FakeOfficial()
        catalog = seed(self.root, self.official)
        patcher = patch.multiple(photos, WORKSPACE=self.root, INTERNAL=local,
                                 PHOTO_ROOT=self.root / 'photos', CATALOG_ROOT=catalog,
                                 INDEX_PATH=local / 'photo-index.json',
                                 RECORDS_PATH=local / 'personal-records.json', PREVIEWS=local / 'previews')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.library = photos.PhotoLibrary()
        self.records_before = photos.RECORDS_PATH.read_bytes()
        self.catalog = CatalogSync(self.root, transport=self.official, on_reload=self.library.reload_catalog)
        self.catalog.publication_guard = lambda: self.library.catalog_gate
        handler_globals = patch.multiple(server, LIBRARY=self.library, CATALOG=self.catalog,
                                         CATALOG_ROOT=catalog, WORKSPACE=self.root,
                                         PHOTO_ROOT=self.root / 'photos')
        handler_globals.start()
        self.addCleanup(handler_globals.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.http.daemon_threads = True
        self.worker = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=0.05), daemon=True)
        self.worker.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'
        self.addCleanup(self.close_server)

    def close_server(self):
        if self.official.release:
            self.official.release.set()
        if self.catalog.thread:
            self.catalog.thread.join(15)
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(3)

    def request(self, path, data=None, *, origin=None, content_type='application/json'):
        headers = {'Content-Type': content_type}
        if origin:
            headers['Origin'] = origin
        body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw

    def test_get_requests_never_start_sync(self):
        for path in ('/api/catalog', '/api/catalog/sync', '/api/state'):
            status, payload = self.request(path)
            self.assertEqual(status, 200)
        self.assertEqual(self.official.calls, [])

    def test_photo_import_saves_and_scans_without_changing_records(self):
        body = jpeg()
        with patch.object(self.library, 'start_scan', return_value=True) as scan:
            status, payload = self.request('/api/photos?name=' + quote('我的实拍.JPEG'), body,
                                           origin=self.url, content_type='application/octet-stream')
        self.assertEqual(status, 201)
        scan.assert_called_once()
        self.assertEqual((photos.PHOTO_ROOT / payload['path']).read_bytes(), body)
        self.library.scan()
        state = self.request('/api/state')[1]
        self.assertEqual(state['summary']['photos'], 1)
        self.assertTrue(state['captures'][0]['preview'])
        self.assertEqual(state['photoImport']['maxFileBytes'], 256 * 1024 * 1024)
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), self.records_before)
        self.assertEqual(self.official.calls, [])

    def test_photo_upload_requires_same_origin_and_binary_type(self):
        body = jpeg()
        self.assertEqual(self.request('/api/photos?name=x.jpg', body, origin='https://example.com',
                                      content_type='application/octet-stream')[0], 403)
        self.assertEqual(self.request('/api/photos?name=x.jpg', body, content_type='text/plain')[0], 400)
        self.assertEqual(list(photos.PHOTO_ROOT.iterdir()), [])

    def test_mpf_jpeg_upload_is_preserved_and_previewed(self):
        body = jpeg_with_previews()
        with patch.object(self.library, 'start_scan', return_value=True):
            status, payload = self.request('/api/photos?name=camera.JPG', body,
                                           origin=self.url, content_type='application/octet-stream')
        self.assertEqual(status, 201)
        self.assertEqual((photos.PHOTO_ROOT / payload['path']).read_bytes(), body)
        self.library.scan()
        state = self.request('/api/state')[1]
        self.assertEqual(state['summary']['photos'], 1)
        self.assertTrue(state['captures'][0]['preview'])
        self.assertEqual(state['captures'][0]['camera'], 'NIKON TEST CAMERA')
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), self.records_before)

    def test_photo_upload_rejects_non_jpeg_corrupt_empty_and_escaping_files(self):
        for path, body in [('/api/photos?name=bad.jpg', b'bad'),
                           ('/api/photos?name=raw.nef', jpeg()),
                           ('/api/photos?name=..%2Fescape.jpg', jpeg())]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, body, content_type='application/octet-stream')[0], 400)
        self.assertEqual(self.request('/api/photos?name=empty.jpg', b'',
                                      content_type='application/octet-stream')[0], 413)
        self.assertEqual(list(photos.PHOTO_ROOT.iterdir()), [])

    def test_duplicate_photo_upload_reports_existing_copy(self):
        (photos.PHOTO_ROOT / 'existing.jpg').write_bytes(jpeg())
        with patch.object(self.library, 'start_scan', return_value=True):
            status, payload = self.request('/api/photos?name=duplicate.jpeg', jpeg(),
                                           content_type='application/octet-stream')
        self.assertEqual(status, 200)
        self.assertEqual(payload, {'status': 'duplicate', 'path': 'existing.jpg'})
        self.assertEqual(len(list(photos.PHOTO_ROOT.iterdir())), 1)

    def test_delete_and_restore_require_local_json_and_update_state(self):
        path = photos.PHOTO_ROOT / 'delete-me.jpg'
        path.write_bytes(jpeg())
        os.utime(path, (100, 100))
        self.library.scan()
        capture = self.library.state()['captures'][0]
        request = {'id': capture['id'], 'files': capture['files']}
        self.assertTrue(self.request('/api/state')[1]['photoDelete'])
        self.assertEqual(self.request('/api/photos/delete')[0], 404)
        self.assertEqual(self.request('/api/photos/delete', request, origin='https://example.com')[0], 403)
        self.assertEqual(self.request('/api/photos/delete', request, content_type='text/plain')[0], 400)
        self.assertTrue(path.exists())
        status, result = self.request('/api/photos/delete', request, origin=self.url)
        self.assertEqual(status, 200)
        self.assertEqual(result['state']['summary']['photos'], 0)
        self.assertEqual(self.request('/api/original?path=delete-me.jpg')[0], 404)
        undo = {'token': result['token']}
        self.assertEqual(self.request('/api/photos/restore', undo, origin='https://example.com')[0], 403)
        self.assertEqual(self.request('/api/photos/restore', undo)[1]['state']['summary']['photos'], 1)
        self.assertEqual(path.read_bytes(), jpeg())
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), self.records_before)
        self.assertEqual(self.official.calls, [])

    def test_deletion_during_web_publication_is_rejected(self):
        website = SimpleNamespace(status=lambda: {'running': True})
        with patch.object(server, 'WEBSITE', website):
            self.assertEqual(self.request('/api/photos/delete', {})[0], 409)
            self.assertEqual(self.request('/api/photos/restore', {})[0], 409)

    def test_post_sync_updates_live_payload_without_touching_personal_records(self):
        self.official.details.append(detail(NEW))
        status, payload = self.request('/api/catalog/sync', {}, origin=self.url)
        self.assertEqual(status, 202)
        self.catalog.thread.join(15)
        status, payload = self.request('/api/catalog')
        self.assertEqual(status, 200)
        self.assertEqual(len(payload['recipes']), 2)
        self.assertEqual(payload['syncMetadata']['newRecipeIds'], [NEW])
        self.assertTrue(all(r['image'].startswith('catalog/') for r in payload['recipes']))
        self.assertIn(NEW, self.library.recipe_ids)
        self.assertEqual(photos.RECORDS_PATH.read_bytes(), self.records_before)

    def test_repeated_post_is_conflict(self):
        self.official.started = threading.Event()
        self.official.release = threading.Event()
        self.assertEqual(self.request('/api/catalog/sync', {})[0], 202)
        self.assertTrue(self.official.started.wait(3))
        status, payload = self.request('/api/catalog/sync', {})
        self.assertEqual(status, 409)
        self.assertTrue(payload['error'])

    def test_failed_publication_never_exposes_new_recipes_to_photo_scan(self):
        self.official.details.append(detail(NEW))
        observed, workers = [], []
        entered = threading.Event()
        completed = threading.Event()
        def scan():
            entered.set()
            self.library.scan()
            completed.set()
        def reload():
            self.library.reload_catalog()
            if not workers:
                worker = threading.Thread(target=scan)
                workers.append(worker)
                worker.start()
                self.assertTrue(entered.wait(3))
                self.assertFalse(completed.wait(0.05))
                raise RuntimeError('Synthetic publication failure')
        self.catalog.on_reload = reload
        with patch.object(self.library, '_scan', side_effect=lambda: observed.append(set(self.library.recipe_ids))):
            self.assertEqual(self.request('/api/catalog/sync', {})[0], 202)
            self.catalog.thread.join(15)
            for worker in workers:
                worker.join(5)
        self.assertEqual(self.catalog.status()['state'], 'failed')
        self.assertTrue(completed.is_set())
        self.assertEqual(len(observed), 1)
        self.assertNotIn(NEW, observed[0])

    def test_cross_site_sync_is_rejected(self):
        self.assertEqual(self.request('/api/catalog/sync', {}, origin='https://example.com')[0], 403)
        self.assertEqual(self.official.calls, [])

    def test_invalid_request_body_is_rejected(self):
        self.assertEqual(self.request('/api/catalog/sync', [1, 2])[0], 400)
        self.assertEqual(self.request('/api/catalog/sync', {}, content_type='text/plain')[0], 400)
        self.assertEqual(self.official.calls, [])

    def test_catalog_download_cannot_escape_catalog_directory(self):
        self.assertEqual(self.request('/catalog/%2e%2e/%2e%2e/local/personal-records.json')[0], 404)

    def test_website_get_and_foreign_origin_never_publish(self):
        website = SimpleNamespace(status=lambda: {'state': 'idle', 'running': False},
                                  start=lambda: self.fail('Unexpected publication'))
        with patch.object(server, 'WEBSITE', website):
            self.assertEqual(self.request('/api/website/sync')[0], 200)
            self.assertEqual(self.request('/api/state')[1]['websiteSync']['state'], 'idle')
            self.assertEqual(self.request('/api/website/sync', {}, origin='https://example.com')[0], 403)
            self.assertEqual(self.request('/api/website/sync', {}, content_type='text/plain')[0], 400)

    def test_website_post_starts_one_job_and_duplicate_is_conflict(self):
        starts = []
        def start():
            starts.append(True)
            return len(starts) == 1
        website = SimpleNamespace(status=lambda: {'state': 'running', 'running': True}, start=start)
        with patch.object(server, 'WEBSITE', website):
            self.assertEqual(self.request('/api/website/sync', {}, origin=self.url)[0], 202)
            self.assertEqual(self.request('/api/website/sync', {}, origin=self.url)[0], 409)
        self.assertEqual(self.official.calls, [])

    def test_catalog_and_website_jobs_do_not_overlap(self):
        website = SimpleNamespace(status=lambda: {'state': 'running', 'running': True},
                                  start=lambda: self.fail('Unexpected publication'))
        with patch.object(server, 'WEBSITE', website):
            self.assertEqual(self.request('/api/catalog/sync', {}, origin=self.url)[0], 409)
        website.status = lambda: {'state': 'idle', 'running': False}
        with patch.object(server, 'WEBSITE', website), patch.object(self.catalog, 'status', return_value={'running': True}):
            self.assertEqual(self.request('/api/website/sync', {}, origin=self.url)[0], 409)
        self.assertEqual(self.official.calls, [])


if __name__ == '__main__':
    unittest.main()
