"""Copy selected photographs into the local library without replacing originals."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time

from PIL import Image

from photo_library import EXTENSIONS

MAX_FILE_BYTES = 256 * 1024 * 1024
IMPORT_LOCK = threading.Lock()


def validate_name(name):
    if (not name or name in {'.', '..'} or len(name.encode('utf-8')) > 220
            or any(ord(char) < 32 or char in '/\\:*?"<>|' for char in name)
            or name.startswith('.') or name.endswith((' ', '.'))):
        raise ValueError('文件名无效，请选择普通的照片文件。')
    if Path(name).suffix.lower() not in {'.jpg', '.jpeg'}:
        raise ValueError('仅支持 JPEG 照片（.jpg / .jpeg）。')


def validate_image(path):
    try:
        image = Image.open(path)
    except Exception as error:
        raise ValueError('无法识别照片内容，请确认文件是可正常打开的 JPEG 照片。') from error
    with image:
        # JPEGs with MPF metadata (including camera previews) are opened as MPO.
        # Keep the original bytes and validate every embedded JPEG, not just the main one.
        if image.format not in {'JPEG', 'MPO'}:
            raise ValueError(f'文件内容是 {image.format}，请选择 JPEG 照片；修改扩展名不会转换格式。')
        try:
            for frame in range(getattr(image, 'n_frames', 1)):
                image.seek(frame)
                image.load()
        except Exception as error:
            raise ValueError('照片内容未能完整读取，请检查文件是否复制完整或能否正常打开。') from error


def import_photo(root, name, source, length):
    validate_name(name)
    if length <= 0 or length > MAX_FILE_BYTES:
        raise ValueError('照片不能为空，单个文件不能超过 256 MB。')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # The scanner ignores .part files. Only complete, validated bytes become visible.
    with tempfile.NamedTemporaryFile(dir=root, prefix='.photo-import-', suffix='.part', delete=False) as output:
        temporary = Path(output.name)
        try:
            digest = hashlib.sha256()
            remaining = length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('照片传输中断，请重新添加。')
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        validate_image(temporary)
        with IMPORT_LOCK:
            for existing in root.rglob('*'):
                if existing.suffix.lower() not in EXTENSIONS or not existing.resolve().is_relative_to(root):
                    continue
                try:
                    if not existing.is_file() or existing.stat().st_size != length:
                        continue
                    with existing.open('rb') as original:
                        same = hashlib.file_digest(original, 'sha256').digest() == digest.digest()
                    if same:
                        return {'status': 'duplicate', 'path': existing.relative_to(root).as_posix()}
                except OSError:
                    continue
            # This copy is complete; allow the next scan to index it immediately.
            stable_time = time.time() - 2
            os.utime(temporary, (stable_time, stable_time))
            destination = root / name
            number = 1
            while True:
                try:
                    # link is atomic and fails if any file (including a symlink) exists.
                    os.link(temporary, destination)
                    break
                except FileExistsError:
                    number += 1
                    destination = root / f'{Path(name).stem} ({number}){Path(name).suffix}'
            return {'status': 'added', 'path': destination.name}
    finally:
        temporary.unlink(missing_ok=True)
