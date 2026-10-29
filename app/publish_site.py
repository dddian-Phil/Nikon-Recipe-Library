"""One-way publication: scan, export reduced copies, verify, Git push, await Pages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from build_static import ROOT, SNAPSHOT, build, export_catalog, snapshot_files
from public_photos import export_photos

BRANCH = 'codex/static-deploy'
PUBLIC_PATHS = ('data/public-catalog/', 'data/public-photos/')
SITE_URL = 'https://dddian-phil.github.io/Nikon-Recipe-Library/'


def command(*args, capture=True, cwd=ROOT):
    result = subprocess.run(args, cwd=cwd, check=True, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.rstrip('\r\n') if capture else ''


def check_deployment():
    if command('git', 'branch', '--show-current') != BRANCH:
        raise ValueError('Use the codex/static-deploy worktree; the current branch will not be switched')
    if command('git', 'diff', '--cached', '--name-only'):
        raise ValueError('The deployment worktree has staged changes; finish that work before publishing photos')
    status = command('git', 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    for entry in status.split('\0'):
        if not entry:
            continue
        # Renames have a second path in porcelain output; reject them conservatively.
        if 'R' in entry[:2] or 'C' in entry[:2] or not entry[3:].startswith(PUBLIC_PATHS):
            raise ValueError('The deployment code has uncommitted changes; review and commit it first: ' + entry[3:])


def refresh_photos(workspace):
    config_path = workspace / 'local/runtime.json'
    config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    base = 'http://127.0.0.1:' + str(int(config.get('port', 8765)))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None):
        req = urllib.request.Request(base + path, data=body,
                headers={'Content-Type': 'application/json', 'Origin': base})
        with opener.open(req, timeout=10) as response:
            return json.load(response)

    try:
        health = request('/api/health')
    except urllib.error.URLError:
        command(sys.executable, '-B', str(workspace / 'app/library_server.py'), '--scan', cwd=workspace)
    else:
        expected = hashlib.sha256(str(workspace).encode()).hexdigest()
        if health.get('service') != 'nikon-local-library' or health.get('workspace') != expected:
            raise ValueError('The configured port belongs to another workspace')
        request('/api/scan', b'{}')
        deadline = time.monotonic() + 180
        while True:
            state = request('/api/state')
            if not state['scanning']:
                if state.get('scanError'):
                    raise ValueError('Photo scan failed: ' + state['scanError'])
                break
            if time.monotonic() > deadline:
                raise ValueError('Photo scan is still running; retry after it finishes')
            time.sleep(0.5)


def prepare(workspace):
    print('1/4 Scanning local JPGs...', flush=True)
    refresh_photos(workspace)
    print('2/4 Exporting public catalog and reduced photo copies...', flush=True)
    export_catalog(workspace / 'data/catalog')
    catalog, _ = snapshot_files(SNAPSHOT)
    result = export_photos(workspace, ROOT / 'data/public-photos', {r['id'] for r in catalog['recipes']})
    build()
    print(json.dumps(result), flush=True)
    print('3/4 Verifying publication boundaries and JavaScript...', flush=True)
    command(sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v', capture=False)
    command('node', '--check', 'app/app.js', capture=False)


def await_deployment(revision):
    deadline = time.monotonic() + 120
    while True:
        runs = json.loads(command('gh', 'run', 'list', '--workflow', 'pages.yml', '--branch', BRANCH,
            '--commit', revision, '--event', 'push', '--json', 'databaseId,headSha,status,conclusion', '--limit', '5'))
        matching = [run for run in runs if run['headSha'] == revision]
        if matching:
            run = matching[0]
            break
        if time.monotonic() > deadline:
            raise ValueError('Git push succeeded, but Pages has not started yet; check GitHub Actions')
        time.sleep(3)
    command('gh', 'run', 'watch', str(run['databaseId']), '--exit-status', '--interval', '5', capture=False)
    outcome = json.loads(command('gh', 'run', 'view', str(run['databaseId']), '--json', 'headSha,conclusion'))
    if outcome != {'headSha': revision, 'conclusion': 'success'}:
        raise ValueError('The deployment did not complete successfully for this Git commit')
    deadline = time.monotonic() + 120
    while True:
        try:
            req = urllib.request.Request(SITE_URL + 'publish-manifest.json?revision=' + revision,
                                          headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(req, timeout=15) as response:
                manifest = json.load(response)
            if manifest.get('revision') == revision:
                print('Published and verified: ' + revision + '\n' + SITE_URL, flush=True)
                return
        except (urllib.error.URLError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise ValueError('Pages finished, but the live version is not confirmed yet; refresh later or retry')
        time.sleep(3)


def push_snapshot():
    remote = command('git', 'remote', 'get-url', 'origin')
    configured = subprocess.run(['git', 'config', '--get-urlmatch', 'http.proxy', remote],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    proxy = urlparse(configured.stdout.strip())
    options = []
    if proxy.hostname in {'127.0.0.1', 'localhost', '::1'}:
        try:
            with socket.create_connection((proxy.hostname, proxy.port or 8080), timeout=1):
                pass
        except OSError:
            print('The configured local Git proxy is unavailable; using a direct connection for this push only.', flush=True)
            options = ['-c', 'http.proxy=']
    command('git', *options, 'push', 'origin', 'HEAD:refs/heads/' + BRANCH, capture=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true', help='Build and verify locally without committing or pushing')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not args.prepare_only:
        check_deployment()
        if not shutil.which('gh'):
            raise ValueError('GitHub CLI (gh) is required to confirm deployment status')
        command('gh', 'auth', 'status', capture=True)
    prepare(workspace)
    if args.prepare_only:
        print('Local preview prepared; nothing committed or pushed.', flush=True)
        return
    # Check again: never scoop up edits made while export/tests were running.
    check_deployment()
    print('4/4 Committing public snapshots, pushing, and waiting for Pages...', flush=True)
    command('git', 'add', '--', 'data/public-catalog', 'data/public-photos')
    staged = command('git', 'diff', '--cached', '--name-only', '-z')
    if any(path and not path.startswith(PUBLIC_PATHS) for path in staged.split('\0')):
        raise ValueError('Other files were staged while publishing; no commit or push was made')
    if staged:
        command('git', 'commit', '-m', 'Refresh public catalog and reduced photo gallery', capture=False)
    revision = command('git', 'rev-parse', 'HEAD')
    push_snapshot()
    await_deployment(revision)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('Website sync NOT confirmed: ' + str(error), file=sys.stderr)
        sys.exit(1)
