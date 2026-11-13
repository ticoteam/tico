import type {Route} from 'playwright';
// Offline browser check of a stage whose Owner is "One of several" (spec "A stage picks its owner from several"): the
// stage reads in task order (When a task arrives, Who works on it, To leave); choosing One of several shows the bots
// with their model family, Pick and Prefer as switches, Limit each, the owner switch and the next-task sentence; a save
// sends the pick as the record stores it, without what the server writes (families, the sentence); the closed row
// says "One of 2". Tasks leave is a two-way switch. Desktop and phone widths. TICO_UI_SHOTS=<dir> keeps screenshots.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
type Rec = Record<string, unknown>;
const done = {id: 'q0', kind: 'owner_done', source: null, condition: null, settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: '', choices: null};
const stage = (id: string, name: string, extra: Rec = {}) => ({id, name, purpose: '', owner: null, by_hand: false, requests_move_on: false,
  task_limit: null, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [{...done, id: `q-${id}`}], ...extra});
const bots = [{id: 'builder', name: 'Builder'}, {id: 'builder-2', name: 'Builder 2'}, {id: 'reviewer-1', name: 'Reviewer 1'}];
const FAMILY: Record<string, string> = {'bot:builder': 'Claude', 'bot:builder-2': 'Claude', 'bot:reviewer-1': 'GPT'};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const saves: {stages: Rec[]}[] = [];
  const pipeline: Rec & {stages: Rec[]; settings_version: number} = {id: 'engineering', name: 'Engineering', goal: '', prefix: 'E',
    manager_id: null, owner_id: 'human:ana', private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null,
    stages: [stage('deck', 'On deck'), stage('build', 'Build', {task_limit: 4})],
    done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
  const shot = async (page: import('playwright').Page, name: string) => {
    const dir = process.env.TICO_UI_SHOTS;
    if (!dir) return;
    fs.mkdirSync(dir, {recursive: true});
    await page.locator('#pe-dialog').screenshot({path: path.join(dir, name + '.png')});
  };
  try {
    for (const width of [1200, 390]) {
      pipeline.stages = [stage('deck', 'On deck'), stage('build', 'Build', {task_limit: 4})];
      saves.length = 0;
      const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
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
        if (p === '/api/employees') return json(bots.map(b => ({id: b.id, slug: b.id, display_name: b.name, state: 'active'})));
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        // As pipeline_settings.save answers: the pick as stored, each listed bot's family, and the next-task sentence.
        if (p === '/api/v2/pipelines/engineering' && r.method() === 'POST') {
          const body = JSON.parse(r.postData() || '{}') as {stages: Rec[]};
          saves.push(body);
          pipeline.stages = body.stages.map(s => {
            const pick = s['pick'] as {bots: string[]} | undefined;
            return pick ? {...s, pick: {...pick, families: Object.fromEntries(pick.bots.map(b => [b, FAMILY[b] ?? '']))},
              pick_next: 'The next task goes to Builder.'} : s;
          });
          pipeline.settings_version += 1;
          return json({pipeline});
        }
        if (p === '/api/v2/pipelines/engineering') return json({pipeline});
        if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
        if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks?pipeline=engineering&layout=columns');
      const dialog = page.locator('#pe-dialog');
      const build = dialog.locator('.pe-stage[data-pe-si="1"]');
      await page.locator('#task-pipeline-edit').click();
      await dialog.locator('[data-pe-pf="goal"]').waitFor();
      await build.locator('> summary').click();

      // Three groups in the order a task moves.
      assert.deepEqual(await build.locator('.pe-h').allInnerTexts(), ['WHEN A TASK ARRIVES', 'WHO WORKS ON IT', 'TO LEAVE'], `${width}: the groups`);

      // One of several, with two Builders, taking turns, Prefer Anyone, owning the task.
      await build.locator('[data-pe-sf="owner"]').selectOption('pick');
      const bot = (id: string) => build.locator(`[data-pe-pick-bot="bot:${id}"]`);
      await bot('builder').check();
      await bot('builder-2').check();
      await build.locator('[data-pe-pick="owns_task"]').check();
      await build.locator('[data-pe-pick="limit_each"]').fill('3');
      await build.locator('[data-pe-seg="pick_prefer"][data-v="new"]').click();
      assert.equal(await build.locator('[data-pe-seg="pick_prefer"][data-v="new"]').getAttribute('aria-checked'), 'true');
      assert.equal(await build.locator('.pe-pick-next').innerText(), 'Save to see who gets the next task.');
      await dialog.locator('[data-pe-save]').click();
      await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
      assert.equal(await dialog.locator('[data-pe-error]:not([hidden])').count(), 0);
      assert.deepEqual(saves[0]!.stages[1]!['owner'], 'pick');
      assert.deepEqual(saves[0]!.stages[1]!['pick'], {bots: ['bot:builder', 'bot:builder-2'], how: 'turns', limit_each: 3, prefer: 'new', owns_task: true});
      assert.equal('pick' in saves[0]!.stages[0]!, false, 'a stage with one owner sends no pick');

      // Saved: each bot's family, and the server's sentence.
      assert.equal(await build.locator('.pe-pick-next').innerText(), 'The next task goes to Builder.');
      assert.match(await bot('builder').locator('..').innerText(), /Builder\s+Claude/);
      await shot(page, `one-of-several-${width}`);

      // Tasks leave by hand: a two-way switch, and the by-hand option appears with it.
      await build.locator('[data-pe-seg="by_hand"][data-v="1"]').click();
      await build.locator('[data-pe-sf="requests_move_on"]').waitFor();
      await build.locator('[data-pe-seg="by_hand"][data-v="0"]').click();

      // Closed: "One of 2" where the owner's name was.
      await build.locator('> summary').click();
      if (width > 720) assert.equal(await build.locator('.pe-stage-owner').innerText(), 'One of 2');
      assert.equal(await build.locator('.pe-stage-owner').getAttribute('title'), 'Builder, Builder 2');
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log('PASS: One of several is set, saved and read back in the regrouped stage window on desktop and phone.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
