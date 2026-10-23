"""Incremental, local-only photo index. Source photographs are read-only."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import struct
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
import tifffile

WORKSPACE = Path(__file__).resolve().parents[1]
INTERNAL = WORKSPACE / 'local'
PHOTO_ROOT = WORKSPACE / 'photos'
CATALOG_ROOT = WORKSPACE / 'data' / 'catalog'
INDEX_PATH = INTERNAL / 'photo-index.json'
RECORDS_PATH = INTERNAL / 'personal-records.json'
PREVIEWS = INTERNAL / 'previews'
EXTENSIONS = {'.jpg', '.jpeg', '.nef'}
TZ = timezone(timedelta(hours=8))


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def atomic_json(path, data, backup=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        previous = path.read_bytes()
        previous_path = path.with_suffix('.previous.json')
        previous_path.write_bytes(previous)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def text(value):
    return str(value or '').rstrip('\0').strip()


def maker_tags(maker):
    if len(maker) < 18 or not maker.startswith(b'Nikon\0') or maker[6] != 2:
        return {}
    base = 10
    endian = '<' if maker[10:12] == b'II' else '>' if maker[10:12] == b'MM' else None
    if not endian:
        return {}
    directory = base + struct.unpack_from(endian + 'I', maker, base + 4)[0]
    if directory + 2 > len(maker):
        return {}
    count = struct.unpack_from(endian + 'H', maker, directory)[0]
    if count > 4096 or directory + 2 + count * 12 > len(maker):
        return {}
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
    result = {}
    for index in range(count):
        position = directory + 2 + index * 12
        tag, kind, amount, pointer = struct.unpack_from(endian + 'HHII', maker, position)
        if tag not in {0x23, 0x1D, 0xA7}:
            continue
        length = sizes.get(kind, 1) * amount
        start = position + 8 if length <= 4 else base + pointer
        if start + length > len(maker):
            continue
        block = maker[start:start + length]
        result[tag] = struct.unpack(endian + 'I', block)[0] if kind == 4 and amount == 1 else block
    return result


def read_metadata(path):
    if path.suffix.lower() in {'.jpg', '.jpeg'}:
        with Image.open(path) as picture:
            main = picture.getexif()
            exif = main.get_ifd(34665)
            model, taken, subsecond, lens, serial = (main.get(272), exif.get(36867), exif.get(37521), exif.get(42036), exif.get(42033))
            maker = exif.get(37500, b'')
            exposure = {'iso': text(exif.get(34855)), 'fNumber': text(exif.get(33437)), 'exposureTime': text(exif.get(33434)), 'focalLength': text(exif.get(37386))}
    else:
        with tifffile.TiffFile(path) as picture:
            tags = picture.pages[0].tags
            exif = tags['ExifTag'].value if 'ExifTag' in tags else {}
            model = tags['Model'].value if 'Model' in tags else ''
            taken, subsecond, lens, serial = (exif.get('DateTimeOriginal'), exif.get('SubsecTimeOriginal'), exif.get('LensModel'), exif.get('BodySerialNumber'))
            maker = exif.get('MakerNote', b'')
            exposure = {out: text(exif.get(key)) for out, key in [('iso', 'ISOSpeedRatings'), ('fNumber', 'FNumber'), ('exposureTime', 'ExposureTime'), ('focalLength', 'FocalLength')]}
    private = maker_tags(maker)
    block = private.get(0x23, b'')
    version = block[:4].decode('ascii', errors='replace') if block else ''
    control, base, issue, issue_code = '', '', '', ''
    if version == '0310' and len(block) >= 48:
        try:
            control = block[8:28].split(b'\0', 1)[0].decode('ascii')
            base = block[28:48].split(b'\0', 1)[0].decode('ascii')
        except UnicodeDecodeError:
            issue = '优化校准名称编码暂不支持，可以手动关联方案。'
            issue_code = 'picture_control_encoding'
    elif version == '0310':
        issue = '优化校准数据不完整，无法读取方案名称；可检查原文件或手动关联。'
        issue_code = 'incomplete_picture_control'
    elif block:
        issue = f'优化校准版本 {version} 暂未支持，可以手动关联方案。'
        issue_code = 'unsupported_picture_control'
    else:
        issue = '文件未提供可读取的尼康优化校准名称，可以手动关联方案。'
        issue_code = 'missing_picture_control'
    return {'camera': text(model), 'takenAt': text(taken), 'subsecond': text(subsecond).rstrip('0'),
            'lens': text(lens), 'cameraSerial': text(serial), 'shutterCount': private.get(0xA7),
            'pictureControlName': control, 'pictureControlBase': base, 'pictureControlVersion': version,
            'pictureControlHash': hashlib.sha256(block).hexdigest() if block else '',
            'metadataIssue': issue, 'metadataIssueCode': issue_code, **exposure}


def photo_problem(error, file, *, preview=False):
    if isinstance(error, PermissionError):
        code, message, action = 'permission_denied', '没有读取权限，或文件被其他程序占用。', '检查文件权限，关闭占用文件的程序后更新照片库。'
    elif isinstance(error, FileNotFoundError):
        code, message, action = 'file_missing', '扫描期间文件被移动或删除。', '确认文件复制完成后更新照片库。'
    elif isinstance(error, (UnidentifiedImageError, tifffile.TiffFileError, EOFError, struct.error)) or any(
            marker in str(error).lower() for marker in ('truncated', 'broken data stream', 'not enough image data', 'corrupt', 'unexpected end')):
        code, message, action = 'invalid_image', '文件格式无效、内容损坏或复制尚未完成。', '从存储卡重新复制完整文件；修改扩展名无法修复文件。'
    elif preview and 'RAW 内嵌 JPEG' in str(error):
        code, message, action = 'missing_raw_preview', 'NEF 中没有找到可用的内嵌 JPEG 预览。', '放入同一次拍摄的 JPG，或打开 NEF 原文件查看。'
    else:
        code = 'preview_failed' if preview else 'metadata_failed'
        message = '无法生成照片预览。' if preview else '无法解析照片元数据。'
        action = '检查原文件是否完整，更新照片库重试；仍失败时可手动关联方案。'
    return {'file': file, 'code': code, 'severity': 'error', 'message': message,
            'action': action, 'detail': f'{type(error).__name__}: {error}'}


def nef_preview(path):
    with tifffile.TiffFile(path) as source:
        candidates = []
        for page in source.pages:
            tags = page.tags
            if 513 in tags and 514 in tags:
                candidates.append((int(tags[513].value), int(tags[514].value)))
            if int(page.compression) in (6, 7) and len(page.dataoffsets) == 1:
                candidates.append((int(page.dataoffsets[0]), int(page.databytecounts[0])))
    with path.open('rb') as source:
        for offset, size in sorted(set(candidates), key=lambda item: -item[1]):
            if size <= 0 or size > 64 * 1024 * 1024:
                continue
            source.seek(offset)
            body = source.read(size)
            if body.startswith(b'\xff\xd8'):
                return Image.open(io.BytesIO(body))
    raise ValueError('未找到可用的 RAW 内嵌 JPEG 预览；可放入同组 JPG。')


def create_preview(source, output):
    picture = nef_preview(source) if source.suffix.lower() == '.nef' else Image.open(source)
    with picture:
        preview = ImageOps.exif_transpose(picture).convert('RGB')
        preview.thumbnail((1600, 1600))
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix('.tmp')
        preview.save(temporary, format='JPEG', quality=87, optimize=True)
        os.replace(temporary, output)


class PhotoLibrary:
    def __init__(self):
        PHOTO_ROOT.mkdir(exist_ok=True)
        PREVIEWS.mkdir(parents=True, exist_ok=True)
        self.recipes = read_json(CATALOG_ROOT / 'data/recipes.json', [])
        self.recipe_ids = {recipe['id'] for recipe in self.recipes}
        self.index = read_json(INDEX_PATH, {'version': 1, 'files': {}, 'captures': [], 'revision': 0})
        self.records = read_json(RECORDS_PATH, {'version': 1, 'recipeFavorites': [], 'photoFavorites': [], 'photoAssignments': {}, 'revision': 0})
        self.lock = threading.RLock()
        self.catalog_gate = threading.RLock()
        self.scan_lock = threading.Lock()
        self.scanning = False
        self.scan_error = ''
        self.checked_at = ''
        if not RECORDS_PATH.exists():
            atomic_json(RECORDS_PATH, self.records)

    def reload_catalog(self):
        with self.catalog_gate, self.lock:
            recipes = read_json(CATALOG_ROOT / 'data/recipes.json', [])
            self.recipes = recipes
            self.recipe_ids = {recipe['id'] for recipe in recipes}

    def start_scan(self):
        if self.scan_lock.locked():
            return False
        threading.Thread(target=self.scan, daemon=True).start()
        return True

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return False
        self.scanning = True
        try:
            with self.catalog_gate:
                self._scan()
            self.scan_error = ''
        except Exception as error:
            self.scan_error = str(error)
        finally:
            self.scanning = False
            self.checked_at = now()
            self.scan_lock.release()
        return True

    def _scan(self):
        previous_files = self.index.get('files', {})
        previous_captures = {capture['id']: capture for capture in self.index.get('captures', [])}
        by_hash = {file['sha256']: file for file in previous_files.values() if file.get('sha256')}
        current, pending_issues = {}, []
        skipped_copying = 0
        for path in sorted(PHOTO_ROOT.rglob('*')):
            if path.suffix.lower() not in EXTENSIONS:
                continue
            relative = path.relative_to(PHOTO_ROOT).as_posix()
            previous = previous_files.get(relative)
            try:
                if not path.resolve().is_relative_to(PHOTO_ROOT.resolve()):
                    continue
                before = path.stat()
                if not stat.S_ISREG(before.st_mode):
                    continue
            except OSError as error:
                pending_issues.append(photo_problem(error, relative))
                if previous:
                    current[relative] = previous
                continue
            if previous and previous['bytes'] == before.st_size and previous['mtimeNs'] == before.st_mtime_ns and not previous.get('readError') and not previous.get('readIssue'):
                current[relative] = previous
                continue
            if before.st_size == 0 or time.time() - before.st_mtime < 1.5:
                skipped_copying += 1
                if previous:
                    current[relative] = previous
                pending_issues.append({'file': relative, 'code': 'copying', 'severity': 'warning',
                                       'message': '文件为空或仍在复制，暂未解析。',
                                       'action': '等待复制完成后更新照片库。'})
                continue
            file = {'path': relative, 'bytes': before.st_size, 'mtimeNs': before.st_mtime_ns}
            try:
                with path.open('rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                cached = by_hash.get(digest)
                metadata = cached['metadata'] if cached and not cached.get('readError') else read_metadata(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    skipped_copying += 1
                    if previous:
                        current[relative] = previous
                    pending_issues.append({'file': relative, 'code': 'copying', 'severity': 'warning',
                                           'message': '读取时文件内容仍在变化。', 'action': '等待复制完成后更新照片库。'})
                    continue
                file.update(sha256=digest, metadata=metadata)
            except Exception as error:
                file['readError'] = str(error)
                file['readIssue'] = photo_problem(error, relative)
                file['metadata'] = previous.get('metadata', {}) if previous else {'camera': '', 'lens': '', 'takenAt': '', 'subsecond': '', 'pictureControlName': '', 'pictureControlBase': '', 'metadataIssue': ''}
                file['sha256'] = hashlib.sha256((relative + str(before.st_size)).encode()).hexdigest()
            current[relative] = file
        buckets = defaultdict(list)
        for file in current.values():
            metadata = file['metadata']
            identity = None
            if metadata.get('takenAt') and metadata.get('shutterCount') is not None and metadata.get('cameraSerial'):
                identity = [metadata['camera'], metadata['cameraSerial'], metadata['shutterCount'], metadata['takenAt'], metadata.get('subsecond', '')]
            elif metadata.get('takenAt'):
                identity = [metadata['camera'], metadata.get('cameraSerial', ''), metadata['takenAt'], metadata.get('subsecond', ''), Path(file['path']).stem.casefold()]
            capture_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24] if identity else file['sha256'][:24]
            buckets[capture_id].append(file)
        captures, issues = [], list(pending_issues)
        for capture_id, versions in buckets.items():
            versions.sort(key=lambda file: (Path(file['path']).suffix.lower() == '.nef', file['path']))
            primary = versions[0]
            metadata = primary['metadata']
            name = metadata.get('pictureControlName', '')
            names = {v['metadata'].get('pictureControlName', '') for v in versions if v['metadata'].get('pictureControlName')}
            conflicting = len(names) > 1
            matches = [r['id'] for r in self.recipes if name and r['pictureControlName'] == name]
            suggestions = []
            if not matches and len(name) == 20:
                suggestions = [r['id'] for r in self.recipes if r['pictureControlName'].startswith(name)]
            automatic = matches[0] if len(matches) == 1 and not conflicting else None
            capture = {'id': capture_id, 'name': Path(primary['path']).stem,
                       **{key: value for key, value in metadata.items() if key not in {'cameraSerial', 'shutterCount', 'pictureControlHash'}},
                       'files': [{'path': v['path'], 'kind': Path(v['path']).suffix[1:].upper(), 'sha256': v['sha256']} for v in versions],
                       'autoRecipeId': automatic, 'candidateRecipeIds': matches if len(matches) > 1 else suggestions,
                       'metadataConflict': conflicting, 'previewSourceHash': primary['sha256']}
            capture_issues = []
            for file in versions:
                if file.get('readIssue'):
                    capture_issues.append(file['readIssue'])
                elif file['metadata'].get('metadataIssue'):
                    capture_issues.append({'file': file['path'],
                                           'code': file['metadata'].get('metadataIssueCode') or 'picture_control_unavailable',
                                           'severity': 'warning', 'message': file['metadata']['metadataIssue'],
                                           'action': '可搜索并手动选择方案；原文件不受影响。'})
            if conflicting:
                capture_issues.append({'file': primary['path'], 'code': 'conflicting_picture_control',
                                       'severity': 'warning', 'message': '同组 JPG / NEF 记录的方案名称不一致。',
                                       'action': '对照原文件后手动关联，不会自动猜测。'})
            thumbnail = PREVIEWS / (capture_id + '.jpg')
            old = previous_captures.get(capture_id, {})
            try:
                if not thumbnail.exists() or old.get('previewSourceHash') != primary['sha256']:
                    create_preview(PHOTO_ROOT / primary['path'], thumbnail)
                capture['preview'] = 'photo-previews/' + thumbnail.name
            except Exception as error:
                capture['preview'] = ''
                capture['previewIssue'] = str(error)
                capture_issues.append(photo_problem(error, primary['path'], preview=True))
            unique_issues = {}
            for issue in capture_issues:
                unique_issues.setdefault((issue['file'], issue['code']), issue)
            capture_issues = sorted(unique_issues.values(), key=lambda issue: issue['severity'] != 'error')
            capture['issues'] = capture_issues
            issues.extend(capture_issues)
            captures.append(capture)
        captures.sort(key=lambda capture: (capture.get('takenAt', ''), capture['name']), reverse=True)
        # Keep warnings and errors visible even when the affected files are cached.
        issues.sort(key=lambda issue: issue['severity'] != 'error')
        report = {'files': len(current), 'captures': len(captures), 'issues': issues,
                  'errors': [issue for issue in issues if issue['severity'] == 'error'], 'copying': skipped_copying}
        changed = current != previous_files or captures != self.index.get('captures', []) or report != self.index.get('scanReport')
        if changed:
            with self.lock:
                self.index = {'version': 1, 'files': current, 'captures': captures,
                              'revision': self.index.get('revision', 0) + 1, 'updatedAt': now(),
                              'scanReport': report}
                atomic_json(INDEX_PATH, self.index)

    def state(self):
        with self.lock:
            captures = []
            assignments = self.records.get('photoAssignments', {})
            favorites = set(self.records.get('photoFavorites', []))
            for original in self.index.get('captures', []):
                capture = dict(original)
                capture['recipeId'] = assignments[capture['id']] if capture['id'] in assignments else capture.get('autoRecipeId')
                capture['association'] = 'manual' if capture['id'] in assignments else 'automatic' if capture.get('autoRecipeId') else 'unmatched'
                capture['favorite'] = capture['id'] in favorites
                captures.append(capture)
            return {'revision': [self.index.get('revision', 0), self.records.get('revision', 0)],
                    'captures': captures, 'records': self.records,
                    'summary': {'files': len(self.index.get('files', {})), 'photos': len(captures),
                                'matched': sum(bool(capture['recipeId']) for capture in captures),
                                'favoritePhotos': sum(capture['favorite'] for capture in captures)},
                    'scanning': self.scanning, 'checkedAt': self.checked_at, 'scanError': self.scan_error,
                    'scanReport': self.index.get('scanReport', {})}

    def edit(self, action, item_id, recipe_id=None):
        with self.lock:
            photo_ids = {photo['id'] for photo in self.index.get('captures', [])}
            if action == 'toggle_recipe':
                if item_id not in self.recipe_ids:
                    raise ValueError('Unknown recipe')
                key = 'recipeFavorites'
            elif action == 'toggle_photo':
                if item_id not in photo_ids:
                    raise ValueError('Unknown photo')
                key = 'photoFavorites'
            elif action in {'assign_photo', 'reset_assignment'}:
                if item_id not in photo_ids or recipe_id is not None and recipe_id not in self.recipe_ids:
                    raise ValueError('Unknown photo or recipe')
                if action == 'reset_assignment':
                    self.records['photoAssignments'].pop(item_id, None)
                else:
                    self.records['photoAssignments'][item_id] = recipe_id
                key = None
            else:
                raise ValueError('Unknown action')
            if key:
                values = set(self.records.get(key, []))
                values.remove(item_id) if item_id in values else values.add(item_id)
                self.records[key] = sorted(values)
            self.records['revision'] = self.records.get('revision', 0) + 1
            self.records['updatedAt'] = now()
            atomic_json(RECORDS_PATH, self.records, backup=True)
        return self.state()

    def import_records(self, incoming):
        if incoming.get('version') != 1:
            raise ValueError('不支持的备份格式')
        recipes, photos, assignments = incoming.get('recipeFavorites'), incoming.get('photoFavorites'), incoming.get('photoAssignments')
        if not isinstance(recipes, list) or not isinstance(photos, list) or not isinstance(assignments, dict):
            raise ValueError('备份内容不完整')
        if any(not isinstance(value, str) or len(value) > 128 for value in recipes + photos + list(assignments)):
            raise ValueError('备份标识格式错误')
        if any(value not in self.recipe_ids for value in recipes) or any(value is not None and value not in self.recipe_ids for value in assignments.values()):
            raise ValueError('备份包含当前资料库没有的方案')
        with self.lock:
            self.records['recipeFavorites'] = sorted(set(self.records.get('recipeFavorites', [])) | set(recipes))
            self.records['photoFavorites'] = sorted(set(self.records.get('photoFavorites', [])) | set(photos))
            self.records['photoAssignments'].update(assignments)
            self.records['revision'] = self.records.get('revision', 0) + 1
            self.records['updatedAt'] = now()
            atomic_json(RECORDS_PATH, self.records, backup=True)
        return self.state()
