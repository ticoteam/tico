import type {Route} from 'playwright';
// Offline browser check of a letter quoted again between showing and clicking (docs/pipelines-v3.md S3): the click on
// what was shown is refused (409 task_requirement, the old ask is not asked any more), and the screen moves to the ask
// that took its place, reads its new proof and hash, and only a new click sends that hash. No dead end.
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
const need = (rid: string, line: string, hour: number, shows: string[] = []) => ({kind: 'ask', id: rid, task_id: 't-' + rid, requirement_id: rid, line,
  since: `2026-11-09T${String(hour).padStart(2, '0')}:00:00Z`, task: brief('t-' + rid, line),
  ask: {requirement_id: rid, kind: 'ok', line, who: 'human:ana', asked_by: 'bot:pm', choices: null, may: ['ok', 'no', 'thumbs'], met: false,
    shows, standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const letter = (id: string, rid: string, blob: string) => ({id, approval_id: rid, status: 'waiting_for_ok', proof: `/api/v2/files/${blob}`,
  recipient: {name: 'Jo Rivera', address_line1: '1 Main St', address_city: 'Springfield'}, mail_class: 'usps_first_class', page_count: 2,
  total_micros: 1840000, shown_hash: 'c'.repeat(64)});
const gate = () => { let open!: () => void; const p = new Promise<void>(r => { open = r; }); return {p, open}; };

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  let needs = [need('r1', 'Mail the lease letter', 10, ['letter:L1']), need('r9', 'Approve the plan', 12)];
  let current = {...letter('L1', 'r1', 'b1'), shown_hash: 'c'.repeat(64)};
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  const pdf = '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n';
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
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: needs.map(n => n.id).join(','), next: null});
      const ok = /^\/api\/v2\/oks\/(r1|r1b)$/.exec(p);
      if (ok) return json({requirement_id: ok[1], subject: 'letter:L1', content: {letter_id: 'L1'}, shown_hash: 'd'.repeat(64), standing: 'waiting', task_id: 't-r1'});
      if (p === '/api/v2/oks/r9') return json({requirement_id: 'r9', subject: 'spec:1', content: {title: 'Plan', body: 'The plan.'}, shown_hash: '3'.repeat(64), standing: 'waiting', task_id: 't-r9'});
      if (p === '/api/v2/letters/L1') return json(current);
      if (p === '/api/v2/files/b1' || p === '/api/v2/files/b2') return route.fulfill({contentType: 'application/pdf', body: pdf});
      if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
        writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
        if (p === '/api/v2/tasks/t-r1/asks/r1/ok')
          return json({error: 'task_requirement', detail: 'This is not asked any more'}, 409);
        if (p === '/api/v2/tasks/t-r1/asks/r1b/ok') {
          if (writes.at(-1)!.body['shown_hash'] !== '9'.repeat(64)) return json({error: 'task_changed'}, 409);
          needs = needs.filter(n => n.id !== 'r1b');
          return json({task: brief('t-r1', 'x'), answer_id: 'a1', requirement_id: 'r1b'});
        }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    await dialog.locator('.dc-q', {hasText: 'Mail the lease letter'}).waitFor();
    await dialog.locator('iframe.dc-proof').waitFor();
    await dialog.getByText("$1.84", {exact: false}).waitFor();

    // Quoted again before the click: a new total and proof, asked again (r1b) on the same task.
    needs = [{...need('r1b', 'Mail the lease letter', 11, ['letter:L1']), task_id: 't-r1', task: brief('t-r1', 'Mail the lease letter')}, needs[1]!];
    current = {...letter('L1', 'r1b', 'b2'), total_micros: 21000000, shown_hash: '9'.repeat(64)};
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();

    // The refused click sent the old hash only; the screen is on the new ask, with the new total.
    await dialog.getByText('$21.00', {exact: false}).waitFor();
    assert.equal(await dialog.locator('.dc-q').innerText(), 'Mail the lease letter');
    await page.waitForTimeout(300);
    assert.deepEqual(writes.map(w => [w.p, w.body['shown_hash']]), [['/api/v2/tasks/t-r1/asks/r1/ok', 'c'.repeat(64)]], 'nothing sent again by itself');
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();
    await dialog.locator('.dc-q', {hasText: 'Approve the plan'}).waitFor();
    assert.deepEqual(writes[1], {p: '/api/v2/tasks/t-r1/asks/r1b/ok', body: {shown_hash: '9'.repeat(64)}});

    assert.deepEqual(errors, []);
    console.log('PASS: a letter quoted again after it was shown is refused, then its new quote is shown and OK\'d by a new click.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
