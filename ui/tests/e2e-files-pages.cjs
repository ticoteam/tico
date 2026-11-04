// Journey: bot files and pages, as Dana. The Engineering Lead's pages and files (the pages seed) show in Library > Files
// with their titles; a page opens and draws its title, its version and its status line. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, call, expectFailure, assertClean, drawn, shot, t, SEEDS} = require('./support/e2e.cjs');

journey('e2e-files-pages', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/library/files'});
  for (const title of ['Launch plan', 'Engineering weekly', 'Signup funnel']) {
    await page.locator('.lf-row', {hasText: title}).first().waitFor({timeout: t(20000)});
  }
  assertClean(page, `${where}: files listed`);

  // The bot's data file: its row draws its title, its kind and the bot that made it, and the hub serves its content
  // (a data file has no page view; its row links to the file itself).
  const csv = page.locator('.lf-row', {hasText: 'Signup funnel'}).first();
  assert.match((await csv.innerText()).replace(/\s+/g, ' '), /Data · Engineering Lead/, `${where}: the data file names its kind and bot`);
  const file = await call(hub, 'dana', 'GET', `files/${await csv.getAttribute('data-id')}`);
  assert.equal(file.status, 200, `${where}: the hub serves the data file`);
  assert.match(String(file.body), /landing,1200/, `${where}: the data file's rows are served`);

  // The replaced page ("Engineering weekly (old)") is listed too; the current one has the exact title.
  await page.locator('.lf-row', {has: page.locator('.lf-title', {hasText: /^\s*Engineering weekly\s*$/})}).first()
    .locator('.lf-title, a').first().click();
  const status = (await drawn(page, '#pg-view .pg-status', text => /v\d+/.test(text), `${where}: the page draws its version`))
    .replace(/\s+/g, ' ').trim();
  assert.match(status, /v\d+/, `${where}: the page shows its version`);
  const view = await drawn(page, '#pg-view', text => text.includes('Engineering weekly'), `${where}: the page draws its title`);
  assert.match(view, /Engineering weekly/, `${where}: the page shows its title`);
  const reader = await open(browser, hub, 'priya', {phone, hash: new URL(page.url()).hash});
  await reader.locator('#pg-view .pg-title').waitFor();
  assert.equal(await reader.locator('[data-pg-audience]').count(), 0);
  expectFailure(reader, 404, /^\/api\/v2\/pages\//, 'GET');
  const access = page.locator('[data-pg-audience]');
  await access.waitFor();
  assert.equal((await access.innerText()).trim(), "Everyone who can see where it's filed");
  await access.click();
  const editor = page.locator('#access-editor');
  await editor.locator('[data-access-everyone]').uncheck();
  await editor.locator('[data-access-pick="people"][value="dana"]').check();
  const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && /\/api\/v2\/pages\//.test(r.url()));
  await editor.locator('button[type=submit]').click();
  assert.equal((await saved).status(), 200);
  await page.locator('.pg-status .pg-lock').waitFor();
  await drawn(reader, '#pg-view', text => text.includes('not yours to see'), `${where}: reader loses the open page`);
  assert.equal(await reader.locator('.pg-card').count(), 0);
  await page.locator('[data-pg-audience]').click();
  await editor.locator('[data-access-everyone]').check();
  const cleared = page.waitForResponse(r => r.request().method() === 'PATCH' && /\/api\/v2\/pages\//.test(r.url()));
  await editor.locator('button[type=submit]').click();
  assert.equal((await cleared).status(), 200);
  await drawn(page, '[data-pg-audience]', text => text.includes("Everyone who can see where it's filed"), `${where}: cleared audience`);
  await reader.locator('#pg-view .pg-title').waitFor();
  assert.equal(await reader.locator('[data-pg-audience], .pg-lock').count(), 0);
  assertClean(reader, `${where}: reader regains the page`);
  await reader.context().close();
  assertClean(page, `${where}: page open`);
  await shot(page, `e2e-page-${where}`);

  go(page, '#/library/files');
  await page.locator('.lf-row', {hasText: 'Signup funnel'}).first().waitFor({timeout: t(20000)});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: files page scrolls sideways by ${overflow}px`);
  assertClean(page, `${where}: back on files`);
}, [...SEEDS, 'pages']);
