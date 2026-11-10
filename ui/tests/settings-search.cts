import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// Settings search (ui/app/settings-search.ts): the box above the Settings tabs asks the settings catalogue
// (GET /v2/settings/catalog), lists each match by its name, and opens the match's card: the tab, scrolled to it.
// A slower answer to older words never replaces a newer one; Escape clears; arrows and Enter pick; a member is not
// offered an admin's or the owner's card. Fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

type Hit = {key: string; name: string; where: string; who: string; card: string};
const CATALOG: (Hit & {words: string})[] = [
  {key: 'team.storage.server', name: 'Storage', where: 'Settings > General > Storage', who: 'owner', card: 'https://tico.example/#/settings/general/settings-storage', words: 'files disk s3'},
  {key: 'computer.sleep', name: 'Sleep when idle', where: 'Settings > Computers > Sleep when idle', who: 'operator', card: '#/settings/devices/settings-sleep', words: 'idle power'},
  {key: 'team.tokens', name: 'API tokens', where: 'Settings > Computers > API tokens', who: 'admin', card: '#/settings/devices/settings-tokens', words: 'token key'},
  {key: 'team.models.defaults', name: 'Default model', where: 'Settings > Models > Defaults', who: 'anyone', card: '#/settings/models/settings-defaults', words: 'model idle default'},
  {key: 'nowhere', name: 'No card', where: 'Elsewhere', who: 'anyone', card: '', words: 'idle'},
];

async function scenario(browser: import('playwright').Browser, role: 'owner' | 'member', width: number) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const errors: string[] = [], asked: string[] = [];
  let hold: Promise<void> | null = null;
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.6.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: 'ana', role: role === 'owner' ? 'owner' : 'member', name: 'Ana', email: 'ana@acme.example', registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({active: [], recent_runs: [], health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/settings/catalog') {
      const q = url.searchParams.get('q') || '';
      asked.push(q);
      if (q === 'slow' && hold) await hold;
      const words = q.toLowerCase().split(/\s+/).filter(Boolean);
      return json({settings: CATALOG.filter(e => words.some(w => `${e.name} ${e.where} ${e.words}`.toLowerCase().includes(w)))
        .map(({words: _, ...hit}) => hit)});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  try {
    await page.goto('https://tico-ui.test/#/settings/general');
    const box = page.locator('#settings-search-q'), hits = page.locator('#settings-search-hits');
    await box.waitFor();
    assert.ok(await box.evaluate(el => !!(el.compareDocumentPosition(document.getElementById('settings-tabs')!) & Node.DOCUMENT_POSITION_FOLLOWING)),
      'the box sits above the tabs');
    assert.equal(await hits.isHidden(), true, 'no list before any words');

    // Matches by name only, best first as the catalogue sends them; a match with no card, or one this person may not
    // open, is not offered.
    await box.fill('idle');
    await hits.locator('a').first().waitFor();
    const names = await hits.locator('a').allInnerTexts();
    assert.deepEqual(names, ['Sleep when idle', 'Default model']);
    assert.equal(await hits.locator('a').first().getAttribute('title'), 'Settings > Computers > Sleep when idle');
    await box.fill('token');
    await page.waitForFunction(() => document.querySelector('#settings-search-hits')?.textContent?.trim() !== 'Sleep when idleDefault model');
    if (role === 'owner') await hits.getByText('API tokens', {exact: true}).waitFor();
    else await hits.getByText('No settings found', {exact: true}).waitFor();

    // An older, slower answer never replaces the newer one.
    let release!: () => void;
    hold = new Promise<void>(done => { release = done; });
    await box.fill('slow');
    await page.waitForTimeout(250);   // the box asks after a short pause in typing
    await box.fill('model');
    await hits.getByText('Default model', {exact: true}).waitFor();
    release(); hold = null;
    await page.waitForTimeout(100);
    assert.deepEqual(await hits.locator('a').allInnerTexts(), ['Default model'], 'the slow answer for older words is dropped');

    // Escape clears the box and the list.
    await box.press('Escape');
    assert.equal(await box.inputValue(), '');
    assert.equal(await hits.isHidden(), true);

    // Arrows and Enter: the second match opens its tab, scrolled to the card.
    await box.fill('idle');
    await hits.locator('a').nth(1).waitFor();
    await box.press('ArrowDown'); await box.press('ArrowDown');
    assert.equal(await hits.locator('li.cur a').innerText(), 'Default model');
    await box.press('Enter');
    await page.waitForFunction(() => location.hash === '#/settings/models');
    await page.locator('#settings-models:not([hidden]) #settings-defaults').waitFor();
    assert.equal(await box.inputValue(), '', 'opening a match clears the search');
    assert.equal(await hits.isHidden(), true);

    // A click: a link the catalogue gives with the server's origin opens here, on General.
    if (role === 'owner') {
      await box.fill('storage');
      await hits.getByText('Storage', {exact: true}).click();
      await page.waitForFunction(() => location.hash === '#/settings/general');
      await page.locator('#settings-general:not([hidden]) #settings-storage').waitFor();
      assert.equal(page.url().startsWith('https://tico-ui.test/'), true);
    } else {
      await box.fill('sleep');
      await hits.getByText('Sleep when idle', {exact: true}).click();
      await page.waitForFunction(() => location.hash === '#/settings/devices');
    }
    assert.ok(asked.every(q => q.length > 0), 'no empty question is sent');
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    assert.equal(sideways, false, 'no sideways scroll');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    await scenario(browser, 'owner', 1280);
    await scenario(browser, 'member', 390);
  } finally { await browser.close(); }
  console.log('settings-search ok');
})().catch(error => { console.error(error); process.exit(1); });
