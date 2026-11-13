import type {Route} from 'playwright';
// Offline browser check of Skip on the ask screen (task #181), against a fake of the new routes, on a 390 px phone and
// on a desktop:
// - every item, a typed choice with no answer row of its own included, shows a Skip button with at least a 40 px tap area, on every width; the phone has no ‹ ›;
// - Skip goes to the next item and sends nothing: the skipped item is not answered or changed;
// - at the last item, Skip wraps to the first one skipped, which is still waiting;
// - on a desktop ‹ › and the arrow keys still step through.
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
// `typed`: a stage's typed choice, which is only its buttons and has no answer row of its own; Skip makes one.
const question = (n: number, line: string, choices: string[], typed = false) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, `Task ${n}`),
  ask: {requirement_id: `r${n}`, kind: 'answer', line, who: 'human:ana', asked_by: 'bot:pm', choices, may: ['answer', 'thumbs'],
    met: false, shows: [], standing: 'waiting', answer: null,
    typed: typed ? {name: 'region', type: 'choice', min: null, max: null, question: line} : undefined}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const Q = ['Which plan do new signups get?', 'Which region first?', 'Ship on Monday?'];

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    for (const [label, viewport] of [['phone', {width: 390, height: 844}], ['desktop', {width: 1200, height: 900}]] as const) {
      const errors: string[] = [], writes: string[] = [];
      let needs = [question(1, Q[0]!, ['Free', 'Pro trial']), question(2, Q[1]!, ['EU', 'US'], true), question(3, Q[2]!, ['Yes', 'No'])];
      const page = await browser.newPage({viewport, serviceWorkers: 'block'});
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
        if (r.method() !== 'GET' && p.startsWith('/api/')) {
          writes.push(`${r.method()} ${p}`);
          const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
          if (m) { needs = needs.filter(n => n.id !== m[2]); return json({task: brief(m[1]!, 'Task'), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('[data-need="r1"]').waitFor();
      await page.locator('#task-play').click();
      const dialog = page.locator('#need-card');
      // Question `q` is on screen, as item `n` of `of`.
      const at = async (q: number, of: number, n = q) => {
        await dialog.locator('.dc-q', {hasText: Q[q - 1]!}).waitFor();
        assert.equal(await dialog.locator('.dc-count').innerText(), `${n} of ${of}`, label);
      };
      const skip = dialog.locator('[data-dc="skip"]');
      const skipBox = async () => {
        assert.equal(await skip.count(), 1, `${label}: one Skip on the card`);
        assert.ok(await skip.isVisible(), `${label}: Skip shows`);
        const box = (await skip.boundingBox())!;
        assert.ok(box.width >= 40 && box.height >= 40, `${label}: Skip's tap area is ${box.width}x${box.height}`);
        assert.ok(box.y >= 0 && box.y + box.height <= viewport.height, `${label}: Skip is in view`);
      };
      await at(1, 3);
      if (label === 'phone') assert.equal(await dialog.locator('[data-dc="next"]').isVisible(), false, 'no › on a phone');

      // Skip: the next item, nothing sent.
      await skipBox();
      await skip.click();
      await at(2, 3);
      await dialog.locator('[data-dc-choice="EU"]').waitFor();          // the typed choice: its buttons, then Skip
      await skipBox();
      await skip.click();
      await at(3, 3);
      assert.deepEqual(writes, [], `${label}: a skip sends nothing`);

      // At the last item Skip wraps to the first one skipped, still waiting.
      await skipBox();
      await skip.click();
      await at(1, 3);
      assert.deepEqual(writes, [], `${label}: a skip sends nothing`);

      // Answering still works after a skip, and the rest stay.
      await dialog.locator('[data-dc-choice="Pro trial"]').click();
      await dialog.locator('.dc-bar:not([hidden])').waitFor();
      await at(2, 2, 1);
      assert.deepEqual(writes, ['POST /api/v2/tasks/t1/asks/r1/answer']);

      if (label === 'desktop') {
        // ‹ › and the arrow keys still step.
        await dialog.locator('[data-dc="next"]').click();
        await at(3, 2, 2);
        await dialog.locator('[data-dc="prev"]').click();
        await at(2, 2, 1);
        await dialog.locator('.dc-count').click();
        await page.keyboard.press('ArrowRight');
        await at(3, 2, 2);
        await page.keyboard.press('ArrowLeft');
        await at(2, 2, 1);
      }
      assert.deepEqual(errors, [], label);
      await page.close();
    }
    console.log('PASS: Skip shows on a phone and a desktop, steps on without answering and wraps to the first skipped; ‹ › and arrows stay on a desktop.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
