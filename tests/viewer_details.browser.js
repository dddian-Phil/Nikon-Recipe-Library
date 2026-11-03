// Run with Playwright CLI run-code --filename against either local or public viewer.
async page => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.waitForFunction(() => library.captures.length > 1);
  const options = () => page.locator('#lens option').evaluateAll(nodes => nodes.map(n => n.value).filter(Boolean).sort());
  const expected = await page.evaluate(() => ({
    recipes: [...new Set(rs.map(r => r.lens).filter(Boolean))].sort(),
    photos: [...new Set(library.captures.map(p => p.lens).filter(Boolean))].sort()
  }));
  check(JSON.stringify(await options()) === JSON.stringify(expected.recipes), 'Recipe options include photo-only lenses');
  await page.locator('#filter-menu summary').click();
  await page.locator('#lens').selectOption(expected.recipes[0]);
  await page.locator('#view-photos').click();
  check(JSON.stringify(await options()) === JSON.stringify(expected.photos), 'Photo options include unused lenses');
  check(await page.locator('#lens').inputValue() === '', 'Recipe filter leaked into photos');
  await page.locator('#filter-menu summary').click();
  await page.locator('#lens').selectOption(expected.photos[0]);
  await page.locator('#view-recipes').click();
  check(await page.locator('#lens').inputValue() === expected.recipes[0], 'Recipe filter was lost');
  await page.locator('#view-photos').click();
  check(await page.locator('#lens').inputValue() === expected.photos[0], 'Photo filter was lost');
  await page.locator('#filter-menu summary').click();
  await page.locator('#reset').click();
  const hearts = page.locator('#grid .photo-tile svg.favorite-icon.is-active');
  check(await hearts.count() > 0, 'Liked photos have no vector heart');
  const heart = await hearts.first().evaluate(node => {
    const style = getComputedStyle(node);
    return {color: style.color, fill: style.fill, width: node.getBoundingClientRect().width, hidden: node.getAttribute('aria-hidden')};
  });
  check(heart.color === 'rgb(255, 247, 0)' && heart.fill === heart.color && heart.width === 20 && heart.hidden === 'true', 'Heart style changed across platforms');
  check(!/[♥♡❤]/u.test(await page.locator('body').textContent()), 'Emoji heart remains in the viewer');
  await page.locator('#grid .card').first().click();
  const rows = await page.locator('#photo-detail table').innerText();
  check(rows.includes('快门速度（秒）') && rows.includes('光圈 F 值') && rows.includes('ISO 感光度') && rows.includes(' mm'), 'Photo parameter names or units are missing');
  check(/1\/\d+/.test(rows), 'Shutter is still decimal');
  await page.locator('#photo-detail table').scrollIntoViewIfNeeded();
  await page.screenshot({path: 'output/playwright/details-390.png'});
  await page.locator('#photo-close').click();
  for (const kind of ['photo', 'recipe']) {
    await page.locator(kind === 'photo' ? '#view-photos' : '#view-recipes').click();
    await page.locator('#filter-menu summary').click();
    await page.locator('#reset').click();
    await page.locator('#grid .card').first().click();
    await page.locator('#' + kind + '-next').click();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowLeft');
    const style = await page.locator('#' + kind + '-next').evaluate(node => {
      const css = getComputedStyle(node);
      return {outline: css.outlineStyle, shadow: css.boxShadow, background: css.backgroundColor};
    });
    check(style.outline === 'none' && style.shadow === 'none' && style.background === 'rgba(0, 0, 0, 0)', kind + ' navigation still has a box');
    await page.locator(kind === 'photo' ? '#photo-close' : '#close').click();
  }
  await page.locator('#view-photos').click();
  await page.screenshot({path: 'output/playwright/hearts-390.png'});
  check(errors.length === 0, errors.join('; '));
  return {recipeLenses: expected.recipes.length, photoLenses: expected.photos.length, heart, errors};
}
