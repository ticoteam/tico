import type {Route} from 'playwright';
// Offline browser check of Tico's line in Flow (ui/app/decide.ts decideOpinions), against a fake of
// POST /api/v2/flow/opinions (backend/flow_opinions.py), on a desktop:
// - a ready opinion puts its line in the bar and, on an OK, makes its pick the main button (a send back, a reply in the
//   chat, leaves none; a question's choices stay as drawn: none looks chosen before a tap);
// - while an ask is pending the next one still waiting is named before ‹ ›, and the pending ones are asked again (only
//   they) until they are ready;
// - in a set of more than 5, the items not yet seen go highest score first and the ones seen keep their place;
// - while the ask on screen waits on Tico no answer is the main button, and the mark moves; ready, the pick is; none
//   leaves the buttons as they always are; an opinion that comes after the person typed in Notes leaves their words;
// - an ask that changes under the same id loses its line and is asked again, and an answer about the old ask is dropped;
// - a request from a closed visit that fails later does not stop the next visit asking;
// - each round is one request of at most 30 asks, pending ones first, then the next ones not yet asked, one at a time;
// - a failing route is asked once: no line, and the screen answers as it did.
// FLOW_SHOTS=<folder> saves the bar with a line and a pending next ask, desktop and phone.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Opinion = {state: 'ready' | 'pending' | 'none'; pick?: string; line?: string; note?: string; score?: number | null};
type Item = Record<string, unknown> & {id: string; ask: Record<string, unknown>};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const TITLES: Record<number, string> = {1: 'Launch note', 2: 'Launch date', 3: 'Signup plan', 4: 'Pick the venue', 5: 'Pricing page',
  6: 'Region', 7: 'Team offsite'};
const title = (n: number) => TITLES[n] ?? `Ask ${n}`;
const since = (n: number) => new Date(Date.parse('2026-11-09T00:00:00Z') + n * 60_000).toISOString();
const brief = (n: number) => ({id: `t${n}`, title: title(n), number: n, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const question = (n: number, at = 100 + n): Item => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`,
  line: `${title(n)}: yes or no?`, since: since(at), task: brief(n),
  ask: {requirement_id: `r${n}`, kind: 'answer', line: `${title(n)}: yes or no?`, who: 'human:ana', asked_by: 'human:ben',
    choices: ['Yes', 'No'], may: ['answer'], met: false, shows: [], standing: 'waiting', answer: null}});
const ok = (n: number): Item => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: `OK the ${title(n).toLowerCase()}`,
  since: since(n), task: brief(n),
  ask: {requirement_id: `r${n}`, kind: 'ok', line: `OK the ${title(n).toLowerCase()}`, who: 'human:ana', asked_by: 'bot:pm',
    choices: null, may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: string[] = [], asked: string[][] = [], times: number[] = [];
    let needs: Item[] = [], version = 1;
    let ops: Record<string, Opinion> = {};
    let failWith = 0, out = 0, mostOut = 0;
    // `hold`: the next opinions request is answered (as it was when asked) only once `release` is called.
    let hold: {status: number; release: () => void; gate: Promise<void>} | null = null;
    const holdNext = (status = 0) => { let release = () => {}; const gate = new Promise<void>(r => { release = r; }); hold = {status, release, gate}; return hold; };
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(version), next: null});
      if (p.startsWith('/api/v2/oks/')) return json({error: 'not_found'}, 404);   // a stage's OK: no grant
      const read = p.match(/^\/api\/v2\/tasks\/t(\d+)$/);
      if (read) return json({task: {...brief(Number(read[1])), created_at: '2026-11-09T09:00:00Z', attachments: [], links: [], conversation_id: 'c' + read[1],
        body: 'Ben is setting the launch plan. The venue and the date go together.'}});
      if (p === '/api/v2/flow/opinions') {
        const ids = (JSON.parse(r.postData() || '{}') as {items: string[]}).items;
        asked.push(ids); times.push(Date.now());
        const held = hold, status = held ? held.status || failWith : failWith;
        const body = status ? {error: {code: 'broken', message: 'No'}}
          : {opinions: ids.map(id => ({id, pick: '', line: '', note: '', score: null, ...(ops[id] || {state: 'none'})}))};
        hold = null;
        out++; mostOut = Math.max(mostOut, out);
        try {
          if (held) await held.gate;
          await json(body, status || 200);
        } catch { /* the page stopped it (closed) */ } finally { out--; }
        return;
      }
      // The task's read names its thread: the ask is answered in its chat (decide.ts decideChatMount).
      if (r.method() !== 'GET' && p.startsWith('/api/') && !p.startsWith('/api/v2/threads/')) {
        writes.push(`${r.method()} ${p}`);
        const m = p.match(/^\/api\/v2\/tasks\/(t\d+)\/asks\/(r\d+)\/answer$/);
        if (m) { needs = needs.filter(n => n.id !== m[2]); version++; return json({task: brief(Number(m[1]!.slice(1))), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = page.locator('#need-card');
    const walk = () => page.evaluate('decideLive(DEC)') as Promise<string[]>;
    const at = (n: number) => dialog.locator('.dc-q', {hasText: title(n)}).waitFor();
    const main = (sel: string) => dialog.locator(sel).evaluate(b => b.classList.contains('primary'));
    const mains = () => dialog.locator('.dc-side .primary').count();
    const said = async () => await dialog.locator('.dc-say').getAttribute('aria-label') ?? '';
    const open = async (first: string) => {
      await page.goto('about:blank');   // a fresh load each time, so the list is read again
      asked.length = 0; times.length = 0;
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator(`[data-need="${first}"]`).waitFor();
      await page.locator('#task-play').click();
    };
    const close = () => dialog.locator('[data-dc="close"]').click();
    const shot = async (name: string) => {
      if (!process.env.FLOW_SHOTS) return;
      await page.waitForTimeout(700);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-desktop.png`)});
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(300);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-phone.png`)});
      await page.setViewportSize({width: 1200, height: 900});
    };

    // ---- 1. Seven asks: lines, the next one pending, the order by score, the poll.
    needs = [ok(1), question(2), question(3), question(4), question(5), question(6), question(7)];
    ops = {
      r1: {state: 'ready', pick: 'send_back', line: 'Send back. The price is missing.', note: 'Add the price.', score: 80},
      r2: {state: 'ready', pick: 'No', line: 'No. The launch moved to Friday because the venue is only free that week.', score: 50},
      r3: {state: 'ready', pick: 'Yes', line: 'Yes. Pro trial is the plan.', score: 5},
      r4: {state: 'pending'},
      r5: {state: 'ready', pick: 'Yes', line: 'Yes. It went out Monday.', score: 90},
      r6: {state: 'ready', pick: '', line: 'Either. Nothing says.', score: null},
      r7: {state: 'ready', pick: 'Yes', line: 'Yes. The barn is booked.', score: 70},
    };
    await open('r2');
    await at(2);   // the list's first (questions before OKs), before any score came
    await dialog.locator('.dc-say', {hasText: 'No. The launch moved to Friday'}).waitFor();
    assert.equal(asked[0]![0], 'r2', 'the item on screen is asked about first');
    assert.deepEqual([...asked[0]!].sort(), ['r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7'], 'one request for the set');
    // A question's choices stay as drawn: none looks chosen before a tap; the line says the pick.
    assert.equal(await main('[data-dc-choice="No"]'), false, 'no choice looks chosen');
    assert.equal(await main('[data-dc-choice="Yes"]'), false);
    assert.equal(await dialog.locator('.dc-up').textContent(), 'Pick the venue', 'the next ask still pending is named');
    assert.equal(await dialog.locator('.dc-flow .flow-mark.live').count(), 0, 'the mark rests: the item on screen is ready');
    // Unseen ones by score, highest first; no score after, in the list's order; the one on screen stays first.
    assert.deepEqual(await walk(), ['r2', 'r5', 'r1', 'r7', 'r3', 'r4', 'r6']);
    await shot('flow-line');
    // Only the pending one is asked again; once ready (and scored highest) it moves up among the unseen.
    const before = asked.length;
    ops['r4'] = {state: 'ready', pick: 'Yes', line: 'Yes. The North barn is free.', score: 99};
    await page.waitForFunction(() => document.querySelector('.decide .dc-up')?.textContent === '', undefined, {timeout: 8000});
    assert.ok(asked.length > before && asked.slice(1).every(ids => ids.length === 1 && ids[0] === 'r4'), `asked again: ${JSON.stringify(asked)}`);
    assert.deepEqual(await walk(), ['r2', 'r4', 'r5', 'r1', 'r7', 'r3', 'r6']);
    // Seen ones keep their place: ‹ goes back to what was just seen.
    await dialog.locator('[data-dc="next"]').click();
    await at(4);
    await dialog.locator('[data-dc="next"]').click();
    await at(5);
    await dialog.locator('[data-dc="prev"]').click();
    await at(4);
    assert.deepEqual((await walk()).slice(0, 3), ['r2', 'r4', 'r5']);
    // The OK: Tico would send it back, which is a reply in the chat: no answer is the main button, and the box stays empty.
    await dialog.locator('[data-dc="next"]').click();
    await dialog.locator('[data-dc="next"]').click();
    await dialog.locator('.dc-say[aria-label="Send back. The price is missing."]').waitFor();
    assert.equal(await main('[data-dc="approve"]'), false);
    assert.equal(await dialog.locator('.dc-chat .p-text').inputValue(), '', 'Tico never types for the person');
    assert.deepEqual(writes, [], 'an opinion answers nothing');
    await close();

    // ---- 2. Waiting on Tico: no main button, the mark moves; typing first keeps the words. None: as always.
    needs = [ok(1), ok(5)];
    ops = {r1: {state: 'pending'}, r5: {state: 'none'}};
    await open('r1');
    await at(1);
    await page.waitForFunction(() => !!document.querySelector('.decide .dc-flow .flow-mark.live'));
    assert.equal(await mains(), 0, 'while Tico thinks, no answer is the main one');
    await dialog.locator('.dc-chat .p-text').fill('My own words');
    ops['r1'] = {state: 'ready', pick: 'send_back', line: 'Send back. The price is missing.', note: 'Add the price.', score: 80};
    await dialog.locator('.dc-say[aria-label="Send back. The price is missing."]').waitFor({timeout: 8000});
    assert.equal(await dialog.locator('.dc-chat .p-text').inputValue(), 'My own words', 'never over their typing');
    assert.equal(await main('[data-dc="approve"]'), false, 'ready: a send back leaves Approve plain');
    assert.equal(await dialog.locator('.dc-flow .flow-mark.live').count(), 0, 'the mark rests once it is ready');
    assert.equal(await dialog.locator('.dc-up').textContent(), '');
    await dialog.locator('[data-dc="next"]').click();
    await at(5);
    assert.equal(await main('[data-dc="approve"]'), true, 'none: Approve is the main one, as always');
    assert.equal(await said(), '');
    await close();

    // ---- 3. The ask changes under the same id: its old line goes, and an answer about the old ask is dropped.
    needs = [question(2)];
    ops = {r2: {state: 'pending'}};
    await open('r2');
    await at(2);
    await page.evaluate(`window.__said = []; new MutationObserver(() => window.__said.push(document.querySelector('.decide .dc-say').getAttribute('aria-label') || ''))
      .observe(document.querySelector('.decide .dc-say'), {attributes: true, attributeFilter: ['aria-label']})`);
    // The next poll is answered about the ask as it was asked, but only after the ask changed.
    ops['r2'] = {state: 'ready', pick: 'No', line: 'Old line', score: 10};
    const polls = asked.length, late = holdNext();
    while (asked.length === polls) await page.waitForTimeout(100);
    (needs[0]!.ask)['line'] = 'Launch date: Friday or Monday?';
    needs[0]!['line'] = 'Launch date: Friday or Monday?';
    (needs[0]!.ask)['choices'] = ['Friday', 'Monday'];
    version++;
    ops['r2'] = {state: 'ready', pick: 'Friday', line: 'Friday. The venue is free.', score: 10};
    await page.evaluate('v2Refresh()');
    await dialog.locator('.dc-q', {hasText: 'Friday or Monday'}).waitFor();
    late.release();
    await dialog.locator('.dc-say', {hasText: 'Friday. The venue is free.'}).waitFor({timeout: 8000});
    assert.equal(await main('[data-dc-choice="Friday"]'), false, 'the new line, with no choice looking chosen');
    assert.ok(!(await page.evaluate('window.__said') as string[]).includes('Old line'), 'the answer about the old ask is dropped');
    // A ready line on an ask that then changes goes at once, and the new one is asked for.
    const n = asked.length;
    needs[0]!['line'] = 'Launch date: Friday, Monday or Tuesday?';
    (needs[0]!.ask)['line'] = 'Launch date: Friday, Monday or Tuesday?';
    (needs[0]!.ask)['choices'] = ['Friday', 'Monday', 'Tuesday'];
    version++;
    ops['r2'] = {state: 'ready', pick: 'Tuesday', line: 'Tuesday. Monday is a holiday.', score: 10};
    await page.evaluate('v2Refresh()');
    await dialog.locator('.dc-say', {hasText: 'Tuesday. Monday is a holiday.'}).waitFor({timeout: 8000});
    assert.ok(asked.length > n && asked.slice(n).every(ids => ids.length === 1 && ids[0] === 'r2'));
    await close();

    // ---- 4. A request from a closed visit, failing after the close, leaves the next visit asking.
    needs = [ok(1)];
    ops = {r1: {state: 'ready', pick: 'send_back', line: 'Send back. The price is missing.', note: '', score: 1}};
    const stale = holdNext(500);
    await open('r1');
    await at(1);
    while (!asked.length) await page.waitForTimeout(50);
    await close();
    await page.locator('#task-play').click();
    await at(1);
    stale.release();
    await dialog.locator('.dc-say[aria-label="Send back. The price is missing."]').waitFor({timeout: 8000});
    assert.equal(await main('[data-dc="approve"]'), false);
    await close();

    // ---- 5. A big walk: one request a round, at most 30 asks, pending ones first, then the next ones not yet asked.
    needs = Array.from({length: 65}, (_, i) => question(10 + i, i));
    ops = Object.fromEntries(needs.map(it => [it.id, {state: 'ready', line: '', score: null} as Opinion]));
    ops['r11'] = {state: 'pending'};
    mostOut = 0;
    await open('r10');
    await at(10);
    while (asked.length < 3) await page.waitForTimeout(100);
    const ids = (i: number) => asked[i]!;
    assert.equal(ids(0).length, 30);
    assert.deepEqual(ids(0).slice(0, 3), ['r10', 'r11', 'r12'], 'from the item on screen on');
    assert.equal(ids(1).length, 30);
    assert.equal(ids(1)[0], 'r11', 'the pending one first');
    assert.deepEqual(ids(1).slice(1, 3), ['r40', 'r41'], 'then the next ones not yet asked');
    assert.deepEqual(ids(2).slice(0, 2), ['r11', 'r69']);
    assert.equal(new Set([...ids(0), ...ids(1).slice(1), ...ids(2).slice(1)]).size, 65, 'each ask is asked once');
    assert.ok(times[1]! - times[0]! >= 1500 && times[2]! - times[1]! >= 1500, `about 2 s apart: ${times.join(', ')}`);
    assert.equal(mostOut, 1, 'one request at a time');
    await close();

    // ---- 6. A failing route: asked once, no line, the main button as always, and the screen answers as before.
    needs = [ok(1), question(2)];
    ops = {};
    failWith = 500;
    await open('r2');
    await at(2);
    await page.waitForTimeout(4500);   // two rounds' worth
    assert.equal(asked.length, 1, `asked once: ${JSON.stringify(asked)}`);
    assert.equal(await said(), '');
    assert.equal(await dialog.locator('.dc-flow .flow-mark.live').count(), 0);
    await dialog.locator('[data-dc="next"]').click();
    await at(1);
    assert.equal(await main('[data-dc="approve"]'), true, 'Approve is the main one, as always');
    await dialog.locator('[data-dc="prev"]').click();
    await at(2);
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await at(1);
    assert.deepEqual(writes, ['POST /api/v2/tasks/t2/asks/r2/answer']);

    assert.deepEqual(errors, []);
    await page.close();
    console.log("PASS: Tico's line picks the button only when ready; changed asks are asked again; rounds are one request of 30 at most; a failing route or a closed visit changes nothing.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
