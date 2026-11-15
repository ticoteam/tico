import type {Route} from 'playwright';
// Offline browser check of a stage's OK that names nothing to show (#3784): Product's OK release, which follows QA.
// Tico Team's task #72 showed only its title, and Tico's line said "Ask shows no release content".
// - It shows the task's page from the earlier stage (the QA page, at its version), not the task's title and text;
// - its one meta line says who asked, that stage, the page's version and the task (#72 ↗), on desktop and phone;
// - Approve stays on and answers the OK alone (no page version: it is not a page's OK);
// - an OK the server gave no earlier page (none, or one the reader may not open) shows the task's title and text.
// TICO_UI_SHOTS=<dir> saves ok-release-page-desktop.png and ok-release-page-phone.png.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'chris', name: 'Chris', email: 'chris@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const RID = 'ok-release';
const brief = (id: string, title: string, number: number) => ({id, title, number, pipeline_id: 'product', stage_id: 'product-ok-release',
  status: 'open', owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:chris', line: 'Waiting on Chris', since: '2026-11-15T10:00:00Z', until: null, ask: true, requirement_id: RID},
  version: 4});
const need = (task: string, title: string, number: number, hour: number, earlier: Record<string, unknown> | null) => ({
  kind: 'ask', id: `v-${task}:${RID}`, task_id: task, requirement_id: RID, line: 'OK the release', since: `2026-11-15T${hour}:00:00Z`,
  task: brief(task, title, number),
  ask: {requirement_id: RID, kind: 'ok', line: 'OK the release', who: 'human:chris', asked_by: null, choices: null, may: ['ok', 'no'],
    met: false, shows: [], standing: 'waiting', answer: null, ...(earlier ? {earlier} : {})}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:chris', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: ['product-qa:QA', 'product-ok-release:OK release'].map(s => ({id: s.split(':')[0], name: s.split(':')[1], purpose: '', owner: null,
    by_hand: false, task_limit: null, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: []})),
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const QA = '<h1>QA: saved carts</h1><p>Before and after: the cart is kept when the tab closes.</p>' + '<p>Checks pass at 9f3c2a1.</p>'.repeat(30);

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const shots = process.env.TICO_UI_SHOTS;
  const needs = [
    need('t-72', 'Saved carts', 72, 10, {file_id: 'pq', version: 2, ref: 'page:pq@2', stage_id: 'product-qa', stage: 'QA', source: ['page:pq@2']}),
    need('t-73', 'Dark mode toggle', 73, 11, {removed: true, label: "A page you can't open"}),
  ];
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  try {
    for (const [label, viewport] of [['desktop', {width: 1280, height: 900}], ['phone', {width: 390, height: 844}]] as const) {
      const page = await browser.newPage({viewport, serviceWorkers: 'block'});
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', async (route: Route) => {
        const r = route.request(), url = new URL(r.url()), p = url.pathname;
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, '../vendor/marked.min.js'), 'utf8')});
        if (p === '/api/me') return json({...me, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'chris', name: 'Chris'}, {id: 'ana', name: 'Ana'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:chris', count: needs.length, items: needs, version: '1', next: null});
        if (p === `/api/v2/oks/${RID}`) return json({error: 'not_found'}, 404);     // a stage's OK: no grant
        if (p === '/api/v2/files/pq' && url.searchParams.get('v') === '2')
          return route.fulfill({contentType: 'text/html', headers: {'content-disposition': 'inline; filename="page.html"'}, body: QA});
        if (p === '/api/v2/tasks/t-73') return json({task: {...brief('t-73', 'Dark mode toggle', 73), body: 'A toggle in Settings for dark mode.'}});
        if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
          writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
          const m = /^\/api\/v2\/tasks\/(t-7[23])\/asks\/([^/]+)\/answer$/.exec(p);
          if (m) return json({task: brief(m[1]!, 'x', 72), answer_id: 'a-' + m[1], requirement_id: m[2]});
          if (p === '/api/v2/flow/opinions' || p === '/api/v2/flow/suggestions') return json({opinions: [], suggestions: []});
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('#task-play').click();
      const dialog = page.locator('#need-card');
      const frame = dialog.locator('iframe.dc-page');

      // OK release on #72: the QA page, under the ask, with one meta line.
      await dialog.locator('.dc-q', {hasText: 'OK the release'}).waitFor();
      await frame.waitFor();
      assert.match(await frame.getAttribute('srcdoc') ?? '', /QA: saved carts/);
      assert.equal(await dialog.locator('.dc-thing .dc-title').count(), 0, `${label}: the page, not the task's title and text`);
      const meta = dialog.locator('.dc-side .dc-meta');
      assert.equal((await meta.innerText()).replace(/\s+/g, ' ').trim(), 'Ana · QA · v2 · #72 ↗');
      assert.equal(await dialog.locator('.dc-owner').count(), 0, 'who asked is said once, on the meta line');
      const link = (await meta.locator('.dc-task-link').boundingBox())!;
      if (label === 'phone') {
        assert.ok(link.height >= 44, `phone: the task link is a 44 px target (${link.height})`);
        const acts = (await dialog.locator('.dc-side .dc-acts').first().boundingBox())!, line = (await meta.boundingBox())!;
        assert.ok(line.y + line.height <= acts.y + 1, 'phone: the meta line sits above the buttons, never over them');
      }
      const pane = (await dialog.locator('.dc-thing').boundingBox())!, box = (await frame.boundingBox())!;
      assert.ok(box.height >= pane.height - 60, `${label}: the page fills the pane (${box.height} of ${pane.height})`);
      await dialog.locator('[data-dc="approve"]:not([disabled])').waitFor();
      if (shots) {
        await dialog.locator('.dc-start').waitFor({state: 'detached'});
        await page.screenshot({path: path.join(shots, `ok-release-page-${label}.png`)});
      }

      // #73: the server gave no page this reader may open: the task's title and text, as before.
      if (label === 'phone') await page.keyboard.press('ArrowRight');
      else await dialog.locator('[data-dc="next"]').click();
      await dialog.locator('.dc-thing .dc-title', {hasText: 'Dark mode toggle'}).waitFor();
      assert.equal(await frame.count(), 0);
      assert.match(await dialog.locator('.dc-thing').innerText(), /A toggle in Settings for dark mode/);
      assert.equal(await dialog.locator('.dc-side .dc-meta').count(), 0);

      if (label === 'desktop') {
        await dialog.locator('[data-dc="prev"]').click();
        await frame.waitFor();
        await dialog.locator('[data-dc="approve"]:not([disabled])').click();
        for (let i = 0; i < 50 && !writes.some(w => w.p === `/api/v2/tasks/t-72/asks/${RID}/answer`); i++) await page.waitForTimeout(100);
        assert.deepEqual(writes.find(w => w.p === `/api/v2/tasks/t-72/asks/${RID}/answer`)?.body, {value: 'ok', version: 4});
      }
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log('PASS: an OK that names nothing shows the QA page with who · stage · version · task on one line; with none it shows the task.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
