"""Archive the public Nikon China recipe catalog without login credentials."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = 'https://imagingcloud.nikon.com.cn'
IR = 'https://api.user.ir.imagingcloud.nikon.com.cn'
LIST = 'https://api.user.cwp.imagingcloud.nikon.com.cn/bff/recipe/release'
ROOT = Path(__file__).resolve().parents[1] / 'data' / 'catalog'
TZ = timezone(timedelta(hours=8))

LABELS = {
    'settingName': '优化校准名称', 'baseName': '原始优化校准', 'quickSharp': '快速锐化',
    'sharpening': '锐化', 'middleRangeSharp': '中等锐化', 'clarity': '清晰度',
    'contrast': '对比度', 'saturation': '饱和度', 'highlight': '亮部', 'shadow': '阴影',
    'whiteLevel': '白电平', 'blackLevel': '黑电平', 'hue': '色相', 'brightness': '亮度',
    'effectLevel': '效果级别', 'filterEffect': '滤镜效果', 'toning': '调色', 'toningSaturation': '调色饱和度',
    'model': '相机型号', 'dateTimeOriginal': '拍摄时间戳（毫秒）', 'exposureTime': '快门速度（秒）',
    'fNumber': '光圈 F 值', 'exposureProgram': '曝光程序', 'iso': 'ISO 感光度',
    'exposureBiasValue': '曝光补偿（EV）', 'meteringMode': '测光模式', 'focalLength': '焦距',
    'exposureMode': '曝光模式', 'whiteBalance': '白平衡', 'lensModel': '镜头型号',
    'focusMode': '对焦模式', 'activeDLighting': '动态 D-Lighting', 'skinSoftening': '皮肤柔和',
    'portraitImpressionBalance': '调整人像形象', 'afSubjectDetection': '拍摄对象识别设定',
    'afPrioritySelection': 'AF 优先', 'afAreaMode': 'AF 区域模式', 'flash': '闪光灯',
}
VALUES = {
    'exposureProgram': {'0': '未定义', '1': 'M（手动）', '2': 'P（程序自动）', '3': 'A（光圈优先）', '4': 'S（快门优先）'},
    'meteringMode': {'1': '平均测光', '2': '中央重点', '3': '点测光', '5': '矩阵测光'},
    'activeDLighting': {'0': '关闭'}, 'skinSoftening': {'0': '关闭'},
    'portraitImpressionBalance': {'0': '关闭'}, 'afSubjectDetection': {'2': '自动'},
    'afPrioritySelection': {'1': '释放'}, 'afAreaMode': {'02_c5': '自动区域 AF'},
}


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def request(url, data=None):
    headers = {'User-Agent': 'NikonPublicRecipeArchive/1.0', 'Referer': BASE + '/', 'Accept-Language': 'zh-CN,zh;q=0.9'}
    if data is not None:
        headers['Content-Type'] = 'text/plain;charset=UTF-8'
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as response:
                return response.read(), response.headers.get('Content-Type', '')
        except (OSError, urllib.error.URLError):
            if attempt == 3:
                raise
            time.sleep(1.5 * (2 ** attempt))


def get_json(url, data=None):
    body, _ = request(url, data)
    return json.loads(body)


def slug(text, limit=48):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:limit] or 'untitled'


def image_info(path):
    data = path.read_bytes()
    if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'GIF8') or data.startswith(b'RIFF')):
        raise ValueError(f'Invalid image: {path.name}')
    info = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            info.update(width=image.width, height=image.height, format=image.format)
    except ImportError:
        pass
    return info


def download(url, path, *, offline=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if offline:
            raise FileNotFoundError(f'Offline archive is missing: {path.name}')
        data, mime = request(url)
        if not (mime.lower().startswith('image/') or mime.lower().split(';')[0] in ('binary/octet-stream', 'application/octet-stream')):
            raise ValueError(f'Unexpected image content type: {mime}')
        temp = path.with_suffix(path.suffix + '.part')
        temp.write_bytes(data)
        image_info(temp)
        temp.replace(path)
    return image_info(path)


def display(key, value):
    if value == 'gamma':
        return '自定义曲线（公开接口未提供曲线数值）'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return VALUES.get(key, {}).get(str(value), str(value) if value is not None else '')


def md_cell(value):
    return str(value or '').replace('|', '\\|').replace('\n', '<br>')


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            safe = {}
            for key in fields:
                value = row.get(key, '')
                if isinstance(value, str) and value.startswith(('=', '+', '@')):
                    value = "'" + value
                safe[key] = value
            writer.writerow(safe)


def main(argv=None, *, root=ROOT, quiet=False):
    ROOT = Path(root)
    emit = (lambda *args, **kwargs: None) if quiet else print
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Rebuild indexes from the completed local archive')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(argv)
    if not args.offline:
        parser.error('Official sync is available only from the web UI. Use --offline to rebuild existing local files.')
    ROOT.mkdir(parents=True, exist_ok=True)
    previous_report = json.loads((ROOT / 'data/collection_report.json').read_text(encoding='utf-8')) if args.offline else {}
    previous_records = {r['id']: r for r in json.loads((ROOT / 'data/recipes.json').read_text(encoding='utf-8'))} if args.offline else {}
    started = previous_report.get('startedAt', now())
    payload = {'ownerUserId': 'nikon', 'bookId': '', 'sortType': 0, 'lang_code': 'zh-cn', 'country': None}
    catalog = json.loads((ROOT / 'raw/catalog.json').read_text(encoding='utf-8')) if args.offline else get_json(LIST, json.dumps(payload).encode())
    if catalog.get('error_code'):
        raise RuntimeError(catalog['error_code'])
    creators = json.loads((ROOT / 'raw/creators.json').read_text(encoding='utf-8')) if args.offline else get_json(IR + '/info/creator')
    save_json(ROOT / 'raw/catalog.json', catalog)
    save_json(ROOT / 'raw/creators.json', creators)
    listed = catalog['recipe']
    assert len({r['id'] for r in listed}) == len(listed), 'Duplicate catalog IDs'
    author_map = defaultdict(list)
    for creator in creators['creators']:
        for recipe in creator.get('recipes', []):
            author_map[recipe['path'].rstrip('/').split('/')[-1]].append(creator)
    emit(json.dumps({'stage': 'catalog', 'recipes': len(listed), 'creators': len(creators['creators']), 'unmapped': [r['id'] for r in listed if r['id'] not in author_map]}), flush=True)
    records, failures, assets = [], [], []

    def fetch_recipe(item):
        rid = item['id']
        detail_path = ROOT / 'raw/details' / (rid + '.json')
        folder = 'recipes/' + rid.lower()
        sample_path = ROOT / folder / 'sample.jpg'
        detail_url = IR + '/publicrecipe/0000000000000001/' + rid + '?sortType=0&isPagenation=false'
        if args.offline or (detail_path.exists() and sample_path.exists() and not args.refresh):
            detail = json.loads(detail_path.read_text(encoding='utf-8'))
        else:
            detail = get_json(detail_url)
            if detail.get('id') != rid:
                raise ValueError(f'Detail ID mismatch: {rid}')
            save_json(detail_path, detail)
        assigned = author_map.get(rid, [])
        record = {'id': rid, 'name': detail['name'], 'authors': [c['name'] for c in assigned],
                  'authorIds': [c['id'] for c in assigned], 'publisher': detail.get('creatorName', ''),
                  'camera': detail.get('camera') or detail.get('exif', {}).get('model', ''),
                  'lens': detail.get('lens') or detail.get('exif', {}).get('lensModel', ''),
                  'publicDate': detail.get('publicDate', ''), 'shootingDate': detail.get('shootingDate', ''),
                  'outline': detail.get('recipeOutline', ''), 'description': detail.get('recipeDetail', ''),
                  'memo': detail.get('recipeMemo', ''), 'pictureControlName': detail.get('pictureControlName', ''),
                  'pictureControl': detail.get('pictureControl', {}), 'exif': detail.get('exif', {}),
                  'shootingLocation': detail.get('shootingLocation', ''), 'tags': detail.get('tags', []),
                  'publicItemSetting': detail.get('publicItemSetting', {}), 'folder': folder,
                  'image': folder + '/sample.jpg', 'document': folder + '/recipe.md',
                  'source': BASE + '/recipe/release/detail/?id=' + rid + '&sortType=0&recipe_lang=zh-cn',
                  'detailApi': detail_url, 'retrievedAt': previous_records.get(rid, {}).get('retrievedAt', now())}
        try:
            info = download(detail['thumbnail'], sample_path, offline=args.offline)
            record['imageInfo'] = info
            asset = {'kind': 'sample', 'recipeId': rid, 'path': record['image'], 'url': detail['thumbnail'], **info}
        except Exception as exc:
            record['imageError'] = str(exc)
            asset = {'kind': 'sample', 'recipeId': rid, 'path': record['image'], 'error': str(exc)}
        return record, asset

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_recipe, item): item for item in listed}
        for index, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                record, asset = future.result()
                records.append(record)
                assets.append(asset)
                if asset.get('error'):
                    failures.append(asset)
            except Exception as exc:
                failures.append({'kind': 'detail', 'recipeId': item['id'], 'error': str(exc)})
            if index % 12 == 0 or index == len(listed):
                emit(json.dumps({'stage': 'details_and_samples', 'done': index, 'total': len(listed), 'failures': len(failures)}), flush=True)
    positions = {item['id']: n for n, item in enumerate(listed)}
    records.sort(key=lambda r: positions[r['id']])
    used_author_ids = {aid for r in records for aid in r['authorIds']}

    def fetch_creator(creator):
        if not creator.get('thumbnail'):
            return None
        path = 'assets/creators/' + creator['id'] + '.png'
        info = download(creator['thumbnail'], ROOT / path, offline=args.offline)
        creator['localPortrait'] = path
        return {'kind': 'creator', 'creatorId': creator['id'], 'path': path, 'url': creator['thumbnail'], **info}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_creator, c): c for c in creators['creators'] if c['id'] in used_author_ids}
        for future in as_completed(futures):
            creator = futures[future]
            try:
                asset = future.result()
                if asset:
                    assets.append(asset)
            except Exception as exc:
                failures.append({'kind': 'creator', 'creatorId': creator['id'], 'error': str(exc)})
    save_json(ROOT / 'data/recipes.json', records)
    save_json(ROOT / 'data/creators.json', creators['creators'])
    save_json(ROOT / 'data/assets.json', assets)
    stats = {'source': BASE + '/recipe/release/', 'scope': '中国站公开创意色彩灵感库；不包含私人方案及其他地区独有方案',
             'startedAt': started, 'finishedAt': previous_report.get('finishedAt', now()), 'catalogCount': len(listed), 'recipeCount': len(records),
             'authorCount': len(used_author_ids), 'directoryAuthorCount': len(creators['creators']),
             'sampleCount': sum(a['kind'] == 'sample' and not a.get('error') for a in assets),
             'portraitCount': sum(a['kind'] == 'creator' and not a.get('error') for a in assets),
             'cameraCount': len({r['camera'] for r in records if r['camera']}),
             'lensCount': len({r['lens'] for r in records if r['lens']}),
             'missingAuthor': [r['id'] for r in records if not r['authors']],
             'missingCamera': [r['id'] for r in records if not r['camera']],
             'missingLens': [r['id'] for r in records if not r['lens']],
             'missingDescription': [r['id'] for r in records if not r['description']],
             'assetBytes': sum(a.get('bytes', 0) for a in assets), 'failures': failures,
             'notes': ['样片为网站公开展示图，非相机 RAW 或保证的原始分辨率文件。',
                       '相机和镜头是样片拍摄器材，不代表方案兼容性限制。',
                       'gamma 表示自定义曲线，公开详情接口未提供曲线采样点；保留原始值，不推测数值。',
                       '未知枚举保留接口原始编码，完整 JSON 同时保存。',
                       '作者根据官方作者目录的方案 ID 关联，保留详情中的发布者字段。']}
    save_json(ROOT / 'data/collection_report.json', stats)
    save_json(ROOT / 'data/field_labels.json', LABELS)
    summary_rows, parameter_rows = [], []
    for r in records:
        ex = r['exif']
        summary_rows.append({'方案名称': r['name'], '作者': ' / '.join(r['authors']), '相机型号': r['camera'],
                             '镜头型号': r['lens'], '发布日期': r['publicDate'][:10], '拍摄日期': r['shootingDate'],
                             '焦距': ex.get('focalLength', ''), '光圈F值': ex.get('fNumber', ''),
                             '快门秒': ex.get('exposureTime', ''), 'ISO': ex.get('iso', ''),
                             '曝光补偿EV': ex.get('exposureBiasValue', ''), '白平衡': ex.get('whiteBalance', ''),
                             '优化校准名称': r['pictureControlName'], '基础优化校准': r['pictureControl'].get('baseName', ''),
                             '概要': r['outline'], '介绍': r['description'], '备注': r['memo'],
                             '本地样片': r['image'], '本地资料': r['document'], '官网链接': r['source'], '方案ID': r['id']})
        for group, obj in [('优化校准', r['pictureControl']), ('拍摄参数', ex), ('公开字段设定', r['publicItemSetting'])]:
            for key, value in obj.items():
                parameter_rows.append({'方案ID': r['id'], '方案名称': r['name'], '作者': ' / '.join(r['authors']),
                                       '类别': group, '参数名称': LABELS.get(key, key), '字段': key,
                                       '原始值': value if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False),
                                       '显示值': display(key, value) if group != '公开字段设定' else value})
        folder = ROOT / r['folder']
        folder.mkdir(parents=True, exist_ok=True)
        lines = [f"# {r['name']}", '', f"作者：{' / '.join(r['authors']) or '未关联'}", '',
                 f"相机：{r['camera'] or '未提供'}  \n镜头：{r['lens'] or '未提供'}", '',
                 f"发布日期：{r['publicDate']}  \n拍摄日期：{r['shootingDate']}", '',
                 '![展示样片](sample.jpg)', '', '## 介绍', '', r['description'] or '官网未提供介绍。', '']
        for label, value in [('概要', r['outline']), ('备注', r['memo']), ('拍摄地点', r['shootingLocation'])]:
            if value:
                lines += [f'## {label}', '', value, '']
        for group, obj in [('优化校准', r['pictureControl']), ('拍摄参数', ex)]:
            lines += [f'## {group}', '', '| 参数 | 显示值 | 接口原始值 |', '|---|---|---|']
            lines += [f"| {md_cell(LABELS.get(key, key))} | {md_cell(display(key, value))} | {md_cell(value)} |" for key, value in obj.items()]
            lines.append('')
        lines += ['相机和镜头是样片拍摄器材；自定义曲线仅保留官网公开的标记，不代表已获取曲线点。', '',
                  f"[官网方案]({r['source']}) · [完整原始 JSON](../../raw/details/{r['id']}.json) · [返回总索引](../../index.md)", '']
        (folder / 'recipe.md').write_text('\n'.join(lines), encoding='utf-8')
    if summary_rows:
        write_csv(ROOT / 'tables/recipes.csv', list(summary_rows[0]), summary_rows)
    write_csv(ROOT / 'tables/parameters.csv', ['方案ID', '方案名称', '作者', '类别', '参数名称', '字段', '原始值', '显示值'], parameter_rows)
    author_rows = []
    for creator in creators['creators']:
        own = [r for r in records if creator['id'] in r['authorIds']]
        author_rows.append({'作者': creator['name'], '身份': creator.get('position', ''), '简介': creator.get('profile', ''),
                            '方案数': len(own), '相机型号': ' / '.join(sorted({r['camera'] for r in own if r['camera']})),
                            '镜头型号': ' / '.join(sorted({r['lens'] for r in own if r['lens']})), '作者ID': creator['id']})
        author_folder = ROOT / 'authors' / creator['id'].lower()
        author_folder.mkdir(parents=True, exist_ok=True)
        creator['document'] = (author_folder / 'author.md').relative_to(ROOT).as_posix()
        lines = [f"# {creator['name']}", '', creator.get('position', ''), '', creator.get('profile', ''), '']
        if creator.get('localPortrait'):
            lines += [f"![作者头像](<../../{creator['localPortrait']}>)", '']
        lines += ['| 方案 | 相机 | 镜头 |', '|---|---|---|']
        lines += [f"| [{md_cell(r['name'])}](<../../{r['document']}>) | {md_cell(r['camera'])} | {md_cell(r['lens'])} |" for r in own]
        lines += ['', '[返回总索引](../../index.md)', '']
        (author_folder / 'author.md').write_text('\n'.join(lines), encoding='utf-8')
    write_csv(ROOT / 'tables/creators.csv', list(author_rows[0]), author_rows)
    save_json(ROOT / 'data/creators.json', creators['creators'])
    save_json(ROOT / 'data/workbook_input.json', {'summary': summary_rows, 'parameters': parameter_rows, 'authors': author_rows})
    index = ['# 尼康云创公开色彩方案资料库', '',
             f"采集时间：{stats['finishedAt']}  \n范围：中国站公开创意色彩灵感库  \n{len(records)} 个方案 · {len(used_author_ids)} 位作者 · {stats['cameraCount']} 种相机 · {stats['lensCount']} 种镜头", '',
             '[方案总表 CSV](tables/recipes.csv) · [作者总表 CSV](tables/creators.csv) · [全部参数 CSV](tables/parameters.csv)', '',
             '## 按作者查看', '', '| 作者 | 方案数 | 身份 |', '|---|---:|---|']
    for c in creators['creators']:
        count = sum(c['id'] in r['authorIds'] for r in records)
        if count:
            index.append(f"| [{md_cell(c['name'])}](<{c['document']}>) | {count} | {md_cell(c.get('position', ''))} |")
    index += ['', '## 全部方案', '', '| 方案 | 作者 | 相机 | 镜头 |', '|---|---|---|---|']
    index += [f"| [{md_cell(r['name'])}](<{r['document']}>) | {md_cell(' / '.join(r['authors']))} | {md_cell(r['camera'])} | {md_cell(r['lens'])} |" for r in records]
    index += ['', '## 按器材查看', '']
    for key, title in [('camera', '相机'), ('lens', '镜头')]:
        index += [f'### {title}', '']
        for value in sorted({r[key] for r in records if r[key]}):
            own = [r for r in records if r[key] == value]
            index += [f'#### {value}（{len(own)} 个方案）', '']
            index += [f"- [{r['name']}](<{r['document']}>) — {' / '.join(r['authors'])}" for r in own]
            index.append('')
    (ROOT / 'index.md').write_text('\n'.join(index), encoding='utf-8')
    readme = ['# 使用说明', '', '从「index.md」按作者或器材打开方案，也可在 Excel 中打开总表后筛选。', '',
              '- tables：含方案总表、作者总表、全部参数，CSV 使用 UTF-8 BOM，便于 Excel 打开。',
              '- recipes：每个方案对应资料文档和已下载的展示样片。',
              '- authors：作者简介和该作者所有方案的索引。',
              '- raw：官方列表、作者目录和每个方案的完整原始 JSON。',
              '- data：整理后的结构化 JSON、素材清单、字段标签和采集报告。', '', '## 数据含义', '']
    readme += ['- ' + note for note in stats['notes']]
    readme += ['', '图片链接可能包含官方临时签名；下载后的本地图片不依赖签名有效期。',
               '空白字段表示官方公开数据未提供；不会用零或猜测值补齐。',
               '身份与器材名称按官方文字保留；同一镜头的不同拼写不擅自合并。',
               '本次采集没有登录，也没有访问私人账号内容。', '',
               f"采集结果：{len(records)}/{len(listed)} 个方案，{stats['sampleCount']} 张展示样片，{stats['portraitCount']} 张作者头像。", '']
    (ROOT / 'README.md').write_text('\n'.join(readme), encoding='utf-8')
    emit(json.dumps(stats, ensure_ascii=True), flush=True)
    return stats


if __name__ == '__main__':
    main()
