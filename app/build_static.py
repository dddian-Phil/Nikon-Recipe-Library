"""Export only public Nikon material and build a backend-free Pages artifact.

Exports are explicit and offline. Normal builds read only committed public
snapshots; only --export-photos reads the selected local photo library.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess

from collect_nikon import BASE, LABELS, VALUES

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'data/public-catalog'
OUTPUT = ROOT / 'dist'
RECIPE_TEXT = ('name', 'publisher', 'camera', 'lens', 'publicDate', 'shootingDate',
               'outline', 'description', 'memo', 'pictureControlName', 'shootingLocation')
PICTURE_KEYS = frozenset(('settingName', 'baseName', 'quickSharp', 'sharpening',
    'middleRangeSharp', 'clarity', 'contrast', 'saturation', 'highlight', 'shadow',
    'whiteLevel', 'blackLevel', 'hue', 'brightness', 'effectLevel', 'filterEffect',
    'toning', 'toningSaturation'))
EXIF_KEYS = frozenset(LABELS) - PICTURE_KEYS
RECIPE_KEYS = frozenset(RECIPE_TEXT) | {'id', 'authors', 'authorIds', 'pictureControl',
    'exif', 'image', 'imageInfo', 'source'}
CREATOR_KEYS = frozenset(('id', 'name', 'position', 'profile', 'localPortrait', 'imageInfo'))
IMAGE_KEYS = ('bytes', 'sha256', 'width', 'height', 'format')
PAYLOAD_KEYS = frozenset(('mode', 'recipes', 'creators', 'labels', 'values', 'report', 'syncMetadata'))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', value):
        raise ValueError('Invalid public catalog ID')
    return value


def text(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Public text field must be a string')
    # Do not publish signed URLs, credentials or machine-local paths, even when
    # someone accidentally adds them to an otherwise allowed public text field.
    if re.search(r'(?i)(?:[?&](?:policy|signature|key-pair-id|token|access_token)=|'
                 r'\b(?:gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]+)|'
                 r'\bBearer\s+\S+|file://|[A-Za-z]:[\\/]|\\\\[^\s\\]+\\)', value):
        raise ValueError('Public text contains a credential, signed URL or local path')
    return value


def parameters(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unknown public parameter field; review before publishing')
    return {key: text(item) for key, item in value.items()}


def confined(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Catalog path escapes its allowed directory')
    return path


def tree_files(root):
    if root.is_symlink():
        raise ValueError('Publishing directories cannot be symbolic links')
    files = set()
    for path in root.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Publishing trees cannot contain links')
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def write_tree(root, files):
    # Never copy a directory wholesale or carry an old, unexpected file forward.
    unexpected = tree_files(root) - set(files) if root.exists() else set()
    if unexpected:
        raise ValueError('Unexpected files in output; refusing publication: ' + ', '.join(sorted(unexpected)))
    for relative, body in files.items():
        path = confined(root, relative)
        if path.is_file() and path.read_bytes() == body:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def sample_path(rid):
    return 'recipes/' + identifier(rid).lower() + '/sample.jpg'


def portrait_path(cid):
    return 'assets/creators/' + identifier(cid) + '.png'


def expected_files(payload):
    return {'catalog.json'} | {'details/' + r['id'] + '.json' for r in payload['recipes']} | {
        sample_path(r['id']) for r in payload['recipes']} | {
        portrait_path(c['id']) for c in payload['creators'] if c['localPortrait']}


def validate_image_info(info):
    if not isinstance(info, dict) or set(info) != set(IMAGE_KEYS) or not re.fullmatch(r'[0-9a-f]{64}', info['sha256']):
        raise ValueError('Invalid public image metadata')
    if any(type(info[k]) is not int or info[k] <= 0 for k in ('bytes', 'width', 'height')):
        raise ValueError('Invalid public image dimensions or size')
    if info['bytes'] > 25 * 1024 * 1024 or info['format'] not in ('JPEG', 'PNG', 'WEBP'):
        raise ValueError('Unsupported public image')


def validate_payload(payload):
    if set(payload) != PAYLOAD_KEYS or payload['mode'] != 'static':
        raise ValueError('Unknown public payload schema')
    if payload['labels'] != LABELS or payload['values'] != VALUES:
        raise ValueError('Unexpected public field definitions')
    if not payload['recipes'] or not payload['creators']:
        raise ValueError('Cannot publish an empty catalog')
    ids = [identifier(r['id']) for r in payload['recipes']]
    cids = [identifier(c['id']) for c in payload['creators']]
    if len(set(i.lower() for i in ids)) != len(ids) or len(set(i.lower() for i in cids)) != len(cids):
        raise ValueError('Duplicate public catalog ID')
    for recipe in payload['recipes']:
        if set(recipe) != RECIPE_KEYS:
            raise ValueError('Unknown recipe fields in public snapshot')
        for key in RECIPE_TEXT:
            text(recipe[key])
        if not recipe['name'].strip() or not isinstance(recipe['authors'], list):
            raise ValueError('Recipe name or authors are invalid')
        for author in recipe['authors']:
            text(author)
        if not isinstance(recipe['authorIds'], list) or not set(recipe['authorIds']).issubset(cids):
            raise ValueError('Unknown public author reference')
        parameters(recipe['pictureControl'], PICTURE_KEYS)
        parameters(recipe['exif'], EXIF_KEYS)
        if recipe['image'] != 'catalog/' + sample_path(recipe['id']):
            raise ValueError('Unexpected sample image path')
        if recipe['source'] != source_url(recipe['id']):
            raise ValueError('Unexpected official source URL')
        validate_image_info(recipe['imageInfo'])
    for creator in payload['creators']:
        if set(creator) != CREATOR_KEYS:
            raise ValueError('Unknown creator fields in public snapshot')
        for key in ('name', 'position', 'profile'):
            text(creator[key])
        if creator['localPortrait'] not in ('', 'catalog/' + portrait_path(creator['id'])):
            raise ValueError('Unexpected creator portrait path')
        if creator['localPortrait']:
            validate_image_info(creator['imageInfo'])
        elif creator['imageInfo'] is not None:
            raise ValueError('Unexpected portrait metadata')
    report = payload['report']
    if set(report) != {'finishedAt', 'recipeCount', 'authorCount', 'sampleCount', 'portraitCount'}:
        raise ValueError('Unknown public report fields')
    text(report['finishedAt'])
    if (report['recipeCount'], report['authorCount'], report['sampleCount'], report['portraitCount']) != (
            len(ids), len(cids), len(ids), sum(bool(c['localPortrait']) for c in payload['creators'])):
        raise ValueError('Public catalog counts do not match')
    metadata = payload['syncMetadata']
    if set(metadata) != {'revision', 'syncedAt', 'newRecipeIds'}:
        raise ValueError('Unknown public sync metadata fields')
    text(metadata['revision'])
    text(metadata['syncedAt'])
    if not isinstance(metadata['newRecipeIds'], list) or not set(metadata['newRecipeIds']).issubset(ids):
        raise ValueError('Invalid new recipe IDs')


def source_url(rid):
    return BASE + '/recipe/release/detail/?id=' + identifier(rid) + '&sortType=0&recipe_lang=zh-cn'


def export_catalog(source, target=SNAPSHOT):
    # The full local validator reads only the official catalog. Import it only
    # during an explicit export; CI builds need no photo/Pillow dependencies.
    from catalog_sync import CatalogSync
    source = Path(source).resolve()
    CatalogSync.validate(source)
    input_paths = ['data/recipes.json', 'data/creators.json', 'data/collection_report.json',
                   'data/assets.json', 'data/sync_metadata.json']
    before = {p: confined(source, p).read_bytes() for p in input_paths if (source / p).exists()}
    recipes = json.loads(before['data/recipes.json'])
    creators = json.loads(before['data/creators.json'])
    assets = {a['path']: a for a in json.loads(before['data/assets.json'])}
    metadata = json.loads(before.get('data/sync_metadata.json', b'{}'))
    report = json.loads(before['data/collection_report.json'])
    files = {}

    def image(relative):
        body = confined(source, relative).read_bytes()
        asset = assets[relative]
        if len(body) != asset['bytes'] or hashlib.sha256(body).hexdigest() != asset['sha256']:
            raise ValueError('Official catalog changed during export')
        files[relative] = body

    public_recipes = []
    for recipe in recipes:
        rid = identifier(recipe['id'])
        if recipe['image'] != sample_path(rid):
            raise ValueError('Unexpected official image path')
        public = {key: text(recipe.get(key, '')) for key in RECIPE_TEXT}
        public.update(id=rid, authors=[text(a) for a in recipe['authors']],
            authorIds=[identifier(a) for a in recipe['authorIds']],
            pictureControl=parameters(recipe['pictureControl'], PICTURE_KEYS),
            exif=parameters(recipe['exif'], EXIF_KEYS), image='catalog/' + sample_path(rid),
            imageInfo={k: recipe['imageInfo'][k] for k in IMAGE_KEYS}, source=source_url(rid))
        public_recipes.append(public)
        files['details/' + rid + '.json'] = json_bytes(public)
        image(sample_path(rid))
    public_creators = []
    for creator in creators:
        cid = identifier(creator['id'])
        portrait = creator.get('localPortrait', '')
        if portrait and portrait != portrait_path(cid):
            raise ValueError('Unexpected official portrait path')
        public_creators.append({'id': cid, 'name': text(creator['name']),
            'position': text(creator.get('position', '')), 'profile': text(creator.get('profile', '')),
            'localPortrait': 'catalog/' + portrait if portrait else '',
            'imageInfo': {k: assets[portrait][k] for k in IMAGE_KEYS} if portrait else None})
        if portrait:
            image(portrait)
    payload = {'mode': 'static', 'recipes': public_recipes, 'creators': public_creators,
        'labels': LABELS, 'values': VALUES,
        'report': {'finishedAt': text(report.get('finishedAt', '')), 'recipeCount': len(recipes),
            'authorCount': len(creators), 'sampleCount': len(recipes),
            'portraitCount': sum(bool(c['localPortrait']) for c in public_creators)},
        'syncMetadata': {'revision': text(metadata.get('revision', '')),
            'syncedAt': text(metadata.get('syncedAt', report.get('finishedAt', ''))),
            'newRecipeIds': metadata.get('newRecipeIds', [])}}
    validate_payload(payload)
    files['catalog.json'] = json_bytes(payload)
    if set(files) != expected_files(payload):
        raise ValueError('Public export does not match the file allowlist')
    if any(confined(source, p).read_bytes() != body for p, body in before.items()):
        raise ValueError('Official sync ran during export; retry after it finishes')
    write_tree(Path(target), files)
    return {'recipes': len(recipes), 'creators': len(creators), 'files': len(files)}


def snapshot_files(root):
    payload = read_json(confined(root, 'catalog.json'))
    validate_payload(payload)
    if tree_files(root) != expected_files(payload):
        raise ValueError('Public snapshot contains missing or non-allowlisted files')
    files = {relative: confined(root, relative).read_bytes() for relative in sorted(expected_files(payload))}
    for recipe in payload['recipes']:
        image = files[sample_path(recipe['id'])]
        if len(image) != recipe['imageInfo']['bytes'] or hashlib.sha256(image).hexdigest() != recipe['imageInfo']['sha256']:
            raise ValueError('Public sample image hash mismatch')
        if files['details/' + recipe['id'] + '.json'] != json_bytes(recipe):
            raise ValueError('Public recipe detail contains unexpected data')
    for creator in payload['creators']:
        if creator['localPortrait']:
            image = files[portrait_path(creator['id'])]
            if len(image) != creator['imageInfo']['bytes'] or hashlib.sha256(image).hexdigest() != creator['imageInfo']['sha256']:
                raise ValueError('Public portrait hash mismatch')
    return payload, files


def csv_bytes(headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v
                      for v in row] for row in rows])
    return stream.getvalue().encode('utf-8-sig')


def build(snapshot=SNAPSHOT, output=OUTPUT, photos_snapshot=None):
    payload, catalog = snapshot_files(Path(snapshot))
    from public_photos import snapshot_files as photo_snapshot_files
    photos_root = Path(photos_snapshot) if photos_snapshot is not None else Path(snapshot).parent / 'public-photos'
    photo_library, photos = ({'version': 1, 'updatedAt': '', 'captures': [], 'recipeFavorites': []}, {})
    if photos_root.exists():
        photo_library, photos = photo_snapshot_files(photos_root, {r['id'] for r in payload['recipes']})
    revision = os.environ.get('GITHUB_SHA', '')
    if not revision:
        try:
            revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                      text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            revision = ''
    if revision and not re.fullmatch(r'[0-9a-f]{40,64}', revision):
        raise ValueError('Invalid publication revision')
    payload['photoLibrary'] = photo_library
    payload['publication'] = {'revision': revision}
    template = (ROOT / 'app/viewer_template.html').read_text(encoding='utf-8')
    style = (ROOT / 'app/ui.css').read_text(encoding='utf-8')
    template = re.sub(r'<style>.*?</style>', lambda _: '<style>\n' + style + '\n</style>', template, count=1, flags=re.S)
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = template.replace('__PAYLOAD__', serialized)
    template = template.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-mode="static">')
    template = template.replace('NIKON IMAGING CLOUD / LOCAL LIBRARY', 'NIKON IMAGING CLOUD / RECIPE LIBRARY')
    template = template.replace('href="/api/backup"', 'href="#"')
    template = template.replace('href="/scheme-table.xlsx"', 'href="./catalog/tables/recipes.csv"').replace('Excel 总表</a>', '配方总表 CSV</a>')
    template = template.replace('href="/guide"', 'href="./guide.html"')
    template = template.replace('资料与备份</summary>', '资料与说明</summary>')
    template = re.sub(r'<footer>.*?</footer>', '<footer>本页展示公开方案、官方样片与我的实拍；关联和收藏状态随本地资料库发布更新。<br>实拍仅提供压缩展示图，原始 JPG 保留在本地。方案器材为官网样片拍摄设备；按名称关联不代表全部参数未经微调。<br><span id="stamp"></span></footer>', template, count=1, flags=re.S)
    files = {'index.html': template.encode('utf-8'),
             'app.js': (ROOT / 'app/app.js').read_bytes(),
             'guide.html': (ROOT / 'app/public_guide.html').read_bytes(), '.nojekyll': b''}
    files.update({'catalog/' + relative: body for relative, body in catalog.items()})
    files.update({'photos/' + relative: body for relative, body in photos.items()})
    files['catalog/tables/recipes.csv'] = csv_bytes(
        ['方案名称', '作者', '相机', '镜头', '发布日期', '基础优化校准', '方案 ID', '官网链接'],
        [[r['name'], ' / '.join(r['authors']), r['camera'], r['lens'], r['publicDate'],
          r['pictureControl'].get('baseName', ''), r['id'], r['source']] for r in payload['recipes']])
    files['publish-manifest.json'] = json_bytes({'schemaVersion': 1,
        'recipeCount': len(payload['recipes']), 'creatorCount': len(payload['creators']),
        'photoCount': len(photo_library['captures']), 'revision': revision,
        'files': [{'path': path, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
                  for path, body in sorted(files.items())]})
    output = Path(output)
    # Retire only previously built, unchanged photo assets listed in the old manifest.
    # This supports edited/deleted photos without accepting arbitrary output files.
    stale = tree_files(output) - set(files) if output.exists() else set()
    if stale:
        if not (output / 'publish-manifest.json').is_file():
            raise ValueError('Unexpected files in output without a previous build manifest')
        previous = read_json(confined(output, 'publish-manifest.json'))
        known = {item['path']: item for item in previous['files']}
        for relative in stale:
            if not re.fullmatch(r'photos/images/[0-9a-f]{24}-(thumb|display)-[0-9a-f]{16}\.jpg', relative) or relative not in known:
                raise ValueError('Unexpected files in output; refusing publication: ' + relative)
            if hashlib.sha256(confined(output, relative).read_bytes()).hexdigest() != known[relative]['sha256']:
                raise ValueError('Unexpected change to previous output photo')
        for relative in stale:
            confined(output, relative).unlink()
    write_tree(output, files)
    if tree_files(Path(output)) != set(files):
        raise ValueError('Published files differ from the allowlist')
    return {'recipes': len(payload['recipes']), 'creators': len(payload['creators']),
            'photos': len(photo_library['captures']),
            'files': len(files), 'bytes': sum(len(body) for body in files.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true', help='Explicitly export the local public catalog before building')
    parser.add_argument('--catalog-source', type=Path, default=ROOT / 'data/catalog')
    parser.add_argument('--export-photos', action='store_true', help='Export reduced JPGs and resolved associations')
    parser.add_argument('--photo-workspace', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.export:
        print(json.dumps({'export': export_catalog(args.catalog_source)}, ensure_ascii=True))
    if args.export_photos:
        from public_photos import export_photos
        catalog, _ = snapshot_files(SNAPSHOT)
        result = export_photos(args.photo_workspace, ROOT / 'data/public-photos', {r['id'] for r in catalog['recipes']})
        print(json.dumps({'photos': result}, ensure_ascii=True))
    print(json.dumps({'build': build()}, ensure_ascii=True))


if __name__ == '__main__':
    main()
