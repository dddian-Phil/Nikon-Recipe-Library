"""Loopback-only application server for the local Nikon recipe library."""
import argparse
import hashlib
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from photo_library import PhotoLibrary, WORKSPACE, INTERNAL, PHOTO_ROOT, CATALOG_ROOT, PREVIEWS

LIBRARY = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, content, status=200, download=None):
        body = json.dumps(content, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, boundary, original=False):
        path = path.resolve()
        if not path.is_relative_to(boundary.resolve()) or not path.is_file():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Cache-Control', 'no-cache')
        if original:
            self.send_header('Content-Disposition', "inline; filename*=UTF-8''" + quote(path.name))
        self.end_headers()
        with path.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == '/api/health':
                self.send_json({'service': 'nikon-local-library', 'workspace': hashlib.sha256(str(WORKSPACE).encode()).hexdigest(), 'port': self.server.server_port})
            elif path == '/api/state':
                self.send_json(LIBRARY.state())
            elif path == '/api/backup':
                with LIBRARY.lock:
                    self.send_json(LIBRARY.records, download='nikon-personal-records.json')
            elif path == '/api/original':
                query = parse_qs(parsed.query)
                self.send_file(PHOTO_ROOT / query.get('path', [''])[0], PHOTO_ROOT, original=True)
            elif path in ('/', '/index.html'):
                self.send_file(Path(__file__).parent / 'viewer.html', Path(__file__).parent)
            elif path == '/app.js':
                self.send_file(Path(__file__).parent / 'app.js', Path(__file__).parent)
            elif path.startswith('/catalog/'):
                self.send_file(CATALOG_ROOT / path.removeprefix('/catalog/'), CATALOG_ROOT)
            elif path.startswith('/photo-previews/'):
                self.send_file(PREVIEWS / path.removeprefix('/photo-previews/'), PREVIEWS)
            elif path == '/scheme-table.xlsx':
                self.send_file(WORKSPACE / 'recipes.xlsx', WORKSPACE)
            elif path == '/guide':
                self.send_file(WORKSPACE / 'guide.html', WORKSPACE)
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            self.send_json({'error': '不允许跨站写入'}, 403)
            return
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.send_json({'error': '需要 JSON 请求'}, 400)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 8 * 1024 * 1024:
                raise ValueError('请求过大')
            incoming = json.loads(self.rfile.read(length) or b'{}')
            if self.path == '/api/scan':
                LIBRARY.start_scan()
                self.send_json(LIBRARY.state())
            elif self.path == '/api/records':
                self.send_json(LIBRARY.edit(incoming.get('action'), incoming.get('id'), incoming.get('recipeId')))
            elif self.path == '/api/import':
                self.send_json(LIBRARY.import_records(incoming))
            else:
                self.send_json({'error': '接口不存在'}, 404)
        except (ValueError, KeyError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except Exception as error:
            self.send_json({'error': str(error)}, 500)


def main():
    global LIBRARY
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--scan', action='store_true')
    args = parser.parse_args()
    LIBRARY = PhotoLibrary()
    if args.scan:
        LIBRARY.scan()
        print(json.dumps(LIBRARY.state()['summary'], ensure_ascii=True))
        return
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    LIBRARY.start_scan()
    def watch():
        while True:
            time.sleep(8)
            LIBRARY.scan()
    threading.Thread(target=watch, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
