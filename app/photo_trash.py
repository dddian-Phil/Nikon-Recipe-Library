"""Recoverable photo removal; originals and personal records remain recoverable."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import uuid

import photo_library as photos
from photo_import import IMPORT_LOCK


def confined(root, relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or Path(relative).is_absolute() or any(part in {'.', '..'} for part in relative.split('/'))):
        raise ValueError('照片路径无效。')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('照片路径超出资料库范围。')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('符号链接照片不能在页面删除或恢复。')
    return path


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


@contextmanager
def editing(library):
    with IMPORT_LOCK:
        if not library.scan_lock.acquire(blocking=False):
            raise ValueError('照片库正在扫描，请稍后重试。')
        try:
            with library.catalog_gate, library.lock:
                yield
        finally:
            library.scan_lock.release()


def transfer(library, pairs):
    """Link complete copies first, then remove sources; never replace any target."""
    linked, removed = [], []
    previous = library.index
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
            linked.append((source, target))
        for source, target in linked:
            source.unlink()
            removed.append((source, target))
        library._scan()
        library.scan_error = ''
        library.checked_at = photos.now()
    except Exception:
        # If rollback encounters a new source, preserve the recovery copy as well.
        for source, target in removed:
            os.link(target, source)
        for _, target in linked:
            target.unlink()
        library.index = previous
        raise


def delete_photo(library, item_id, expected_files):
    with editing(library):
        capture = next((item for item in library.index.get('captures', []) if item['id'] == item_id), None)
        if not capture:
            raise ValueError('照片已不存在，请刷新列表。')
        if capture['files'] != expected_files:
            raise ValueError('这张照片的文件已变化，请刷新后重新确认。')
        files, sources = [], []
        for entry in capture['files']:
            source = confined(photos.PHOTO_ROOT, entry['path'])
            indexed = library.index['files'][entry['path']]
            if not source.is_file():
                raise ValueError('照片文件已移动，请更新照片库后重试。')
            before = source.stat()
            sha256 = digest(source)
            after = source.stat()
            if ((before.st_size, before.st_mtime_ns) != (indexed['bytes'], indexed['mtimeNs'])
                    or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                    or (not indexed.get('readError') and sha256 != entry['sha256'])):
                raise ValueError('照片内容已变化，请更新照片库后重新确认。')
            files.append({'path': entry['path'], 'sha256': sha256})
            sources.append(source)
        token = uuid.uuid4().hex
        folder = confined(photos.INTERNAL, 'deleted-photos/' + token)
        manifest = {'version': 1, 'id': item_id, 'name': capture['name'],
                    'deletedAt': photos.now(), 'files': files}
        photos.atomic_json(folder / 'manifest.json', manifest)
        transfer(library, [(source, folder / 'files' / entry['path']) for source, entry in zip(sources, files)])
        return {'token': token, 'name': capture['name'], 'files': len(files), 'state': library.state()}


def restore_photo(library, token):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('删除记录无效。')
    with editing(library):
        folder = confined(photos.INTERNAL, 'deleted-photos/' + token)
        manifest = photos.read_json(confined(folder, 'manifest.json'), None)
        if not isinstance(manifest, dict) or not manifest.get('files'):
            raise ValueError('找不到这次删除的记录。')
        pairs = []
        for entry in manifest['files']:
            source = confined(folder / 'files', entry['path'])
            target = confined(photos.PHOTO_ROOT, entry['path'])
            if target.exists():
                raise ValueError('原位置已有同名文件，未覆盖；请先移开同名文件再撤销。')
            if not source.is_file() or digest(source) != entry['sha256']:
                raise ValueError('回收文件已移动或变化，请检查本地回收目录。')
            pairs.append((source, target))
        transfer(library, pairs)
        return {'name': manifest['name'], 'state': library.state()}
