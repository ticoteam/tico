// The Files section of a bot's right rail (ui/bot-files.ts): one row each, a kind's mark (or an image's thumbnail) and
// the name without its extension, three rows and "N more" / "Less" inline, no counts, subtitle or buttons. The whole
// row opens the file. A linked document opens at its provider
// (a new tab), a stored file in the review view (ui/app/file-review.ts; a CSV as a table, a quoted comma kept in one cell),
// a file that did not sync is plain text, never a storage address; on a computer and on a phone. Expanded, a search
// field and the filter icon narrow the list on the server (q, kind). The files a bot names in a message are links to them
// (ui/app/file-mentions.ts), asked of the server in one request; a bare name two files share stays text unless the
// folder named before it says which.
// Fixtures only, no network.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SCREENSHOT_DIR;
declare function fileMentionLinks(html: string, bot: string): string;   // ui/app/file-mentions.ts, in the page

const now = Date.now(), iso = (ms: number) => new Date(now + ms).toISOString(), min = 60e3;
const bots = [{name: 'cmo', display_name: 'AI CMO', org_parent: '', host: 'keeper', status: 'active', can_chat: true,
  users: [{id: 'ana', name: 'Ana'}], schedules: []}];
const GDOC = 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit';
const file = (id: string, extra: Record<string, unknown>) => ({id, bot: 'cmo', title: id, kind: 'document', mime: '', locator: 'tico_blob', scope: 'task', version: 1,
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
type Row = (typeof files)[number];
const CSV = files.find(f => f.title === 'Traffic export')!;
CSV.name = 'traffic.csv';
const csvText = 'page,note,visits\r\n/home,"Hello, world",120\r\n/pricing,"Two\nlines ""quoted""",80\r\n' + Array.from({length: 1200}, (_, i) => `/p${i},row,${i}`).join('\n') + '\n';

interface State { rows: Row[]; listed: (string | null)[]; patched: unknown[]; opened: string[]; thumbs?: string[]; asked?: string[]; mentions?: string[][] }
// What the mentions route knows, as the server would answer (backend/files.py Files.mentions): paths and unique names.
const NAMED: Record<string, string> = {'reports/ticket-topics.csv': 'file-csv', 'ticket-topics.csv': 'file-csv',
  'drafts/2026-11-10-paiva-reply/notes-for-chris.docx': 'file-titled'};
// A 2x2 PNG, an image's thumbnail.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DwnwEIGBgY/jMwAAA3BAP9Wq7fJQAAAABJRU5ErkJggg==', 'base64');

async function open(browser: import('playwright').Browser, viewport: {width: number; height: number}, state: State) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    // A list, as the server sends it: the catch-all {} broke the sidebar tree whenever it landed ahead of
    // /api/v2/status, and the bot page never opened (the bot-needs.cts hang from the v0.3.54 release check).
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'cmo', state: 'idle'}]});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/bots/cmo/files/mentions') {
      state.mentions = [...(state.mentions || []), q.getAll('m')];
      return json({bot: 'cmo', files: Object.fromEntries(q.getAll('m').filter(m => NAMED[m]).map(m => [m, state.rows.find(f => f.id === NAMED[m])]))});
    }
    if (p === '/api/v2/bots/cmo/files') {
      state.listed.push(q.get('limit'));
      state.asked = [...(state.asked || []), url.search];
      const limit = Number(q.get('limit')), want = (q.get('q') || '').toLowerCase(), kind = q.get('kind');
      const rows = state.rows.filter(f => (!want || f.title.toLowerCase().includes(want)) && (!kind || f.kind === kind));
      return json({bot: 'cmo', files: rows.slice(0, limit), total: rows.length, next_cursor: rows.length > limit ? 'next' : null,
        has_more: rows.length > limit, can_manage: true, actors: {}});
    }
    const thumb = p.match(/^\/api\/v2\/files\/([^/]+)\/thumb$/);
    if (thumb) { state.thumbs = [...(state.thumbs || []), thumb[1] + '?' + url.search.slice(1)];
      return thumb[1] === 'file-thumb-ok' ? route.fulfill({contentType: 'image/png', body: PNG}) : route.fulfill({status: 404, contentType: 'application/json', body: '{}'}); }
    const patch = p.match(/^\/api\/v2\/files\/([^/]+)$/);
    if (patch && route.request().method() === 'PATCH') {
      state.patched.push([patch[1], JSON.parse(route.request().postData()!)]);
      state.rows = state.rows.filter(f => f.id !== patch[1]);
      return json({file: {id: patch[1]}});
    }
    if (patch && patch[1] === CSV.id) { state.opened.push(patch[1]!); return route.fulfill({contentType: 'text/csv', headers: {'Content-Disposition': "attachment; filename*=UTF-8''traffic.csv"}, body: csvText}); }
    if (patch) { state.opened.push(patch[1]!); return route.fulfill({contentType: 'application/octet-stream', headers: {'Content-Disposition': "attachment; filename*=UTF-8''weekly.md"}, body: '# Weekly'}); }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  try {
    for (const [tag, viewport, url] of [['desktop', {width: 1280, height: 900}, '#/bot/cmo'], ['phone', {width: 390, height: 800}, '#/bot/cmo/tasks']] as [string, {width: number; height: number}, string][]) {
      const state: State = {rows: files.slice(), listed: [], patched: [], opened: []};
      const {page, errors} = await open(browser, viewport, state);
      await page.goto('https://tico-ui.test/' + url);
      const card = page.locator('#bot-files');
      await card.locator('.bf-row').first().waitFor();
      assert.equal(await card.locator('.bf-row').count(), 3, tag + ': three rows');
      assert.equal((await card.locator('h2').textContent())!.trim(), 'Files', tag + ': no count in the title');
      assert.deepEqual(await card.locator('.bf-mark').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.bfIcon)), ['link', 'description', 'table_chart'], tag + ': a mark for each kind');
      assert.equal((await card.locator('[data-bf-all]').innerText()).trim(), '2 more', tag + ': "N more" for the rest');
      assert.equal(await card.locator('.sub, [data-bf-add], [data-bf-remove], [data-bf-promote], .bf-meta, .bf-open, button.ghost:not([data-bf-all])').count(), 0, tag + ': just icons and names');
      assert.deepEqual(await card.locator('.bf-name').allInnerTexts(), ['Q4 launch plan', 'Weekly report', 'Budget'], tag + ': newest activity first');
      const link = card.locator('a.bf-row').nth(0);
      assert.equal(await link.getAttribute('href'), 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit');
      assert.equal(await link.getAttribute('target'), '_blank', tag + ': a linked file opens in a new tab');
      assert.match((await link.getAttribute('rel'))!, /noopener/);
      const stored = card.locator('.bf-row').nth(1);
      assert.equal(await stored.getAttribute('href'), '/api/v2/files/file-000000000000000000000002', tag + ': the whole row is the link');
      const unsynced = card.locator('.bf-row').nth(2);
      assert.equal(await unsynced.evaluate(el => el.tagName), 'DIV', tag + ': no link for a file that did not sync');
      assert.equal(await unsynced.locator('a').count(), 0);
      // A row is 32px on a computer and at least 44px on a phone, the mark 24px.
      const h = (await stored.boundingBox())!.height;
      assert.ok(viewport.width < 760 ? h >= 44 : Math.round(h) === 32, `${tag}: row height ${h}`);
      const mark = (await stored.locator('.bf-mark').boundingBox())!;
      assert.deepEqual([Math.round(mark.width), Math.round(mark.height)], [24, 24], tag + ': a 24px mark');
      // Hover shades a row that opens; a focused row shows the focus ring.
      const bg = await stored.evaluate(el => getComputedStyle(el).backgroundColor);
      await stored.hover();
      await page.waitForFunction(() => getComputedStyle(document.querySelectorAll('#bot-files .bf-row')[1]!).backgroundColor !== 'rgba(0, 0, 0, 0)');
      assert.notEqual(await stored.evaluate(el => getComputedStyle(el).backgroundColor), bg, tag + ': hover shades the row');
      await link.focus();
      await page.keyboard.press('Tab');
      assert.equal(await stored.evaluate(el => document.activeElement === el && el.matches(':focus-visible') ? getComputedStyle(el).outlineStyle : 'not focused'), 'solid', tag + ': the focus ring');
      // A long name is cut with an ellipsis and keeps its full name as a tooltip.
      assert.equal(await stored.getAttribute('title'), 'Weekly report');
      assert.equal(await stored.locator('.bf-name').evaluate(el => getComputedStyle(el).textOverflow), 'ellipsis');
      if (shots) for (const scheme of ['light', 'dark'] as const) {
        await page.emulateMedia({colorScheme: scheme});
        await card.screenshot({path: path.join(shots, `bot-files-${tag}-${scheme}.png`)});
      }
      // A stored file opens in the review view (it asks the route for the bytes), not by navigating away.
      // Enter on the focused row opens it, as a click does.
      if (viewport.width < 760) await stored.click(); else await page.keyboard.press('Enter');
      await page.waitForSelector('dialog.frv[open]');
      await until(() => state.opened.includes('file-000000000000000000000002'));
      assert.equal(new URL(page.url()).pathname, '/', tag + ': still on the app');
      await page.keyboard.press('Escape');
      await page.waitForSelector('dialog.frv', {state: 'detached'});
      // Show all opens the rest inline; Show less folds it back.
      await card.locator('[data-bf-all]').click();
      await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 5);
      // A CSV opens as a table: a quoted comma stays in one cell, a quoted line break too, 1,000 rows and a note.
      await card.locator('a.bf-row', {hasText: 'Traffic export'}).click();
      const table = page.locator('dialog.frv[open] table.csv');
      await table.waitFor();
      assert.deepEqual(await table.locator('thead th').allTextContents(), ['page', 'note', 'visits'], tag + ': the header row');
      assert.equal(await table.locator('tbody tr').count(), 1000, tag + ': the first 1,000 rows');
      assert.equal(await table.locator('tbody tr').nth(0).locator('td').nth(1).innerText(), 'Hello, world', tag + ': a quoted comma stays in its cell');
      assert.equal(await table.locator('tbody tr').nth(1).locator('td').nth(1).textContent(), 'Two\nlines "quoted"', tag + ': a quoted line break and quote too');
      assert.equal(await table.locator('tbody tr').nth(0).locator('td').count(), 3);
      assert.match(await page.locator('.csv-note').innerText(), /Showing 1,000 of 1,202 rows/);
      if (shots) for (const scheme of ['light', 'dark'] as const) {
        await page.emulateMedia({colorScheme: scheme});
        await page.screenshot({path: path.join(shots, `bot-files-csv-${tag}-${scheme}.png`)});
      }
      assert.equal(await page.locator('dialog.frv [data-frv-dl]').isVisible(), true, tag + ': Download on top');
      assert.equal(await table.locator('thead th').first().evaluate(el => getComputedStyle(el).position), 'sticky', tag + ': the header stays put');
      await page.keyboard.press('Escape');
      await page.waitForSelector('dialog.frv', {state: 'detached'});
      assert.equal(new URL(page.url()).hash, url, tag + ': back on the bot\'s page');
      await card.locator('[data-bf-less]').click();
      await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 3);
      assert.doesNotMatch(await card.innerHTML(), /s3:\/\/|X-Amz|amazonaws/i, tag + ': no storage address');
      assert.deepEqual(state.patched, [], tag + ': nothing was changed');
      if (viewport.width < 760) {
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'phone: no sideways scroll');
        assert((await card.boundingBox())!.width <= 390, 'phone: the card fits');
      }
      assert.deepEqual(errors, [], tag + ': page errors');
      await page.close();
      console.log(`bot files card (${tag}): plain list, Show all, a link opens externally, a stored file and a CSV in the viewer`);
    }
    // Each kind's mark and name (AC-9 to AC-12): an image's thumbnail (its kind's icon when the thumbnail is gone), a
    // page, a CSV named without its folder and extension, and a link kept as written; 19 files read "16 more".
    const kinds = [
      file('file-thumb-ok', {title: 'charts/2026-11-12-hig-bot-chat.jpg', name: '2026-11-12-hig-bot-chat.jpg', kind: 'image', mime: 'image/jpeg', thumb_blob_id: 'b-thumb', version: 3}),
      file('file-page', {title: 'page-owner-chat.html', name: 'page-owner-chat.html', kind: 'document', mime: 'text/html'}),
      file('file-csv', {title: 'reports/ticket-topics.csv', name: 'ticket-topics.csv', kind: 'spreadsheet', mime: 'text/csv'}),
      file('file-link', {title: 'Q3 plan / draft', kind: 'document', locator: 'remote_link', name: null, open: {type: 'external', url: GDOC}}),
      file('file-thumb-gone', {title: 'logo.png', name: 'logo.png', kind: 'image', mime: 'image/png', thumb_blob_id: 'b-gone'}),
      file('file-json', {title: '.json', name: '.json', kind: 'data', mime: 'application/json'}),
      file('file-titled', {title: 'Q4 budget / final.md', name: 'budget.md', kind: 'document', mime: 'text/markdown'}),
      ...Array.from({length: 12}, (_, i) => file('file-more-' + i, {title: 'notes ' + i}))];
    const state: State = {rows: kinds, listed: [], patched: [], opened: []};
    const {page, errors} = await open(browser, {width: 1280, height: 900}, state);
    await page.goto('https://tico-ui.test/#/bot/cmo');
    const card = page.locator('#bot-files');
    await card.locator('.bf-row').first().waitFor();
    assert.equal((await card.locator('[data-bf-all]').innerText()).trim(), '16 more', 'kinds: 19 files, 3 shown');
    await card.locator('[data-bf-all]').click();
    await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 19);
    assert.equal(await card.locator('[data-bf-all], [data-bf-more]').count(), 0, 'kinds: all of them shown');
    assert.equal((await card.locator('[data-bf-less]').innerText()).trim(), 'Less', 'kinds: expanded reads Less');
    const first = card.locator('.bf-row').nth(0);
    await page.waitForFunction(() => (document.querySelector('#bot-files .bf-row img.bf-thumb') as HTMLImageElement | null)?.naturalWidth === 2);
    assert.equal(await first.locator('img.bf-thumb').getAttribute('src'), '/api/v2/files/file-thumb-ok/thumb?v=3', 'kinds: the thumbnail at its version');
    assert.equal(await first.locator('img.bf-thumb').evaluate(el => getComputedStyle(el).objectFit), 'cover');
    const marks = async () => card.locator('.bf-row').evaluateAll(rows => rows.slice(0, 7).map(r => r.querySelector('img.bf-thumb') ? 'thumbnail' : r.querySelector('.bf-mark .nav-icon')?.textContent));
    // The thumbnail that answers 404 falls back to the image icon.
    for (const end = Date.now() + t(5000); (await marks())[4] !== 'image' && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert.deepEqual(await marks(), ['thumbnail', 'article', 'table_chart', 'link', 'image', 'draft', 'description'], 'kinds: a mark for each kind');
    // A stored file a bot titled with a slash keeps its title whole: only a title that is the file's path loses its folder.
    assert.deepEqual((await card.locator('.bf-name').allInnerTexts()).slice(0, 7),
      ['2026-11-12-hig-bot-chat', 'page-owner-chat', 'ticket-topics', 'Q3 plan / draft', 'logo', '.json', 'Q4 budget / final'], 'kinds: names without folder or extension; a link and a given title as written');
    assert.equal(await card.locator('.bf-row').nth(2).getAttribute('title'), 'reports/ticket-topics.csv', 'kinds: the tooltip is the full title');
    // Glyphs the icon font must hold, or a mark reads as its name.
    const icons = fs.readFileSync(path.join(__dirname, '..', 'vendor', 'fonts', 'icons.txt'), 'utf8').split('\n');
    for (const name of ['article', 'description', 'table_chart', 'image', 'movie', 'graphic_eq', 'link', 'draft']) assert.ok(icons.includes(name), 'icon font has ' + name);
    // The minute's reload draws nothing new, so the thumbnail is not asked for again.
    const asked = state.thumbs!.length;
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await card.locator('[data-bf-less]').click();
    await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 3);
    assert.equal((await card.locator('[data-bf-all]').innerText()).trim(), '16 more');
    assert.ok(state.thumbs!.length <= asked + 1, `kinds: thumbnails asked ${state.thumbs!.length} times`);
    // Expanded: search and the filter icon (no word), asked of the server, still newest first; Less clears both.
    assert.equal(await card.locator('.bf-tools').isVisible(), false, 'find: no search while folded');
    await card.locator('[data-bf-all]').click();
    await card.locator('.bf-q').fill('budget');
    await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 1);
    assert.deepEqual(await card.locator('.bf-name').allInnerTexts(), ['Q4 budget / final'], 'find: the server\'s answer');
    assert.ok(state.asked!.some(a => /[?&]q=budget(&|$)/.test(a)) && !state.asked!.some(a => /q=b(&|$)|q=bu(&|$)/.test(a)), 'find: one request once typing stops');
    assert.equal((await card.locator('.bf-fbtn').innerText()).trim(), '', 'find: the filter icon, no word');
    await card.locator('.bf-q').fill('');
    await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 19);
    await card.locator('.bf-fbtn').click();
    await card.locator('[data-bf-kind="data"]').click();
    await page.waitForFunction(() => [...document.querySelectorAll('#bot-files .bf-name')].map(el => el.textContent).join() === '.json');
    assert.deepEqual(await card.locator('.bf-name').allInnerTexts(), ['.json'], 'find: one kind');
    assert.ok(state.asked!.at(-1)!.includes('kind=data'));
    await card.locator('.bf-q').fill('nothing like it');
    await card.locator('.bf-none').waitFor();
    assert.equal((await card.locator('.bf-none').innerText()).trim(), 'Nothing matches.');
    await card.locator('[data-bf-less]').click();
    await page.waitForFunction(() => document.querySelectorAll('#bot-files .bf-row').length === 3);
    assert.ok(!/[?&](q|kind)=/.test(state.asked!.at(-1)!) && await card.locator('.bf-tools').isHidden(), 'find: Less clears search and kind');
    // A message's files: a path, a unique bare name, a bare name after its folder; a bare name two files share (no folder
    // before it) and a web address stay as they are. One request asks them all.
    const said = '<ul><li>See <code>reports/ticket-topics.csv</code> and ticket-topics.csv</li>'
      + '<li>In <code>drafts/2026-11-10-paiva-reply/</code>: <code>notes-for-chris.docx</code></li>'
      + '<li>Also notes-for-chris.docx and <a href="https://example.test/a.pdf">https://example.test/a.pdf</a></li></ul>';
    const linked = await page.evaluate(async said => {
      const first = fileMentionLinks(said, 'cmo');
      for (let i = 0; i < 100 && !fileMentionLinks(said, 'cmo').includes('data-file-ref'); i++) await new Promise(r => setTimeout(r, 50));
      const host = document.createElement('div');
      host.id = 'said';
      host.innerHTML = fileMentionLinks(said, 'cmo');
      document.body.append(host);
      return {first, refs: [...host.querySelectorAll<HTMLElement>('a[data-file-ref]')].map(a => [a.textContent, a.dataset.fileRef])};
    }, said);
    assert.equal(linked.first, said, 'mentions: text until the server answers');
    assert.deepEqual(linked.refs, [['reports/ticket-topics.csv', 'file-csv'], ['ticket-topics.csv', 'file-csv'], ['notes-for-chris.docx', 'file-titled']], 'mentions: one file each, never a guess');
    assert.equal(state.mentions!.length, 1, 'mentions: one request');
    // Clicked as a message's link is (the fixture's host sits under the sidebar, so the click is sent to it).
    await page.locator('#said a[data-file-ref="file-titled"]').evaluate(a => (a as HTMLElement).click());
    await page.waitForSelector('dialog.frv[open]');
    await until(() => state.opened.includes('file-titled'));
    await page.keyboard.press('Escape');
    await page.waitForSelector('dialog.frv', {state: 'detached'});
    if (shots) await card.screenshot({path: path.join(shots, 'bot-files-kinds.png')});
    assert.deepEqual(errors, [], 'kinds: page errors');
    await page.close();
    console.log('bot files card (kinds): marks, a thumbnail and its fallback, names without extensions, 16 more; search, kind, named files as links');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
async function until(test: () => boolean, ms = 5000) { for (const end = Date.now() + t(ms); !test(); ) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 50)); } }
