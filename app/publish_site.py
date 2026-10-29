"""One-way publication: scan, export reduced copies, verify, Git push, await Pages."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
EVENTS = False
STARTED = time.monotonic()


def progress(phase, message, **values):
    values['elapsedSeconds'] = round(time.monotonic() - STARTED, 1)
    if EVENTS:
        print('WEBSITE_SYNC ' + json.dumps({'phase': phase, 'message': message, **values}), flush=True)
    else:
        print(message, flush=True)


def friendly_error(error):
    message = str(error)
    for token, explanation in (
        ('uncommitted', '网页部署文件有尚未完成的修改，请先保存并提交这些修改，再重新同步。'),
        ('staged changes', '网页部署目录有待提交的修改，请先完成这些修改。'),
        ('scan is incomplete', '照片仍在复制或读取失败，请等照片准备好后重试。'),
        ('edited since', '照片在准备期间发生变化，请重新同步。'),
        ('changed during', '照片或关联在准备期间发生变化，请重新同步。'),
        ('GitHub CLI', '未找到 GitHub 登录工具，请完成 GitHub CLI 安装和登录。'),
        ('Pages has not started', '内容已上传，但网页还未开始更新，请稍后重试。'),
        ('live version is not confirmed', '网页发布已完成，但暂时无法确认线上版本，请稍后重试。'),
        ('already running', '网页同步已在进行，请等待完成后再试。')):
        if token in message:
            return explanation
    if isinstance(error, subprocess.CalledProcessError):
        args = error.cmd if isinstance(error.cmd, (list, tuple)) else [error.cmd]
        if 'auth' in args:
            return 'GitHub 登录已失效，请重新登录 GitHub 后再同步。'
        if 'push' in args:
            return '上传 GitHub 失败，请检查网络；本地照片和记录已保留。'
        if 'watch' in args:
            return 'GitHub 未能完成网页发布，请查看部署结果后重试。'
        return '发布检查未通过，详细原因已保存在本地同步日志中。'
    return '同步未完成，请检查网络和本地照片后重试；详细原因已保存在本地同步日志中。'


@contextmanager
def publication_lock():
    path = ROOT / 'local/website-publish.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Website publication is already running') from error
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


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
    progress('scanning', '正在检查新增和修改的实拍照片…')
    refresh_photos(workspace)
    progress('exporting', '正在生成实拍展示图并整理本地方案、收藏和关联…')
    export_catalog(workspace / 'data/catalog')
    catalog, _ = snapshot_files(SNAPSHOT)
    result = export_photos(workspace, ROOT / 'data/public-photos', {r['id'] for r in catalog['recipes']})
    print(json.dumps(result), flush=True)
    progress('testing', '正在校验网页文件、图片尺寸和完整性…')
    build()


def online_revision_matches(revision):
    try:
        req = urllib.request.Request(SITE_URL + 'publish-manifest.json?revision=' + revision,
                                      headers={'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(req, timeout=12) as response:
            manifest = json.load(response)
        return manifest.get('revision') == revision
    except (urllib.error.URLError, ValueError):
        return False


def await_deployment(revision):
    progress('deploying', '内容已上传，GitHub 正在更新网页…')
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
    progress('verifying', '正在确认线上网页已更新…')
    deadline = time.monotonic() + 120
    while True:
        if online_revision_matches(revision):
            print('Published and verified: ' + revision + '\n' + SITE_URL, flush=True)
            progress('verified', '网页已更新。', revision=revision, url=SITE_URL)
            return
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
    global EVENTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true', help='Build and verify locally without committing or pushing')
    parser.add_argument('--events', action='store_true', help='Emit publication progress for the local web UI')
    args = parser.parse_args()
    EVENTS = args.events
    with publication_lock():
        publish(args)


def publish(args):
    workspace = args.workspace.resolve()
    progress('checking', '正在检查网页同步配置…')
    if not args.prepare_only:
        check_deployment()
    prepare(workspace)
    if args.prepare_only:
        print('Local preview prepared; nothing committed or pushed.', flush=True)
        return
    # Check again: never scoop up edits made while export/tests were running.
    check_deployment()
    changes = command('git', 'status', '--porcelain=v1', '--untracked-files=all', '--', *PUBLIC_PATHS)
    revision = command('git', 'rev-parse', 'HEAD')
    if not changes:
        progress('verifying', '本地内容没有变化，正在确认网页版本…')
        if online_revision_matches(revision):
            progress('verified', '网页已经是最新，无需重复发布。', revision=revision, url=SITE_URL, unchanged=True)
            return
    if not shutil.which('gh'):
        raise ValueError('GitHub CLI (gh) is required to confirm deployment status')
    command('gh', 'auth', 'status', capture=True)
    progress('pushing', '正在上传方案和实拍到 GitHub…')
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
        progress('failed', friendly_error(error))
        sys.exit(1)
