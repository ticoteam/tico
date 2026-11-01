// Journey: bot files and pages, as Dana. The Engineering Lead's pages and files (the pages seed) show in Library > Files
// with their titles; a page opens and draws its title, its version and its status line. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, assertClean, expectError, shot, t, SEEDS} = require('./support/e2e.cjs');

journey('e2e-files-pages', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/library/files'});
  // Known bug, reported and not fixed here: opening a bot page raises an uncaught SecurityError (a read of
  // navigator.serviceWorker from the page view's sandboxed frame). It shows once a page is opened.
  expectError(page, /Service worker is disabled because the context is sandboxed/, 'page view sandbox serviceWorker read');
  for (const title of ['Launch plan', 'Engineering weekly', 'Signup funnel']) {
    await page.locator('.lf-row', {hasText: title}).first().waitFor({timeout: t(20000)});
  }
  assertClean(page, `${where}: files listed`);

  // The replaced page ("Engineering weekly (old)") is listed too; the current one has the exact title.
  await page.locator('.lf-row', {has: page.locator('.lf-title', {hasText: /^\s*Engineering weekly\s*$/})}).first()
    .locator('.lf-title, a').first().click();
  const view = page.locator('#pg-view');
  await view.locator('.pg-status').waitFor({timeout: t(20000)});
  const status = (await view.locator('.pg-status').innerText()).replace(/\s+/g, ' ').trim();
  assert.match(status, /v\d+/, `${where}: the page shows its version`);
  assert.match(await view.innerText(), /Engineering weekly/, `${where}: the page shows its title`);
  assertClean(page, `${where}: page open`);
  await shot(page, `e2e-page-${where}`);

  go(page, '#/library/files');
  await page.locator('.lf-row', {hasText: 'Signup funnel'}).first().waitFor({timeout: t(20000)});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: files page scrolls sideways by ${overflow}px`);
  assertClean(page, `${where}: back on files`);
}, [...SEEDS, 'pages']);
