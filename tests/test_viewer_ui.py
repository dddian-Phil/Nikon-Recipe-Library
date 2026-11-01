"""Check generated viewer structure without touching the real photo library."""
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from build_viewer import build


class ViewerParser(HTMLParser):
    VOID = {'meta', 'link', 'input', 'img', 'br', 'hr'}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.ids = Counter()
        self.elements = {}
        self.payload = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = attrs.get('id')
        if key:
            self.ids[key] += 1
            self.elements[key] = (attrs, [a.get('id') for _, a in self.stack])
        if tag not in self.VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        if self.stack and self.stack[-1][1].get('id') == 'payload':
            self.payload += text


class ViewerUITests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'local/test-runs'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def page(self, recipes=None):
        catalog = self.root / 'catalog'
        (catalog / 'data').mkdir(parents=True, exist_ok=True)
        (catalog / 'data/recipes.json').write_text(
            json.dumps(recipes or [], ensure_ascii=False), encoding='utf-8')
        with patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
            result = build(root=catalog, output=self.root / 'viewer.html')
        html = Path(result['viewer']).read_text(encoding='utf-8')
        parser = ViewerParser()
        parser.feed(html)
        return html, parser

    def test_controls_remain_unique_and_help_is_inside_menu(self):
        _, parser = self.page()
        self.assertTrue(all(count == 1 for count in parser.ids.values()))
        for key in ('scan-photos', 'sync-catalog', 'publish-website', 'utility-menu',
                    'shortcut-help', 'import-records', 'records-file', 'library-status',
                    'view-recipes', 'view-photos', 'q', 'filter-menu', 'author', 'camera',
                    'lens', 'base', 'reset', 'only-favorites', 'only-new', 'only-mine',
                    'only-unmatched', 'recipe-sort', 'density-comfort', 'density-compact',
                    'photo-like', 'shortcuts', 'shortcuts-close'):
            self.assertEqual(parser.ids[key], 1, key)
        for key in ('shortcut-help', 'sync-catalog', 'publish-website', 'import-records'):
            self.assertIn('utility-menu', parser.elements[key][1])
        self.assertNotIn('utility-menu', parser.elements['scan-photos'][1])
        for key in ('view-recipes', 'view-photos', 'q', 'filter-menu', 'only-favorites',
                    'only-new', 'only-mine', 'only-unmatched', 'recipe-sort',
                    'density-comfort', 'density-compact'):
            self.assertIn('browse-controls', parser.elements[key][1])
        self.assertEqual(parser.elements['library-status'][0]['aria-live'], 'polite')
        for key in ('library-alert', 'catalog-sync-status', 'website-sync-panel'):
            self.assertIn('hidden', parser.elements[key][0])

    def test_gallery_controls_keep_labels_and_notices_outside_menus(self):
        _, parser = self.page()
        for key in ('library-status', 'library-alert', 'catalog-sync-status',
                    'catalog-sync-error', 'website-sync-panel'):
            ancestors = parser.elements[key][1]
            self.assertNotIn('utility-menu', ancestors)
            self.assertNotIn('filter-menu', ancestors)
        self.assertIn('hidden', parser.elements['view-help'][0])
        self.assertEqual(parser.elements['q'][0]['type'], 'search')
        self.assertEqual(parser.elements['recipe-sort'][0]['aria-label'], '方案排序')
        self.assertEqual(parser.elements['density-comfort'][0]['aria-pressed'], 'true')
        self.assertEqual(parser.elements['density-compact'][0]['aria-pressed'], 'false')
        self.assertIn('data-local-only', parser.elements['publish-website'][0])
        self.assertIn('data-local-only', parser.elements['website-sync-panel'][0])

    def test_viewer_navigation_has_named_icon_controls(self):
        html, parser = self.page()
        for key in ('recipe-prev', 'recipe-next', 'photo-prev', 'photo-next'):
            attrs, _ = parser.elements[key]
            self.assertTrue(attrs['aria-label'])
            self.assertEqual(attrs['title'], attrs['aria-label'])
        self.assertEqual(html.count('class="lucide lucide-chevron-left"'), 2)
        self.assertEqual(html.count('class="lucide lucide-chevron-right"'), 2)

    def test_viewer_close_icons_keep_accessible_names(self):
        html, parser = self.page()
        for key in ('close', 'photo-close'):
            attrs, _ = parser.elements[key]
            self.assertEqual(attrs['class'], 'viewer-close')
            self.assertTrue(attrs['aria-label'])
            self.assertEqual(attrs['title'], attrs['aria-label'])
        self.assertEqual(html.count('class="lucide lucide-x"'), 2)

    def test_payload_and_embedded_styles_survive_header_changes(self):
        recipe = {'id': 'preview', 'name': '长标题 </script> 与引号 "',
                  'image': 'recipes/preview/sample.jpg', 'document': 'preview.md',
                  'folder': 'recipes/preview'}
        html, parser = self.page([recipe])
        payload = json.loads(parser.payload)
        self.assertEqual(payload['recipes'][0]['name'], recipe['name'])
        self.assertEqual(payload['recipes'][0]['image'], 'catalog/' + recipe['image'])
        self.assertNotIn('__PAYLOAD__', html)
        css = (Path(__file__).resolve().parents[1] / 'app/ui.css').read_text(encoding='utf-8')
        self.assertIn(css, html)
        self.assertIn('<script src="/app.js"></script>', html)


if __name__ == '__main__':
    unittest.main()
