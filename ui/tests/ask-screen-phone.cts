import type {Page, Route} from 'playwright';
// Offline browser check of the ask screen on a 390 px phone (task #454, spec "Review on a phone"), against a fake of the
// new routes. An ask is answered in its task's chat (decide.ts decideChatMount):
// - an OK has tabs, Chat and what it shows, and opens on what it shows; a question on a task with no documents has
//   none; the chat and its row take the rest, the row at the bottom;
// - Approve and Snooze stay fully visible, at least 44 px tall and tappable while ↶ shows: it sits in the top bar;
// - ‹ › show on the phone, 8 px clear of ✕: next opens the next item and previous comes back;
// - a second tap on the same spot within 400 ms of the next item coming in approves nothing;
// - a question shows on screen in its chat, above a full-width box; Snooze and ⋯ are two icons at the right end of the
//   tabs row, the title cut before them;
// - with the keyboard up (the visible area shrinks, viewport.ts) the row stays inside it;
// - an OK with nothing attached (a pitch is the task itself) shows its task's title and text in the Task tab; an OK whose only show is a page shows the page and not the task's text;
// - at 1440 px: two columns, the chat column 420 px, ‹ ›, no tap guard.
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
const ok = (n: number, line: string, shows: string[] = []) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, `Task ${n}`),
  ask: {requirement_id: `r${n}`, kind: 'ok', line, who: 'human:ana', asked_by: 'human:bo', choices: null, may: ['ok', 'no'],
    met: false, shows, standing: 'waiting', answer: null}});
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
      // The icon font, so the shots show icons, not their names.
      if (p.startsWith('/vendor/fonts/')) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(path.join(__dirname, '..', p))});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'bo', name: 'Bo'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      // An OK with nothing attached: no grant to read.
      if (p === '/api/v2/oks/r5' || p === '/api/v2/oks/r6') return json({detail: 'not found'}, 404);
      // The only thing an OK shows can be a page (`page:<id>`, no version): no text of its own, but not empty.
      if (p === '/api/v2/pages/p6') return json({page: {id: 'p6', title: 'Launch plan', body: 'Launch plan body.', kind: 'doc',
        owner_id: 'bot:pm', updated_at: '2026-11-09T10:00:00Z'}});
      const okRead = p.match(/^\/api\/v2\/oks\/(r\d)$/);
      if (okRead) return json({requirement_id: okRead[1], subject: `spec:${okRead[1]}`, content: long(Q[Number(okRead[1]!.slice(1)) - 1]!),
        shown_hash: okRead[1]!.slice(1).repeat(64), standing: 'waiting', task_id: okRead[1]!.replace('r', 't')});
      // The question's task has no text of its own: nothing to show.
      if (p === '/api/v2/tasks/t3') return json({task: {...brief('t3', 'Task 3'), request: null, conversation_id: 'c3'}});
      // The bare OK's task: the pitch is its text.
      if (p === '/api/v2/tasks/t5') return json({task: {...brief('t5', 'Pitch: a weekly digest'), request: {text: 'Send each person one email on Monday with what changed.'}, conversation_id: 'c5'}});
      if (p === '/api/v2/tasks/t6') return json({task: {...brief('t6', 'Task 6'), request: {text: 'Task six text.'}, conversation_id: 'c6'}});
      const own = r.method() === 'GET' ? p.match(/^\/api\/v2\/tasks\/(t\d)$/) : null;
      if (own) return json({task: {...brief(own[1]!, 'Task'), conversation_id: 'c' + own[1]!.slice(1)}});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});   // Tico has none: Flow as without it
      if (r.method() !== 'GET' && p.startsWith('/api/') && !p.startsWith('/api/v2/threads/')) {
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
    // An OK opens on its document tab, so its ask (in the Chat tab) may be out of sight.
    const at = async (q: number) => { await dialog.locator('.dc-q', {hasText: Q[q - 1]!}).waitFor({state: 'attached'}); };
    const approve = '#need-card [data-dc="approve"]', sendback = '#need-card [data-dc="sendback"]', snooze = '#need-card [data-dc="snooze"]';
    await at(1);
    await page.locator(`${approve}:not([disabled])`).waitFor();
    if (shots) await page.screenshot({path: path.join(shots, 'ask-phone-390-open.png')});

    // An OK opens on its document tab; Chat holds the chat, its row at the bottom; each button 44 px.
    check(await page.locator('#need-card [data-dc="tab"][data-dc-tab="task"]').getAttribute('aria-selected') === 'true', 'an OK opens on its document tab');
    check(await page.locator('#need-card .dc-thing').isVisible() && !await page.locator('#need-card .dc-chat').isVisible(), 'the document shows, not the chat');
    await page.locator('#need-card [data-dc="tab"][data-dc-tab="chat"]').click();
    await page.locator('#need-card .dc-chat .p-text').waitFor();
    const bar = (await box(page, `${approve} >> xpath=..`))!;
    check(VIEW.height - (bar.y + bar.height) <= 24, `the row is at the bottom of the screen (${bar.y + bar.height})`);
    for (const sel of [approve, snooze]) {
      const b = (await box(page, sel))!;
      check(b.height >= 44, `${sel} is 44 px tall (${b.height})`);
    }
    const chatBox = (await box(page, '#need-card .dc-chat .p-text'))!;
    check(chatBox.y + chatBox.height <= bar.y + 1, `the chat's box sits above the row (${chatBox.y + chatBox.height} > ${bar.y})`);
    check(await page.locator(sendback).count() === 0, 'no Send back: a reply in the chat sends it back');

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
    } else {
      check(false, '‹ › are needed to walk on a phone');
    }
    await page.locator(`${approve}:not([disabled])`).waitFor();

    // Approve: "✓ Approved · Undo" on the top bar's row, over nothing else: not the page, not the buttons; all three
    // buttons visible, 44 px, and reached by a tap.
    const spot = (await box(page, approve))!;
    await page.mouse.click(spot.x + spot.width / 2, spot.y + spot.height / 2);
    await at(2);
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    await page.waitForTimeout(450);                                   // past the 400 ms a new item takes no taps
    check(await dialog.locator('.dc-bar:not([hidden])').count() === 1, 'the message still shows');
    if (shots) await page.screenshot({path: path.join(shots, 'ask-phone-390-approved.png')});
    const toast = (await box(page, '#need-card .dc-bar'))!, row = (await box(page, `${approve} >> xpath=..`))!;
    const nav = (await box(page, '#need-card .dc-nav'))!;
    check(Math.abs((toast.y + toast.height / 2) - (nav.y + nav.height / 2)) < 2, `the undo sits on the top bar's row, beside ‹ › (${JSON.stringify(toast)} vs ${JSON.stringify(nav)})`);
    check((await page.locator('#need-card .dc-bar').innerText()).startsWith('✓ Approved'), 'it says what was done');
    check(toast.y + toast.height <= row.y, `the undo is clear of the buttons (${toast.y + toast.height} > ${row.y})`);
    const undoBtn = (await box(page, '#need-card .dc-bar [data-dc="undo"]'))!;
    check(undoBtn.width >= 44 && undoBtn.height >= 44, `the undo is a 44 px target (${undoBtn.width}x${undoBtn.height})`);
    check((await page.locator('#need-card .dc-bar [data-dc="undo"]').innerText()).trim() === 'Undo', 'the button says Undo');
    // Nothing of the bar's own moves for it, and it covers nothing: what is under its middle is the undo itself.
    const covers = await page.evaluate(() => {
      const r = document.querySelector('#need-card .dc-bar')!.getBoundingClientRect();
      const under = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      const others = [...document.querySelectorAll<HTMLElement>('#need-card .dc-head > :not(.dc-nav):not(.dc-sayw):not(.dc-bar), #need-card .dc-nav > button')]
        .filter(e => e.offsetParent).map(e => e.getBoundingClientRect())
        .filter(o => o.width && o.left < r.right && o.right > r.left && o.top < r.bottom && o.bottom > r.top);
      return [!!under?.closest('.dc-bar'), others.length];
    });
    check(covers[0] === true && covers[1] === 0, `the undo overlaps no control, the page or the buttons (${JSON.stringify(covers)})`);
    for (const sel of [approve, snooze]) {
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

    // An ask with nothing to show opens on its question, right under the header (‹ or › to it).
    if (!await dialog.locator('.dc-q', {hasText: Q[2]!}).count()) await page.locator(await page.locator(prev).isEnabled() ? prev : next).click();
    await at(3);
    await dialog.locator('.dc-thing .dc-bg').waitFor({state: 'attached'});   // its task: one line under it (#3786)
    await page.waitForTimeout(100);
    check(await dialog.locator('[data-dc="tab"]').count() === 0, 'a question on a task with no documents: no tabs, just its chat');
    const head = (await box(page, '#need-card .dc-head'))!, q = (await box(page, '#need-card .dc-msg-ask .dc-q'))!;
    const composer = (await box(page, '#need-card .dc-chat .thv-composer .p-pill'))!;
    check(q.y >= head.y + head.height && q.y + q.height <= composer.y, `the question is on screen in the chat, above the box (${q.y}..${q.y + q.height}, box at ${composer.y})`);
    // No forward action: the box is full width, and Snooze and ⋯ are two icons at the right end of the tabs row, ⋯ last.
    check(composer.width >= VIEW.width - 40, `the box is full width (${composer.width})`);
    const tabs = (await box(page, '#need-card .dc-tabs'))!, sz = (await box(page, snooze))!, more = (await box(page, '#need-card [data-dc="ask-more"]'))!;
    const title = (await box(page, '#need-card .dc-tabs-t'))!;
    check([sz, more].every(b => b.y >= tabs.y - 1 && b.y + b.height <= tabs.y + tabs.height + 1 && b.width >= 44 && b.height >= 44),
      `Snooze and ⋯ are 44 px icons in the tabs row (${JSON.stringify([tabs, sz, more])})`);
    check(more.x >= sz.x + sz.width - 1 && more.x + more.width <= VIEW.width, 'Snooze then ⋯ at the right end');
    check(title.x + title.width <= sz.x + 1 && title.width > 40, `the title gives way, cut before Snooze (${JSON.stringify(title)})`);
    check(await dialog.locator('.dc-tabs-t').evaluate(e => getComputedStyle(e).textOverflow === 'ellipsis'), 'the title is cut with an ellipsis');
    if (shots) await page.screenshot({path: path.join(shots, 'ask-phone-390-question.png')});
    await page.close();

    // An OK with nothing attached (no grant, no shows): its task's title and text above the question, the title once.
    needs = [ok(5, 'OK to go ahead?')];
    const bare = await browser.newPage({viewport: VIEW, serviceWorkers: 'block', hasTouch: true});
    bare.on('pageerror', (error: Error) => errors.push(error.message));
    await serve(bare);
    await bare.goto('http://tico-ui.test/#/tasks');
    await bare.locator('[data-need="r5"]').click();
    await bare.locator('#need-card .dc-q', {hasText: 'OK to go ahead?'}).waitFor({state: 'attached'});
    await bare.locator(`${approve}:not([disabled])`).waitFor();   // it opens on its Task tab
    const pitch = bare.locator('#need-card .dc-thing .dc-md', {hasText: 'one email on Monday'});
    await pitch.waitFor();
    check(await bare.locator('#need-card .dc-thing').getByText('Pitch: a weekly digest').count() === 1, 'the Task tab: its title once, then its text');
    if (shots) await bare.screenshot({path: path.join(shots, 'ask-phone-390-empty-ok.png')});
    if (process.env.FLOW_SHOTS) await bare.locator('#need-card .dc-start').waitFor({state: 'detached'}).then(() => bare.screenshot({path: path.join(process.env.FLOW_SHOTS, 'phone-bare-ok.png')}));
    await bare.close();

    // An OK whose only show is a page has no text, yet its pane shows the page: it is not bare.
    needs = [ok(6, 'OK the launch plan?', ['page:p6'])];
    const paged = await browser.newPage({viewport: VIEW, serviceWorkers: 'block', hasTouch: true});
    paged.on('pageerror', (error: Error) => errors.push(error.message));
    await serve(paged);
    await paged.goto('http://tico-ui.test/#/tasks');
    await paged.locator('[data-need="r6"]').click();
    await paged.locator('#need-card .dc-q', {hasText: 'OK the launch plan?'}).waitFor({state: 'attached'});   // on its document tab
    await paged.locator('#need-card .dc-thing').getByText('Launch plan', {exact: false}).first().waitFor({state: 'attached'});
    check(await paged.locator('#need-card .dc-thing').isVisible(), 'an OK showing only a page keeps its pane');
    check(await paged.locator('#need-card .dc-thing').getByText('Launch plan', {exact: false}).first().isVisible(),
      'an OK showing only a page shows the page');
    await paged.waitForTimeout(200);
    check(!await paged.locator('#need-card .dc-thing').getByText('Task six text.').count(), 'an OK showing a page shows only the page');
    await paged.close();

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
    check(Math.abs(side.width - 420) <= 1 && side.y === thing.y, `1440: two columns, the chat column 420 px (${side.width})`);
    check(await wide.locator('#need-card [data-dc="next"]').isVisible(), '1440: › shows');
    check(!await wide.locator('#need-card .dc-tabs').isVisible(), '1440: the task is the left column, no tabs');
    // The 400 ms tap guard is a phone's: a desktop's buttons answer at once after Approve.
    await wide.locator(approve).click();
    await wide.locator('#need-card .dc-q', {hasText: Q[1]!}).waitFor();
    check(!await wide.locator('#need-card .decide.dc-calm').count(), '1440: no tap guard after Approve');
    if (shots) await wide.screenshot({path: path.join(shots, 'ask-desktop-1440.png')});
    await wide.close();

    assert.deepEqual(errors, []);
    assert.deepEqual(fails, [], `\n- ${fails.join('\n- ')}`);
    console.log('PASS: on a phone each item opens on its Chat tab, Task shows the task, the row stays tappable under ↶, ‹ › step, a second tap is ignored, a question opens on itself and a bare OK opens to its task.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
