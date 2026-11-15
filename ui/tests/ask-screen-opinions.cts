import type {Route} from 'playwright';
// Offline browser check of Tico's line in Flow (ui/app/decide.ts decideOpinions), against a fake of
// POST /api/v2/flow/opinions (backend/flow_opinions.py), on a desktop:
// - a ready opinion puts its line in the bar and makes its pick the main button; an OK's send back fills an empty Notes;
// - while an ask is pending the next one still waiting is named before ‹ ›, and the pending ones are asked again (only
//   they) until they are ready;
// - in a set of more than 5, the items not yet seen go highest score first and the ones seen keep their place;
// - an opinion that comes after the person typed in Notes leaves their words, and the mark moves only while the item on
//   screen is pending;
// - a failing route is asked once: no line, and the screen answers as it did.
// FLOW_SHOTS=<folder> saves the bar with a line and a pending next ask, and an ask's snooze choices, desktop and phone.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Opinion = {state: 'ready' | 'pending' | 'none'; pick?: string; line?: string; note?: string; score?: number | null};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const TITLES: Record<number, string> = {1: 'Launch note', 2: 'Launch date', 3: 'Signup plan', 4: 'Pick the venue', 5: 'Pricing page',
  6: 'Region', 7: 'Team offsite'};
const brief = (n: number) => ({id: `t${n}`, title: TITLES[n], number: n, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const question = (n: number) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: `${TITLES[n]}: yes or no?`,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(n), may_snooze: true,
  ask: {requirement_id: `r${n}`, kind: 'answer', line: `${TITLES[n]}: yes or no?`, who: 'human:ana', asked_by: 'human:ben',
    choices: ['Yes', 'No'], may: ['answer'], met: false, shows: [], standing: 'waiting', answer: null}});
const ok = (n: number) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: `OK the ${TITLES[n]!.toLowerCase()}`,
  since: `2026-11-09T0${n}:00:00Z`, task: brief(n), may_snooze: true,
  ask: {requirement_id: `r${n}`, kind: 'ok', line: `OK the ${TITLES[n]!.toLowerCase()}`, who: 'human:ana', asked_by: 'bot:pm',
    choices: null, may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: string[] = [], asked: string[][] = [];
    let needs: Record<string, unknown>[] = [];
    let ops: Record<string, Opinion> = {};
    let failWith = 0;
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', (route: Route) => {
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
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      if (p.startsWith('/api/v2/oks/')) return json({error: 'not_found'}, 404);   // a stage's OK: no grant
      const read = p.match(/^\/api\/v2\/tasks\/t(\d)$/);
      if (read) return json({task: {...brief(Number(read[1])), created_at: '2026-11-09T09:00:00Z', attachments: [], links: [],
        body: 'Ben is setting the launch plan. The venue and the date go together.'}});
      if (p === '/api/v2/flow/opinions') {
        const ids = (JSON.parse(r.postData() || '{}') as {items: string[]}).items;
        asked.push(ids);
        if (failWith) return json({error: {code: 'broken', message: 'No'}}, failWith);
        return json({opinions: ids.map(id => ({id, pick: '', line: '', note: '', score: null, ...(ops[id] || {state: 'none'})}))});
      }
      if (r.method() !== 'GET' && p.startsWith('/api/')) {
        writes.push(`${r.method()} ${p}`);
        const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
        if (m) { needs = needs.filter(n => n['id'] !== m[2]); return json({task: brief(Number(m[1]!.slice(1))), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = page.locator('#need-card');
    const walk = () => page.evaluate('decideLive(DEC)') as Promise<string[]>;
    const at = (n: number) => dialog.locator('.dc-q', {hasText: TITLES[n]!}).waitFor();
    const main = (sel: string) => dialog.locator(sel).evaluate(b => b.classList.contains('primary'));
    const open = async (first: string) => {
      await page.goto('about:blank');   // a fresh load each time, so the list is read again
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator(`[data-need="${first}"]`).waitFor();
      await page.locator('#task-play').click();
    };
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
      r2: {state: 'ready', pick: 'No', line: 'No. The launch is on Friday.', score: 50},
      r3: {state: 'ready', pick: 'Yes', line: 'Yes. Pro trial is the plan.', score: 5},
      r4: {state: 'pending'},
      r5: {state: 'ready', pick: 'Yes', line: 'Yes. It went out Monday.', score: 90},
      r6: {state: 'ready', pick: '', line: 'Either. Nothing says.', score: null},
      r7: {state: 'ready', pick: 'Yes', line: 'Yes. The barn is booked.', score: 70},
    };
    await open('r2');
    await at(2);   // the list's first (questions before OKs), before any score came
    await dialog.locator('.dc-say', {hasText: 'No. The launch is on Friday.'}).waitFor();
    assert.equal(asked[0]![0], 'r2', 'the item on screen is asked about first');
    assert.deepEqual([...asked[0]!].sort(), ['r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7'], 'one request for the set');
    assert.equal(await main('[data-dc-choice="No"]'), true, "the pick is the main button");
    assert.equal(await main('[data-dc-choice="Yes"]'), false);
    assert.equal(await dialog.locator('.dc-up').textContent(), 'Pick the venue', 'the next ask still pending is named');
    assert.equal(await dialog.locator('.dc-flow .flow-mark.live').count(), 0, 'the mark rests: the item on screen is ready');
    // Unseen ones by score, highest first; no score after, in the list's order; the one on screen stays first.
    assert.deepEqual(await walk(), ['r2', 'r5', 'r1', 'r7', 'r3', 'r4', 'r6']);
    await shot('flow-line');
    await dialog.locator('[data-dc="snooze"]').click();
    await dialog.locator('[data-dc="snooze-hour"]').waitFor();
    assert.deepEqual(await dialog.locator('.dc-more button').allTextContents(), ['1 hour', 'Tomorrow', 'Next week']);
    await shot('flow-snooze');
    await dialog.locator('[data-dc="snooze"]').click();
    // Only the pending one is asked again; once ready (and scored highest) it moves up among the unseen.
    await page.waitForFunction(() => document.querySelector('.decide .dc-up')?.textContent === 'Pick the venue');
    const before = asked.length;
    ops['r4'] = {state: 'ready', pick: 'Yes', line: 'Yes. The North barn is free.', score: 99};
    await page.waitForFunction(() => document.querySelector('.decide .dc-up')?.textContent === '', undefined, {timeout: 8000});
    assert.ok(asked.length > before && asked.slice(before).every(ids => ids.length === 1 && ids[0] === 'r4'), `asked again: ${JSON.stringify(asked)}`);
    assert.deepEqual(await walk(), ['r2', 'r4', 'r5', 'r1', 'r7', 'r3', 'r6']);
    // Seen ones keep their place: ‹ goes back to what was just seen.
    await dialog.locator('[data-dc="next"]').click();
    await at(4);
    await dialog.locator('[data-dc="next"]').click();
    await at(5);
    await dialog.locator('[data-dc="prev"]').click();
    await at(4);
    assert.deepEqual((await walk()).slice(0, 3), ['r2', 'r4', 'r5']);
    // The OK: Send back is the main button, and its note fills the empty Notes.
    await dialog.locator('[data-dc="next"]').click();
    await dialog.locator('[data-dc="next"]').click();
    await dialog.locator('.dc-say', {hasText: 'Send back. The price is missing.'}).waitFor();
    assert.equal(await dialog.locator('#dc-notes').inputValue(), 'Add the price.');
    assert.equal(await main('[data-dc="sendback"]'), true);
    assert.equal(await main('[data-dc="approve"]'), false);
    assert.equal(await dialog.locator('[data-dc="sendback"]').isEnabled(), true, 'the note is there, so Send back is on');
    assert.deepEqual(writes, [], 'an opinion answers nothing');
    await dialog.locator('[data-dc="close"]').click();

    // ---- 2. Typing first: the opinion that comes after leaves the person's words. The mark moves while it is pending.
    needs = [ok(1)];
    ops = {r1: {state: 'pending'}};
    asked.length = 0;
    await open('r1');
    await at(1);
    await page.waitForFunction(() => !!document.querySelector('.decide .dc-flow .flow-mark.live'));
    await dialog.locator('#dc-notes').fill('My own words');
    ops['r1'] = {state: 'ready', pick: 'send_back', line: 'Send back. The price is missing.', note: 'Add the price.', score: 80};
    await dialog.locator('.dc-say', {hasText: 'Send back. The price is missing.'}).waitFor({timeout: 8000});
    assert.equal(await dialog.locator('#dc-notes').inputValue(), 'My own words', 'never over their typing');
    assert.equal(await dialog.locator('.dc-flow .flow-mark.live').count(), 0, 'the mark rests once it is ready');
    assert.equal(await dialog.locator('.dc-up').textContent(), '');
    await dialog.locator('[data-dc="close"]').click();

    // ---- 3. A failing route: asked once, no line, and the screen answers as before.
    needs = [question(2), question(3)];
    ops = {};
    failWith = 500;
    asked.length = 0;
    await open('r2');
    await at(2);
    await page.waitForTimeout(4500);   // two polls' worth
    assert.equal(asked.length, 1, `asked once: ${JSON.stringify(asked)}`);
    assert.equal(await dialog.locator('.dc-say').textContent(), '');
    assert.equal(await dialog.locator('.dc-flow .flow-mark.live').count(), 0);
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await at(3);
    assert.deepEqual(writes, ['POST /api/v2/tasks/t2/asks/r2/answer']);

    assert.deepEqual(errors, []);
    await page.close();
    console.log("PASS: Tico's line picks the button and fills an empty Notes only; pending ones are polled and named; unseen items go by score; a failing route changes nothing.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
