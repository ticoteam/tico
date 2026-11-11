import type {Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// The live view of a website account's browser (ui/app/browser-view.ts), offline:
// - a tool page's Open browser: POST …/browser, then the picture about once a second with the page's address above it;
//   none read while the tab is hidden;
// - Take control (POST …/browser/control {take: true}): a click lands at the page's own pixels, the box types, an empty
//   box and ⏎ send Enter, ⌫ Backspace, the wheel scrolls; Give back; Done closes and gives control back;
// - a browser host that is offline: "<label> is offline";
// - the ask screen (a `browser:<tool>/<account>` show ref): the view inline at 1440, and Done answers the ask "Done";
//   at 390 an Open browser button opens it full screen with 48 px controls, no sideways scroll.
// TICO_UI_SHOTS=<dir> saves screenshots in both themes.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

interface Write {path: string; body: Record<string, unknown>}
const ANA = {subject: 'human:ana', kind: 'person', id: 'ana', name: 'Ana', initials: 'A'};
const TOOL = {tool: {id: 'w1', name: 'TIDY admin', kind: 'website', sites: ['c.tidy.com'], central_only: false}, status: {label: 'Signed in', tone: 'ok'},
  accounts: [{id: 'a1', kind: 'shared', username: 'ops@tidy.com', has_totp: true, holder: null, access: [ANA],
    session: {state: 'signed_in', where: 'central', computer: 'Mac mini', by: null}, can_use: true, can_manage: true}],
  host: {runner_id: 'c1', label: 'Mac mini', online: true}, recent: [], can_manage: true, private: false, can_set_audience: false, audience: null, seen_by: []};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:support-bot', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null}, version: 3});
const NEED = {kind: 'ask', id: 'r1', task_id: 't-r1', requirement_id: 'r1', line: 'Sign in to TIDY admin for me', since: '2026-11-09T10:00:00Z',
  task: brief('t-r1', 'Refund booking 88120'),
  ask: {requirement_id: 'r1', kind: 'answer', line: 'Sign in to TIDY admin for me', who: 'human:ana', asked_by: 'bot:support-bot', choices: ['Done'],
    may: ['answer'], met: false, shows: ['browser:w1/a1'], standing: 'waiting', answer: null}};
const PIPELINE = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false, factory_id: null, preset: null,
  settings_version: 1, retired_at: null, stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}], done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

const shots: string[] = [];
async function shot(page: import('playwright').Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  for (const theme of ['light', 'dark']) {
    await page.evaluate((t: string) => document.documentElement.setAttribute('data-theme', t), theme);
    await page.evaluate(() => Promise.all(document.getAnimations()
      .filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
    const file = path.join(dir, `browser-${name}-${theme}.png`);
    await page.screenshot({animations: 'disabled', path: file});
    shots.push(file);
  }
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));
}
const sideways = (page: import('playwright').Page) => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);

async function frameJpeg(browser: import('playwright').Browser): Promise<Buffer> {
  const p = await browser.newPage({viewport: {width: 1280, height: 800}});
  await p.setContent(`<body style="margin:0;font:16px system-ui;background:#f6f7f9;display:grid;place-items:center;height:100vh">
    <form style="width:360px;padding:32px;background:#fff;border:1px solid #dde;border-radius:12px;display:grid;gap:12px">
    <h2 style="margin:0">TIDY admin</h2><input placeholder="Email" style="height:40px;padding:0 10px"><input placeholder="Password" type="password" style="height:40px;padding:0 10px">
    <button style="height:40px;background:#1a73e8;color:#fff;border:0;border-radius:6px">Sign in</button></form></body>`);
  const buf = await p.screenshot({type: 'jpeg', quality: 70});
  await p.close();
  return buf;
}

async function start(browser: import('playwright').Browser, hash: string, width: number, frame: Buffer, {offline = false} = {}) {
  const page = await browser.newPage({viewport: {width, height: width < 500 ? 844 : 900}, serviceWorkers: 'block', hasTouch: width < 500});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  const writes: Write[] = [];
  const state = {frames: 0};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, credential_access: true, mover: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{id: 'support-bot', name: 'support-bot', display_name: 'Support Bot', status: 'active', owner_id: 'ana'}]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [PIPELINE]});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: 1, items: [NEED], version: '1', next: null});
    if (p === '/api/v2/website-tools/w1') return json(TOOL);
    if (p === '/api/v2/website-tools/w1/accounts/a1/browser/frame') {
      state.frames++;
      return route.fulfill({contentType: 'image/jpeg', headers: {'X-Page-Url': 'https://c.tidy.com/login?next=%2Fbookings', 'X-Page-Width': '1280', 'X-Page-Height': '800'}, body: frame});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (method === 'POST' && p.startsWith('/api/v2/')) {
      const body = JSON.parse(req.postData() || '{}') as Record<string, unknown>;
      writes.push({path: p, body});
      if (p === '/api/v2/website-tools/w1/accounts/a1/browser' && offline)
        return json({error: {code: 'browser_host_offline', detail: 'Mac mini is offline', computer: 'Mac mini'}}, 409);
      if (p === '/api/v2/tasks/t-r1/asks/r1/answer') return json({task: brief('t-r1', 'x'), answer_id: 'ans-1', requirement_id: 'r1'});
      return json({ok: true, where: 'central'});
    }
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  const sent = (suffix: string) => writes.filter(w => w.path.endsWith(suffix)).map(w => w.body);
  return {page, errors, writes, state, sent};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const frame = await frameJpeg(browser);
    // A tool page's Open browser, with control.
    {
      const {page, errors, state, sent} = await start(browser, '#/tools/website/w1', 1440, frame);
      await page.locator('#tool-website [data-tw-open]').click();
      const view = page.locator('dialog.bv-dialog[open] .bv');
      await view.locator('[data-bv-img]:visible').waitFor();
      assert.deepEqual(sent('/accounts/a1/browser'), [{}], 'the session is opened');
      assert.equal(await view.locator('[data-bv-url]').innerText(), 'c.tidy.com/login', 'the page address, no query');
      assert.equal(await view.locator('[data-bv-type]').isHidden(), true, 'no typing without control');
      // About once a second.
      const before = state.frames;
      await page.waitForTimeout(3200);
      const read = state.frames - before;
      assert.ok(read >= 2 && read <= 5, `about one picture a second (${read} in 3.2 s)`);
      await shot(page, 'open-desktop');
      // A click without control goes nowhere.
      await view.locator('[data-bv-img]').click();
      assert.deepEqual(sent('/browser/input'), [], 'no input without control');
      await view.locator('[data-bv-control]').click();
      await view.locator('[data-bv-you]:visible').waitFor();
      assert.deepEqual(sent('/browser/control'), [{take: true}]);
      assert.equal(await view.locator('[data-bv-control]').innerText(), 'Give back');
      // A click at the picture's middle is the page's middle; a quarter in is a quarter of the page.
      const box = (await view.locator('[data-bv-img]').boundingBox())!;
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.click(box.x + box.width / 4, box.y + box.height / 4);
      await page.waitForTimeout(200);
      const clicks = sent('/browser/input').filter(b => b.kind === 'click');
      assert.equal(clicks.length, 2, 'two clicks');
      assert.ok(Math.abs(Number(clicks[0]!.x) - 640) <= 2 && Math.abs(Number(clicks[0]!.y) - 400) <= 2, `middle: ${JSON.stringify(clicks[0])}`);
      assert.ok(Math.abs(Number(clicks[1]!.x) - 320) <= 2 && Math.abs(Number(clicks[1]!.y) - 200) <= 2, `quarter: ${JSON.stringify(clicks[1])}`);
      // Typing, Enter from an empty box, the keys row, the wheel.
      await view.locator('[data-bv-text]').fill('ops@tidy.com');
      await view.locator('[data-bv-text]').press('Enter');
      await view.locator('[data-bv-text]').press('Enter');
      await view.locator('[data-bv-key="Backspace"]').click();
      await view.locator('[data-bv-key="Tab"]').click();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.wheel(0, 300);
      await page.waitForTimeout(500);
      const rest = sent('/browser/input').filter(b => b.kind !== 'click');
      assert.deepEqual(rest.slice(0, 4), [{kind: 'type', text: 'ops@tidy.com'}, {kind: 'key', key: 'Enter'}, {kind: 'key', key: 'Backspace'}, {kind: 'key', key: 'Tab'}]);
      assert.equal(rest[4]?.kind, 'scroll');
      assert.ok(Number(rest[4]?.dy) > 0, 'scrolled down');
      await shot(page, 'control-desktop');
      // Give back, then take again; Done gives it back and closes.
      await view.locator('[data-bv-control]').click();
      await view.locator('[data-bv-control]', {hasText: 'Take control'}).waitFor();
      assert.equal(await view.locator('[data-bv-type]').isHidden(), true);
      await view.locator('[data-bv-control]').click();
      await view.locator('[data-bv-you]:visible').waitFor();
      await view.locator('[data-bv-done]').click();
      await page.locator('dialog.bv-dialog').waitFor({state: 'detached'});
      await page.waitForTimeout(200);
      assert.deepEqual(sent('/browser/control'), [{take: true}, {take: false}, {take: true}, {take: false}], 'Done gives control back');
      // Closed: nothing more is read.
      const after = state.frames;
      await page.waitForTimeout(1500);
      assert.equal(state.frames, after, 'a closed view reads nothing');
      // A hidden tab reads nothing; shown again, it goes on.
      await page.locator('#tool-website [data-tw-open]').click();
      await page.locator('dialog.bv-dialog[open] [data-bv-img]:visible').waitFor();
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange')); });
      await page.waitForTimeout(300);
      const hidden = state.frames;
      await page.waitForTimeout(2200);
      assert.equal(state.frames, hidden, 'paused while hidden');
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange')); });
      await page.waitForTimeout(1200);
      assert.ok(state.frames > hidden, 'goes on when shown');
      await page.locator('dialog.bv-dialog [data-bv-close]').click();
      await page.locator('dialog.bv-dialog').waitFor({state: 'detached'});
      assert.deepEqual(errors, [], 'no page errors');
      await page.close();
    }
    // The browser host is offline.
    {
      const {page, errors, state} = await start(browser, '#/tools/website/w1', 1440, frame, {offline: true});
      await page.locator('#tool-website [data-tw-open]').click();
      await page.locator('dialog.bv-dialog[open] [data-bv-msg]', {hasText: 'Mac mini is offline'}).waitFor();
      await page.waitForTimeout(1200);
      assert.equal(state.frames, 0, 'no pictures from an offline host');
      await shot(page, 'offline-desktop');
      assert.deepEqual(errors, [], 'offline: no page errors');
      await page.close();
    }
    // The ask screen: inline at 1440, full screen at 390; Done answers "Done".
    for (const width of [1440, 390]) {
      const tag = width < 500 ? 'phone' : 'desktop';
      const {page, errors, sent} = await start(browser, '#/tasks', width, frame);
      await page.locator('[data-need="r1"]').first().waitFor();
      await page.locator('#task-play').click();
      const card = page.locator('#need-card');
      await card.locator('.dc-browser').waitFor();
      await card.locator('[data-dc-choice="Done"]').waitFor();
      let view = card.locator('.dc-browser .bv');
      if (tag === 'phone') {
        await card.locator('.dc-browser [data-bv-launch]').waitFor();
        assert.equal(await sideways(page), false, 'phone: the ask has no sideways scroll');
        await shot(page, `ask-${tag}`);
        await card.locator('.dc-browser [data-bv-launch]').click();
        view = page.locator('dialog.bv-dialog[open] .bv');
      }
      await view.locator('[data-bv-img]:visible').waitFor();
      assert.equal(await view.locator('[data-bv-url]').innerText(), 'c.tidy.com/login');
      if (tag === 'phone') {
        await view.locator('[data-bv-control]').click();
        await view.locator('[data-bv-you]:visible').waitFor();
        for (const sel of ['[data-bv-control]', '[data-bv-done]', '[data-bv-key="Enter"]', '[data-bv-text]', '[data-bv-close]']) {
          const h = (await view.locator(sel).boundingBox())!.height;
          assert.ok(h >= 47.5, `phone: ${sel} is 48 px (${h})`);
        }
        const dialogBox = (await page.locator('dialog.bv-dialog').boundingBox())!;
        assert.ok(dialogBox.width >= 389 && dialogBox.height >= 843, `phone: full screen (${dialogBox.width}x${dialogBox.height})`);
        // A tap is a click at the page's pixels.
        const img = (await view.locator('[data-bv-img]').boundingBox())!;
        await page.touchscreen.tap(img.x + img.width / 2, img.y + img.height / 2);
        await page.waitForTimeout(300);
        const tap = sent('/browser/input').find(b => b.kind === 'click');
        assert.ok(tap && Math.abs(Number(tap.x) - 640) <= 3 && Math.abs(Number(tap.y) - 400) <= 3, `phone: tap mapped (${JSON.stringify(tap)})`);
        assert.equal(await sideways(page), false, 'phone: the view has no sideways scroll');
      } else {
        assert.equal(await sideways(page), false, 'desktop: no sideways scroll');
      }
      await shot(page, `ask-view-${tag}`);
      await view.locator('[data-bv-done]').click();
      await card.locator('.dc-bar:not([hidden])').waitFor();
      assert.equal(sent('/api/v2/tasks/t-r1/asks/r1/answer')[0]?.value, 'Done', `${tag}: Done answers the ask`);
      await page.locator('dialog.bv-dialog').waitFor({state: 'detached', timeout: 3000});   // the view is closed
      assert.deepEqual(errors, [], `${tag}: no page errors`);
      await page.close();
    }
    if (shots.length) console.log(`screenshots:\n${shots.join('\n')}`);
    console.log('browser-view: ok');
  } finally {
    await browser.close();
  }
})().catch((e: unknown) => { console.error(e); process.exit(1); });
