"""User-triggered catalog refresh with validated staging and recoverable promotion.

Only start() performs remote requests. Construction, payload() and status() never
contact Nikon. Transactions touch data/catalog and sibling staging paths only.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urlparse
import urllib.error
import urllib.request
import uuid

from PIL import Image

from collect_nikon import BASE, IR, LIST, LABELS, VALUES, main as rebuild_archive, now


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else copy.deepcopy(default)


def official_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or not parsed.hostname or
            not parsed.hostname.endswith('.imagingcloud.nikon.com.cn') or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('官方素材地址未通过 HTTPS 和域名验证')
    return url


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, official_url(newurl))


def request_bytes(url, data=None, *, limit=32 * 1024 * 1024):
    official_url(url)
    headers = {'User-Agent': 'NikonRecipeLibrary/1.0', 'Referer': BASE + '/',
               'Accept-Language': 'zh-CN,zh;q=0.9'}
    if data is not None:
        headers['Content-Type'] = 'text/plain;charset=UTF-8'
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.build_opener(OfficialRedirects()).open(request, timeout=20) as response:
                official_url(response.geturl())
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise ValueError('官方响应超过安全大小限制')
                return body
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt:
                raise
        except (OSError, urllib.error.URLError):
            if attempt:
                raise
        time.sleep(0.5)


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', value):
        raise ValueError('官方方案或作者 ID 无效')
    return value


def validate_list(items, label):
    if not isinstance(items, list) or not items:
        raise ValueError(f'{label}为空或格式不正确；保留原目录')
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f'{label}条目格式不正确')
        key = valid_id(item.get('id')).lower()
        if key in seen:
            raise ValueError(f'{label}存在重复 ID')
        seen.add(key)
        if not isinstance(item.get('name'), str) or not item['name'].strip():
            raise ValueError(f'{label}缺少名称')
    return items


def image_digest(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('素材文件超过安全大小限制')
    with Image.open(path) as image:
        if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 60_000_000:
            raise ValueError('素材图片格式或尺寸无效')
        image.verify()
    # JPEG verify() checks headers only; decoding also catches truncated pixels.
    with Image.open(path) as image:
        image.load()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(recipe):
    return {key: value for key, value in recipe.items()
            if key not in {'retrievedAt', 'imageInfo', 'imageError'}} | {
                'imageSha256': recipe.get('imageInfo', {}).get('sha256')}


class CatalogSync:
    def __init__(self, workspace, *, on_reload=None, transport=request_bytes):
        self.workspace = Path(workspace).resolve()
        self.parent = self.workspace / 'data'
        self.root = self.parent / 'catalog'
        self.journal = self.parent / '.catalog-transaction.json'
        self.status_path = self.workspace / 'local/catalog-sync.json'
        self.lock = threading.RLock()
        self.job_lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.on_reload = on_reload or (lambda: None)
        self.publication_guard = nullcontext
        self.transport = transport
        self.thread = None
        self.recover()
        self._status = read_json(self.status_path, {'state': 'idle', 'message': '尚未手动同步', 'running': False})
        if self._status.get('running'):
            committed = read_json(self.root / 'data/sync_metadata.json', {})
            if self._status.get('jobId') and committed.get('jobId') == self._status['jobId']:
                self._status.update(state='succeeded', running=False, error=None,
                                    message='上次同步已完成，完整目录已恢复可用', result=committed)
            else:
                self._status.update(state='failed', running=False,
                                    message='上次同步被中断，已恢复完整目录；可点击重新同步', error='同步进程中断')
            write_json(self.status_path, self._status)

    def _safe_transaction_path(self, name, prefix):
        if not isinstance(name, str) or not re.fullmatch(re.escape(prefix) + r'[0-9a-f]{32}', name):
            raise ValueError('同步事务路径无效')
        path = (self.parent / name).resolve()
        if path.parent != self.parent.resolve():
            raise ValueError('同步事务路径越界')
        return path

    def _remove(self, path):
        # All recursive deletion targets must be verified transaction siblings.
        path = self._safe_transaction_path(path.name, '.catalog-stage-' if path.name.startswith('.catalog-stage-') else '.catalog-backup-')
        if path.exists():
            shutil.rmtree(path)

    def recover(self):
        transaction = read_json(self.journal)
        if not transaction:
            return
        stage = self._safe_transaction_path(transaction['stage'], '.catalog-stage-')
        backup = self._safe_transaction_path(transaction['backup'], '.catalog-backup-')
        if transaction['phase'] != 'committed':
            if backup.exists():
                if self.root.exists():
                    if stage.exists():
                        self._remove(stage)
                    self.root.rename(stage)
                backup.rename(self.root)
            elif not transaction['hadCatalog'] and self.root.exists():
                if stage.exists():
                    self._remove(stage)
                self.root.rename(stage)
        self._remove(stage)
        self._remove(backup)
        self.journal.unlink()

    def status(self):
        with self.status_lock:
            return copy.deepcopy(self._status)

    def _progress(self, message):
        with self.status_lock:
            self._status['message'] = message

    def payload(self):
        with self.lock:
            recipes = read_json(self.root / 'data/recipes.json', [])
            creators = read_json(self.root / 'data/creators.json', [])
            for recipe in recipes:
                for key in ('image', 'document', 'folder'):
                    recipe[key] = 'catalog/' + recipe[key]
            for creator in creators:
                if creator.get('localPortrait'):
                    creator['localPortrait'] = 'catalog/' + creator['localPortrait']
            return {'recipes': recipes, 'creators': creators, 'labels': LABELS, 'values': VALUES,
                    'report': read_json(self.root / 'data/collection_report.json', {}),
                    'syncMetadata': read_json(self.root / 'data/sync_metadata.json', {}),
                    'sync': self.status()}

    def start(self):
        if not self.job_lock.acquire(blocking=False):
            return False
        try:
            with self.status_lock:
                self._status = {'state': 'running', 'running': True, 'startedAt': now(),
                                'jobId': uuid.uuid4().hex, 'message': '正在读取官方目录…', 'error': None}
                write_json(self.status_path, self._status)
            self.thread = threading.Thread(target=self._run, daemon=True, name='manual-catalog-sync')
            self.thread.start()
        except Exception as error:
            with self.status_lock:
                self._status = {'state': 'failed', 'running': False,
                                'message': '无法开始同步，原目录未改变', 'error': str(error)}
            self.job_lock.release()
            raise
        return True

    def _json(self, url, data=None):
        result = json.loads(self.transport(url, data, limit=8 * 1024 * 1024))
        if not isinstance(result, dict) or result.get('error_code'):
            raise ValueError('官方接口返回错误或无效 JSON')
        return result

    def _image(self, url, target):
        official_url(url)
        body = self.transport(url, limit=32 * 1024 * 1024)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        image_digest(target)

    def _prepare(self, stage):
        with self.lock:
            if self.root.exists():
                shutil.copytree(self.root, stage)
            else:
                stage.mkdir(parents=True)
        old = read_json(stage / 'data/recipes.json', [])
        old_by_id = {recipe['id']: recipe for recipe in old}
        old_catalog = read_json(stage / 'raw/catalog.json', {'recipe': []})
        old_creators = read_json(stage / 'raw/creators.json', {'creators': []})
        request = json.dumps({'ownerUserId': 'nikon', 'bookId': '', 'sortType': 0,
                              'lang_code': 'zh-cn', 'country': None}).encode()
        catalog = self._json(LIST, request)
        listed = validate_list(catalog.get('recipe'), '官方方案目录')
        directory = self._json(IR + '/info/creator')
        authors = validate_list(directory.get('creators'), '官方作者目录')
        remote_ids = {item['id'] for item in listed}
        remote_author_ids = {creator['id'] for creator in authors}
        for creator in authors:
            if not isinstance(creator.get('recipes', []), list):
                raise ValueError('官方作者的方案关联格式不正确')
            for item in creator.get('recipes', []):
                if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                    raise ValueError('官方作者的方案关联格式不正确')

        def fetch_detail(item):
            rid = item['id']
            detail = self._json(IR + '/publicrecipe/0000000000000001/' + rid + '?sortType=0&isPagenation=false')
            if detail.get('id') != rid or not isinstance(detail.get('name'), str) or not detail['name'].strip():
                raise ValueError(f'方案 {rid} 的详情 ID 或名称无效')
            if not isinstance(detail.get('pictureControl'), dict) or not isinstance(detail.get('exif', {}), dict):
                raise ValueError(f'方案 {rid} 的参数格式无效')
            write_json(stage / 'raw/details' / (rid + '.json'), detail)
            self._image(detail.get('thumbnail', ''), stage / 'recipes' / rid.lower() / 'sample.jpg')

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_detail, item) for item in listed]
            for count, future in enumerate(as_completed(futures), 1):
                future.result()
                self._progress(f'正在验证方案及样片 {count} / {len(listed)}…')

        # Retain offline/withdrawn recipes so favorites and associations keep resolving.
        catalog['recipe'] = listed + [item for item in old_catalog.get('recipe', []) if item['id'] not in remote_ids]
        old_author_by_id = {creator['id']: creator for creator in old_creators.get('creators', [])}
        for creator in authors:
            known = {item['path'] for item in creator.get('recipes', [])}
            creator['recipes'] = creator.get('recipes', []) + [
                item for item in old_author_by_id.get(creator['id'], {}).get('recipes', [])
                if item['path'].rstrip('/').split('/')[-1] not in remote_ids and item['path'] not in known]
        directory['creators'] = authors + [c for c in old_creators.get('creators', []) if c['id'] not in remote_author_ids]
        used = {creator['id'] for creator in authors if any(
            item['path'].rstrip('/').split('/')[-1] in remote_ids for item in creator.get('recipes', []))}
        portraits = [creator for creator in authors if creator['id'] in used and creator.get('thumbnail')]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self._image, creator['thumbnail'], stage / 'assets/creators' / (creator['id'] + '.png')) for creator in portraits]
            for count, future in enumerate(as_completed(futures), 1):
                future.result()
                self._progress(f'正在验证作者头像 {count} / {len(portraits)}…')
        write_json(stage / 'raw/catalog.json', catalog)
        write_json(stage / 'raw/creators.json', directory)
        write_json(stage / 'data/recipes.json', old)
        write_json(stage / 'data/collection_report.json', {'startedAt': now(), 'finishedAt': now()})
        self._progress('正在生成目录并校验所有文件…')
        report = rebuild_archive(['--offline'], root=stage, quiet=True)
        if report['failures']:
            raise ValueError('目录生成不完整；' + str(report['failures'][0].get('error', '素材缺失')))
        new_recipes = read_json(stage / 'data/recipes.json')
        if not set(old_by_id).issubset({recipe['id'] for recipe in new_recipes}):
            raise ValueError('验证发现已有方案丢失；保留原目录')
        added = [r['id'] for r in new_recipes if r['id'] not in old_by_id]
        changed = [r['id'] for r in new_recipes if r['id'] in old_by_id and fingerprint(r) != fingerprint(old_by_id[r['id']])]
        metadata = {'revision': uuid.uuid4().hex, 'syncedAt': now(), 'newRecipeIds': added,
                    'changedRecipeIds': changed, 'officialCount': len(listed),
                    'retainedCount': len(new_recipes) - len(listed), 'jobId': self.status().get('jobId')}
        write_json(stage / 'data/sync_metadata.json', metadata)
        self.validate(stage)
        return metadata

    @staticmethod
    def validate(root):
        root = root.resolve()
        recipes = validate_list(read_json(root / 'data/recipes.json'), '生成的方案目录')
        creators = validate_list(read_json(root / 'data/creators.json'), '生成的作者目录')
        report = read_json(root / 'data/collection_report.json')
        if report.get('failures') or report.get('recipeCount') != len(recipes):
            raise ValueError('方案数量或采集结果校验失败')
        assets = read_json(root / 'data/assets.json', [])
        manifest = {asset['path']: asset for asset in assets}
        expected = {recipe['image'] for recipe in recipes} | {c['localPortrait'] for c in creators if c.get('localPortrait')}
        if len(manifest) != len(assets) or not expected.issubset(manifest):
            raise ValueError('素材清单不完整或存在重复路径')
        for item in recipes + creators:
            for field in ('document', 'image', 'localPortrait'):
                if not item.get(field):
                    continue
                path = (root / item[field]).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError(f'目录文件缺失或路径越界：{field}')
                if field in ('image', 'localPortrait'):
                    digest = image_digest(path)
                    asset = manifest[item[field]]
                    if digest != asset.get('sha256') or path.stat().st_size != asset.get('bytes'):
                        raise ValueError('素材清单的大小或哈希验证失败')
                    if field == 'image' and digest != item.get('imageInfo', {}).get('sha256'):
                        raise ValueError('展示样片哈希验证失败')
        for recipe in recipes:
            detail = read_json(root / 'raw/details' / (valid_id(recipe['id']) + '.json'))
            if not detail or detail.get('id') != recipe['id']:
                raise ValueError('原始方案详情验证失败')

    def _promote(self, stage):
        backup = self._safe_transaction_path('.catalog-backup-' + uuid.uuid4().hex, '.catalog-backup-')
        transaction = {'phase': 'prepared', 'stage': stage.name, 'backup': backup.name,
                       'hadCatalog': self.root.exists()}
        with self.lock, self.publication_guard():
            write_json(self.journal, transaction)
            try:
                if self.root.exists():
                    self.root.rename(backup)
                stage.rename(self.root)
                self.on_reload()
                transaction['phase'] = 'committed'
                write_json(self.journal, transaction)
            except Exception:
                # Restore files first, then restore the in-memory recipe lookup.
                self.recover()
                self.on_reload()
                raise
            # A cleanup failure cannot turn an already committed update into a failure.
            try:
                self.recover()
            except OSError:
                pass

    def _run(self):
        stage = self._safe_transaction_path('.catalog-stage-' + uuid.uuid4().hex, '.catalog-stage-')
        try:
            metadata = self._prepare(stage)
            self._progress('校验通过，正在切换目录…')
            self._promote(stage)
            result = {'state': 'succeeded', 'running': False, 'finishedAt': now(), 'error': None,
                      'message': f"同步完成：新增 {len(metadata['newRecipeIds'])} 个，更新 {len(metadata['changedRecipeIds'])} 个",
                      'result': metadata}
        except Exception as error:
            result = {'state': 'failed', 'running': False, 'finishedAt': now(),
                      'message': '同步失败，原目录和个人记录已保留', 'error': f'{type(error).__name__}: {error}'}
        finally:
            if stage.exists() and not self.journal.exists():
                try:
                    self._remove(stage)
                except OSError:
                    pass
        try:
            with self.status_lock:
                self._status = result
                write_json(self.status_path, result)
        finally:
            self.job_lock.release()
