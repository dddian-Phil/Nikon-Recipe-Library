// Open a built public gallery, then use Playwright CLI run-code --filename.
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const origin = new URL(page.url()).origin, external = [], failures = [], errors = [], api = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin !== origin) external.push(request.url());
    if (url.pathname.includes('/api/')) api.push(request.url());
  });
  page.on('response', response => { if (response.status() >= 400) failures.push(response.url()); });
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width: 1440, height: 1000});
  await page.reload();
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.evaluate(() => document.fonts.ready);
  const payload = await page.locator('#payload').evaluate(node => JSON.parse(node.textContent));
  check(payload.mode === 'static', 'Expected the read-only public build');
  const client = await page.context().newCDPSession(page);
  await client.send('DOM.enable');
  await client.send('CSS.enable');
  const root = await client.send('DOM.getDocument');
  const node = await client.send('DOM.querySelector', {nodeId: root.root.nodeId, selector: '#view-recipes'});
  const fonts = (await client.send('CSS.getPlatformFontsForNode', {nodeId: node.nodeId})).fonts;
  // Variable fonts may expose their base-style name through the platform API.
  check(fonts.some(font => font.isCustomFont && font.familyName.startsWith('Noto Sans SC')), 'The bundled web font was not used');
  for (const selector of ['.brand', '.library-intro h1']) {
    check(!(await page.locator(selector).evaluate(node => getComputedStyle(node).fontFamily)).includes('Noto Sans SC'), 'Brand or main title changed');
  }
  for (const timezoneId of ['Asia/Shanghai', 'America/Los_Angeles', 'Europe/London']) {
    await client.send('Emulation.setTimezoneOverride', {timezoneId});
    const sameTime = await page.evaluate(() => {
      const local = formatCatalogStamp({syncMetadata: {syncedAt: '2026-10-30T02:01:02+08:00'}});
      const utc = formatCatalogStamp({report: {finishedAt: '2026-10-29T18:01:02Z'}});
      return local === utc && local.includes('2026\u5e7410\u670830\u65e5 02:01') && local.includes('\u5317\u4eac\u65f6\u95f4');
    });
    check(sameTime, 'Timestamp depends on visitor timezone');
  }
  await client.send('Emulation.setTimezoneOverride', {timezoneId: 'Asia/Shanghai'});
  const stamp = await page.locator('#stamp').textContent();
  check(!/T\d{2}:\d{2}/.test(stamp) && stamp.includes('\u5317\u4eac\u65f6\u95f4'), 'Raw or ambiguous timestamp remains');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({width, height: 900});
    await page.locator('#view-recipes').click();
    await page.evaluate(() => scrollTo(0, 0));
    await page.locator('#grid img').first().evaluate(img => img.decode());
    const colors = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > innerWidth,
      same: [...document.querySelectorAll('#grid .card img')].every(img =>
        getComputedStyle(img).backgroundColor === getComputedStyle(document.body).backgroundColor)
    }));
    check(colors.same && !colors.overflow, 'Image backdrop or layout mismatch at ' + width);
    await page.screenshot({path: 'output/playwright/v2-refresh-' + width + '.png'});
    await page.locator('#filter-menu > summary').click();
    check(await page.locator('.tools').evaluate(node => {
      const box = node.getBoundingClientRect();
      return box.x >= 0 && box.right <= innerWidth &&
        node.contains(document.elementFromPoint(box.x + 25, Math.min(box.bottom - 20, innerHeight - 20)));
    }), 'Filter menu is clipped or covered');
    await page.keyboard.press('Escape');
    await page.locator('#grid .card').first().click();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path: 'output/playwright/v2-refresh-detail-' + width + '.png'});
    await page.locator('#close').click();
    await page.locator('#view-photos').click();
    check(await page.locator('#grid .card').count() === payload.photoLibrary.captures.length, 'Published photos changed');
    check(await page.locator('#grid button.favorite-action').count() === 0, 'Writable favorites exposed');
    await page.locator('footer').scrollIntoViewIfNeeded();
    await page.locator('footer').screenshot({path: 'output/playwright/v2-refresh-footer-' + width + '.png'});
  }
  check(!external.length && !failures.length && !errors.length && !api.length, JSON.stringify({external, failures, errors, api}));
  const fontRequests = await page.evaluate(() => performance.getEntriesByType('resource').filter(entry => entry.name.endsWith('.woff2')).length);
  check(fontRequests > 0, 'No self-hosted fonts requested');
  return {recipes: payload.recipes.length, photos: payload.photoLibrary.captures.length, fonts, fontRequests, stamp, external, failures, errors, api};
}
