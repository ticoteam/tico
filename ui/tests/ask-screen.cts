import type {Route} from 'playwright';
// Offline browser check of Tasks › You and the ask screen (docs/pipelines-v3.md S2, S3), against a fake of the new routes:
// - You lists each item as the sentence asked, under Answer and Review; Flow opens the first;
// - an OK shows exactly what GET /api/v2/oks/{id} returned, and Approve sends that preview's hash;
// - when the engine says it changed (409 task_changed), the new content is shown with "It changed. Look again." and
//   nothing is sent again until a new click, which sends the new hash;
// - after Approve the top bar has ↶ named Undo (no word beside it; POST /api/v2/answers/{id}/undo) and the next item
//   shows; ↶ fades after a few seconds, and stays while it has the keyboard's focus;
// - a question answers with its choice; then All done;
// - ⋯ is last on an OK's row and holds Close task: it closes the task with what is typed in Notes as the reason, moves
//   on with no confirm, and ↶ reopens it (with a why, which a reopen needs).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const ask = (rid: string, kind: string, line: string, choices: string[] | null) => ({requirement_id: rid, kind, line, who: 'human:ana',
  asked_by: 'bot:pm', choices, may: kind === 'ok' ? ['ok', 'no', 'thumbs'] : ['answer', 'thumbs'], met: false, shows: [], standing: 'waiting', answer: null});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  let needs = [
    {kind: 'ask', id: 'r1', task_id: 't1', requirement_id: 'r1', line: 'Approve the spec for Checkout flow', since: '2026-11-09T10:00:00Z',
      task: brief('t1', 'Checkout flow'), ask: ask('r1', 'ok', 'Approve the spec for Checkout flow', null)},
    {kind: 'ask', id: 'r2', task_id: 't2', requirement_id: 'r2', line: 'Which plan do new signups get?', since: '2026-11-09T11:00:00Z',
      task: brief('t2', 'Signup plans'), ask: ask('r2', 'answer', 'Which plan do new signups get?', ['Free', 'Pro trial'])},
  ];
  let version = 1;
  const content = () => ({title: 'Checkout flow: spec', body: version === 1 ? 'Guests can pay without an account.' : 'Guests can pay. The summary stays visible.'});
  const hash = () => (version === 1 ? 'a' : 'b').repeat(64);
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  try {
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
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      if (p === '/api/v2/oks/r3') return json({requirement_id: 'r3', subject: 'spec:3', content: {title: 'Pricing page: pitch', body: 'A page per plan.'}, shown_hash: 'c'.repeat(64), standing: 'waiting', task_id: 't3'});
      if (p === '/api/v2/oks/r1') return json({requirement_id: 'r1', subject: 'spec:1', content: content(), shown_hash: hash(), standing: 'waiting', task_id: 't1'});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});   // Tico has none: Flow as without it
      if (p === '/api/v2/flow/suggestions') return json({state: 'ready', suggestions: []});   // none: All done as without it
      if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
        const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        writes.push({p, body});
        if (p === '/api/v2/tasks/t1/asks/r1/ok') {
          if (body['shown_hash'] !== hash()) return json({error: 'task_changed', detail: 'What you saw is not what is asked now. Open it again.'}, 409);
          needs = needs.filter(n => n.id !== 'r1');
          return json({task: brief('t1', 'Checkout flow'), answer_id: 'a1', requirement_id: 'r1'});
        }
        if (p === '/api/v2/tasks/t2/asks/r2/answer') {
          needs = needs.filter(n => n.id !== 'r2');
          return json({task: brief('t2', 'Signup plans'), answer_id: 'a2', requirement_id: 'r2'});
        }
        if (p === '/api/v2/answers/a2/undo') return json({task: brief('t2', 'Signup plans')});
        if (p === '/api/v2/tasks/t3/move') {
          needs = body['to'] === 'closed' ? needs.filter(n => n.id !== 'r3') : needs;
          return json({task: {...brief('t3', 'Pricing page'), status: body['to'] === 'closed' ? 'closed' : 'open'}});
        }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    const row = page.locator('[data-need="r1"]');
    await row.waitFor();
    assert.equal((await row.locator('.tk-title').innerText()).trim(), 'Checkout flow', 'the row leads with its task');
    assert.equal(await row.getAttribute('title'), 'Approve the spec for Checkout flow', 'the ask is its tooltip');
    assert.deepEqual(await page.locator('#task-body .tk-group').evaluateAll(els => els.map(e => e.firstChild?.textContent?.trim())), ['Answer', 'Review']);

    // Play opens the first in the list's order: the question (Answer) before the OK (Review).
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    await dialog.locator('.dc-q').waitFor();
    assert.equal(await dialog.locator('[data-dc="prev"]').isDisabled(), true, 'the first of the walk');
    assert.equal(await dialog.locator('.dc-q').innerText(), 'Which plan do new signups get?');
    await dialog.locator('[data-dc="next"]').click();

    // The OK: what the server showed, and Approve with its hash.
    await dialog.getByText('Guests can pay without an account.', {exact: true}).waitFor();
    assert.equal(await dialog.locator('.dc-q').innerText(), 'Approve the spec for Checkout flow');
    version = 2;                                      // it changes after it was shown
    await dialog.locator('[data-dc="approve"]').click();
    await dialog.locator('.dc-changed').waitFor();
    assert.equal(await dialog.locator('.dc-changed').innerText(), 'It changed. Look again.');
    await dialog.getByText('Guests can pay. The summary stays visible.', {exact: true}).waitFor();
    await page.waitForTimeout(300);
    assert.deepEqual(writes.map(w => [w.p, w.body['shown_hash']]), [['/api/v2/tasks/t1/asks/r1/ok', 'a'.repeat(64)]], 'not sent again by itself');
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    assert.deepEqual(writes[1], {p: '/api/v2/tasks/t1/asks/r1/ok', body: {shown_hash: 'b'.repeat(64)}});
    const undo = dialog.locator('.dc-bar [data-dc="undo"]');
    assert.deepEqual([await undo.getAttribute('aria-label'), await undo.getAttribute('title'), (await undo.innerText()).trim()], ['Undo', 'Undo', ''], '↶ alone, named Undo');
    assert.equal(await dialog.locator('.dc-bar [role="status"]').evaluate(e => e.getBoundingClientRect().width <= 1), true, 'what was done, for a screen reader only');
    assert.ok(await dialog.locator('.dc-head .dc-bar').count() === 1, 'the undo is in the top bar');
    // ↶ stays while it has the keyboard's focus, and fades a few seconds after it is left.
    await undo.focus();
    await page.waitForTimeout(4600);
    assert.equal(await dialog.locator('.dc-bar:not([hidden]):not(.dc-bar-out)').count(), 1, 'focused, it stays');
    await undo.blur();
    await dialog.locator('.dc-bar[hidden]').waitFor({state: 'attached', timeout: 6000});

    // The question is next: its choice answers it.
    assert.equal(await dialog.locator('.dc-q').innerText(), 'Which plan do new signups get?');
    await dialog.locator('[data-dc-choice="Pro trial"]').click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes[2], {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value: 'Pro trial', version: 3}});

    // Undo on the bar takes the last answer back.
    await dialog.locator('[data-dc="undo"]').click();
    await dialog.locator('.dc-bar[hidden]').waitFor({state: 'attached'});
    assert.deepEqual(writes[3], {p: '/api/v2/answers/a2/undo', body: {}});
    await dialog.locator('[data-dc="close"]').first().click();
    assert.equal(await dialog.evaluate((d: HTMLDialogElement) => d.open), false);

    // Close task from an OK's ⋯: last on its row; the note is the reason; no confirm; ↶ reopens it.
    needs = [{kind: 'ask', id: 'r3', task_id: 't3', requirement_id: 'r3', line: 'Approve the pitch for Pricing page', since: '2026-11-09T12:00:00Z',
      task: brief('t3', 'Pricing page'), ask: ask('r3', 'ok', 'Approve the pitch for Pricing page', null)}];
    await page.evaluate('v2Refresh()');
    await page.locator('[data-need="r3"]').waitFor();
    await page.locator('#task-play').click();
    await dialog.getByText('A page per plan.', {exact: true}).waitFor();
    const more = dialog.locator('[data-key] > .dc-acts [data-dc="ask-more"]');
    assert.equal(await dialog.locator('[data-key] > .dc-acts > :last-child').evaluate(e => !!e.querySelector('[data-dc="ask-more"]')), true, '⋯ is last on the row');
    await dialog.locator('#dc-notes').fill('no not worth the clutter');
    await more.click();
    if (process.env.FLOW_SHOTS) {
      const shot = require('node:path').join(process.env.FLOW_SHOTS, 'ok-more');
      await page.waitForTimeout(900);   // past "Getting ready to flow"
      await page.screenshot({path: `${shot}-desktop.png`});
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(400);
      await page.screenshot({path: `${shot}-phone.png`});
      await page.setViewportSize({width: 1200, height: 900});
      await page.waitForTimeout(300);
    }
    const n = writes.length;
    await dialog.locator('[role="menuitem"][data-dc="close-task"]').click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes[n], {p: '/api/v2/tasks/t3/move', body: {to: 'closed', why: 'no not worth the clutter', version: 3}});
    await dialog.locator('[data-dc="undo"]').click();
    await dialog.locator('.dc-bar[hidden]').waitFor({state: 'attached'});
    assert.deepEqual(writes[n + 1], {p: '/api/v2/tasks/t3/move', body: {to: 'reopen', why: 'Undone'}});

    assert.deepEqual(errors, []);
    console.log('PASS: You lists the asks; an OK sends the hash of what it showed, shows a change and waits for a new click; a choice answers; ↶ Undo fades; Close task from ⋯.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
