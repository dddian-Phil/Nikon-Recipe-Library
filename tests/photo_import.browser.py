"""Run against an isolated library using installed Chrome and optional Playwright.

PYTHONPATH=local/browser-test-tools .venv/bin/python -B tests/photo_import.browser.py
"""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_catalog_sync import FakeOfficial, seed
from test_photo_import import jpeg, jpeg_with_previews


def run():
    output = ROOT / 'output/playwright'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'local/test-runs', prefix='jpeg-browser-') as temporary:
        workspace = Path(temporary)
        (workspace / 'app').mkdir()
        for source in (ROOT / 'app').iterdir():
            if source.is_file() and source.name != 'viewer.html':
                shutil.copy2(source, workspace / 'app' / source.name)
        seed(workspace, FakeOfficial())
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        with (workspace / 'server.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-B', str(workspace / 'app/library_server.py'),
                                        '--port', str(port)], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(url + '/api/health', timeout=1):
                            break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError((workspace / 'server.log').read_text())
                        time.sleep(.1)
                else:
                    raise RuntimeError('Test server did not start')
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='chrome', headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1440, 'height': 1040})
                        errors, requests, checks = [], [], []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.on('request', lambda request: requests.append(request.url) if '/api/photos?' in request.url else None)
                        page.goto(url)
                        page.wait_for_function('Boolean(library.photoImport)')
                        assert not page.locator('#photo-import').is_visible()
                        page.locator('#view-photos').click()
                        assert page.locator('#photo-import').is_visible()
                        assert page.locator('#photo-import').inner_text().strip() == '添加照片\n删除照片'
                        for selector in ('#add-photos', '#delete-photos'):
                            assert page.locator(selector + ' svg').count() == 1
                            assert page.locator(selector + ' svg').get_attribute('aria-hidden') == 'true'
                            assert page.locator(selector).bounding_box()['height'] >= 44
                        assert page.locator('#delete-photos').is_disabled()
                        assert page.locator('#photo-files').get_attribute('accept') == '.jpg,.jpeg,image/jpeg'
                        assert 'NEF' not in page.locator('#photo-import').inner_text()
                        assert '添加照片' in page.locator('#empty').inner_text()
                        checks.append('JPEG-only controls and empty state')
                        records_before = (workspace / 'local/personal-records.json').read_bytes()

                        def choose(files):
                            with page.expect_file_chooser() as event:
                                page.locator('#add-photos').click()
                            event.value.set_files(files)
                            page.wait_for_function("!importingPhotos && document.getElementById('photo-import-status').textContent.startsWith('已添加')")
                            assert page.locator('#photo-files').input_value() == ''
                            assert page.locator('#add-photos').is_enabled()
                            assert page.locator('#add-photos svg').count() == 1
                            assert page.locator('#add-photos-label').inner_text() == '添加照片'

                        def file(name, color='green'):
                            return {'name': name, 'mimeType': 'image/jpeg', 'buffer': jpeg(color)}

                        choose([file('我的实拍.JPEG')])
                        page.wait_for_function('library.captures.length === 1')
                        assert (workspace / 'photos/我的实拍.JPEG').read_bytes() == jpeg()
                        assert page.locator('#grid .photo-tile').count() == 1
                        checks.append('Native file chooser, EXIF preservation and automatic scan')
                        choose([file('重复.jpg')])
                        assert '1 个已存在' in page.locator('#photo-import-status').inner_text()
                        assert len(list((workspace / 'photos').iterdir())) == 1
                        checks.append('Duplicate content skipped')
                        choose([file('我的实拍.JPEG', 'blue')])
                        page.wait_for_function('library.captures.length === 2')
                        assert (workspace / 'photos/我的实拍 (2).JPEG').read_bytes() == jpeg('blue')
                        assert (workspace / 'photos/我的实拍.JPEG').read_bytes() == jpeg()
                        checks.append('Same name collision keeps both originals')
                        choose([{'name': '损坏.jpg', 'mimeType': 'image/jpeg', 'buffer': b'broken'}, file('新照片.jpg', 'red')])
                        assert '已添加 1 个文件，1 个未添加' in page.locator('#photo-import-status').inner_text()
                        assert '损坏.jpg' in page.locator('#photo-import-errors').inner_text()
                        page.wait_for_function('library.captures.length === 3')
                        checks.append('Partial batch failure continues and identifies failed file')
                        count = len(requests)
                        choose([{'name': 'raw.NEF', 'mimeType': 'application/octet-stream', 'buffer': b'raw'},
                                {'name': 'photo.png', 'mimeType': 'image/png', 'buffer': b'png'}])
                        assert len(requests) == count
                        assert '2 个未添加' in page.locator('#photo-import-status').inner_text()
                        checks.append('NEF and PNG blocked before upload')

                        page.locator('#photo-import').scroll_into_view_if_needed()
                        page.evaluate("""() => {
                            window.testPhotoTransfer = new DataTransfer();
                            testPhotoTransfer.items.add(new File(['test'], 'test.jpg', {type:'image/jpeg'}));
                            const zone = document.getElementById('photo-import');
                            const icon = document.querySelector('#add-photos svg');
                            for (const target of [zone, icon]) target.dispatchEvent(new DragEvent('dragenter', {bubbles:true,dataTransfer:testPhotoTransfer}));
                            icon.dispatchEvent(new DragEvent('dragleave', {bubbles:true,dataTransfer:testPhotoTransfer}));
                        }""")
                        assert page.locator('#photo-import').evaluate("node=>node.classList.contains('is-dragging')")
                        assert page.locator('#add-photos-label').inner_text() == '松开添加'
                        page.screenshot(path=str(output / 'jpeg-drag-desktop.png'), animations='disabled')
                        page.evaluate("document.getElementById('photo-import').dispatchEvent(new DragEvent('dragleave',{bubbles:true,dataTransfer:testPhotoTransfer}))")
                        assert not page.locator('#photo-import').evaluate("node=>node.classList.contains('is-dragging')")
                        assert page.locator('#add-photos-label').inner_text() == '添加照片'
                        page.evaluate("""() => {
                            document.getElementById('photo-import').dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:testPhotoTransfer}));
                            document.dispatchEvent(new DragEvent('dragend'));
                        }""")
                        assert not page.locator('#photo-import').evaluate("node=>node.classList.contains('is-dragging')")
                        checks.append('Drag target highlights on entry, stays active over icons and resets after leaving/cancel')

                        page.locator('#q').fill('找不到的照片')
                        page.evaluate("""bytes => {
                            const transfer = new DataTransfer();
                            transfer.items.add(new File([new Uint8Array(bytes)], '拖入.jpg', {type:'image/jpeg'}));
                            const zone = document.getElementById('photo-import');
                            zone.dispatchEvent(new DragEvent('dragenter', {bubbles:true,dataTransfer:transfer}));
                            if (!zone.classList.contains('is-dragging')) throw Error('Drop highlight missing');
                            zone.dispatchEvent(new DragEvent('drop', {bubbles:true,dataTransfer:transfer}));
                        }""", list(jpeg('yellow')))
                        page.wait_for_function("!importingPhotos && document.getElementById('photo-import-status').textContent.includes('清除当前筛选')")
                        assert page.locator('#q').input_value() == '找不到的照片'
                        page.locator('#q').fill('')
                        page.wait_for_function('library.captures.length === 4')
                        assert page.locator('#add-photos-label').inner_text() == '添加照片'
                        assert not page.locator('#photo-import').evaluate("node=>node.classList.contains('is-dragging')")
                        checks.append('Drag and drop with retained filter and visible guidance')
                        assert (workspace / 'local/personal-records.json').read_bytes() == records_before
                        page.locator('#grid .card').first.click()
                        assert 'NIKON TEST CAMERA' in page.locator('#photo-detail').inner_text()
                        page.locator('#photo-close').click()
                        page.locator('#grid .favorite-action').first.click()
                        page.wait_for_function('library.records.photoFavorites.length === 1')
                        assert page.locator('#add-photos').is_enabled()
                        assert page.locator('#delete-photos').is_enabled()
                        checks.append('Details and likes still work; capability survives record updates')
                        assert page.evaluate("document.getElementById('grid').compareDocumentPosition(document.getElementById('photo-import')) & Node.DOCUMENT_POSITION_FOLLOWING")
                        assert page.locator('#photo-import').bounding_box()['y'] >= page.locator('#grid').bounding_box()['y'] + page.locator('#grid').bounding_box()['height']
                        assert page.locator('#grid .photo-delete').count() == 0
                        assert page.locator('#grid .card[aria-pressed]').count() == 0
                        checks.append('Minimal add/delete toolbar follows the gallery; no card delete controls')

                        liked = page.evaluate('library.captures.find(photo=>photo.favorite)')
                        records_before_delete = (workspace / 'local/personal-records.json').read_bytes()
                        photo_selector = '[data-photo-id="' + liked['id'] + '"]'
                        page.locator('#delete-photos').click()
                        assert page.locator('#delete-selected-photos').is_disabled()
                        assert not page.locator('#add-photos').is_visible()
                        count = len(requests)
                        page.evaluate("""() => {
                            const zone=document.getElementById('photo-import');
                            zone.dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:testPhotoTransfer}));
                            zone.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:testPhotoTransfer}));
                        }""")
                        assert len(requests) == count
                        assert not page.locator('#photo-import').evaluate("node=>node.classList.contains('is-dragging')")
                        page.locator(photo_selector + ' .card').click()
                        assert not page.locator('#photo-modal').is_visible()
                        assert page.locator(photo_selector + ' .card').get_attribute('aria-pressed') == 'true'
                        page.locator('#delete-selected-photos').click()
                        assert page.locator('#photo-delete-dialog').is_visible()
                        assert page.locator('#cancel-photo-delete').evaluate('node=>node===document.activeElement')
                        assert liked['name'] == page.locator('#photo-delete-name').inner_text()
                        page.locator('#cancel-photo-delete').click()
                        assert page.locator('#grid .photo-tile').count() == 4
                        assert (workspace / 'photos' / liked['files'][0]['path']).exists()
                        checks.append('Delete confirmation identifies photo; cancel keeps it')

                        page.locator('#delete-selected-photos').click()
                        page.keyboard.press('ArrowRight')
                        assert page.locator('#photo-delete-name').inner_text() == liked['name']
                        page.keyboard.press('Escape')
                        assert not page.locator('#photo-delete-dialog').is_visible()
                        assert page.locator('#cancel-photo-selection').is_visible()
                        page.locator('#delete-selected-photos').click()
                        page.route('**/api/photos/delete', lambda route: route.fulfill(status=409, content_type='application/json', body='{"error":"正在发布网页，请稍后再试。"}'))
                        page.locator('#confirm-photo-delete').click()
                        page.wait_for_function("!deletingPhoto && !document.getElementById('photo-delete-error').hidden")
                        assert '正在发布网页' in page.locator('#photo-delete-error').inner_text()
                        assert page.locator('#photo-delete-dialog').is_visible()
                        assert page.locator('#confirm-photo-delete').is_enabled()
                        page.unroute('**/api/photos/delete')
                        page.set_viewport_size({'width': 390, 'height': 844})
                        page.screenshot(path=str(output / 'jpeg-delete-confirm-390.png'))
                        page.locator('#confirm-photo-delete').click()
                        page.wait_for_function('!deletingPhoto && library.captures.length === 3')
                        assert not page.locator('#photo-modal').is_visible()
                        assert not page.locator('#photo-delete-dialog').is_visible()
                        assert not (workspace / 'photos' / liked['files'][0]['path']).exists()
                        assert page.locator('#photo-delete-feedback').is_visible()
                        assert (workspace / 'local/personal-records.json').read_bytes() == records_before_delete
                        checks.append('Selection deletion handles cancel, Escape and retry without changing personal records')

                        page.reload()
                        page.wait_for_function('Boolean(library.photoDelete)')
                        page.locator('#view-photos').click()
                        assert page.locator('#undo-photo-delete').is_visible()
                        page.locator('#undo-photo-delete').click()
                        page.wait_for_function('!undoingPhotoDelete && library.captures.length === 4')
                        assert page.evaluate('library.captures.some(photo=>photo.id===library.records.photoFavorites[0] && photo.favorite)')
                        assert (workspace / 'photos' / liked['files'][0]['path']).exists()
                        assert not page.locator('#photo-delete-feedback').is_visible()
                        checks.append('Undo survives page reload and restores the photo with its like')

                        page.locator('#delete-photos').click()
                        page.locator('#select-all-photos').click()
                        assert page.locator('#delete-selected-photos').inner_text() == '删除所选（4）'
                        page.locator('#q').fill('拖入')
                        assert page.locator('#delete-selected-photos').inner_text() == '删除所选（1）'
                        page.locator('#select-all-photos').click()
                        assert page.locator('#delete-selected-photos').is_disabled()
                        page.locator('#select-all-photos').click()
                        page.locator('#q').fill('')
                        assert page.locator('#delete-selected-photos').inner_text() == '删除所选（1）'
                        page.locator('#view-recipes').click()
                        page.locator('#view-photos').click()
                        assert page.locator('#delete-photos').is_visible()
                        page.locator('#grid .card').first.click()
                        assert page.locator('#photo-modal').is_visible()
                        assert page.locator('#photo-detail .photo-delete').count() == 0
                        page.locator('#photo-close').click()
                        page.locator('#delete-photos').click()
                        page.locator('#grid .card').first.focus()
                        page.keyboard.press('Space')
                        assert page.locator('#delete-selected-photos').inner_text() == '删除所选（1）'
                        page.keyboard.press('Escape')
                        assert page.locator('#grid .card[aria-pressed]').count() == 0
                        checks.append('Selections track visible filters, reset between panes, and support keyboard/cancel')

                        def select_first_two():
                            page.locator('#delete-photos').click()
                            page.locator('#grid .card').nth(0).click()
                            page.locator('#grid .card').nth(1).click()
                            page.locator('#delete-selected-photos').click()
                            assert page.locator('#photo-delete-title').inner_text() == '删除这 2 张实拍？'

                        select_first_two()
                        attempts = []

                        def fail_second_delete(route):
                            attempts.append(route.request.post_data_json['id'])
                            if len(attempts) == 2:
                                route.fulfill(status=409, content_type='application/json', body='{"error":"测试：第二张未删除"}')
                            else:
                                route.continue_()

                        page.route('**/api/photos/delete', fail_second_delete)
                        page.locator('#confirm-photo-delete').click()
                        page.wait_for_function("!deletingPhoto && !document.getElementById('photo-delete-error').hidden")
                        assert page.evaluate('library.captures.length') == 3
                        assert '已删除 1 张，其余未删除' in page.locator('#photo-delete-error').inner_text()
                        assert page.evaluate('recentPhotoDelete.length') == 1
                        page.unroute('**/api/photos/delete')
                        page.locator('#confirm-photo-delete').click()
                        page.wait_for_function('!deletingPhoto && library.captures.length === 2')
                        assert page.evaluate('recentPhotoDelete.length') == 2
                        assert (workspace / 'local/personal-records.json').read_bytes() == records_before_delete
                        checks.append('Batch retry deletes only remaining photos and retains all undo tokens')

                        restores = []

                        def fail_second_restore(route):
                            restores.append(route.request.post_data_json['token'])
                            if len(restores) == 2:
                                route.fulfill(status=409, content_type='application/json', body='{"error":"测试：恢复冲突"}')
                            else:
                                route.continue_()

                        page.route('**/api/photos/restore', fail_second_restore)
                        page.locator('#undo-photo-delete').click()
                        page.wait_for_function("!undoingPhotoDelete && document.getElementById('photo-delete-status').textContent.includes('恢复失败')")
                        assert page.evaluate('library.captures.length') == 3
                        assert page.evaluate('recentPhotoDelete.length') == 1
                        page.evaluate('refresh()')
                        assert '恢复失败' in page.locator('#photo-delete-status').inner_text()
                        page.unroute('**/api/photos/restore')
                        page.reload()
                        page.wait_for_function('Boolean(library.photoDelete)')
                        page.locator('#view-photos').click()
                        page.locator('#undo-photo-delete').click()
                        page.wait_for_function('!undoingPhotoDelete && library.captures.length === 4')
                        checks.append('Partial undo preserves unrestored photos across reload and retry')

                        page.locator('#toast').wait_for(state='hidden')
                        page.set_viewport_size({'width': 1440, 'height': 1040})
                        page.evaluate('async()=>{scrollTo(0,0);await new Promise(requestAnimationFrame)}')
                        page.screenshot(path=str(output / 'jpeg-import-desktop.png'))
                        page.locator('#photo-import').scroll_into_view_if_needed()
                        page.screenshot(path=str(output / 'jpeg-import-desktop-bottom.png'))

                        def check_selection_geometry():
                            for tile in page.locator('#grid .photo-tile').all():
                                image = tile.locator('img').bounding_box()
                                marker = tile.locator('.photo-selection-mark').bounding_box()
                                title = tile.locator('h3').bounding_box()
                                assert marker['y'] >= image['y'] + image['height'] + 8
                                assert marker['x'] >= title['x']
                                assert marker['x'] + marker['width'] <= title['x'] + title['width']
                                assert marker['y'] + marker['height'] <= title['y'] + title['height'] + 1
                                assert tile.locator('img').evaluate("node=>getComputedStyle(node).outlineStyle") == 'none'
                                assert tile.locator('.card').evaluate("node=>getComputedStyle(node,'::after').content") == 'none'

                        page.locator('#delete-photos').click()
                        page.locator('#select-all-photos').click()
                        check_selection_geometry()
                        page.screenshot(path=str(output / 'jpeg-select-desktop.png'), animations='disabled')
                        page.locator('#cancel-photo-selection').click()
                        for width in (390, 320):
                            page.set_viewport_size({'width': width, 'height': 844})
                            assert page.locator('#add-photos').is_visible()
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            assert page.locator('#photo-import').bounding_box()['y'] >= page.locator('#grid').bounding_box()['y'] + page.locator('#grid').bounding_box()['height']
                            page.evaluate('async()=>{scrollTo(0,0);await new Promise(requestAnimationFrame)}')
                            page.screenshot(path=str(output / f'jpeg-import-{width}.png'))
                            page.locator('#photo-import').scroll_into_view_if_needed()
                            page.screenshot(path=str(output / f'jpeg-import-{width}-bottom.png'))
                            page.locator('#delete-photos').click()
                            page.locator('#grid .card').first.click()
                            check_selection_geometry()
                            page.evaluate("toast('已恢复 1 张照片')")
                            page.screenshot(path=str(output / f'jpeg-select-{width}.png'), animations='disabled')
                            bounds = page.locator('#photo-import').bounding_box()
                            assert 0 <= bounds['y'] < 844 - bounds['height'] + 1
                            toast_bounds = page.locator('#toast').bounding_box()
                            assert toast_bounds['y'] + toast_bounds['height'] < bounds['y']
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            page.locator('#cancel-photo-selection').click()
                            page.locator('#toast').wait_for(state='hidden')
                            page.locator('#density-compact').click()
                            page.locator('#delete-photos').click()
                            page.locator('#select-all-photos').click()
                            check_selection_geometry()
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            page.screenshot(path=str(output / f'jpeg-select-compact-{width}.png'), animations='disabled')
                            page.locator('#cancel-photo-selection').click()
                            page.locator('#density-comfort').click()
                        checks.append('Desktop, 390px and 320px layout without overflow')
                        checks.append('Selection marks stay below images in both densities; images have no overlay or selection outline')
                        page.locator('#delete-photos').click()
                        page.locator('#select-all-photos').click()
                        page.locator('#delete-selected-photos').click()
                        page.locator('#confirm-photo-delete').click()
                        page.wait_for_function('!deletingPhoto && library.captures.length===0')
                        assert page.locator('#empty').is_visible()
                        assert '下方' in page.locator('#empty').inner_text()
                        assert page.locator('#add-photos').is_enabled()
                        assert page.locator('#delete-photos').is_disabled()
                        page.locator('#undo-photo-delete').click()
                        page.wait_for_function('!undoingPhotoDelete && library.captures.length===4')
                        checks.append('Delete all leaves add available and one undo restores the entire batch')
                        page.evaluate("""bytes => {
                            const transfer = new DataTransfer();
                            transfer.items.add(new File([new Uint8Array(bytes)], '相机多图.JPG', {type:'image/jpeg'}));
                            const zone = document.getElementById('photo-import');
                            zone.dispatchEvent(new DragEvent('dragenter', {bubbles:true,dataTransfer:transfer}));
                            zone.dispatchEvent(new DragEvent('drop', {bubbles:true,dataTransfer:transfer}));
                        }""", list(jpeg_with_previews()))
                        page.wait_for_function('!importingPhotos && library.captures.length===5')
                        assert (workspace / 'photos/相机多图.JPG').read_bytes() == jpeg_with_previews()
                        mpf_photo = page.evaluate("library.captures.find(photo=>photo.files.some(file=>file.path==='相机多图.JPG'))")
                        assert mpf_photo['preview']
                        page.locator('[data-photo-id="' + mpf_photo['id'] + '"] .card').click()
                        assert 'NIKON TEST CAMERA' in page.locator('#photo-detail').inner_text()
                        page.locator('#photo-close').click()
                        checks.append('Camera JPEG with MPF previews imports by drag/drop with exact bytes, metadata and preview')
                        count = len(requests)
                        page.evaluate("data.mode='static'; update(); addPhotos([new File(['raw'],'raw.NEF')])")
                        assert not page.locator('#photo-import').is_visible()
                        assert page.locator('#grid .photo-delete').count() == 0
                        assert not page.locator('#photo-delete-feedback').is_visible()
                        assert len(requests) == count
                        checks.append('Static mode hides and disables local import')
                        assert not errors, errors
                        result = {'checks': checks, 'pageErrors': errors, 'uploadRequests': len(requests)}
                        (output / 'jpeg-import-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                    finally:
                        browser.close()
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    run()
