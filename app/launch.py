"""Start, reuse and stop this workspace's local server on macOS."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


class Launcher:
    def __init__(self, workspace=ROOT):
        self.root = Path(workspace).resolve()
        self.local = self.root / 'local'
        config_path = self.local / 'runtime.json'
        self.config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        self.port = self.config.get('port', 8765)
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError('local/runtime.json 的 port 必须是 1–65535 的整数。')
        self.url = 'http://127.0.0.1:' + str(self.port)
        self.fingerprint = hashlib.sha256(str(self.root).encode()).hexdigest()
        self.process_path = self.local / 'runtime-process.json'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @contextmanager
    def locked(self):
        import fcntl
        self.local.mkdir(parents=True, exist_ok=True)
        with (self.local / 'launcher.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def request(self, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.url + path, data=body,
            headers={'Content-Type': 'application/json', 'Origin': self.url})
        with self.opener.open(request, timeout=3) as response:
            return json.load(response)

    def health(self):
        try:
            state = self.request('/api/health')
        except urllib.error.HTTPError as error:
            raise ValueError('端口已被其他程序占用，请调整 local/runtime.json 的 port。') from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                return None
            raise ValueError('无法确认本地服务状态，请检查端口或 local/runtime.log。') from error
        except (ValueError, TimeoutError) as error:
            raise ValueError('端口没有返回资料库状态，请检查 local/runtime.json 的 port。') from error
        if not isinstance(state, dict) or state.get('service') != 'nikon-local-library' or state.get('workspace') != self.fingerprint:
            raise ValueError('这个端口属于另一个程序或资料库，请调整 local/runtime.json 的 port。')
        return state

    def ensure_server(self):
        if self.health():
            # An existing service may still have HTML generated before a code edit.
            subprocess.run([sys.executable, '-B', str(self.root / 'app/build_viewer.py')],
                           cwd=self.root, check=True)
            return
        log_path = self.local / 'runtime.log'
        with log_path.open('a', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-u', '-B',
                str(self.root / 'app/library_server.py'), '--port', str(self.port)],
                cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, env=dict(os.environ, PYTHONUNBUFFERED='1'))
        try:
            for _ in range(80):
                if process.poll() is not None:
                    raise ValueError('资料库启动失败，请查看 local/runtime.log；缺少依赖时重新运行 setup-mac.command。')
                state = self.health()
                if state:
                    if state.get('pid') != process.pid:
                        raise ValueError('另一个资料库进程已占用端口，请重试。')
                    temporary = self.process_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'pid': process.pid, 'port': self.port,
                        'workspace': self.fingerprint}) + '\n', encoding='utf-8')
                    temporary.replace(self.process_path)
                    return
                time.sleep(0.25)
            raise ValueError('资料库启动超时，请查看 local/runtime.log。')
        except Exception:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise

    def open(self, browser=True):
        self.ensure_server()
        print('资料库已打开：' + self.url + '/')
        if browser and not webbrowser.open(self.url + '/'):
            print('请在浏览器打开上面的地址。')

    def update(self):
        self.ensure_server()
        self.request('/api/scan', {})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = self.request('/api/state')
            if not state['scanning']:
                if state.get('scanError'):
                    raise ValueError(state['scanError'])
                print(json.dumps(state['summary'], ensure_ascii=False))
                return
            time.sleep(0.3)
        raise ValueError('照片仍在扫描，可在网页查看进度。')

    def stop(self):
        state = self.health()
        if state is None:
            print('资料库已经停止。')
            return
        process = json.loads(self.process_path.read_text(encoding='utf-8')) if self.process_path.exists() else {}
        pid = process.get('pid')
        if (not isinstance(pid, int) or pid <= 1 or pid != state.get('pid') or
                process.get('workspace') != self.fingerprint or process.get('port') != self.port):
            raise ValueError('服务并非由当前 Mac 启动器启动，请在原来的终端停止它。')
        status = self.request('/api/state')
        if status.get('scanning'):
            raise ValueError('照片仍在扫描，请等待完成后再关闭资料库。')
        if any((status.get(key) or {}).get('running') for key in ('catalogSync', 'websiteSync')):
            raise ValueError('同步或发布仍在进行，请等待完成后再关闭资料库。')
        os.kill(pid, signal.SIGTERM)
        for _ in range(40):
            try:
                stopped = self.health() is None
            except (ConnectionResetError, ValueError):
                # A request may race with the server closing its listening socket.
                stopped = False
            if stopped:
                self.process_path.unlink(missing_ok=True)
                print('资料库已停止。')
                return
            time.sleep(0.1)
        raise ValueError('资料库尚未停止，请检查 local/runtime.log。')

    def configure(self):
        self.local.mkdir(parents=True, exist_ok=True)
        self.config.update(python=sys.executable, port=self.port)
        path = self.local / 'runtime.json'
        path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Mac 运行环境已配置。双击 open-library.command 打开资料库。')

    def sync(self, prepare_only=False):
        deployment = self.local / 'worktrees/static-deploy'
        script = deployment / 'app/publish_site.py'
        if not script.is_file():
            raise ValueError('缺少网页部署目录，请按 docs/macos.md 重建部署 worktree。')
        args = [sys.executable, '-u', '-B', str(script), '--workspace', str(self.root)]
        if prepare_only:
            args.append('--prepare-only')
        subprocess.run(args, cwd=deployment, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['open', 'update', 'stop', 'configure', 'sync'])
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise ValueError('需要 Python 3.11 或更新版本，请先运行 setup-mac.command。')
    launcher = Launcher()
    with launcher.locked():
        if args.action == 'open':
            launcher.open(browser=not args.no_browser)
        elif args.action == 'sync':
            launcher.sync(prepare_only=args.prepare_only)
        else:
            getattr(launcher, args.action)()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
