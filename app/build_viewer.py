"""Build a viewer even before the first user-triggered official catalog sync."""
import json
from pathlib import Path
from collect_nikon import ROOT, LABELS, VALUES, save_json


def build(root=ROOT, output=None):
    root = Path(root)
    def read(name, default):
        path = root / 'data' / name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    recipes = read('recipes.json', [])
    creators = read('creators.json', [])
    report = read('collection_report.json', {})
    for recipe in recipes:
        for key in ['image', 'document', 'folder']:
            recipe[key] = 'catalog/' + recipe[key]
    for creator in creators:
        if creator.get('localPortrait'):
            creator['localPortrait'] = 'catalog/' + creator['localPortrait']
    payload = json.dumps({'recipes': recipes, 'creators': creators, 'report': report,
                          'labels': LABELS, 'values': VALUES,
                          'syncMetadata': read('sync_metadata.json', {})}, ensure_ascii=False)
    payload = payload.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    program = Path(__file__).parent
    template = (program / 'viewer_template.html').read_text(encoding='utf-8')
    start, end = template.index('<style>') + len('<style>'), template.index('</style>')
    template = template[:start] + '\n' + (program / 'ui.css').read_text(encoding='utf-8') + '\n' + template[end:]
    viewer = Path(output) if output else program / 'viewer.html'
    viewer.parent.mkdir(parents=True, exist_ok=True)
    viewer.write_text(template.replace('__PAYLOAD__', payload), encoding='utf-8')
    save_json(root / 'data/field_labels.json', LABELS)
    return {'viewer': str(viewer), 'recipes': len(recipes)}


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=True))
