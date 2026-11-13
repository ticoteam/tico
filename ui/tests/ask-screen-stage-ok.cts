import type {Route} from 'playwright';
// Offline browser check of a stage's page OK on the ask screen (docs/pipelines-v3.md S3). A stage's requirement ("OK Ana")
// is the same on every task in the stage, so two tasks in Spec ask it with one requirement id:
// - each ask shows its own task's page at its own version, never the other's;
// - the heading is the task's title, with the version under it, not the person's own name;
// - the page fills the pane (desktop and phone), scrolling inside itself;
// - Approve answers the ask's own task with the page it showed;
// - a page version that is Markdown, not HTML, is drawn as Markdown (headings, lists), not as one run-on paragraph.
// TICO_UI_SHOTS=<dir> saves a screenshot of each.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const RID = 'spec-ok-ana';
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const need = (task: string, title: string, file: string, version: number, hour: number, line = `OK Ana (version ${version})`) => ({
  kind: 'ask', id: `v-${task}:${RID}`, task_id: task, requirement_id: RID, line, since: `2026-11-09T${String(hour).padStart(2, '0')}:00:00Z`,
  task: brief(task, title),
  ask: {requirement_id: RID, kind: 'page', line, who: 'human:ana', asked_by: 'bot:pm', choices: null, may: ['ok', 'no'], met: false,
    shows: [`page:${file}@${version}`], standing: 'waiting', answer: null, page: {file_id: file, version, ref: `page:${file}@${version}`}}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const pages: Record<string, string> = {
  'pa@1': '<h1>Board chat proposals</h1>' + '<p>Proposal.</p>'.repeat(80),
  'pb@3': '<h1>Paste images spec</h1>' + '<p>Paste.</p>'.repeat(80),
  'pc@2': '\uFEFF\n## What was built\n\n- **Retry** on a failed paste\n- A size limit\n\n<script>window.mdRan = 1</script>\n',
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const shots = process.env.TICO_UI_SHOTS;
  const needs = [
    need('t-a', 'Talk to the right bot from a pipeline board', 'pa', 1, 10),
    need('t-b', 'Images in bot chat: paste fails', 'pb', 3, 11, 'Jev is unsure: no mobile layout. OK Ana (version 3)'),
    need('t-c', 'Paste retries', 'pc', 2, 12),
  ];
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  try {
    for (const [label, viewport] of [['desktop', {width: 1200, height: 900}], ['phone', {width: 390, height: 844}]] as const) {
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
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: '1', next: null});
        if (p === `/api/v2/oks/${RID}`) return json({error: 'not_found'}, 404);     // a stage's OK: no grant
        const file = /^\/api\/v2\/files\/(p[abc])$/.exec(p), body = file ? pages[`${file[1]}@${url.searchParams.get('v')}`] : undefined;
        if (body) return route.fulfill({contentType: 'text/html', headers: {'content-disposition': 'inline; filename="page.html"'}, body});
        if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
          writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
          const m = /^\/api\/v2\/tasks\/(t-[ab])\/asks\/([^/]+)\/answer$/.exec(p);
          if (m) return json({task: brief(m[1]!, 'x'), answer_id: 'a-' + m[1], requirement_id: m[2]});
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('#task-play').click();
      const dialog = page.locator('#need-card');
      const frame = dialog.locator('iframe.dc-page');

      // The first ask: its own page, headed by its task.
      await dialog.locator('.dc-q', {hasText: 'Talk to the right bot from a pipeline board'}).waitFor();
      await frame.waitFor();
      assert.match(await frame.getAttribute('srcdoc') ?? '', /Board chat proposals/);
      assert.match(await dialog.locator('.dc-sub').innerText(), /Spec · Version 1/);
      assert.equal(await dialog.locator('.dc-said').count(), 0, 'the stage line names only the person asked: nothing to add');
      const pane = (await dialog.locator('.dc-thing').boundingBox())!, box = (await frame.boundingBox())!;
      assert.ok(box.height >= pane.height - 60, `${label}: the page fills the pane (${box.height} of ${pane.height})`);
      assert.ok(box.height > (label === 'phone' ? 300 : 500), `${label}: the page is tall (${box.height})`);
      if (shots) await page.screenshot({path: path.join(shots, `stage-ok-${label}-1.png`)});

      // The second ask, same requirement id: its own page at its own version, never the first one's.
      if (label === 'phone') await page.keyboard.press('ArrowRight');     // ‹ › are left off on a phone
      else await dialog.locator('[data-dc="next"]').click();
      await dialog.locator('.dc-q', {hasText: 'Images in bot chat: paste fails'}).waitFor();
      await dialog.locator('iframe.dc-page[title="Version 3"]').waitFor();
      assert.equal(await frame.count(), 1);
      assert.match(await frame.getAttribute('srcdoc') ?? '', /Paste images spec/);
      assert.doesNotMatch(await frame.getAttribute('srcdoc') ?? '', /Board chat proposals/);
      assert.match(await dialog.locator('.dc-sub').innerText(), /Version 3/);
      assert.equal(await dialog.locator('.dc-said').innerText(), 'Jev is unsure: no mobile layout.');
      if (shots) await page.screenshot({path: path.join(shots, `stage-ok-${label}-2.png`)});

      // The third ask's page is Markdown: drawn as Markdown, never as raw text in the frame, and its script never runs.
      if (label === 'phone') await page.keyboard.press('ArrowRight');
      else await dialog.locator('[data-dc="next"]').click();
      await dialog.locator('.dc-q', {hasText: 'Paste retries'}).waitFor();
      const doc = dialog.locator('.dc-page-md');
      await doc.waitFor();
      assert.equal(await frame.count(), 0);
      assert.equal(await doc.locator('h2').innerText(), 'What was built');
      assert.deepEqual(await doc.locator('li').allInnerTexts(), ['Retry on a failed paste', 'A size limit']);
      assert.equal(await doc.locator('strong').innerText(), 'Retry');
      assert.equal(await doc.locator('script').count(), 0);
      assert.equal(await page.evaluate(() => (window as unknown as {mdRan?: number}).mdRan), undefined);
      if (shots) await page.screenshot({path: path.join(shots, `stage-ok-${label}-3.png`)});
      if (label === 'phone') await page.keyboard.press('ArrowLeft');
      else await dialog.locator('[data-dc="prev"]').click();
      await dialog.locator('.dc-q', {hasText: 'Images in bot chat: paste fails'}).waitFor();
      await dialog.locator('iframe.dc-page[title="Version 3"]').waitFor();

      if (label === 'desktop') {
        // Approve answers this task's ask with this page; the first ask is still the first task's, with its own page.
        await dialog.locator('[data-dc="approve"]:not([disabled])').click();
        await dialog.locator('.dc-q', {hasText: 'Paste retries'}).waitFor();
        await dialog.locator('[data-dc="prev"]').click();
        await dialog.locator('.dc-q', {hasText: 'Talk to the right bot from a pipeline board'}).waitFor();
        assert.deepEqual(writes.find(w => w.p === `/api/v2/tasks/t-b/asks/${RID}/answer`)?.body, {value: 'ok', page_version: 'pb@3', version: 3});
        assert.match(await frame.getAttribute('srcdoc') ?? '', /Board chat proposals/);
        await dialog.locator('[data-dc="approve"]:not([disabled])').click();
        for (let i = 0; i < 50 && !writes.some(w => w.p === `/api/v2/tasks/t-a/asks/${RID}/answer`); i++) await page.waitForTimeout(100);
        assert.deepEqual(writes.find(w => w.p === `/api/v2/tasks/t-a/asks/${RID}/answer`)?.body, {value: 'ok', page_version: 'pa@1', version: 3});
      }
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log('PASS: two tasks asking one stage OK each show their own page, full height, under their own title; Approve answers its own task; a Markdown page is drawn as Markdown.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
