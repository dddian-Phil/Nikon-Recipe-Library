"""Read example images without modifying them or the recipe viewer.

Decode the documented Nikon PictureControl3 name fields for the observed 0310
layout. Unknown layouts are reported rather than guessed. No RAW processing.
"""
import hashlib
import json
import struct
from collections import defaultdict
from pathlib import Path
from PIL import Image
import tifffile

WORKSPACE = Path(__file__).resolve().parents[1]
INPUT = WORKSPACE / 'photos'
OUTPUT = WORKSPACE / 'local/history/sample-metadata'
CATALOG = WORKSPACE / 'data/catalog/data/recipes.json'
DOCUMENTATION = 'https://exiftool.sourceforge.net/TagNames/Nikon.html#PictureControl3'


def clean(value):
    return str(value or '').rstrip('\x00').strip()


def picture_control_block(maker):
    if not maker.startswith(b'Nikon\x00') or maker[6] != 2:
        raise ValueError('Unsupported Nikon MakerNote header')
    base = 10
    endian = '<' if maker[base:base + 2] == b'II' else '>' if maker[base:base + 2] == b'MM' else None
    if not endian:
        raise ValueError('Unknown MakerNote byte order')
    start = base + struct.unpack_from(endian + 'I', maker, base + 4)[0]
    count = struct.unpack_from(endian + 'H', maker, start)[0]
    if count > 4096 or start + 2 + count * 12 > len(maker):
        raise ValueError('Invalid MakerNote directory')
    for index in range(count):
        position = start + 2 + index * 12
        tag, kind, size, offset = struct.unpack_from(endian + 'HHII', maker, position)
        if tag == 0x0023:
            if kind != 7 or size < 48:
                raise ValueError('Unexpected PictureControlData type or size')
            begin = base + offset
            if begin + size > len(maker):
                raise ValueError('PictureControlData exceeds MakerNote bounds')
            return maker[begin:begin + size]
    raise ValueError('PictureControlData not found')


def read_file(path, recipes):
    if path.suffix.upper() == '.JPG':
        with Image.open(path) as image:
            exif = image.getexif()
            detail = exif.get_ifd(34665)
            model = clean(exif.get(272))
            taken = clean(detail.get(36867))
            lens = clean(detail.get(42036))
            maker = detail.get(37500, b'')
    else:
        with tifffile.TiffFile(path) as image:
            tags = image.pages[0].tags
            detail = tags['ExifTag'].value
            model = clean(tags['Model'].value)
            taken = clean(detail.get('DateTimeOriginal'))
            lens = clean(detail.get('LensModel'))
            maker = detail.get('MakerNote', b'')
    block = picture_control_block(maker)
    version = block[:4].decode('ascii', errors='strict')
    if version != '0310':
        raise ValueError(f'Unsupported PictureControl version: {version}')
    name = block[8:28].split(b'\x00', 1)[0].decode('ascii', errors='strict')
    base = block[28:48].split(b'\x00', 1)[0].decode('ascii', errors='strict')
    matches = [r for r in recipes if r['pictureControlName'] == name]
    return {'file': path.name, 'camera': model, 'lens': lens, 'takenAt': taken,
            'pictureControlVersion': version, 'pictureControlName': name,
            'pictureControlBase': base, 'pictureControlTag': 'Nikon MakerNote 0x0023',
            'pictureControlDataHex': block.hex(),
            'pictureControlDataSha256': hashlib.sha256(block).hexdigest(),
            'matches': [{'id': r['id'], 'name': r['name'], 'authors': r['authors'], 'source': r['source']} for r in matches],
            'matchMethod': 'exact_picture_control_name' if len(matches) == 1 else 'no_unique_exact_match',
            'parameterEqualityWithCloudChecked': False}


recipes = json.loads(CATALOG.read_text(encoding='utf-8'))
files = [read_file(p, recipes) for p in sorted(INPUT.iterdir()) if p.suffix.upper() in ('.JPG', '.NEF')]
grouped = defaultdict(list)
for file in files:
    grouped[Path(file['file']).stem].append(file)
groups = []
for stem, pair in grouped.items():
    same = len({r['pictureControlDataSha256'] for r in pair}) == 1
    assert same, f'JPG / NEF PictureControl mismatch: {stem}'
    assert len({(r['camera'], r['takenAt'], r['lens']) for r in pair}) == 1
    first = pair[0]
    groups.append({'capture': stem, 'files': [r['file'] for r in pair],
                   'samePictureControlBlock': same,
                   **{k: first[k] for k in ['camera', 'lens', 'takenAt', 'pictureControlName', 'pictureControlBase', 'matches']}})
OUTPUT.mkdir(parents=True, exist_ok=True)
report = {'inputFolder': str(INPUT), 'fileCount': len(files), 'captureCount': len(groups),
          'documentation': DOCUMENTATION, 'files': files, 'captures': groups,
          'notes': ['仅按机内优化校准名称与公开目录匹配，不等同于确认所有参数未被调整。',
                    'AUTO 记录不关联到具体云创方案。',
                    '此次仅读取原文件，未修改浏览入口或原始照片。']}
(OUTPUT / 'metadata_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# 样片优化校准读取结果', '',
         f'读取 {len(files)} 个文件，共 {len(groups)} 组 JPG＋NEF；每组优化校准数据完全一致。', '',
         '| 照片组 | 机内优化校准名称 | 基础优化校准 | 公开目录匹配 |', '|---|---|---|---|']
for group in groups:
    match = group['matches'][0] if len(group['matches']) == 1 else None
    label = f"{match['name']}（{' / '.join(match['authors'])}）" if match else '未匹配具体云创方案'
    lines.append(f"| {group['capture']} | {group['pictureControlName']} | {group['pictureControlBase']} | {label} |")
lines += ['', f"相机：{groups[0]['camera']}  \n镜头：{groups[0]['lens']}", '',
          '这是文件中记录的拍摄时优化校准。名称匹配不等同于参数完全一致，本次未比较云创曲线或其他调整。', '',
          '[字段说明](' + DOCUMENTATION + ')', '']
(OUTPUT / 'read-results.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps({'fileCount': len(files), 'captureCount': len(groups), 'captures': groups}, ensure_ascii=True, indent=2))
