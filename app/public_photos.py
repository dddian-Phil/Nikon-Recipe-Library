"""Export explicit, reduced JPEG copies and a read-only photo-library snapshot."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re

from PIL import Image, ImageCms, ImageOps

from build_static import confined, identifier, json_bytes, read_json, text, tree_files

TEXT_FIELDS = ('name', 'takenAt', 'camera', 'lens', 'iso', 'fNumber', 'exposureTime',
               'focalLength', 'pictureControlName', 'pictureControlBase', 'metadataIssueCode')
CAPTURE_KEYS = set(TEXT_FIELDS) | {'id', 'recipeId', 'association', 'favorite',
    'metadataConflict', 'thumbnail', 'preview', 'thumbnailInfo', 'imageInfo'}
LIBRARY_KEYS = {'version', 'updatedAt', 'captures', 'recipeFavorites'}
IMAGE_KEYS = {'bytes', 'sha256', 'width', 'height', 'format'}


def photo_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{24}', value):
        raise ValueError('Invalid photo ID')
    return value


def image_path(pid, kind, digest):
    return f'images/{photo_id(pid)}-{kind}-{digest[:16]}.jpg'


def validate_library(payload, recipe_ids):
    if set(payload) != LIBRARY_KEYS or payload['version'] != 1:
        raise ValueError('Unexpected public photo library fields')
    text(payload['updatedAt'])
    favorites = payload['recipeFavorites']
    if not isinstance(favorites, list) or not all(isinstance(v, str) for v in favorites):
        raise ValueError('Invalid public recipe favorites')
    if len(set(favorites)) != len(favorites) or not set(favorites).issubset(recipe_ids):
        raise ValueError('Unknown public favorite recipe')
    if not isinstance(payload['captures'], list):
        raise ValueError('Invalid public photo list')
    ids, expected = set(), {'library.json'}
    for capture in payload['captures']:
        if set(capture) != CAPTURE_KEYS:
            raise ValueError('Unexpected public photo fields')
        pid = photo_id(capture['id'])
        if pid in ids:
            raise ValueError('Duplicate public photo ID')
        ids.add(pid)
        for key in TEXT_FIELDS:
            text(capture[key])
        if capture['recipeId'] is not None and capture['recipeId'] not in recipe_ids:
            raise ValueError('Photo references an unpublished recipe; refresh the public catalog')
        if capture['association'] not in {'automatic', 'manual', 'unmatched'}:
            raise ValueError('Invalid photo association')
        if type(capture['favorite']) is not bool or type(capture['metadataConflict']) is not bool:
            raise ValueError('Invalid photo flags')
        for kind, field, info_field, limit in (
                ('thumb', 'thumbnail', 'thumbnailInfo', 480), ('display', 'preview', 'imageInfo', 1280)):
            info = capture[info_field]
            if not isinstance(info, dict) or set(info) != IMAGE_KEYS or info['format'] != 'JPEG':
                raise ValueError('Invalid public photo image metadata')
            if not isinstance(info['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', info['sha256']):
                raise ValueError('Invalid public photo hash')
            if any(type(info[k]) is not int or info[k] <= 0 for k in ('bytes', 'width', 'height')):
                raise ValueError('Invalid public photo dimensions')
            if max(info['width'], info['height']) > limit or info['bytes'] > 4 * 1024 * 1024:
                raise ValueError('Public photo exceeds its display limit')
            relative = image_path(pid, kind, info['sha256'])
            if capture[field] != 'photos/' + relative:
                raise ValueError('Unexpected public photo URL')
            expected.add(relative)
    return expected


def validate_images(payload, files):
    for capture in payload['captures']:
        for field, info_field in (('thumbnail', 'thumbnailInfo'), ('preview', 'imageInfo')):
            body, info = files[capture[field].removeprefix('photos/')], capture[info_field]
            if len(body) != info['bytes'] or hashlib.sha256(body).hexdigest() != info['sha256']:
                raise ValueError('Public photo hash mismatch')
            with Image.open(io.BytesIO(body)) as picture:
                if picture.format != 'JPEG' or picture.size != (info['width'], info['height']):
                    raise ValueError('Public photo format or dimensions mismatch')
                if picture.getexif() or set(picture.info) - {'jfif', 'jfif_version', 'jfif_unit', 'jfif_density'}:
                    raise ValueError('Public photo must not contain EXIF, GPS, ICC, comments or other metadata')
                picture.verify()


def snapshot_files(root, recipe_ids):
    root = Path(root)
    payload = read_json(confined(root, 'library.json'))
    expected = validate_library(payload, recipe_ids)
    if tree_files(root) != expected:
        raise ValueError('Photo snapshot contains missing or unexpected files')
    files = {path: confined(root, path).read_bytes() for path in sorted(expected)}
    validate_images(payload, files)
    return payload, files


def reduced_jpegs(body):
    with Image.open(io.BytesIO(body)) as source:
        if source.format not in {'JPEG', 'MPO'}:
            raise ValueError('Only JPG source photos are published')
        # Nikon JPGs may contain an MPF/MPO container; publish only the primary image.
        source.seek(0)
        oriented = ImageOps.exif_transpose(source)
        if source.info.get('icc_profile'):
            oriented = ImageCms.profileToProfile(oriented,
                ImageCms.ImageCmsProfile(io.BytesIO(source.info['icc_profile'])),
                ImageCms.createProfile('sRGB'), outputMode='RGB')
        else:
            oriented = oriented.convert('RGB')
        # A fresh pixel buffer cannot carry source EXIF, comments, GPS or thumbnails.
        pixels = Image.frombytes('RGB', oriented.size, oriented.tobytes())
        result = []
        for kind, limit, quality in (('thumb', 480, 75), ('display', 1280, 80)):
            picture = pixels.copy()
            picture.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            stream = io.BytesIO()
            picture.save(stream, format='JPEG', quality=quality, optimize=True)
            output = stream.getvalue()
            result.append((kind, output, {'bytes': len(output),
                'sha256': hashlib.sha256(output).hexdigest(),
                'width': picture.width, 'height': picture.height, 'format': 'JPEG'}))
        return result


def export_photos(workspace, target, recipe_ids):
    workspace, target = Path(workspace).resolve(), Path(target)
    source_root = workspace / 'photos'
    index_path = workspace / 'local/photo-index.json'
    records_path = workspace / 'local/personal-records.json'
    before_index, before_records = index_path.read_bytes(), records_path.read_bytes()
    index, records = json.loads(before_index), json.loads(before_records)
    if index.get('scanReport', {}).get('copying') or index.get('scanReport', {}).get('errors'):
        raise ValueError('Photo scan is incomplete; finish copying or resolve read errors before publishing')
    known_files = {p for p in index['files'] if Path(p).suffix.lower() in {'.jpg', '.jpeg'}}
    current_files = {p.relative_to(source_root).as_posix() for p in source_root.rglob('*')
                     if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg'}}
    if known_files != current_files:
        raise ValueError('Photos changed since the last scan; update the local photo library first')
    favorites, assignments = set(records.get('photoFavorites', [])), records.get('photoAssignments', {})
    captures, files, source_hashes = [], {}, {}
    for original in index['captures']:
        versions = [v for v in original['files'] if Path(v['path']).suffix.lower() in {'.jpg', '.jpeg'}]
        if not versions:
            continue
        primary = versions[0]
        source = confined(source_root, primary['path'])
        body = source.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != primary['sha256']:
            raise ValueError('A JPG was edited since the scan; update the local photo library first')
        source_hashes[source] = digest
        pid = photo_id(original['id'])
        recipe = assignments.get(pid, original.get('autoRecipeId'))
        capture = {key: text(original.get(key, '') or '') for key in TEXT_FIELDS}
        capture.update(id=pid, recipeId=recipe,
            association='manual' if pid in assignments else 'automatic' if recipe else 'unmatched',
            favorite=pid in favorites, metadataConflict=bool(original.get('metadataConflict')))
        for kind, image, info in reduced_jpegs(body):
            relative = image_path(pid, kind, info['sha256'])
            files[relative] = image
            capture['thumbnail' if kind == 'thumb' else 'preview'] = 'photos/' + relative
            capture['thumbnailInfo' if kind == 'thumb' else 'imageInfo'] = info
        captures.append(capture)
    payload = {'version': 1, 'updatedAt': max(index.get('updatedAt', ''), records.get('updatedAt', '')),
        'captures': captures, 'recipeFavorites': sorted(set(records.get('recipeFavorites', [])) & recipe_ids)}
    files['library.json'] = json_bytes(payload)
    expected = validate_library(payload, recipe_ids)
    validate_images(payload, files)
    if before_index != index_path.read_bytes() or before_records != records_path.read_bytes():
        raise ValueError('Local records changed during export; retry')
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in source_hashes.items()):
        raise ValueError('A JPG changed during export; retry after editing finishes')
    # Only managed image names may be retired. Never remove an arbitrary extra file.
    existing = tree_files(target) if target.exists() else set()
    for relative in existing:
        if relative != 'library.json' and not re.fullmatch(r'images/[0-9a-f]{24}-(thumb|display)-[0-9a-f]{16}\.jpg', relative):
            raise ValueError('Unexpected file in photo snapshot: ' + relative)
    target.mkdir(parents=True, exist_ok=True)
    for relative, image in files.items():
        path = confined(target, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(image)
        os.replace(temporary, path)
    for relative in existing - expected:
        confined(target, relative).unlink()
    return {'photos': len(captures), 'files': len(files), 'bytes': sum(map(len, files.values()))}
