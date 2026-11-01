"""Run explicit website publication in the background of the local library."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

SITE_URL = 'https://dddian-phil.github.io/Nikon-Recipe-Library/'
EVENT_PREFIX = 'WEBSITE_SYNC '


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


class WebsiteSync:
    def __init__(self, workspace, process_factory=None):
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / 'local/website-sync.json'
        self.log = self.workspace / 'local/website-sync.log'
        self.lock = threading.RLock()
        self.thread = None
        self.process_factory = process_factory or subprocess.Popen
        self._status = {'state': 'idle', 'running': False, 'message': '', 'error': '', 'url': SITE_URL}
        if self.path.exists():
            self._status.update(json.loads(self.path.read_text(encoding='utf-8')))
        if self._status.get('running'):
            self._update(state='failed', running=False, message='上次网页同步被中断，请重新同步。',
                         error='尚未确认网页更新完成。')

    def status(self):
        with self.lock:
            return dict(self._status)

    def _update(self, **values):
        with self.lock:
            self._status.update(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self._status, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.path)

    def start(self):
        with self.lock:
            if self._status['running']:
                return False
            self._update(state='running', running=True, message='正在准备同步到网页…', error='', startedAt=now(),
                         revision='', finishedAt='')
            self.thread = threading.Thread(target=self._run, daemon=True)
            try:
                self.thread.start()
            except Exception:
                self._update(state='failed', running=False, message='未能开始网页同步。', error='请重新点击同步。')
                raise
            return True

    def _run(self):
        verified = None
        unchanged = False
        reported_error = ''
        try:
            deployment = self.workspace / 'local/worktrees/static-deploy'
            if not (deployment / 'app/publish_site.py').is_file():
                deployment = self.workspace
            script = deployment / 'app/publish_site.py'
            if not script.is_file():
                raise ValueError('未找到网页部署程序，请先完成网页部署配置。')
            environment = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
            with self.log.open('w', encoding='utf-8') as log:
                process = self.process_factory([sys.executable, '-u', '-B', str(script),
                    '--workspace', str(self.workspace), '--events'], cwd=deployment,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                    errors='replace', env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        if not line.startswith(EVENT_PREFIX):
                            continue
                        event = json.loads(line[len(EVENT_PREFIX):])
                        if event.get('phase') == 'failed':
                            reported_error = str(event.get('message', ''))[:500]
                        elif event.get('phase') == 'verified':
                            revision = event.get('revision', '')
                            if re.fullmatch(r'[0-9a-f]{40,64}', revision) and event.get('url') == SITE_URL:
                                verified = revision
                                unchanged = event.get('unchanged') is True
                        elif event.get('phase') in {'checking', 'scanning', 'exporting', 'testing', 'pushing', 'deploying', 'verifying'}:
                            self._update(message=str(event['message'])[:300])
                    code = process.wait()
                finally:
                    process.stdout.close()
            if code != 0 or not verified:
                if not reported_error:
                    reported_error = '同步未完成，请检查网络后重试；详细记录保存在本地。'
                raise ValueError(reported_error)
            self._update(state='succeeded', running=False,
                         message='网页已经是最新，无需重复发布。' if unchanged else '网页已更新：本地方案、实拍及收藏状态已同步。',
                         revision=verified, finishedAt=now(), error='')
        except Exception as error:
            self._update(state='failed', running=False, message='网页同步未完成。', error=str(error)[:500], finishedAt=now())
