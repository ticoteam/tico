// The viewer's own Granola account on the Meetings page (ui/app/meetings-granola.ts), shown only in Sources
// (ui/app/meetings-sources.ts): Connect opens a dialog with a short code and an Open Granola link, polls the connect
// status every `interval` seconds until connected, expired or denied; a connected account syncs once when the page
// opens and the list refreshes; Sync now and Remove (confirmed) sit on its row; a signed-out account offers
// "Sign in again" and a dot on Sources; the API-key importer is a dialog link.
// Fixtures only, no network. GRANOLA_SHOTS=<dir> also saves screenshots.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
import type {Browser, LaunchOptions, Page} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RoutesSupport from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RoutesSupport.Api;
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
interface World { role: string; meetings: unknown[]; granola: Record<string, unknown>; syncSteps: number; syncLeft: number; finish: (() => void) | null; polls: {state: string; email?: string}[]; calls: string[]; listed: number; missing?: boolean; code?: Record<string, unknown> }
// Page-defined state of the Granola module (ui/app/meetings-granola.ts), read inside page.evaluate callbacks.
declare const MEET: {gTimer: unknown; gSyncTimer: unknown; gflow: unknown; gSyncing: unknown};

const shots = process.env.GRANOLA_SHOTS || '';
if (shots) fs.mkdirSync(shots, {recursive: true});
const now = Date.now();
const ago = (minutes: number) => new Date(now - minutes * 6e4).toISOString();
const meeting = (id: string, title: string, minutes: number) => ({id, title, source: 'granola', kind: 'meeting', status: 'done', started: ago(minutes), created: ago(minutes),
  duration_ms: 1800000, participants: [{name: 'Ana', email: 'ana@acme.example'}, {name: 'Ben'}], outbox: {doc: [], task: [], feature: []}});
// Shapes as the server sends them (OpenAPI GranolaStatus / GranolaSync); `syncing` is filled in by the fake below.
const OFF = {mode: 'off', connected: false, plan_hint: null, last_synced_at: null, last_error: null, imported_count: 0, skipped: 0, needs_signin: false};
const ON = {mode: 'account', connected: true, email: 'ana@acme.example', plan_hint: 'free', last_synced_at: ago(3), last_error: null, imported_count: 42, skipped: 0, needs_signin: false};
const CODE = {user_code: 'WDJB-MJHT', verification_uri: 'https://mcp-auth.granola.ai/device',
  verification_uri_complete: 'https://mcp-auth.granola.ai/device?user_code=WDJB-MJHT', expires_in: 600, interval: 3};

async function open(browser: Browser, viewport: {width: number; height: number}, w: World, scheme: 'light' | 'dark' = 'dark') {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', colorScheme: scheme, hasTouch: viewport.width < 760, isMobile: viewport.width < 760});
  await context.addInitScript((theme: string) => { try { localStorage.setItem('tico.theme', theme); } catch {} }, scheme);
  const page = await context.newPage();
  // Install before the pause target: a busy machine can take time between the two protocol calls.
  await page.clock.install({time: new Date(now - 3600000)});
  await page.clock.pauseAt(new Date(now));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: w.role, name: 'Ana', email: 'ana@acme.example', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/setup/getting-started') return json({items: [], done: 0, total: 0, complete: true, dismissed: true, tour_seen: true, cards_dismissed: [],
      can_build: true, owner: true, empty: {docs: true, market: true, tasks: true, updates: true, goals: true, meetings: true}});
    if (p === '/api/meetings/sources') return json({sources: [{id: 'import', name: 'Import', status: 'available'}]});
    if (p === '/api/v2/meetings') { w.listed++; return json({meetings: w.meetings, pending_count: 0}); }
    if (p === '/api/v2/meeting-importers') return json({computers: [{id: 'mac', label: 'Test Mac', platform: 'macos', online: true}],
      importers: [{source: 'granola', name: 'Granola', enabled: false, runner_id: '', status: 'off', imported_total: 0, error: '',
        setup: {file: 'secrets/granola.env', keys: ['GRANOLA_API_KEY'], doc: 'docs/meetings.md'}}]});
    if (p === '/api/v2/meetings/granola') {
      w.calls.push('status');
      if (w.missing) return json({error: {detail: 'Not found'}}, 404);
      // A sync runs for `syncLeft` more status reads, then `finish` lands its result.
      if (w.syncLeft > 0) { w.syncLeft--; return json({...w.granola, syncing: true}); }
      if (w.finish && count(w, 'sync')) { const finish = w.finish; w.finish = null; finish(); }
      return json({...w.granola, syncing: false});
    }
    if (p === '/api/v2/meetings/granola/connect' && method === 'POST') { w.calls.push('connect'); return json(w.code || CODE); }
    if (p === '/api/v2/meetings/granola/connect' && method === 'DELETE') { w.calls.push('disconnect'); w.granola = {...OFF}; return json({ok: true}); }
    if (p === '/api/v2/meetings/granola/connect/status') {
      w.calls.push('poll');
      const next = w.polls.length > 1 ? w.polls.shift()! : w.polls[0]!;
      if (next.state === 'connected') w.granola = {...ON, email: next.email, last_synced_at: null, imported_count: 0};
      return json(next);
    }
    if (p === '/api/v2/meetings/granola/sync' && method === 'POST') {
      w.calls.push('sync');
      w.syncLeft = w.syncSteps;
      return json({state: 'syncing', last_synced_at: w.granola.last_synced_at});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/meetings');
  return {page, errors, context};
}
const world = (extra: Partial<World> = {}): World => ({role: 'owner', meetings: [meeting('g1', 'Renewal call with Dana', 300)], granola: {...OFF}, syncSteps: 0, syncLeft: 0, finish: null,
  polls: [{state: 'pending'}], calls: [], listed: 0, ...extra});
// Granola's row in Sources, opened when it is not open yet.
const G = '#meet-src-dialog [data-src=granola]';
const sources = async (page: Page) => {
  if (await page.locator('#meet-src-dialog[open]').count()) return;
  await page.locator('.notes-head .ph-more').click();   // Sources is in the header's ⋯ (task #338)
  await page.locator('#meet-sources .meet-src-btn').click();
  await page.locator(G).waitFor();
};
// The row's facts as read on screen: CSS puts the ' · ' between them.
const lineText = (page: Page) => page.locator(G + ' .msrc-line').evaluate((el: Element) => [...(el.children as unknown as Element[])].map(c => c.textContent!.trim()).join(' · '));
const count = (w: World, name: string) => w.calls.filter(c => c === name).length;
const until = async (fn: () => unknown, what: string, ms = 10000) => {
  for (const end = Date.now() + t(ms); !await fn();) { if (Date.now() > end) throw new Error('timed out: ' + what); await new Promise(r => setTimeout(r, 10)); }
};
// HTTP responses finish in real time; wait for each handler to arm its next fake timer before advancing again.
const poll = async (page: Page, w: World, name: string, ms: number) => {
  const before = count(w, name);
  const timer = await page.evaluate(name => name === 'poll' ? MEET.gTimer : MEET.gSyncTimer, name);
  await page.clock.fastForward(ms);
  await until(() => count(w, name) === before + 1, name + ' request');
  await until(() => page.evaluate(([name, timer]: unknown[]) => name === 'poll' ? !MEET.gflow || (!!MEET.gTimer && MEET.gTimer !== timer)
    : !MEET.gSyncing || (!!MEET.gSyncTimer && MEET.gSyncTimer !== timer), [name, timer]), name + ' response');
};
const shot = async (page: Page, name: string) => { if (shots) await page.screenshot({path: path.join(shots, name + '.png')}); };

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  const desk = {width: 1280, height: 800};
  try {
    // Functional contracts run once; both themes and viewports keep the layout checks below.
    {
      const scheme = 'dark';
      // ---- connect: Connect Granola -> code + Open Granola -> pending, pending -> connected -> one sync
      let w = world({polls: [{state: 'pending'}, {state: 'pending'}, {state: 'connected', email: 'ana@acme.example'}]});
      let {page, errors, context} = await open(browser, desk, w, scheme);
      await page.locator('.meet-row').first().waitFor();
      assert.equal(await page.locator('#meet-granola, .mg-row').count(), 0, 'no Granola row on the page');
      await sources(page);
      const connect = page.locator(G + ' [data-act=g-connect]');
      assert.equal(await connect.innerText(), 'Connect');
      assert.equal(await page.locator('#meet-src-dialog ul[aria-label="Not connected"] [data-src=granola]').count(), 1);
      assert.equal(await lineText(page), 'Granola');
      assert.equal(await page.locator(G + ' .mg-key').count(), 0, 'the key link lives in the dialog');
      assert.equal(count(w, 'sync'), 0, 'no sync when not connected');
      await connect.click();
      await page.locator('#mg-dialog #mg-code').waitFor();
      await page.clock.fastForward(30);
      assert.equal(await page.locator('#mg-code').innerText(), 'WDJB-MJHT');
      assert.equal(await page.locator('#mg-dialog [data-g=open]').getAttribute('href'), CODE.verification_uri_complete);
      assert.equal(await page.locator('#mg-dialog [data-g=open]').getAttribute('target'), '_blank');
      assert.equal(await page.locator('#mg-dialog .mg-key').innerText(), 'Use an API key');
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.g), 'open', 'focus moves to Open Granola');
      assert.equal(await page.locator('#mg-live').getAttribute('aria-live'), 'polite');
      await page.waitForFunction(() => /Enter it in Granola/.test(document.querySelector('#mg-live')?.textContent || ''));
      await shot(page, `granola-code-${scheme}`);
      await page.clock.fastForward(CODE.interval * 1000 - 31);
      assert.equal(count(w, 'poll'), 0, 'the server interval is respected');
      await poll(page, w, 'poll', 1);
      await poll(page, w, 'poll', CODE.interval * 1000);
      await poll(page, w, 'poll', CODE.interval * 1000);
      await page.locator(G + ' [data-act=g-remove]').waitFor();
      assert.equal(count(w, 'poll'), 3, 'polled until connected');
      await page.clock.fastForward(30);
      await page.waitForFunction(() => /Granola connected/.test(document.querySelector('#mg-live')?.textContent || ''));
      await until(() => count(w, 'sync') === 1, 'a new connection syncs once');
      assert.equal(count(w, 'connect'), 1);
      assert.match(await lineText(page), /^Granola · Connected · ana@acme\.example · (Syncing…|Not synced yet|Synced .+) · \d+ notes?$/);
      assert.equal(await page.locator('#meet-src-dialog ul[aria-label=Connected] [data-src=granola]').count(), 1, 'connected moves up');
      assert.equal(await page.evaluate(() => (document.activeElement?.closest('[data-src]') as HTMLElement | null)?.dataset.src), 'granola', 'focus lands on the row');
      assert.equal(await page.locator('#mg-code').count(), 0);
      assert.equal(await page.locator('#mg-dialog[open]').count(), 0, 'the dialog closes once connected');
      const polls = count(w, 'poll');
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'poll'), polls, 'polling stops once connected');
      assert.deepEqual(errors, []);
      await context.close();

      // ---- expired and denied: one short line, Connect Granola again, polling stops
      for (const [state, words] of [['expired', 'The code expired. Try again.'], ['denied', 'Granola access was denied.'], ['needs_signin', 'Granola sign-in failed. Try again.']] as const) {
        w = world({polls: [{state: 'pending'}, {state}]});
        ({page, errors, context} = await open(browser, desk, w, scheme));
        await sources(page);
        await page.locator(G + ' [data-act=g-connect]').click();
        await page.locator('#mg-code').waitFor();
        await poll(page, w, 'poll', CODE.interval * 1000);
        await poll(page, w, 'poll', CODE.interval * 1000);
        await page.locator(G + ' .msrc-err').waitFor();
        assert.equal(await page.locator(G + ' .msrc-err').innerText(), words);
        assert.equal(await page.locator(G + ' .msrc-err').getAttribute('role'), 'alert');
        assert.equal(await page.locator('#mg-code').count(), 0);
        assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.act), 'g-connect', state + ': focus on Connect');
        // Signed out is a dot on Sources; a code that expired or was denied is not.
        assert.equal(await page.locator('#meet-sources .meet-src-btn .dot').count(), state === 'needs_signin' ? 1 : 0);
        const n = count(w, 'poll');
        await page.clock.fastForward(15000);
        assert.equal(count(w, 'poll'), n, state + ': polling stops');
        assert.equal(count(w, 'sync'), 0);
        assert.deepEqual(errors, []);
        await context.close();
      }

      // ---- connected: sync once on open, list refreshes when last_synced_at moves; free plan line; Sync and Disconnect in …
      const before = ago(30);
      w = world({granola: {...ON, last_synced_at: before, imported_count: 41}, syncSteps: 1});
      w.finish = () => { w.granola = {...ON, last_synced_at: ago(3), imported_count: 42, skipped: 2}; w.meetings = [meeting('g2', 'Pricing review', 5), ...w.meetings]; };
      ({page, errors, context} = await open(browser, desk, w, scheme));
      await sources(page);
      await page.locator(G + ' .mg-syncing .mg-spin').waitFor();
      assert.match(await lineText(page), /· Syncing… · 41 notes$/);
      await until(() => page.evaluate(() => !!MEET.gSyncTimer), 'sync timer armed');
      await page.clock.fastForward(2999);
      assert.equal(count(w, 'status'), 1, 'no status poll before three seconds');
      await poll(page, w, 'status', 1);
      assert.equal(await page.locator('.meet-row').count(), 1, 'the list waits for the sync to finish');
      await page.clock.fastForward(4499);
      assert.equal(count(w, 'status'), 2, 'the next sync poll backs off to 4.5 seconds');
      await poll(page, w, 'status', 1);
      await page.waitForFunction(() => document.querySelectorAll('.meet-row').length === 2, null, {timeout: t(12000)});
      assert.equal(count(w, 'sync'), 1, 'one sync when the page opens');
      assert.deepEqual(await page.locator('.meet-row .note-title').allInnerTexts(), ['Pricing review', 'Renewal call with Dana']);
      await page.waitForFunction((G: string) => /42 notes/.test(document.querySelector(G + ' .msrc-line')?.textContent || ''), G);
      assert.equal(await lineText(page), 'Granola · Connected · ana@acme.example · Synced 3m ago · 42 notes · 2 skipped');
      assert.equal(await page.locator(G + ' .mg-skip').getAttribute('title'), '2 notes from Granola could not be imported');
      const polled = count(w, 'status');
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'status'), polled, 'polling stops once the sync is done');
      assert.equal(await page.locator(G + ' .msrc-line > span[title^="Free plan"]').innerText(), '42 notes');
      assert.equal(await page.locator(G + ' [data-act=g-connect]').count(), 0);
      assert.equal(await page.locator('#meet-sources .meet-src-btn .dot').count(), 0, 'no dot while all is well');
      await shot(page, `granola-connected-${scheme}`);
      await page.locator(G + ' [data-act=g-sync]').click();
      await until(() => count(w, 'sync') === 2, 'Sync asks again');
      await page.waitForFunction((G: string) => /Syncing…/.test(document.querySelector(G + ' .msrc-line')?.textContent || ''), G);
      // Remove asks first; a dismissed confirm keeps the account.
      page.once('dialog', d => d.dismiss());
      await page.locator(G + ' [data-act=g-remove]').click();
      assert.equal(count(w, 'disconnect'), 0);
      page.once('dialog', d => { assert.equal(d.message(), 'Remove Granola?'); d.accept(); });
      await page.locator(G + ' [data-act=g-remove]').click();
      await page.locator(G + ' [data-act=g-connect]').waitFor();
      await page.clock.fastForward(30);
      assert.equal(count(w, 'disconnect'), 1);
      assert.equal(await lineText(page), 'Granola');
      await page.waitForFunction(() => /Granola disconnected/.test(document.querySelector('#mg-live')?.textContent || ''));
      assert.deepEqual(errors, []);
      await context.close();

      // ---- needs sign-in: no sync on open; "Sign in to Granola again" restarts the flow
      w = world({granola: {...ON, connected: false, plan_hint: 'paid', needs_signin: true, last_error: 'Granola needs sign-in again'}});
      ({page, errors, context} = await open(browser, desk, w, scheme));
      await page.locator('#meet-sources .meet-src-btn .dot').waitFor({state: 'attached'});
      await page.locator('.notes-head .ph-more.meet-src-trouble').waitFor();   // the closed ⋯ shows it too
      await sources(page);
      const again = page.locator(G + ' [data-act=g-connect]');
      assert.equal(await again.innerText(), 'Sign in again');
      assert.equal(await lineText(page), 'Granola · Signed out · ana@acme.example');
      assert.equal(await page.locator(G + ' .msrc-err').innerText(), 'Granola needs sign-in again');
      assert.equal(await page.locator(G + ' [data-act=g-remove]').count(), 1, 'Remove stays reachable');
      await shot(page, `granola-needs-signin-${scheme}`);
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'sync'), 0, 'no sync while signed out');
      await again.click();
      await page.locator('#mg-code').waitFor();
      assert.equal(count(w, 'connect'), 1);
      await page.locator('#mg-dialog footer [data-g=cancel]').click();
      assert.equal(await page.locator('#mg-code').count(), 0);
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'poll'), 0, 'cancel stops the device-code timer');
      assert.deepEqual(errors, []);
      await context.close();
    }

    // ---- the API-key importer stays reachable for the owner
    let w = world();
    let {page, errors, context} = await open(browser, desk, w);
    await sources(page);
    await page.locator(G + ' [data-act=g-connect]').click();
    await page.locator('#mg-dialog .mg-key').click();
    await page.locator('dialog.import-modal[aria-label="Connect Granola"] form[data-importer=granola]').waitFor();
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    await context.close();

    // ---- a member: no key link, but Connect works for their own account
    w = world({role: 'member'});
    ({page, errors, context} = await open(browser, desk, w));
    await sources(page);
    assert.equal(await page.locator(G + ' [data-act=g-connect]').isDisabled(), false);
    await page.locator(G + ' [data-act=g-connect]').click();
    await page.locator('#mg-code').waitFor();
    assert.equal(await page.locator('#mg-dialog .mg-key').count(), 0);
    await page.locator('#mg-dialog footer [data-g=cancel]').click();
    assert.equal(await page.locator('#meet-src-dialog [data-src=zoom] [data-act=imp-connect]').isDisabled(), true);
    await context.close();

    // ---- an older server without the route: no row, the tile opens the API-key importer as before
    w = world({missing: true});
    ({page, errors, context} = await open(browser, desk, w));
    await page.locator('.meet-row').first().waitFor();
    await until(() => count(w, 'status') > 0, 'status asked');
    await sources(page);
    assert.equal(await page.locator(G + ' [data-act=g-connect]').count(), 0, 'no account sign-in');
    await page.locator(G + ' [data-act=imp-connect]').click();
    await page.locator('dialog[aria-label="Connect Granola"] form[data-importer=granola]').waitFor();
    await context.close();

    // ---- a sign-in link off Granola's own https host is not a link: the code and the host as text
    w = world({code: {...CODE, verification_uri: 'https://granola.example.net/device', verification_uri_complete: 'javascript:alert(1)'}});
    ({page, errors, context} = await open(browser, desk, w));
    await sources(page);
    await page.locator(G + ' [data-act=g-connect]').click();
    await page.locator('#mg-code').waitFor();
    assert.equal(await page.locator('#mg-dialog a').count(), 0, 'no link');
    assert.equal(await page.locator('#mg-dialog .mg-host').innerText(), 'granola.example.net');
    assert.deepEqual(errors, []);
    await context.close();

    // ---- a sync already running when the page opens (the schedule): follow it, no second sync
    w = world({granola: {...ON}, syncLeft: 1});
    ({page, errors, context} = await open(browser, desk, w));
    await sources(page);
    await page.locator(G + ' .mg-syncing').waitFor();
    await until(() => page.evaluate(() => !!MEET.gSyncTimer), 'existing sync timer armed');
    await poll(page, w, 'status', 3000);
    await page.locator(G + ' .mg-syncing').waitFor({state: 'detached', timeout: t(8000)});
    assert.equal(count(w, 'sync'), 0, 'no sync asked while one runs');
    assert.deepEqual(errors, []);
    await context.close();

    // ---- desktop and phone, both themes: code, account row and menu fit without sideways scroll
    for (const [device, viewport] of [['desktop', desk], ['phone', {width: 390, height: 844}]] as [string, {width: number; height: number}][]) {
      for (const scheme of ['dark', 'light'] as const) {
        w = world();
        ({page, errors, context} = await open(browser, viewport, w, scheme));
        await sources(page);
        await page.locator(G + ' [data-act=g-connect]').click();
        await page.locator('#mg-code').waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${device}-${scheme}: code fits`);
        const box = await page.locator('#mg-dialog [data-g=open]').boundingBox();
        assert(box && box.x + box.width <= viewport.width, 'Open Granola is on screen');
        await shot(page, `granola-${device}-code-${scheme}`);
        w.granola = {...ON, email: 'ana.longname@acme-example-company.example'};
        await page.reload();
        await sources(page);
        await page.locator(G + ' [data-act=g-remove]').waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${device}-${scheme}: connected row fits`);
        assert(await page.locator('#meet-src-dialog').evaluate(d => d.scrollWidth <= d.clientWidth), `${device}-${scheme}: panel fits`);
        const remove = (await page.locator(G + ' [data-act=g-remove]').boundingBox())!;
        assert(remove.x >= 16 && remove.x + remove.width <= viewport.width - 16, 'Remove on screen');
        await shot(page, `granola-${device}-connected-${scheme}`);
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    console.log('meetings-granola ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
