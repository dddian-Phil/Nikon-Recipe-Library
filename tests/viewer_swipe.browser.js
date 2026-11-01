// Run with Playwright CLI run-code --filename after opening the local or static library.
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width: 390, height: 844});
  const touch = await page.context().newCDPSession(page);
  await touch.send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5});
  const isStatic = await page.locator('#payload').evaluate(node => JSON.parse(node.textContent).mode === 'static');
  if (!isStatic) {
    // Keep local photo tests deterministic without reading or changing private records.
    const recipes = await page.locator('#payload').evaluate(node => JSON.parse(node.textContent).recipes.slice(0, 3));
    const captures = recipes.map((recipe, index) => ({
      id: 'swipe-fixture-' + index, name: 'Touch fixture ' + index,
      recipeId: recipe.id, autoRecipeId: recipe.id, association: 'auto',
      preview: recipe.image, camera: recipe.camera, lens: recipe.lens,
      pictureControlName: recipe.pictureControlName, pictureControlBase: recipe.pictureControl.baseName,
      takenAt: '', favorite: false, files: [{kind: 'JPG', path: 'fixture-' + index + '.jpg'}]
    }));
    await page.route(new URL('/api/state', page.url()).href, route => route.fulfill({
      json: {captures, records: {recipeFavorites: [], photoFavorites: [], photoAssignments: {}},
        summary: {files: 3, photos: 3, matched: 3, favoritePhotos: 0},
        revision: ['swipe-fixture', 1], scanning: false, scanError: ''}
    }));
  }
  await page.reload();
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#grid .card').length > 1);
  if (!isStatic) await page.waitForFunction(() => document.querySelector('#view-photos').textContent.includes('(') || document.querySelector('#view-photos').textContent.includes('\uff08'));
  const output = 'output/playwright/' + (isStatic ? 'static' : 'local');
  const nextFrame = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  async function swipe(target, dx, dy = 0, {cancel = false, twoFingers = false} = {}) {
    const box = await target.boundingBox();
    check(box, 'Missing gesture target');
    const x = box.x + box.width / 2 - dx / 2;
    const y = Math.max(box.y + 25, Math.min(box.y + box.height / 2 - dy / 2, 650));
    const point = (fraction, id = 1) => ({
      x: x + dx * fraction + (id === 2 ? 35 : 0),
      y: y + dy * fraction, id
    });
    await touch.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [point(0)]});
    if (twoFingers) await touch.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [point(0), point(0, 2)]});
    for (let step = 1; step <= 8; step++) {
      const points = [point(step / 8)];
      if (twoFingers) points.push(point(step / 8, 2));
      await touch.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: points});
      await nextFrame();
    }
    await touch.send('Input.dispatchTouchEvent', {type: cancel ? 'touchCancel' : 'touchEnd', touchPoints: []});
    await nextFrame();
  }
  async function tap(target) {
    // Let native scrolling settle and keep intentional taps outside the double-tap window.
    await page.waitForTimeout(400);
    const box = await target.boundingBox();
    await touch.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: box.x + box.width / 2, y: box.y + box.height / 2, id: 1}]});
    await touch.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
    await page.waitForTimeout(400);
  }
  async function verify(kind, focused) {
    const dialog = page.locator(kind === 'recipe' ? '#modal' : '#photo-modal');
    const image = dialog.locator('.detailimage img');
    const position = page.locator('#' + kind + '-position');
    const focus = page.locator('#' + kind + '-focus');
    const close = page.locator(kind === 'recipe' ? '#close' : '#photo-close');
    const isFocused = async () => (await dialog.getAttribute('class') || '').includes('is-focused');
    const initial = await position.textContent();
    await image.evaluate(img => img.decode());
    if (focused) await focus.click();
    check(await image.evaluate(img => getComputedStyle(img.parentElement).touchAction) === 'pan-y pinch-zoom', kind + ': missing touch policy');
    await swipe(image, -150);
    check((await position.textContent()).startsWith('2 /'), kind + ': left swipe did not go forward');
    check(await isFocused() === focused, kind + ': swipe changed viewing mode');
    await swipe(image, 150);
    check(await position.textContent() === initial, kind + ': right swipe did not go back');
    await swipe(image, 150);
    check(await position.textContent() === initial, kind + ': moved before first photo');
    check(await isFocused() === focused, kind + ': edge swipe changed viewing mode');
    await swipe(image, -24);
    check(await position.textContent() === initial, kind + ': short drag changed image');
    check(await isFocused() === focused, kind + ': short drag changed viewing mode');
    await swipe(image, -20, -110);
    check(await position.textContent() === initial, kind + ': vertical scroll changed image');
    await dialog.evaluate(node => { node.scrollTop = 0; });
    await swipe(image, -150, 0, {cancel: true});
    check(await position.textContent() === initial, kind + ': cancelled gesture changed image');
    await swipe(image, -130, 0, {twoFingers: true});
    check(await position.textContent() === initial, kind + ': two-finger gesture changed image');
    await touch.send('Emulation.setPageScaleFactor', {pageScaleFactor: 1.5});
    await swipe(image, -110);
    check(await position.textContent() === initial, kind + ': zoomed image pan changed image');
    await touch.send('Emulation.setPageScaleFactor', {pageScaleFactor: 1});
    await dialog.evaluate(node => { node.scrollTop = 0; });
    const imageBox = await image.boundingBox();
    await page.mouse.move(imageBox.x + 200, imageBox.y + 80);
    await page.mouse.down();
    await page.mouse.move(imageBox.x + 40, imageBox.y + 80, {steps: 6});
    await page.mouse.up();
    check(await position.textContent() === initial, kind + ': mouse drag changed image');
    if (await isFocused() !== focused) await focus.click();
    if (focused) {
      await swipe(page.locator('#' + kind + '-strip'), -120);
      check(await position.textContent() === initial, kind + ': thumbnail scrolling changed image');
    }
    await tap(image);
    check(await isFocused() !== focused, kind + ': ordinary tap no longer changes mode');
    await tap(image);
    check(await isFocused() === focused, kind + ': ordinary tap no longer restores mode');
    await swipe(image, -150);
    await page.screenshot({path: output + '-' + kind + (focused ? '-focus' : '-detail') + '-swipe.png'});
    await close.click();
    await page.locator('#grid .card').first().click();
    if (focused) await focus.click();
    await swipe(image, -150);
    check((await position.textContent()).startsWith('2 /'), kind + ': swipe stopped after reopening');
    await close.click();
    await page.locator('#grid .card').last().click();
    if (focused) await focus.click();
    const last = await position.textContent();
    await swipe(image, -150);
    check(await position.textContent() === last, kind + ': moved past last photo');
    check(await isFocused() === focused, kind + ': final swipe changed viewing mode');
    await close.click();
  }
  for (const focused of [false, true]) {
    await page.locator('#grid .card').first().click();
    await verify('recipe', focused);
  }
  await page.locator('#view-photos').click();
  const photos = await page.locator('#grid .card').count();
  check(photos > 1, 'Photo swipe coverage requires at least two indexed or published photos');
  for (const focused of [false, true]) {
    await page.locator('#grid .card').first().click();
    if (!isStatic) check(await page.locator('.source-files h3').textContent() === '\u539f\u6587\u4ef6', 'Original-files heading still has parenthetical text');
    await verify('photo', focused);
  }
  await page.locator('#q').fill(await page.locator('#grid h3').first().textContent());
  const filtered = await page.locator('#grid .card').count();
  await page.locator('#grid .card').first().click();
  await page.locator('#photo-focus').click();
  check((await page.locator('#photo-position').textContent()).endsWith('/ ' + filtered), 'Swipe queue ignores current filters');
  await page.locator('#photo-close').click();
  await page.locator('#q').fill('');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({width, height: 900});
    for (const kind of ['recipe', 'photo']) {
      await page.locator(kind === 'recipe' ? '#view-recipes' : '#view-photos').click();
      await page.locator('#grid .card').first().click();
      const dialog = page.locator(kind === 'recipe' ? '#modal' : '#photo-modal');
      const nav = dialog.locator('.viewer-topnav');
      await page.locator('#' + kind + '-next').click();
      await page.mouse.move(0, 0);
      const layout = await nav.evaluate(node => ({
        centers: [...node.querySelectorAll('svg,.viewer-position')].map(item => {
          const box = item.getBoundingClientRect();return box.y + box.height / 2;
        }),
        buttons: [...node.querySelectorAll('button')].map(button => ({
          outline: getComputedStyle(button).outlineStyle,
          shadow: getComputedStyle(button).boxShadow
        }))
      }));
      check(layout.centers.length === 3 && Math.max(...layout.centers) - Math.min(...layout.centers) < 1, 'Arrow and counter alignment: ' + JSON.stringify(layout));
      check(layout.buttons.every(button => button.outline === 'none' && button.shadow === 'none'), 'Pointer navigation has a focus highlight');
      check(await dialog.locator('.dialogtop').evaluate(node => node.scrollWidth <= node.clientWidth), 'Toolbar overflows at ' + width);
      const closeStyle = await dialog.locator('.viewer-close').evaluate(button => {
        const box = button.getBoundingClientRect(), icon = button.querySelector('svg').getBoundingClientRect(), style = getComputedStyle(button);
        return {width: box.width, height: box.height, iconWidth: icon.width, iconHeight: icon.height,
          text: button.textContent.trim(), label: button.getAttribute('aria-label'),
          background: style.backgroundColor, border: style.borderWidth,
          dx: (icon.x + icon.width / 2) - (box.x + box.width / 2),
          dy: (icon.y + icon.height / 2) - (box.y + box.height / 2)};
      });
      check(closeStyle.width === 44 && closeStyle.height === 44, 'Close target is not 44px: ' + JSON.stringify(closeStyle));
      check(closeStyle.iconWidth === 18 && closeStyle.iconHeight === 18 && Math.abs(closeStyle.dx) < 1 && Math.abs(closeStyle.dy) < 1, 'Close icon is not centered');
      check(closeStyle.text === '' && closeStyle.label, 'Close icon lacks an accessible name or still shows text');
      check(closeStyle.background === 'rgba(0, 0, 0, 0)' && closeStyle.border === '0px', 'Close button has a persistent background or border');
      await dialog.locator('.dialogtop').screenshot({path: output + '-' + kind + '-nav-' + width + '.png'});
      await page.keyboard.press('Shift+Tab');
      const keyboardFocus = await page.evaluate(() => ({
        nav: !!document.activeElement.closest('.viewer-topnav'),
        outline: getComputedStyle(document.activeElement).outlineStyle,
        shadow: getComputedStyle(document.activeElement).boxShadow
      }));
      check(keyboardFocus.nav && keyboardFocus.outline === 'none' && keyboardFocus.shadow !== 'none', 'Keyboard focus lost its quiet indicator');
      await page.locator(kind === 'recipe' ? '#close' : '#photo-close').click();
    }
  }
  check(errors.length === 0, 'Browser errors: ' + errors.join(', '));
  return {mode: isStatic ? 'static' : 'local', photos, filtered, errors, modes: ['detail', 'focus'], navigationWidths: [1440, 390, 320], checks: 'touch swipes, boundaries, vertical scroll, multitouch, zoom, cancellation, taps, filtered queue, aligned icons, no pointer highlight, keyboard focus'};
}
