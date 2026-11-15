import type {Route} from 'playwright';
// Offline browser check of Suggested rules under Flow's All done (ui/app/decide.ts decideSugHTML), against a fake of
// POST /api/v2/flow/suggestions and its /add, /undo, /dismiss and /feedback (backend/flow_suggestions.py):
// - All done asks once, reads again about every 2 s while the answer is pending, and stops when it is ready;
// - each row is the rule, then "Pipeline / Stage · 4 of 6 send backs", Add, ✎ and ✕;
// - Add mutes the row, says Added and leaves only ↩; ↩ puts it back;
// - ✎ opens one box with Send; sending dims the row with "Tico is rewriting it" until the new text comes;
// - ✕ takes the row away; with nothing to show there is no list and no heading;
// - a read sent before an action never puts back what the action changed, one read is out at a time, and the list is
//   read once more after each action; a refused Add says why on its row;
// - someone who may change no pipeline is never asked for any;
// - on a phone the page does not scroll sideways and a long rule wraps.
// FLOW_SHOTS=<folder> saves All done with suggestions, desktop and phone.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Suggestion = {id: string; text: string; state: 'open' | 'added' | 'dismissed'; pending: boolean; notes_used: number;
  notes_of: number; pipeline_id: string; pipeline: string; stage_id: string; stage: string; requirement_id: string};
type Item = Record<string, unknown> & {id: string};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = {id: 't1', title: 'Launch date', number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3};
const question = (): Item => ({kind: 'ask', id: 'r1', task_id: 't1', requirement_id: 'r1', line: 'Launch date: yes or no?',
  since: '2026-11-09T10:00:00Z', task: brief,
  ask: {requirement_id: 'r1', kind: 'answer', line: 'Launch date: yes or no?', who: 'human:ana', asked_by: 'human:ben',
    choices: ['Yes', 'No'], may: ['answer'], met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = (may: boolean) => ({id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana',
  private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: may});
const row = (id: string, text: string, used: number, of: number, more: Partial<Suggestion> = {}): Suggestion => ({id, text,
  state: 'open', pending: false, notes_used: used, notes_of: of, pipeline_id: 'product', pipeline: 'Product', stage_id: 'spec',
  stage: 'Spec', requirement_id: 'q1', ...more});
const TESTS = 'Lists the tests run and their results, with a link to the run for each one, so a reviewer can check them.';
const REWRITTEN = 'Lists every test run, with its result.';

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: string[] = [], reads: number[] = [];
    let needs: Item[] = [], may = true, rows: Suggestion[] = [], states: string[] = [];
    let rewrite: Suggestion | null = null;
    // `hold`: the next read answers (with the rows as they were when it was asked) only once `release` is called.
    let hold: {release: () => void; gate: Promise<void>} | null = null, out = 0, mostOut = 0, full = '';
    const holdNext = () => { let release = () => {}; const gate = new Promise<void>(r => { release = r; }); hold = {release, gate}; return hold; };
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
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline(may)]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      if (p.startsWith('/api/v2/oks/')) return json({error: 'not_found'}, 404);
      if (p === '/api/v2/tasks/t1') return json({task: {...brief, created_at: '2026-11-09T09:00:00Z', attachments: [], links: [], body: ''}});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});
      if (p === '/api/v2/flow/suggestions') {
        reads.push(Date.now());
        // A rewrite under way lands on the read after the next one.
        if (rewrite && states.length === 0) { rows = rows.map(x => x.id === rewrite!.id ? rewrite! : x); rewrite = null; }
        const state = states.shift() || 'ready', held = hold, now = rows.map(x => ({...x}));
        hold = null;
        out++; mostOut = Math.max(mostOut, out);
        try {
          if (held) await held.gate;
          await json({state, suggestions: now});
        } catch { /* closed */ } finally { out--; }
        return;
      }
      const acted = p.match(/^\/api\/v2\/flow\/suggestions\/([^/]+)\/(add|undo|dismiss|feedback)$/);
      if (acted && acted[2] === 'add' && acted[1] === full) {
        writes.push(`add ${acted[1]}`);
        return json({error: {code: 'conflict', detail: 'This check already has 20 added rules. Undo one first.'}}, 409);
      }
      if (acted) {
        writes.push(`${acted[2]} ${acted[1]}${acted[2] === 'feedback' ? ' ' + (JSON.parse(r.postData() || '{}') as {text: string}).text : ''}`);
        const at = rows.findIndex(x => x.id === acted[1]), was = rows[at]!;
        const now: Suggestion = acted[2] === 'add' ? {...was, state: 'added'} : acted[2] === 'undo' ? {...was, state: 'open'}
          : acted[2] === 'dismiss' ? {...was, state: 'dismissed'} : {...was, pending: true};
        rows[at] = now;
        if (acted[2] === 'dismiss') rows.splice(at, 1);
        if (acted[2] === 'feedback') { rewrite = {...was, text: REWRITTEN}; states = ['pending']; }
        return json({suggestion: now});
      }
      if (r.method() !== 'GET' && p.startsWith('/api/')) {
        const m = p.match(/^\/api\/v2\/tasks\/(t\d+)\/asks\/(r\d+)\/answer$/);
        if (m) { needs = []; return json({task: brief, answer_id: 'a-' + m[2], requirement_id: m[2]}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = page.locator('#need-card'), list = dialog.locator('.dc-sug');
    const sr = (n: number) => list.locator('.dc-sr').nth(n);
    const done = async () => {
      await page.goto('about:blank');
      reads.length = 0;
      needs = [question()];
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('[data-need="r1"]').waitFor();
      await page.locator('#task-play').click();
      await dialog.locator('[data-dc-choice="Yes"]').click();
      await dialog.getByText('All done').waitFor();
    };
    const until = async (ok: () => boolean, what: string) => {
      for (const end = Date.now() + 6000; !ok();) {
        assert.ok(Date.now() < end, what);
        await page.waitForTimeout(50);
      }
    };
    const shot = async (name: string) => {
      if (!process.env.FLOW_SHOTS) return;
      await page.waitForTimeout(400);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-desktop.png`)});
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(300);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-phone.png`)});
      await page.setViewportSize({width: 1200, height: 900});
    };

    // ---- 1. The list: asked at All done, read again while pending, then left alone.
    rows = [row('s1', TESTS, 4, 6)];
    states = ['pending'];
    setTimeout(() => { rows = [row('s1', TESTS, 4, 6), row('s2', 'Names the PR it is about.', 3, 6)]; }, 500);
    await done();
    await sr(1).waitFor();
    while (reads.length < 2) await page.waitForTimeout(100);
    assert.equal(await list.locator('h3').innerText(), 'Suggested rules');
    assert.equal(await sr(0).locator('.tx b').innerText(), TESTS);
    assert.equal(await sr(0).locator('.tx span').innerText(), 'Product / Spec · 4 of 6 send backs');
    assert.deepEqual(await sr(0).locator('button').evaluateAll(bs => bs.map(b => b.textContent + '|' + (b.getAttribute('title') || ''))),
      ['Add|', '✎|Tell Tico what to change', '✕|Dismiss']);
    assert.equal(reads.length, 2, `read again once it was pending: ${reads.length}`);
    assert.ok(reads[1]! - reads[0]! >= 1500, `about 2 s apart: ${reads[1]! - reads[0]!}`);
    await page.waitForTimeout(2600);
    assert.equal(reads.length, 2, 'ready: no more reads');
    const desktopLine = await sr(0).locator('.tx b').evaluate(b => getComputedStyle(b).whiteSpace);
    assert.equal(desktopLine, 'nowrap', 'one line on a desktop');

    // ---- 2. Add, then Undo.
    await sr(0).getByRole('button', {name: 'Add'}).click();
    await list.locator('.dc-sr.on').waitFor();
    assert.equal(await sr(0).locator('.tx span').innerText(), 'Added');
    assert.deepEqual(await sr(0).locator('button').evaluateAll(bs => bs.map(b => b.getAttribute('aria-label'))), ['Undo']);
    await sr(0).getByRole('button', {name: 'Undo'}).click();
    await sr(0).getByRole('button', {name: 'Add'}).waitFor();
    assert.equal(await list.locator('.dc-sr.on').count(), 0);
    assert.equal(await sr(0).locator('.tx span').innerText(), 'Product / Spec · 4 of 6 send backs');

    // ---- 3. ✎: one box with Send; sending dims the row until the new text comes.
    await sr(1).getByRole('button', {name: 'Tell Tico what to change'}).click();
    const box = list.locator('#dc-sug-say');
    await box.waitFor();
    assert.equal(await list.locator('.dc-sf').count(), 1);
    assert.equal(await list.locator('[data-dc="sug-send"]').isDisabled(), true, 'Send waits for words');
    await sr(0).getByRole('button', {name: 'Tell Tico what to change'}).click();   // another row's ✎: its box instead
    assert.equal(await list.locator('.dc-sf').count(), 1);
    assert.equal(await list.locator('.dc-sf').getAttribute('data-sug'), 's1');
    await sr(0).getByRole('button', {name: 'Tell Tico what to change'}).click();   // again: closed
    assert.equal(await list.locator('.dc-sf').count(), 0);
    await sr(1).getByRole('button', {name: 'Tell Tico what to change'}).click();
    await box.fill('Say every test');
    await list.locator('[data-dc="sug-send"]').click();
    await list.locator('.dc-sr.busy').waitFor();
    assert.equal(await sr(1).locator('.tx span').innerText(), 'Tico is rewriting it');
    assert.equal(await sr(1).getByRole('button', {name: 'Add'}).isDisabled(), true, 'no Add while it is rewritten');
    assert.equal(await list.locator('.dc-sf').count(), 0);
    await sr(1).locator('.tx b', {hasText: REWRITTEN}).waitFor({timeout: 8000});
    assert.equal(await list.locator('.dc-sr.busy').count(), 0);
    assert.equal(await sr(1).locator('.tx span').innerText(), 'Product / Spec · 3 of 6 send backs');

    // The end screen with suggestions, one added: desktop and phone.
    await sr(0).getByRole('button', {name: 'Add'}).click();
    await list.locator('.dc-sr.on').waitFor();
    await shot('flow-suggestions');

    // ---- 4. A phone: no sideways scroll, and a long rule wraps.
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(200);
    const wide = await page.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 390, `no sideways scroll on a phone: ${wide}`);
    assert.equal(await sr(0).locator('.tx b').evaluate(b => getComputedStyle(b).whiteSpace), 'normal', 'it wraps on a phone');
    const lines = await sr(0).locator('.tx b').evaluate(b => Math.round(b.getBoundingClientRect().height / parseFloat(getComputedStyle(b).lineHeight)));
    assert.ok(lines > 1, `the long rule takes more than one line: ${lines}`);
    const right = await sr(0).locator('button').last().evaluate(b => b.getBoundingClientRect().right);
    assert.ok(right <= 390, `its buttons fit: ${right}`);
    await page.setViewportSize({width: 1200, height: 900});

    // ---- 5. ✕ takes a row away; with the last gone there is no list and no heading.
    await sr(0).getByRole('button', {name: 'Undo'}).click();
    await sr(0).getByRole('button', {name: 'Dismiss'}).click();
    await list.locator('.dc-sr').nth(1).waitFor({state: 'detached'});
    assert.equal(await sr(0).locator('.tx b').innerText(), REWRITTEN);
    await sr(0).getByRole('button', {name: 'Dismiss'}).click();
    await list.waitFor({state: 'detached'});
    assert.equal(await dialog.getByText('Suggested rules').count(), 0);
    assert.deepEqual(writes, ['add s1', 'undo s1', 'feedback s2 Say every test', 'add s1', 'undo s1', 'dismiss s1', 'dismiss s2']);

    // ---- 6. A read sent before an Add answers after it: dropped, so the row stays added; then one fresh read.
    rows = [row('s1', TESTS, 4, 6), row('s2', 'Names the PR it is about.', 3, 6)]; states = ['pending'];
    mostOut = 0; writes.length = 0;
    await done();
    await until(() => reads.length >= 1, 'asked');
    const held = holdNext();   // the poll 2 s on is held, answering with s1 still open
    await until(() => reads.length >= 2, 'read again while pending');
    await sr(0).getByRole('button', {name: 'Add'}).click();
    await list.locator('.dc-sr.on').waitFor();
    await page.waitForTimeout(300);
    held.release();
    await page.waitForTimeout(300);
    assert.equal(await sr(0).locator('.tx span').innerText(), 'Added', 'the older read did not flip it back');
    await until(() => reads.length >= 3, 'read once more after the Add');
    assert.equal(await sr(0).getByRole('button', {name: 'Undo'}).count(), 1);
    assert.equal(mostOut, 1, 'one read at a time');
    await page.waitForTimeout(2400);
    assert.equal(reads.length, 3, 'ready: no more reads');

    // A refused Add says why on its row, and the row stays as it was.
    full = 's2';
    await sr(1).getByRole('button', {name: 'Add'}).click();
    await sr(1).locator('.tx span', {hasText: 'already has 20 added rules'}).waitFor();
    assert.equal(await sr(1).locator('.tx span').innerText(), 'This check already has 20 added rules. Undo one first.');
    assert.equal(await sr(1).getByRole('button', {name: 'Add'}).count(), 1);
    assert.deepEqual(writes, ['add s1', 'add s2']);
    full = '';
    await dialog.locator('[data-dc="close"]').first().click();

    // ---- 7. Nothing to show: no list and no heading.
    rows = []; states = [];
    await done();
    await page.waitForTimeout(300);
    assert.equal(reads.length, 1);
    assert.equal(await list.count(), 0);
    assert.equal(await dialog.getByText('Suggested rules').count(), 0);

    // ---- 8. Someone who may change no pipeline: never asked.
    may = false;
    rows = [row('s1', TESTS, 4, 6)];
    await done();
    await page.waitForTimeout(600);
    assert.equal(reads.length, 0, 'not asked');
    assert.equal(await list.count(), 0);

    // ---- 9. An empty answer (another server) or a failing one: nothing, no more reads.
    may = true;
    let failed = 0;
    await page.route('**/api/v2/flow/suggestions', route => { failed++; return route.fulfill({status: 500, contentType: 'application/json', body: '{}'}); });
    await done();
    await page.waitForTimeout(2600);
    assert.equal(failed, 1, 'a failure stops the reads');
    assert.equal(await list.count(), 0);

    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: Suggested rules list under All done; Add/Undo, ✎ rewrite pending then new text, ✕; none without rows or the right; phone fits.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
