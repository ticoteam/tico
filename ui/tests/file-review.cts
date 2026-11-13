// The file review view (ui/app/file-review.ts, docs/files.md Review), on a bot's page against fixtures:
//  - a file in the bot's Files opens full screen at #/file/<id>: the file on the left, the DM with the bot on the right,
//    the name, Download and the clock on top; Esc returns to the bot's page as it was (not drawn again);
//  - the clock lists the versions; one picked shows (and is in the address), the version mark goes back to the latest;
//    Compare with previous shows a line diff;
//  - a message sent carries refs.from_page {page: 'file', label, item: {kind: 'file', id, name: 'v<n>'}};
//  - a new version the bot publishes shows on the thread's live event, without a reload;
//  - a Word file is drawn in a sandboxed frame (no scripts) with its heading, table and own picture, and without its
//    javascript: link, its picture on another site or its HTML chunk; nothing is fetched outside the app; Compare reads
//    its text;
//  - a Google file shows Google's /preview with Open in Google; a cold link with ?task= opens the task's thread;
//  - on a phone the file and the chat take turns, with no sideways scroll.
// TICO_SCREENSHOT_DIR keeps the screenshots. Fixtures only, no network.
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as ThreadsFake from './support/threads-fake.cts';
import type * as DocxSample from './support/docx-sample.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFake.Api;
const {docxSample} = require('./support/docx-sample.cts') as DocxSample.Api;
declare const LIVE: {handlers: Map<string, Set<(d: unknown) => void>>};
const shots = process.env.TICO_SCREENSHOT_DIR;

const now = Date.now(), iso = (ms: number) => new Date(now + ms).toISOString(), min = 60e3;
const bots = [{name: 'cmo', display_name: 'AI CMO', org_parent: '', host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}];
const MD = 'file-000000000000000000000001', DOCX = 'file-000000000000000000000002', GDOC = 'file-000000000000000000000003';
const GURL = 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit';
const row = (id: string, extra: Record<string, unknown>) => ({id, bot: 'cmo', title: id, kind: 'document', mime: '', locator: 'tico_blob', scope: 'bot', version: 1,
  state: 'ready', synced: true, size: 10, name: id + '.md', open: {type: 'tico', url: '/api/v2/files/' + id}, provider: '', provider_label: '', note: '', source: '',
  task_id: null, task_title: null, working: false, github_url: null, actor: 'bot:cmo', action: 'modified', first_activity_at: iso(-90 * min),
  last_activity_at: iso(-90 * min), archived: false, ...extra});

type Version = {version: number; name: string; body: string | Buffer; created_at: string; actor: string};
interface State { versions: Record<string, Version[]>; outside: string[]; dm: string[]; sent: ThreadsFake.ThreadsPost[] }

async function open(browser: import('playwright').Browser, viewport: {width: number; height: number}, state: State, docx: Buffer[]) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors: string[] = [];
  page.on('pageerror', e => { if (!/Service worker is disabled because the context is sandboxed/i.test(e.message)) errors.push(e.message); });
  const threads = threadsFake({bot: 'cmo', sent: state.sent, messages: [{id: 'm-1', sender_id: 'bot:cmo', body: 'The plan is ready.'}]});
  const rows = () => [
    row(MD, {title: 'Launch plan', name: 'plan.md', version: state.versions[MD]!.length, last_activity_at: iso(-5 * min)}),
    row(DOCX, {title: 'Pricing memo', name: 'memo.docx', version: 2, last_activity_at: iso(-10 * min)}),
    row(GDOC, {title: 'Q4 brief', locator: 'remote_link', open: {type: 'external', url: GURL}, provider: 'google', provider_label: 'Google', name: null, last_activity_at: iso(-20 * min)}),
  ];
  state.versions[DOCX] = docx.map((body, i) => ({version: i + 1, name: 'memo.docx', body, created_at: iso((i - 3) * 60 * min), actor: 'bot:cmo'}));
  await page.context().route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') { state.outside.push(url.href); return route.abort(); }
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    const vendor = p.match(/^\/(?:tico\/ui\/)?vendor\/([\w.-]+\.js)$/);
    if (vendor) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, '../vendor', vendor[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'cmo', state: 'idle'}]});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/tasks/t1') return json({task: {id: 't1', title: 'Publish the pricing page', conversation_id: 't-dm-cmo', status: 'open'}});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/bots/cmo/files') { const all = rows(); return json({bot: 'cmo', files: all, total: all.length, next_cursor: null, has_more: false, can_manage: true, actors: {}}); }
    if (p === '/api/v2/threads/dm' && method === 'GET') state.dm.push(url.searchParams.get('with') || '');
    const out = threads(p, url, method, method === 'GET' ? null : req.postDataJSON?.() ?? null);
    if (out !== undefined) return json(out);
    const info = p.match(/^\/api\/v2\/files\/([\w-]+)\/info$/);
    if (info) { const r = rows().find(x => x.id === info[1]); return r ? json({file: r}) : route.fulfill({status: 404, contentType: 'application/json', body: '{}'}); }
    const vers = p.match(/^\/api\/v2\/files\/([\w-]+)\/versions$/);
    if (vers) {
      const list = state.versions[vers[1]!] || [];
      return json({file: vers[1], versions: list.slice().reverse().map((v, i) => ({version: v.version, current: i === 0, name: v.name, size: v.body.length,
        mime: '', actor: v.actor, created_at: v.created_at, url: `/api/v2/files/${vers[1]}/versions/${v.version}`}))});
    }
    const bytes = p.match(/^\/api\/v2\/files\/([\w-]+)(?:\/versions\/(\d+))?$/);
    if (bytes && method === 'GET') {
      const list = state.versions[bytes[1]!] || [], n = Number(bytes[2] || url.searchParams.get('v') || list.length);
      const v = list.find(x => x.version === n);
      if (!v) return route.fulfill({status: 404, body: ''});
      return route.fulfill({contentType: 'application/octet-stream', headers: {'Content-Disposition': `attachment; filename*=UTF-8''${v.name}`}, body: v.body});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors};
}
async function until(test: () => boolean | Promise<boolean>, ms = 8000) { for (const end = Date.now() + ms; !(await test()); ) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 50)); } }
const frv = 'dialog.frv[open]';

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  const docx = [await docxSample(['We charge per seat.']), await docxSample(['We charge per seat.', 'Annual plans save 20%.'])];
  try {
    // ---- desktop: a Markdown file from the bot's Files
    {
      const state: State = {versions: {[MD]: [
        {version: 1, name: 'plan.md', body: '# Plan\n\nOld line\n', created_at: iso(-3 * 60 * min), actor: 'bot:cmo'},
        {version: 2, name: 'plan.md', body: '# Plan\n\nNew line\n', created_at: iso(-30 * min), actor: 'human:ana'}]}, outside: [], dm: [], sent: []};
      const {page, errors} = await open(browser, {width: 1280, height: 860}, state, docx);
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const card = page.locator('#bot-files');
      await card.locator('.bf-row').first().waitFor();
      await page.evaluate(() => { document.querySelector<HTMLElement>('#bot-files')!.dataset.kept = '1'; });
      await card.locator('a.bf-row', {hasText: 'Launch plan'}).click();
      await page.waitForSelector(frv);
      assert.equal(new URL(page.url()).hash, '#/file/' + MD, 'the address is the file\'s');
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'New line'}).waitFor();
      assert.equal(await page.locator(`${frv} .frv-title`).innerText(), 'Launch plan');
      assert.equal(await page.locator(`${frv} [data-frv-dl]`).getAttribute('href'), `/api/v2/files/${MD}/versions/2`, 'Download takes the version on screen');
      assert.equal(await page.locator(`${frv} [data-frv-dl]`).getAttribute('download'), '');
      assert.equal(await page.locator(`${frv} [data-frv-ext]`).isHidden(), true, 'a stored file has no Open');
      await page.locator(`${frv} .frv-chat .th-msg[data-mid="m-1"]`).waitFor();
      assert.deepEqual(state.dm, ['bot:cmo'], 'the chat is the DM with the file\'s bot');
      const preview = await page.locator(`${frv} .frv-preview`).boundingBox(), chat = await page.locator(`${frv} .frv-chat`).boundingBox();
      assert.ok(preview && chat && preview.width > 800 && chat.x >= preview.x + preview.width - 1 && chat.width >= 320, `file left, chat right: ${JSON.stringify({preview, chat})}`);
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-markdown-desktop.png')});

      // the versions: v1 shows and is in the address; the version mark returns to the latest
      await page.locator(`${frv} [data-frv-hist]`).click();
      const items = page.locator(`${frv} [data-frv-hist-list] [data-frv-v]`);
      assert.equal(await items.count(), 2, 'one row per version');
      assert.match(await items.nth(0).innerText(), /v2[\s\S]*You/, 'newest first, with who');
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-history-desktop.png')});
      await items.nth(1).click();
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'Old line'}).waitFor();
      assert.equal(new URL(page.url()).hash, `#/file/${MD}?v=1`);
      assert.equal(await page.locator(`${frv} [data-frv-dl]`).getAttribute('href'), `/api/v2/files/${MD}/versions/1`);
      await page.locator(`${frv} [data-frv-latest]`).click();
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'New line'}).waitFor();
      assert.equal(await page.locator(`${frv} [data-frv-latest]`).isHidden(), true);
      // Compare with previous: a line diff
      await page.locator(`${frv} [data-frv-hist]`).click();
      await page.locator(`${frv} [data-frv-cmp]`).click();
      await page.locator(`${frv} .tf-diff-lines .add`, {hasText: 'New line'}).waitFor();
      assert.match(await page.locator(`${frv} .tf-diff-lines .del`).innerText(), /Old line/);
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-compare-desktop.png')});
      await page.locator(`${frv} [data-frv-hist]`).click();
      await page.locator(`${frv} [data-frv-cmp]`).click();
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'New line'}).waitFor();

      // a message names the file and the version on screen
      await page.locator(`${frv} .frv-chat .p-text`).fill('Shorten the second section');
      await page.locator(`${frv} .frv-chat .p-text`).press('Enter');
      await until(() => state.sent.length > 0);
      assert.deepEqual((state.sent.at(-1) as {refs?: {from_page?: unknown}}).refs?.from_page,
        {page: 'file', label: 'Launch plan', item: {kind: 'file', id: MD, name: 'v2'}});

      // the bot publishes v3: the thread's live event shows it, no reload
      state.versions[MD]!.push({version: 3, name: 'plan.md', body: '# Plan\n\nThird line\n', created_at: iso(0), actor: 'bot:cmo'});
      await page.evaluate(() => { for (const fn of LIVE.handlers.get('messages') || []) fn({conversation_id: 't-dm-cmo'}); });
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'Third line'}).waitFor({timeout: 6000});

      // Esc returns to the bot's page as it was
      await page.locator(`${frv} .frv-title`).click();
      await page.keyboard.press('Escape');
      await page.waitForSelector('dialog.frv', {state: 'detached'});
      assert.equal(new URL(page.url()).hash, '#/bot/cmo', 'back where it came from');
      assert.equal(await page.locator('#bot-files').getAttribute('data-kept'), '1', 'the page under it was not drawn again');

      // ---- a Word file: drawn in a sandboxed frame, stripped of what could act or reach out
      await card.locator('a.bf-row', {hasText: 'Pricing memo'}).click();
      const frame = page.locator(`${frv} .frv-preview iframe.html-view`);
      await frame.waitFor({timeout: 15000});
      assert.equal(await frame.getAttribute('sandbox'), 'allow-popups allow-popups-to-escape-sandbox', 'no scripts, its own origin');
      const doc = await frame.getAttribute('srcdoc') || '';
      assert.match(doc, /Pricing memo/); assert.match(doc, /Annual plans save 20%/); assert.match(doc, /<table/);
      assert.match(doc, /<img[^>]+src="data:[^"]+"/, 'its own picture shows');
      assert.equal((doc.match(/<img[^>]+src=/g) || []).length, 1, 'only that one: the picture on another site has no address');
      assert.match(doc, /href="https:\/\/example\.com\/pricing"/, 'a web link stays');
      assert.doesNotMatch(doc, /javascript:|<script|<iframe|evil\.example|DOCX_CHUNK/i, 'nothing that acts or reaches out');
      assert.match(doc, /Content-Security-Policy/);
      const inside = page.frameLocator(`${frv} .frv-preview iframe.html-view`);
      await inside.locator('table').waitFor();
      assert.equal(await inside.locator('a', {hasText: 'Open me'}).getAttribute('href'), null, 'the javascript: link is gone');
      assert.equal(await page.evaluate(() => (window as unknown as {DOCX_LINK?: number; DOCX_CHUNK?: number}).DOCX_LINK ?? (window as unknown as {DOCX_CHUNK?: number}).DOCX_CHUNK ?? 0), 0);
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-docx-desktop.png')});
      await page.locator(`${frv} [data-frv-hist]`).click();
      await page.locator(`${frv} [data-frv-cmp]`).click();
      await page.locator(`${frv} .tf-diff-lines .add`, {hasText: 'Annual plans save 20%.'}).waitFor();
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-docx-compare-desktop.png')});
      await page.keyboard.press('Escape');
      await page.waitForSelector('dialog.frv', {state: 'detached'});

      // ---- a Google file: Google's own preview, and Open in Google
      await card.locator('a.bf-row', {hasText: 'Q4 brief'}).click();
      const g = page.locator(`${frv} iframe.frv-google`);
      await g.waitFor();
      assert.equal(await g.getAttribute('src'), 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/preview');
      assert.equal(await page.locator(`${frv} [data-frv-ext]`).getAttribute('href'), GURL);
      assert.equal(await page.locator(`${frv} [data-frv-ext]`).getAttribute('aria-label'), 'Open in Google');
      assert.equal(await page.locator(`${frv} [data-frv-dl]`).isHidden(), true);
      assert.equal(await page.locator(`${frv} [data-frv-hist]`).isHidden(), true);
      await page.locator(`${frv} [data-frv-close]`).click();
      await page.waitForSelector('dialog.frv', {state: 'detached'});
      assert.equal(new URL(page.url()).hash, '#/bot/cmo');
      assert.deepEqual(state.outside.filter(u => !u.startsWith('https://docs.google.com/')), [], 'nothing fetched outside the app but Google\'s preview');
      assert.deepEqual(errors, [], 'page errors');
      await page.close();
      console.log('file review (desktop): file + DM, versions, compare, from_page, live version, Word file sandboxed, Google preview, Esc back');
    }
    // ---- a cold link from a task: the task's thread beside the file; Close goes to the task
    {
      const state: State = {versions: {[MD]: [{version: 1, name: 'plan.md', body: '# Plan\n\nOnly line\n', created_at: iso(-30 * min), actor: 'bot:cmo'}]}, outside: [], dm: [], sent: []};
      const {page, errors} = await open(browser, {width: 1280, height: 860}, state, docx);
      await page.goto(`https://tico-ui.test/#/file/${MD}?task=t1`);
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'Only line'}).waitFor();
      await page.locator(`${frv} .frv-chat .th-msg[data-mid="m-1"]`).waitFor();
      assert.deepEqual(state.dm, [], 'the task\'s own thread, not a DM');
      await page.locator(`${frv} [data-frv-close]`).click();
      await page.waitForSelector('dialog.frv', {state: 'detached'});
      assert.equal(new URL(page.url()).hash, '#/task/t1', 'Close goes to the task');
      assert.deepEqual(errors, [], 'page errors');
      await page.close();
      console.log('file review (cold link): the task\'s thread, Close to the task');
    }
    // ---- phone: the file and the chat take turns
    {
      const state: State = {versions: {[MD]: [{version: 1, name: 'plan.md', body: '# Plan\n\nOn a phone\n', created_at: iso(-30 * min), actor: 'bot:cmo'}]}, outside: [], dm: [], sent: []};
      const {page, errors} = await open(browser, {width: 390, height: 800}, state, docx);
      await page.goto(`https://tico-ui.test/#/file/${MD}?bot=cmo`);
      await page.locator(`${frv} .frv-preview .md`, {hasText: 'On a phone'}).waitFor();
      assert.equal(await page.locator(`${frv} .frv-chat`).isHidden(), true, 'the file first');
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-phone-file.png')});
      await page.locator(`${frv} [data-frv-pane="chat"]`).click();
      await page.locator(`${frv} .frv-chat .th-msg[data-mid="m-1"]`).waitFor();
      assert.equal(await page.locator(`${frv} .frv-preview`).isHidden(), true, 'then the chat');
      if (shots) await page.screenshot({path: path.join(shots, 'file-review-phone-chat.png')});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no sideways scroll');
      await page.locator(`${frv} [data-frv-close]`).click();
      await page.waitForSelector('dialog.frv', {state: 'detached'});
      assert.equal(new URL(page.url()).hash, '#/bot/cmo');
      assert.deepEqual(errors, [], 'page errors');
      await page.close();
      console.log('file review (phone): file and chat take turns');
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
