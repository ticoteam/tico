import type {Page, Route} from 'playwright';
// Offline browser check of the ask screen on a 390 px phone (task #454, spec "Review on a phone"), against a fake of the
// new routes:
// - the screen scrolls as one: the thing asked about, then the question and Notes; nothing else scrolls, and at the end
//   of the scroll no text is under the button bar, which is pinned to the bottom with a solid background;
// - Approve, Send back and Skip stay fully visible, at least 44 px tall and tappable while "Approved · Undo" shows: the
//   message sits above the bar;
// - ‹ › show on the phone, 8 px clear of ✕: next opens the next item and previous comes back;
// - a second tap on the same spot within 400 ms of the next item coming in approves nothing;
// - Send back is on with an empty note: a tap focuses Notes with "Say what to change" and sends nothing; Notes starts one
//   line tall;
// - an ask with nothing to show opens on its question, right under the header;
// - with the keyboard up (the visible area shrinks, viewport.ts) the bar stays inside it;
// - at 1440 px the screen is as before: two columns, ‹ ›, Notes 7 lines, Send back off until there is a note, no tap guard.
// Each check is collected, so a run on the old code lists every one that fails.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const ok = (n: number, line: string) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, `Task ${n}`),
  ask: {requirement_id: `r${n}`, kind: 'ok', line, who: 'human:ana', asked_by: 'human:bo', choices: null, may: ['ok', 'no'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const question = (n: number, line: string) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, `Task ${n}`),
  ask: {requirement_id: `r${n}`, kind: 'answer', line, who: 'human:ana', asked_by: 'human:bo', choices: ['Free', 'Pro trial'],
    may: ['answer'], met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const Q = ['Approve the invoice run', 'Approve the launch email', 'Which plan do new signups get?', 'Approve the price change'];
// Content like a real review: several screens of text.
const long = (what: string) => ({title: what, body: Array.from({length: 40}, (_, i) => `${what}, paragraph ${i + 1}: the numbers, who gets it and when it goes out.`).join('\n\n')});
const VIEW = {width: 390, height: 844};

type Box = {x: number; y: number; width: number; height: number};
// What a tap at the centre of `sel` lands on: the element itself (or inside it), else what covers it.
const hit = (page: Page, sel: string) => page.evaluate((s: string) => {
  const el = document.querySelector<HTMLElement>(s);
  if (!el) return 'missing';
  const r = el.getBoundingClientRect(), at = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return at && (at === el || el.contains(at)) ? 'self' : (at ? `${at.tagName.toLowerCase()}.${String(at.className)}` : 'nothing');
}, sel);
const box = async (page: Page, sel: string): Promise<Box | null> => page.locator(sel).first().boundingBox();
// The elements on the screen that scroll (their content is taller than they are, and they let it scroll).
const scrollers = (page: Page) => page.evaluate(() => [...document.querySelectorAll<HTMLElement>('#need-card *')].filter(el => {
  const o = getComputedStyle(el).overflowY;
  return (o === 'auto' || o === 'scroll') && el.scrollHeight > el.clientHeight + 1;
}).map(el => String(el.className)));

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [], fails: string[] = [], writes: string[] = [];
  const check = (ok: boolean, what: string) => { if (!ok) fails.push(what); };
  const shots = process.env.TICO_UI_SHOTS;
  let needs = [ok(1, Q[0]!), ok(2, Q[1]!), question(3, Q[2]!), ok(4, Q[3]!)];
  const serve = (page: Page) => page.route('**/*', (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'bo', name: 'Bo'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      const okRead = p.match(/^\/api\/v2\/oks\/(r\d)$/);
      if (okRead) return json({requirement_id: okRead[1], subject: `spec:${okRead[1]}`, content: long(Q[Number(okRead[1]!.slice(1)) - 1]!),
        shown_hash: okRead[1]!.slice(1).repeat(64), standing: 'waiting', task_id: okRead[1]!.replace('r', 't')});
      // The question's task has no text of its own: nothing to show.
      if (p === '/api/v2/tasks/t3') return json({task: {...brief('t3', 'Task 3'), body: ''}});
      if (r.method() !== 'GET' && p.startsWith('/api/')) {
        writes.push(`${r.method()} ${p}`);
        const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/(?:answer|ok)$/);
        if (m) { needs = needs.filter(n => n.id !== m[2]); return json({task: brief(m[1]!, 'Task'), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
  try {
    const page = await browser.newPage({viewport: VIEW, serviceWorkers: 'block', hasTouch: true});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await serve(page);
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    // Answers come first in the walk (the question is item 1); open on the first OK.
    await page.locator('[data-need="r1"]').click();
    const dialog = page.locator('#need-card');
    const at = async (q: number) => { await dialog.locator('.dc-q', {hasText: Q[q - 1]!}).waitFor(); };
    const approve = '#need-card [data-dc="approve"]', sendback = '#need-card [data-dc="sendback"]', skip = '#need-card [data-dc="skip"]';
    await at(1);
    await page.locator(`${approve}:not([disabled])`).waitFor();
    if (shots) await page.screenshot({path: path.join(shots, 'ask-phone-390-open.png')});

    // One scroll: only the screen's body scrolls; the thing and the question do not scroll on their own.
    const scrolling = await scrollers(page);
    check(scrolling.length === 1, `one scroll area, not ${scrolling.length}: ${JSON.stringify(scrolling)}`);
    const scrolledBy = await page.evaluate(() => ['.dc-thing .dc-doc', '.dc-side .dc-q'].map(s => {
      for (let el = document.querySelector<HTMLElement>(`#need-card ${s}`)?.parentElement; el; el = el.parentElement)
        if (/auto|scroll/.test(getComputedStyle(el).overflowY)) return String(el.className);
      return 'none';
    }));
    check(scrolledBy[0] === scrolledBy[1], `the thing and the question scroll together (${JSON.stringify(scrolledBy)})`);

    // The bar: pinned to the bottom, solid, each button at least 44 px and the one a tap at its centre reaches.
    const bar = (await box(page, `${approve} >> xpath=..`))!;
    check(Math.abs(bar.y + bar.height - VIEW.height) <= 1, `the bar ends at the bottom of the screen (${bar.y + bar.height})`);
    const barBg = await page.locator(`${approve} >> xpath=..`).evaluate(el => getComputedStyle(el).backgroundColor);
    check(!/rgba\(.*,\s*0\)$|transparent/.test(barBg), `the bar has a solid background (${barBg})`);
    for (const sel of [approve, sendback, skip]) {
      const b = (await box(page, sel))!;
      check(b.height >= 44, `${sel} is 44 px tall (${b.height})`);
    }
    // Scrolled to the end, the last of the content (Attach) ends above the bar, and the content scrolled under the bar
    // is hidden by it.
    await page.evaluate(() => { for (const el of document.querySelectorAll<HTMLElement>('#need-card *')) el.scrollTop = el.scrollHeight; });
    const attach = (await box(page, '#need-card .dc-attach'))!, barNow = (await box(page, `${approve} >> xpath=..`))!;
    check(attach.y + attach.height <= barNow.y + 1, `Attach ends above the bar at the end of the scroll (${attach.y + attach.height} > ${barNow.y})`);
    await page.evaluate(() => { for (const el of document.querySelectorAll<HTMLElement>('#need-card *')) el.scrollTop = 0; });

    // Notes starts one line tall; Send back is on, and with no note it asks for one and sends nothing.
    const notes = (await box(page, '#dc-notes'))!;
    check(notes.height <= 48, `Notes starts one line tall (${notes.height} px)`);
    check(await page.locator(sendback).isEnabled(), 'Send back is on with an empty note');
    if (await page.locator(sendback).isEnabled()) {
      await page.locator(sendback).click();
      check(await page.evaluate(() => document.activeElement?.id === 'dc-notes'), 'Send back with no note focuses Notes');
      check(await page.locator('#dc-notes').getAttribute('placeholder') === 'Say what to change', 'Notes says "Say what to change"');
      // A redraw of the side keeps it: attach a file, then take it off with ✕ (which draws the side again).
      await page.locator('#need-card input[data-dc-pick]').setInputFiles({name: 'shot.png', mimeType: 'image/png', buffer: Buffer.from('png')});
      await page.locator('#need-card [data-dc="file-drop"]').click();
      await page.locator('#need-card [data-dc="file-drop"]').waitFor({state: 'detached'});
      check(await page.locator('#dc-notes').getAttribute('placeholder') === 'Say what to change', 'Notes still says "Say what to change" after a redraw');
      check(writes.length === 0, `an empty Send back sends nothing (${JSON.stringify(writes)})`);
    }

    // The bar's height changes with no redraw (here its padding, as a safe-area inset does): the end of the scroll
    // follows it.
    const barH = () => page.evaluate(() => {
      const row = document.querySelector<HTMLElement>('#need-card [data-dc="approve"]')!.parentElement!;
      return [row.offsetHeight, parseFloat(getComputedStyle(document.querySelector('#need-card .decide')!).getPropertyValue('--dc-bar-h'))];
    });
    await page.locator(`${approve} >> xpath=..`).evaluate(el => { (el as HTMLElement).style.paddingBottom = '60px'; });
    await page.waitForTimeout(300);
    const [rowH, room] = await barH();
    check(rowH === room, `the scroll's room follows the bar's height (bar ${rowH} px, room ${room} px)`);
    await page.locator(`${approve} >> xpath=..`).evaluate(el => { (el as HTMLElement).style.paddingBottom = ''; });
    await page.waitForTimeout(300);

    // ‹ › on the phone, 8 px clear of ✕; next and back.
    const next = '#need-card [data-dc="next"]', prev = '#need-card [data-dc="prev"]';
    check(await page.locator(next).isVisible() && await page.locator(prev).isVisible(), '‹ › show on a phone');
    if (await page.locator(next).isVisible()) {
      const n = (await box(page, next))!, x = (await box(page, '#need-card [data-dc="close"]'))!;
      check(n.width >= 44 && n.height >= 44, `› is 44 px (${n.width}x${n.height})`);
      check(x.x - (n.x + n.width) >= 8, `› is 8 px clear of ✕ (${x.x - (n.x + n.width)})`);
      await page.locator(next).click();
      await at(2);
      await page.locator(`${approve}:not([disabled])`).waitFor();       // its content is read and held
      await page.locator(prev).click();
      await at(1);
      // Leaving the item clears the hint: back on it, Notes is plain again.
      check(await page.locator('#dc-notes').getAttribute('placeholder') === null, 'the hint is gone after leaving the item');
    } else {
      // The old way to read items 2 and 4 first, so each is ready the moment it comes back: Skip through them, round
      // the question and on to item 1.
      for (const n of [2, 4]) { await page.locator(skip).click(); await at(n); await page.locator(`${approve}:not([disabled])`).waitFor(); }
      await page.locator(skip).click(); await at(3);
      await page.locator(skip).click(); await at(1);
    }
    await page.locator(`${approve}:not([disabled])`).waitFor();

    // Approve: "Approved · Undo" above the bar; all three buttons visible, 44 px, and reached by a tap.
    const spot = (await box(page, approve))!;
    await page.mouse.click(spot.x + spot.width / 2, spot.y + spot.height / 2);
    await at(2);
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    await page.waitForTimeout(450);                                   // past the 400 ms a new item takes no taps
    check(await dialog.locator('.dc-bar:not([hidden])').count() === 1, 'the message still shows');
    if (shots) await page.screenshot({path: path.join(shots, 'ask-phone-390-approved.png')});
    const toast = (await box(page, '#need-card .dc-bar'))!, row = (await box(page, `${approve} >> xpath=..`))!;
    check(toast.y + toast.height <= row.y, `"Approved · Undo" is above the bar (${toast.y + toast.height} > ${row.y})`);
    for (const sel of [approve, sendback, skip]) {
      const b = (await box(page, sel))!, reached = await hit(page, sel);
      check(b.height >= 44 && b.y + b.height <= VIEW.height && reached === 'self', `${sel} is visible and tappable while the message shows (${b.height} px, tap reaches ${reached})`);
    }

    // The keyboard up: the visible area is 480 px high (viewport.ts); the bar stays inside it.
    await page.evaluate(() => document.documentElement.style.setProperty('--chat-viewport-height', '480px'));
    await page.waitForTimeout(50);
    const up = (await box(page, `${approve} >> xpath=..`))!;
    check(up.y + up.height <= 481, `with the keyboard up the bar stays above it (${up.y + up.height})`);
    await page.evaluate(() => document.documentElement.style.removeProperty('--chat-viewport-height'));

    // A second tap: with item 4 read once (so its Approve is on the moment it comes in), Approve item 2, then tap the
    // same spot at once. Nothing more is approved.
    await page.waitForTimeout(450);
    if (await page.locator(next).isVisible()) { await page.locator(next).click(); await at(4); await page.locator(`${approve}:not([disabled])`).waitFor(); await page.locator(prev).click(); }
    await at(2);
    await page.locator(`${approve}:not([disabled])`).waitFor();
    const spot2 = (await box(page, approve))!;
    await page.mouse.click(spot2.x + spot2.width / 2, spot2.y + spot2.height / 2);
    await at(4);
    await page.mouse.click(spot2.x + spot2.width / 2, spot2.y + spot2.height / 2);
    await page.waitForTimeout(500);
    check(JSON.stringify(writes) === JSON.stringify(['POST /api/v2/tasks/t1/asks/r1/ok', 'POST /api/v2/tasks/t2/asks/r2/ok']),
      `a second tap within 400 ms approves nothing (${JSON.stringify(writes)})`);

    // An ask with nothing to show opens on its question, right under the header (Skip from the last wraps to it).
    if (!await dialog.locator('.dc-q', {hasText: Q[2]!}).count()) await page.locator(skip).click();
    await at(3);
    await dialog.locator('.dc-thing .dc-doc').waitFor({state: 'attached'});
    await page.waitForTimeout(100);
    const head = (await box(page, '#need-card .dc-head'))!, q = (await box(page, '#need-card .dc-q'))!;
    check(q.y - (head.y + head.height) <= 24, `the question opens the screen (${q.y - (head.y + head.height)} px under the header)`);
    if (shots) await page.screenshot({path: path.join(shots, 'ask-phone-390-question.png')});
    await page.close();

    // 1440 px: as before.
    needs = [ok(1, Q[0]!), ok(2, Q[1]!), question(3, Q[2]!), ok(4, Q[3]!)];
    const wide = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    wide.on('pageerror', (error: Error) => errors.push(error.message));
    await serve(wide);
    await wide.goto('http://tico-ui.test/#/tasks');
    await wide.locator('[data-need="r1"]').click();
    await wide.locator('#need-card .dc-q', {hasText: Q[0]!}).waitFor();
    await wide.locator(`${approve}:not([disabled])`).waitFor();
    const side = (await box(wide, '#need-card .dc-side'))!, thing = (await box(wide, '#need-card .dc-thing'))!;
    check(Math.abs(side.width - 420) <= 1 && side.y === thing.y, `1440: two columns, the answer column 420 px (${side.width})`);
    check(await wide.locator('#need-card [data-dc="next"]').isVisible(), '1440: › shows');
    check(await wide.locator('#dc-notes').getAttribute('rows') === '7', '1440: Notes starts at 7 lines');
    check(await wide.locator(sendback).isDisabled(), '1440: Send back is off with an empty note');
    // The 400 ms tap guard is a phone's: a desktop's buttons answer at once after Approve.
    await wide.locator(approve).click();
    await wide.locator('#need-card .dc-q', {hasText: Q[1]!}).waitFor();
    check(!await wide.locator('#need-card .decide.dc-calm').count(), '1440: no tap guard after Approve');
    if (shots) await wide.screenshot({path: path.join(shots, 'ask-desktop-1440.png')});
    await wide.close();

    assert.deepEqual(errors, []);
    assert.deepEqual(fails, [], `\n- ${fails.join('\n- ')}`);
    console.log('PASS: on a phone the ask screen scrolls as one, its bar stays tappable under "Approved · Undo", ‹ › step, a second tap is ignored, Send back asks for a note and an empty ask opens on its question.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
