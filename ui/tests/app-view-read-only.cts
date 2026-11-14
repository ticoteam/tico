// A bot's read-only view of the app (backend/app_view.py) on a real hub (ui/tests/support/real-hub.cts). The bot's run
// opens a view and the page is signed in with the view's token, as `tico bot app-shot` does. On Tasks, a task window,
// Company › Mine, Company › Dashboard and a thread, every visible, enabled button and field is a read on this test's
// allowlist: New task, Follow, the page +, answer buttons and the message box do not show, and the box is a grey
// "Read-only view" line. Tasks reads the bot's name and its open tasks by stage, with a line that Needs you is a
// person's list; Company › Mine is one card of the bot's tasks. A Company place that fails to load says "This page
// couldn't load." with Try again, never the server's text. A person sees New task and the message box as before.
// The bot's list keeps up through the refresh (an edit, a new task, one closed or given away) and pages with More, and C
// opens no New task dialog in a view.
import type {Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};
declare const S: {me?: {name?: string}};
declare function refresh(force?: boolean): Promise<void>;

const BOT = 'builder-claude';
const OWN = 'The bot works on this';

async function call<T = Record<string, unknown>>(hub: RealHubSupport.Hub, token: string, method: string, p: string, body?: unknown): Promise<T> {
  const r = await fetch(hub.url + '/api' + p, {method, headers: {Authorization: 'Bearer ' + token, 'content-type': 'application/json',
    ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})}, body: (body === undefined ? undefined : JSON.stringify(body)) as string});
  const json = await r.json().catch(() => ({}));
  assert.ok(r.status < 300, `${method} ${p}: ${r.status} ${JSON.stringify(json)}`);
  return json as T;
}

// Kept apart from view-only.ts's VIEW_READS on purpose: the test checks that list rather than reading it.
// The reads a view may offer, by what the control is: moving between places, opening, searching, filtering, closing
// and showing more. Anything else visible and enabled is a write a screenshot must not offer.
const READS = [
  '#side a', '#side button', '#mobile-nav button', '#account', '#side-search', '#nav-toggle',
  '#task-scope', '#task-q', '#task-filter', '#task-menu', '#task-mi-archive', '#task-mi-layout button', '#task-archive-x',
  '#task-archives button',
  '.tl-mi', '#task-more button', '.tk-row', '[data-task]', '[data-reload]', '[data-dash-retry]',
  '.dash-tabs a', '.dash-tabs button', '[role="tab"]', '.tmodal [aria-label="Close"]', '.tmodal .x', '[data-modal-close]', '[data-task-earlier]', '[data-task-tab]',
  '[data-show-more]', '.linkish[data-open]', 'a[href]',
];

// Every visible, enabled control under the main page and any open dialog, as a short description, when it is not a read.
const writesShown = (page: Page) => page.evaluate((reads: string[]) => {
  const out: string[] = [];
  const shown = (el: Element) => { const r = (el as HTMLElement).getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  for (const el of document.querySelectorAll('button, input, textarea, select, [contenteditable="true"], [role="button"], [role="checkbox"], [role="switch"], [role="menuitem"], [role="menuitemradio"]')) {
    if (!shown(el)) continue;
    if ((el as HTMLButtonElement).disabled || el.getAttribute('aria-disabled') === 'true' || el.closest('[inert]')) continue;
    if (reads.some(sel => el.matches(sel) || el.closest(sel) === el)) continue;
    if (el.closest('#side')) continue;
    out.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : ''} "${((el as HTMLElement).innerText || el.getAttribute('aria-label') || (el as HTMLInputElement).placeholder || '').trim().slice(0, 40)}"`);
  }
  return out;
}, READS);

const taskId = (r: {task?: {id?: string}; id?: string}) => String(r.task?.id ?? r.id ?? '');
const go = async (page: Page, hash: string) => { await page.evaluate(h => { location.hash = h; }, hash); };
const ready = (page: Page) => page.waitForFunction(() => {
  const who = document.querySelector('#account .account-email')?.textContent || '';
  return who !== '' && who !== 'Signing in…' && (document.querySelector('#main')?.children.length as number) > 0;
}, null, {timeout: t(20000)});

(async () => {
  const hub = await startHub({seeds: ['org']});
  let browser: import('playwright').Browser | undefined;
  try {
    const dana = hub.tokens.dana!;
    // The bot owns one open task, and an owner turns its view on.
    const own = taskId(await call(hub, dana, 'POST', '/v2/tasks', {title: OWN, body: 'Its own work.', owner: BOT}));
    await call(hub, dana, 'POST', `/v2/bots/${BOT}/app-view`, {on: true});
    // Its run, claimed and started as its computer would, opens the view.
    const {attempt} = await call<{attempt?: {id: string; token: string}}>(hub, hub.runner, 'POST', '/v2/jobs/claim', {bot: BOT, start_places: true});
    if (!attempt) throw new Error('the bot has a run to claim');
    await call(hub, hub.runner, 'POST', `/v2/attempts/${attempt.id}/started`, {thread_id: 'local-thread'});
    const view = await call<{token: string}>(hub, attempt.token, 'POST', '/v2/app-view', {});
    assert.ok(view.token.startsWith('tico_av_'));

    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const context = await browser.newContext({viewport: {width: 1280, height: 800}, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + view.token}});
    const page = await context.newPage();
    const writes: string[] = [];
    page.on('request', r => { if (!['GET', 'HEAD', 'OPTIONS'].includes(r.method()) && r.url().startsWith(hub.url)) writes.push(`${r.method()} ${r.url()}`); });
    await page.goto(hub.url + '/#/tasks');
    await ready(page);
    const name = await page.evaluate(() => S.me?.name || '');
    assert.ok(name, 'the view knows the bot by name');

    // Tasks: the bot's name with its count, the line about Needs you, and its open task under its stage.
    await page.locator('#task-body .tk-row', {hasText: OWN}).waitFor({timeout: t(15000)});
    assert.match(await page.locator('#task-scope').innerText(), new RegExp(`^${name}\\s*1`), 'the scope is the bot, with its count');
    assert.match(await page.locator('#task-body').innerText(), /Needs you is a person's list/);
    assert.ok(await page.locator('#task-body .tk-group').count() >= 1, 'its tasks are grouped by stage');
    assert.deepEqual(await writesShown(page), [], 'Tasks offers nothing that writes');
    // Its ⋯ menu keeps Archived and drops New task, and C opens no New task dialog.
    await page.locator('#task-menu').click();
    await page.locator('#task-mi-archive').waitFor({state: 'visible', timeout: t(15000)});
    assert.equal(await page.locator('#task-mi-new').isVisible(), false, 'no New task in ⋯');
    assert.deepEqual(await writesShown(page), [], 'the Tasks ⋯ menu offers nothing that writes');
    await page.keyboard.press('Escape');
    await page.locator('#task-body').click({position: {x: 5, y: 5}});
    await page.keyboard.press('c');
    await page.waitForTimeout(300);
    assert.equal(await page.locator('dialog[open]').count(), 0, 'C opens no New task dialog in a view');

    // The bot's list keeps up: an edit shows, a new task for it comes in, one closed or given away goes out. A view has no
    // live stream (the server refuses it one), so the app's periodic refresh catches up from the list's cursor.
    const row = (title: string) => page.locator('#task-body .tk-row', {hasText: title});
    const poll = () => page.evaluate(() => refresh(false));
    await call(hub, dana, 'POST', `/v2/tasks/${own}`, {title: OWN + ' today'});
    await poll();
    await row(OWN + ' today').waitFor({timeout: t(15000)});
    const second = taskId(await call(hub, dana, 'POST', '/v2/tasks', {title: 'Second for the bot', body: 'More work.', owner: BOT}));
    await poll();
    await row('Second for the bot').waitFor({timeout: t(15000)});
    const third = taskId(await call(hub, dana, 'POST', '/v2/tasks', {title: 'Third for the bot', body: 'More work.', owner: BOT}));
    await poll();
    await row('Third for the bot').waitFor({timeout: t(15000)});
    await call(hub, dana, 'POST', `/v2/tasks/${third}/owner`, {owner: 'dana', why: 'Dana takes it'});
    await poll();
    await row('Third for the bot').waitFor({state: 'detached', timeout: t(15000)});
    // A later page: read one row at a time, More brings the next and keeps it.
    await page.route(/\/api\/v2\/tasks\?.*owner=/, r => r.continue({url: r.request().url().replace(/([?&])limit=\d+/, '$1limit=1')}));
    await go(page, '#/home');
    await page.locator('#dash-grid').waitFor({timeout: t(15000)});
    await go(page, '#/tasks');
    await page.locator('#task-more button').waitFor({state: 'visible', timeout: t(15000)});
    assert.equal(await page.locator('#task-body .tk-row').count(), 1, 'one row a page');
    assert.deepEqual(await writesShown(page), [], 'More is a read');
    await page.locator('#task-more button').click();
    await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-row').length === 2, null, {timeout: t(15000)});
    await page.unroute(/\/api\/v2\/tasks\?.*owner=/);
    await call(hub, dana, 'POST', `/v2/tasks/${second}/move`, {to: 'closed', why: 'Not needed'});
    await poll();
    await row('Second for the bot').waitFor({state: 'detached', timeout: t(15000)});
    assert.ok(await row(OWN).count() === 1, 'its own open task stays');

    // A task window: no Follow, no page +, no answer buttons, no box; the box is a grey "Read-only view" line.
    await page.locator('#task-body .tk-row', {hasText: OWN}).click();
    await page.locator('.tmodal[open]').waitFor({timeout: t(15000)});
    await page.locator('.tmodal[open] .view-only-line').waitFor({timeout: t(15000)});
    assert.equal(await page.locator('.tmodal[open] .view-only-line').innerText(), 'Read-only view');
    assert.deepEqual(await writesShown(page), [], 'the task window offers nothing that writes');
    // Nothing that only writes shows, even greyed out: the page +, Ask someone and Follow are gone; × still closes.
    assert.equal(await page.locator('.tmodal[open] :is([data-tp-add], [data-req-ask], [data-prop-follow])').evaluateAll(els => els.filter(e => (e as HTMLElement).offsetWidth > 0).length), 0, 'no greyed-out write in the task window');
    await page.locator('.tmodal[open] [data-modal-close]').click();
    await page.locator('.tmodal[open]').waitFor({state: 'detached', timeout: t(15000)});

    // Company › Mine: one card, the bot's tasks, and no error.
    await go(page, '#/home');
    await page.locator('#dash-grid', {hasText: OWN}).waitFor({timeout: t(15000)});
    const grid = await page.locator('#dash-grid').innerText();
    assert.ok(grid.includes(`${name}'s tasks`) && !/No such place/.test(grid), grid);
    assert.deepEqual(await writesShown(page), [], 'Company › Mine offers nothing that writes');

    // Company › Dashboard.
    await go(page, '#/overview');
    await page.locator('#dash-grid .dash-pin, #dash-grid .dash-failed, #dash-grid .empty').first().waitFor({timeout: t(15000)});
    assert.deepEqual(await writesShown(page), [], 'the Dashboard offers nothing that writes');

    // A thread: the bot's own DM page.
    await go(page, `#/bot/${BOT}`);
    await page.locator('#main .view-only-line').waitFor({timeout: t(15000)});
    assert.deepEqual(await writesShown(page), [], 'a thread offers nothing that writes');
    // A write drawn later (a goal that loads after the page) is greyed out in the turn it is drawn, not a frame later.
    assert.equal(await page.evaluate(async () => {
      const b = document.createElement('button');
      b.textContent = 'Set goal';
      document.querySelector('#main')!.append(b);
      await Promise.resolve();
      const off = b.disabled;
      b.remove();
      return off;
    }), true, 'a write drawn late is off at once');
    // A phone: the bottom bar moves between places (Team, Assistant, More), so none of it is greyed out.
    await page.setViewportSize({width: 390, height: 844});
    await go(page, '#/tasks');
    await page.locator('#task-body .tk-row').first().waitFor({timeout: t(15000)});
    assert.deepEqual(await page.locator('#mobile-nav button:visible').evaluateAll(bs => bs.filter(b => (b as HTMLButtonElement).disabled).map(b => b.id)), [], 'the phone bar is all reads');
    assert.deepEqual(await writesShown(page), [], 'Tasks on a phone offers nothing that writes');
    assert.deepEqual(writes, [], 'the view sent no write');
    await context.close();

    // A person: New task and the message box are there; a place that fails says so plainly.
    const person = await pageAs(browser, hub, 'dana');
    await ready(person);
    await go(person, '#/tasks');
    // New task sits in ⋯ (a first-run tour covers the page, so the menu is read rather than opened).
    await person.locator('#task-mi-new').waitFor({state: 'attached', timeout: t(15000)});
    assert.equal(await person.locator('#task-mi-new').evaluate(b => getComputedStyle(b).display !== 'none' && !(b as HTMLButtonElement).disabled), true, 'a person keeps New task');
    await person.route('**/api/v2/places/dashboard*', r => r.fulfill({status: 404, contentType: 'application/json',
      body: JSON.stringify({error: {code: 'not_found', message: 'No such place: places are dashboard, team:<group>'}})}));
    await go(person, '#/overview');
    const failed = person.locator('#dash-grid .dash-failed');
    await failed.waitFor({timeout: t(15000)});
    assert.match(await failed.innerText(), /^This page couldn't load\.\s*Try again$/);
    await person.unroute('**/api/v2/places/dashboard*');
    await failed.getByRole('button', {name: 'Try again'}).evaluate(b => (b as HTMLButtonElement).click());
    await failed.waitFor({state: 'detached', timeout: t(15000)});
    await person.context().close();
    console.log('ok app-view-read-only');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
