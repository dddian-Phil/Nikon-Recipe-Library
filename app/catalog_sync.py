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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit
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


def response_validators(response):
    values = {}
    for header, key in (('ETag', 'etag'), ('Last-Modified', 'modified')):
        value = response.headers.get(header) if response.headers else None
        if isinstance(value, str) and len(value) <= 1024 and '\r' not in value and '\n' not in value:
            values[key] = value
    return values


def asset_identity(url):
    parsed = urlparse(official_url(url))
    # Rotating access signatures do not change the underlying image resource.
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in {'signature', 'policy', 'key-pair-id', 'expires'}]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))


def request_bytes(url, data=None, *, limit=32 * 1024 * 1024, conditional=None, response_info=None):
    official_url(url)
    headers = {'User-Agent': 'NikonRecipeLibrary/1.0', 'Referer': BASE + '/',
               'Accept-Language': 'zh-CN,zh;q=0.9'}
    if data is not None:
        headers['Content-Type'] = 'text/plain;charset=UTF-8'
    headers.update(conditional or {})
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.build_opener(OfficialRedirects()).open(request, timeout=20) as response:
                official_url(response.geturl())
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise ValueError('官方响应超过安全大小限制')
                if response_info is not None:
                    response_info.update(response_validators(response))
                return body
        except urllib.error.HTTPError as error:
            if error.code == 304 and conditional:
                if response_info is not None:
                    response_info.update(response_validators(error))
                return None
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


def directory_entry(item):
    """Compare published directory content, not likes or rotating access tokens."""
    result = {key: value for key, value in item.items() if key not in {'like', 'is_new'}}
    for key in ('img', 'thumbnail'):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {**value, 'url': asset_identity(value['url'])}
        elif value:
            result[key] = asset_identity(value)
    return result


def local_image_matches(path, expected):
    try:
        return bool(expected) and hashlib.sha256(path.read_bytes()).hexdigest() == expected
    except OSError:
        return False


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
        self._http_cache = {}
        self._image_counts = {'downloaded': 0, 'reused': 0}
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
        key = target.relative_to(self._cache_root).as_posix()
        identity = asset_identity(url)
        cached = self._http_cache.get(key, {})
        if not isinstance(cached, dict):
            cached = {}
        conditional = {}
        if (cached.get('resource') == identity and target.is_file() and
                target.stat().st_size == cached.get('bytes') and
                hashlib.sha256(target.read_bytes()).hexdigest() == cached.get('sha256')):
            for name, field in (('If-None-Match', 'etag'), ('If-Modified-Since', 'modified')):
                value = cached.get(field)
                if isinstance(value, str) and 0 < len(value) <= 1024 and '\r' not in value and '\n' not in value:
                    conditional[name] = value
        info = {}
        body = self.transport(url, limit=32 * 1024 * 1024, conditional=conditional, response_info=info)
        if body is None:
            if not conditional:
                raise ValueError('官方图片返回未修改，但没有可验证的本地副本')
            saved = dict(cached)
            saved.update(info)
            count = 'reused'
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            digest = image_digest(target)
            saved = {'resource': identity, 'sha256': digest, 'bytes': len(body), **info}
            count = 'downloaded'
        with self.status_lock:
            self._http_cache[key] = saved
            self._image_counts[count] += 1

    def _prepare(self, stage):
        with self.lock:
            old = read_json(self.root / 'data/recipes.json', [])
            old_catalog = read_json(self.root / 'raw/catalog.json', {'recipe': []})
            old_creators = read_json(self.root / 'raw/creators.json', {'creators': []})
        old_by_id = {recipe['id']: recipe for recipe in old}
        old_listed = {item['id']: item for item in old_catalog.get('recipe', [])}
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

        pending = []
        for item in listed:
            rid = item['id']
            previous = old_by_id.get(rid, {})
            try:
                detail = read_json(self.root / 'raw/details' / (rid + '.json'), {})
                detail_present = (detail.get('id') == rid and isinstance(detail.get('pictureControl'), dict))
            except (OSError, ValueError, AttributeError):
                detail_present = False
            if (not previous or directory_entry(item) != directory_entry(old_listed.get(rid, {})) or
                    not detail_present or not local_image_matches(
                        self.root / 'recipes' / rid.lower() / 'sample.jpg',
                        previous.get('imageInfo', {}).get('sha256'))):
                pending.append(item)

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
        assets = {item['path']: item for item in read_json(self.root / 'data/assets.json', [])}
        portraits = []
        for creator in authors:
            if creator['id'] not in used or not creator.get('thumbnail'):
                continue
            previous_url = old_author_by_id.get(creator['id'], {}).get('thumbnail')
            relative = 'assets/creators/' + creator['id'] + '.png'
            if (not previous_url or asset_identity(creator['thumbnail']) != asset_identity(previous_url) or
                    not local_image_matches(self.root / relative, assets.get(relative, {}).get('sha256'))):
                portraits.append(creator)

        unchanged = (not pending and not portraits and
            [directory_entry(item) for item in catalog['recipe']] ==
            [directory_entry(item) for item in old_catalog.get('recipe', [])] and
            [directory_entry(item) for item in directory['creators']] ==
            [directory_entry(item) for item in old_creators.get('creators', [])] and
            (self.root / 'data/creators.json').is_file() and
            (self.root / 'data/collection_report.json').is_file() and
            all((self.root / 'recipes' / item['id'].lower() / 'recipe.md').is_file() for item in old))
        if unchanged:
            # A directory check does not create a new content revision or clear new badges.
            metadata = read_json(self.root / 'data/sync_metadata.json', {})
            return {**metadata, 'revision': metadata.get('revision', ''),
                    'newRecipeIds': [], 'changedRecipeIds': [], 'officialCount': len(listed),
                    'retainedCount': len(old) - len(listed), 'unchanged': True,
                    'detailsFetched': 0, 'requestCount': 2, 'imageDownloads': 0, 'imageReused': 0}

        self._progress(f'目录检查完成，需要更新 {len(pending)} 个方案，其余复用本地资料…')
        with self.lock:
            if self.root.exists():
                shutil.copytree(self.root, stage)
            else:
                stage.mkdir(parents=True)
        self._cache_root = stage
        self._http_cache = read_json(stage / 'raw/http-cache.json', {})
        if not isinstance(self._http_cache, dict):
            self._http_cache = {}
        self._image_counts = {'downloaded': 0, 'reused': 0}

        def fetch_detail(item):
            rid = item['id']
            detail = self._json(IR + '/publicrecipe/0000000000000001/' + rid + '?sortType=0&isPagenation=false')
            if detail.get('id') != rid or not isinstance(detail.get('name'), str) or not detail['name'].strip():
                raise ValueError(f'方案 {rid} 的详情 ID 或名称无效')
            if not isinstance(detail.get('pictureControl'), dict) or not isinstance(detail.get('exif', {}), dict):
                raise ValueError(f'方案 {rid} 的参数格式无效')
            write_json(stage / 'raw/details' / (rid + '.json'), detail)
            self._image(detail.get('thumbnail', ''), stage / 'recipes' / rid.lower() / 'sample.jpg')

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(fetch_detail, item) for item in pending]
            for count, future in enumerate(as_completed(futures), 1):
                future.result()
                self._progress(f'正在下载新增或目录有变化的方案 {count} / {len(pending)}…')

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(self._image, creator['thumbnail'], stage / 'assets/creators' / (creator['id'] + '.png')) for creator in portraits]
            for count, future in enumerate(as_completed(futures), 1):
                future.result()
                self._progress(f'正在下载新增或变化的作者头像 {count} / {len(portraits)}…')
        write_json(stage / 'raw/catalog.json', catalog)
        write_json(stage / 'raw/http-cache.json', self._http_cache)
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
                    'retainedCount': len(new_recipes) - len(listed), 'jobId': self.status().get('jobId'),
                    'detailsFetched': len(pending), 'requestCount': 2 + len(pending) * 2 + len(portraits),
                    'imageDownloads': self._image_counts['downloaded'], 'imageReused': self._image_counts['reused']}
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
            if not metadata.get('unchanged'):
                self._progress('校验通过，正在切换目录…')
                self._promote(stage)
            result = {'state': 'succeeded', 'running': False, 'finishedAt': now(), 'error': None,
                      'message': '目录没有变化，已复用本地方案。' if metadata.get('unchanged') else
                          f"同步完成：新增 {len(metadata['newRecipeIds'])} 个，更新 {len(metadata['changedRecipeIds'])} 个",
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
