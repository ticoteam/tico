import type {Route} from 'playwright';
// Offline browser check of a stage's approval in the pipeline editor: the row names the document a person approves
// ("Approve Spec"); its editor holds the Document (task_requirements.APPROVED) and the Approver (any person, or one named
// person, never a bot). + Requirement › Approval… adds one on the Spec; a change to the document is the row's words and
// what a save sends (kind page, settings.document, people only). Shots (TICO_SHOTS) at desktop and phone, light and dark.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
type Req = Record<string, unknown>;
const stage = (id: string, name: string, requirements: Req[]) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [stage('spec', 'Spec', [{id: 'q0', kind: 'page', source: null, condition: null,
    settings: {document: 'Spec', rubric: 'States the goal.'}, can_push_past: true, who_oks: 'people_only', person_id: null,
    line: 'Spec', choices: null}])],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const saves: {stages: {requirements: Req[]}[]}[] = [];
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
      if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{id: 'ops', name: 'Ops', kind: 'bot'}]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/pipelines/product' && r.method() === 'POST') {
        const body = JSON.parse(r.postData() || '{}') as {stages: ReturnType<typeof stage>[]};
        saves.push(body);
        let n = 0;
        pipeline.stages = body.stages.map(s => ({...s, requirements: s.requirements.map(q => ({...q, id: q['id'] ?? `q${++n + 10}`, choices: q['choices'] ?? null}))}));
        pipeline.settings_version += 1;
        return json({pipeline});
      }
      if (p === '/api/v2/pipelines/product') return json({pipeline});
      if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
      if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks?pipeline=product&layout=columns');
    const dialog = page.locator('#pe-dialog');
    const at = (ri: number, rf: string) => dialog.locator(`[data-pe-si="0"][data-pe-ri="${ri}"][data-pe-rf="${rf}"]`);
    const row = (ri: number) => dialog.locator(`[data-pe-leave-row="req:${ri}"]`);
    const rows = () => dialog.locator('[data-pe-leave-row] .sr-text').allInnerTexts();
    const open = async (ri: number) => {
      const b = row(ri).locator('.sr-open');
      if (await b.getAttribute('aria-expanded') !== 'true') await b.click();
      await row(ri).locator('.pe-row-ed').waitFor();
    };

    await page.locator('[data-pv-settings="spec"]').click();
    await dialog.locator('.pe-stage-one[data-pe-si="0"]').waitFor();
    assert.deepEqual(await rows(), ['Approve Spec'], 'the row names the document');
    await open(0);
    assert.equal(await at(0, 'document').inputValue(), 'Spec');
    assert.deepEqual(await at(0, 'document').locator('option').allInnerTexts(), ['Spec', 'QA report', 'Release report', 'Invoice', 'Proposal', 'Results', 'Checked in production']);
    assert.equal(await at(0, 'approver').locator('option:checked').innerText(), 'Any person');
    assert.equal(await at(0, 'approver').locator('option', {hasText: 'Ops'}).count(), 0, 'a person approves a document, never a bot');
    assert.doesNotMatch(await dialog.innerText(), /Jev|Pitch/, 'no "Jev", no Pitch');

    const shots = process.env.TICO_SHOTS;
    if (shots) {
      fs.mkdirSync(shots, {recursive: true});
      for (const [size, width, height] of [['desktop', 1200, 900], ['phone', 390, 844]] as const) {
        await page.setViewportSize({width, height});
        await page.waitForTimeout(200);
        for (const theme of ['light', 'dark']) {
          await page.evaluate(t => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set(t), theme);
          await page.waitForTimeout(150);
          await page.screenshot({path: path.join(shots, `stage-editor-approval-${size}-${theme}.png`)});
        }
      }
      await page.setViewportSize({width: 1200, height: 900});
    }

    // The QA report, from Ben: the row says so, and a save sends it as the record stores it.
    await at(0, 'document').selectOption('QA report');
    await at(0, 'approver').selectOption('human:ben').catch(() => at(0, 'approver').selectOption({label: 'Ben'}));
    assert.match((await rows())[0] ?? '', /^Approve QA report/);
    // + Requirement › Approval… adds one on the Spec, from any person.
    await dialog.locator('[data-pe-req-add]').click();
    await page.locator('#pe-req-pick:popover-open [data-pe-req-kind="approval"]').click();
    await row(1).locator('.pe-row-ed').waitFor();
    assert.equal(await at(1, 'document').inputValue(), 'Spec');
    await dialog.locator('[data-pe-save]').click();
    await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
    assert.equal(saves.length, 1);
    const [qa, spec] = saves[0]!.stages[0]!.requirements;
    assert.deepEqual([qa!['kind'], qa!['line'], (qa!['settings'] as Req)['document'], qa!['who_oks']], ['page', 'QA report', 'QA report', 'people_only']);
    assert.match(String(qa!['person_id']), /ben$/);
    assert.deepEqual([spec!['kind'], spec!['line'], spec!['settings'], spec!['who_oks'], spec!['person_id']],
      ['page', 'Spec', {document: 'Spec'}, 'people_only', null]);

    assert.deepEqual(errors, []);
    console.log('pipeline-editor-approval: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
