import json
from pathlib import Path
from collect_nikon import ROOT, LABELS, VALUES, save_json

recipes = json.loads((ROOT / 'data/recipes.json').read_text(encoding='utf-8'))
creators = json.loads((ROOT / 'data/creators.json').read_text(encoding='utf-8'))
report = json.loads((ROOT / 'data/collection_report.json').read_text(encoding='utf-8'))
for recipe in recipes:
    for key in ['image', 'document', 'folder']:
        recipe[key] = 'catalog/' + recipe[key]
for creator in creators:
    if creator.get('localPortrait'):
        creator['localPortrait'] = 'catalog/' + creator['localPortrait']
payload = json.dumps({'recipes': recipes, 'creators': creators, 'report': report, 'labels': LABELS, 'values': VALUES}, ensure_ascii=False)
payload = payload.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
template = (Path(__file__).parent / 'viewer_template.html').read_text(encoding='utf-8')
style_start, style_end = template.index('<style>') + len('<style>'), template.index('</style>')
template = template[:style_start] + '\n' + (Path(__file__).parent / 'ui.css').read_text(encoding='utf-8') + '\n' + template[style_end:]
viewer_path = Path(__file__).parent / 'viewer.html'
viewer_path.write_text(template.replace('__PAYLOAD__', payload), encoding='utf-8')
save_json(ROOT / 'data/field_labels.json', LABELS)
print(json.dumps({'viewer': str(viewer_path), 'recipes': len(recipes)}, ensure_ascii=True))
