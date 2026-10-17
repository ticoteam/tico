// The Files section of a bot's right rail (ui/bot-files.js): a plain list of names, three rows and a small
// "+N" / "Less" inline, no counts, subtitle or buttons. A linked document opens at its provider
// (a new tab), a stored file in the app's viewer (a CSV as a table, a quoted comma kept in one cell),
// a file that did not sync is plain text, never a storage address; on a computer and on a phone.
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SCREENSHOT_DIR;

const now = Date.now(), iso = ms => new Date(now + ms).toISOString(), min = 60e3;
const bots = [{name: 'cmo', display_name: 'AI CMO', org_parent: '', host: 'keeper', status: 'active', can_chat: true,
  users: [{id: 'ana', name: 'Ana'}], schedules: []}];
const GDOC = 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit';
const file = (id, extra) => ({id, bot: 'cmo', title: id, kind: 'document', mime: '', locator: 'tico_blob', scope: 'task', version: 1,
  state: 'ready', synced: true, size: 10, name: id + '.md', open: {type: 'tico', url: '/api/v2/files/' + id}, provider: '', provider_label: '',
  note: '', source: '', task_id: null, task_title: null, working: false, github_url: null, actor: 'bot:cmo', actor_name: 'AI CMO',
  action: 'modified', first_activity_at: iso(-90 * min), last_activity_at: iso(-90 * min), archived: false, ...extra});
const files = [
  file('file-000000000000000000000001', {title: 'Q4 launch plan', kind: 'document', locator: 'remote_link', mime: '', open: {type: 'external', url: GDOC},
    provider: 'google', provider_label: 'Google', note: 'Link opens in Google (requires access)', task_id: 't1', task_title: 'Publish the pricing page',
    working: true, last_activity_at: iso(-12 * min)}),
  file('file-000000000000000000000002', {title: 'Weekly report', github_url: 'https://github.com/acme/emp-cmo/blob/abc1234/reports/weekly.md', last_activity_at: iso(-3 * 60 * min)}),
  file('file-000000000000000000000003', {title: 'Budget', kind: 'spreadsheet', state: 'not_synced', synced: false, open: null, last_activity_at: iso(-5 * 60 * min)}),
  file('file-000000000000000000000004', {title: 'Traffic export', kind: 'spreadsheet', source: 's3', last_activity_at: iso(-30 * 60 * min)}),
  file('file-000000000000000000000005', {title: 'Old brief', last_activity_at: iso(-60 * 60 * min)}),
];
const CSV = files.find(f => f.title === 'Traffic export');
CSV.name = 'traffic.csv';
const csvText = 'page,note,visits\r\n/home,"Hello, world",120\r\n/pricing,"Two\nlines ""quoted""",80\r\n' + Array.from({length: 1200}, (_, i) => `/p${i},row,${i}`).join('\n') + '\n';

async function open(browser, viewport, state) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    // A list, as the server sends it: the catch-all {} broke the sidebar tree whenever it landed ahead of
    // /api/v2/status, and the bot page never opened (the bot-needs.cjs hang from the v0.3.54 release check).
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'cmo', state: 'idle'}]});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/bots/cmo/files') {
      state.listed.push(q.get('limit'));
      const limit = Number(q.get('limit')), rows = state.rows;
      return json({bot: 'cmo', files: rows.slice(0, limit), total: rows.length, next_cursor: rows.length > limit ? 'next' : null,
        has_more: rows.length > limit, can_manage: true, actors: {}});
    }
    const patch = p.match(/^\/api\/v2\/files\/([^/]+)$/);
    if (patch && route.request().method() === 'PATCH') {
      state.patched.push([patch[1], JSON.parse(route.request().postData())]);
      state.rows = state.rows.filter(f => f.id !== patch[1]);
      return json({file: {id: patch[1]}});
    }
    if (patch && patch[1] === CSV.id) { state.opened.push(patch[1]); return route.fulfill({contentType: 'text/csv', headers: {'Content-Disposition': "attachment; filename*=UTF-8''traffic.csv"}, body: csvText}); }
    if (patch) { state.opened.push(patch[1]); return route.fulfill({contentType: 'application/octet-stream', headers: {'Content-Disposition': "attachment; filename*=UTF-8''weekly.md"}, body: '# Weekly'}); }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [tag, viewport, url] of [['desktop', {width: 1280, height: 900}, '#/bot/cmo'], ['phone', {width: 390, height: 800}, '#/bot/cmo/tasks']]) {
      const state = {rows: files.slice(), listed: [], patched: [], opened: []};
      const {page, errors} = await open(browser, viewport, state);
      await page.goto('https://tico-ui.test/' + url);
      const card = page.locator('#bot-files');
      await card.locator('.bf-row').first().waitFor();
      assert.equal(await card.locator('.bf-row').count(), 3, tag + ': three rows');
      assert.equal((await card.locator('h2').textContent()).trim(), 'Files', tag + ': no count in the title');
      assert.equal(await card.locator('.nav-icon').count(), 0, tag + ': names only, no icon');
      assert.equal((await card.locator('[data-bf-all]').innerText()).trim(), '+2', tag + ': a small +N for the rest');
      assert.equal(await card.locator('.sub, [data-bf-add], [data-bf-remove], [data-bf-promote], .bf-meta, .bf-open, button.ghost:not([data-bf-all])').count(), 0, tag + ': just icons and names');
      assert.deepEqual(await card.locator('.bf-name').allInnerTexts(), ['Q4 launch plan', 'Weekly report', 'Budget'], tag + ': newest activity first');
      const link = card.locator('.bf-row').nth(0).locator('a.bf-name');
      assert.equal(await link.getAttribute('href'), 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit');
      assert.equal(await link.getAttribute('target'), '_blank', tag + ': a linked file opens in a new tab');
      assert.match(await link.getAttribute('rel'), /noopener/);
      const stored = card.locator('.bf-row').nth(1);
      assert.equal(await stored.locator('a.bf-name').getAttribute('href'), '/api/v2/files/file-000000000000000000000002');
      const unsynced = card.locator('.bf-row').nth(2);
      assert.equal(await unsynced.locator('a').count(), 0, tag + ': no link for a file that did not sync');
      // A long name is cut with an ellipsis and keeps its full name as a tooltip.
      assert.equal(await stored.locator('.bf-name').getAttribute('title'), 'Weekly report');
      assert.equal(await stored.locator('.bf-name').evaluate(el => getComputedStyle(el).textOverflow), 'ellipsis');
      if (shots) for (const scheme of ['light', 'dark']) {
        await page.emulateMedia({colorScheme: scheme});
        await card.screenshot({path: path.join(shots, `bot-files-${tag}-${scheme}.png`)});
      }
      // A stored file opens in the app's viewer (the viewer asks the route for the bytes), not by navigating away.
      await stored.locator('a.bf-name').click();
      await page.waitForSelector('dialog#doc-viewer[open]');
      await until(() => state.opened.includes('file-000000000000000000000002'));
      assert.equal(new URL(page.url()).pathname, '/', tag + ': still on the app');
      await page.keyboard.press('Escape');
      await page.waitForSelector('dialog#doc-viewer:not([open])', {state: 'attached'});
      // Show all opens the rest inline; Show less folds it back.
      await card.locator('[data-bf-all]').click();
      await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 5);
      // A CSV opens as a table: a quoted comma stays in one cell, a quoted line break too, 1,000 rows and a note.
      await card.locator('.bf-row', {hasText: 'Traffic export'}).locator('a.bf-name').click();
      const table = page.locator('dialog#doc-viewer[open] table.csv');
      await table.waitFor();
      assert.deepEqual(await table.locator('thead th').allTextContents(), ['page', 'note', 'visits'], tag + ': the header row');
      assert.equal(await table.locator('tbody tr').count(), 1000, tag + ': the first 1,000 rows');
      assert.equal(await table.locator('tbody tr').nth(0).locator('td').nth(1).innerText(), 'Hello, world', tag + ': a quoted comma stays in its cell');
      assert.equal(await table.locator('tbody tr').nth(1).locator('td').nth(1).textContent(), 'Two\nlines "quoted"', tag + ': a quoted line break and quote too');
      assert.equal(await table.locator('tbody tr').nth(0).locator('td').count(), 3);
      assert.match(await page.locator('.csv-note').innerText(), /Showing 1,000 of 1,202 rows/);
      if (shots) for (const scheme of ['light', 'dark']) {
        await page.emulateMedia({colorScheme: scheme});
        await page.screenshot({path: path.join(shots, `bot-files-csv-${tag}-${scheme}.png`)});
      }
      assert.equal(await page.locator('dialog#doc-viewer [data-doc-download]').count(), 1, tag + ': Download beside it');
      assert.equal(await table.locator('thead th').first().evaluate(el => getComputedStyle(el).position), 'sticky', tag + ': the header stays put');
      await page.keyboard.press('Escape');
      await page.waitForSelector('dialog#doc-viewer:not([open])', {state: 'attached'});
      await card.locator('[data-bf-less]').click();
      await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 3);
      assert.doesNotMatch(await card.innerHTML(), /s3:\/\/|X-Amz|amazonaws/i, tag + ': no storage address');
      assert.deepEqual(state.patched, [], tag + ': nothing was changed');
      if (viewport.width < 760) {
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'phone: no sideways scroll');
        assert((await card.boundingBox()).width <= 390, 'phone: the card fits');
      }
      assert.deepEqual(errors, [], tag + ': page errors');
      await page.close();
      console.log(`bot files card (${tag}): plain list, Show all, a link opens externally, a stored file and a CSV in the viewer`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
async function until(test, ms = 5000) { for (const end = Date.now() + t(ms); !test(); ) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 50)); } }
