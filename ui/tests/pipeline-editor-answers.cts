import type {Route} from 'playwright';
// Offline browser check of a stage's typed answer in the pipeline editor (docs/pipelines-v3.md kind 6, C30): Answer is
// under Responses in Needs; its name follows its title; a number keeps its Min and Max, a choice its choices (Yes / No
// in one click). A save sends them as the record stores them (kind answer, settings name/type/min/max/question,
// choices), and the editor opened again on the saved record shows the same fields. The release switch defaults on;
// an explicit opt-out survives save/reload, and can be enabled again on desktop and phone.
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
  stages: [stage('spec', 'Spec', [{id: 'q0', kind: 'owner_done', source: null, condition: null, settings: {}, can_push_past: true,
    who_oks: 'none', person_id: null, line: '', choices: null}])],
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
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      // As pipeline_settings.save answers: the record as stored, each new requirement with its id.
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
    await page.locator('[data-pv-settings="spec"]').click();
    await dialog.locator('[data-pe-pf="goal"]').waitFor();

    // A number answer: Answer sits under Responses; the name follows the title.
    await dialog.locator('[data-pe-req-add]').click();
    assert.deepEqual(await at(1, 'kind').locator('optgroup').evaluateAll(gs => gs.map(g => g.getAttribute('label'))), ['Responses', 'Links']);
    assert.equal(await at(1, 'kind').locator('optgroup[label="Responses"] option[value="answer"]').count(), 1);
    await at(1, 'kind').selectOption('answer');
    await at(1, 'line').fill('Confidence');
    assert.equal(await at(1, 'answer_name').inputValue(), 'confidence', 'the name follows the title');
    assert.equal(await at(1, 'person_id').locator('option:checked').innerText(), 'Task owner');
    await at(1, 'question').fill('How sure are you that it works?');
    await at(1, 'answer_type').selectOption('number');
    await at(1, 'min').fill('0');
    await at(1, 'max').fill('100');

    // A choice: Yes / No in one click.
    await dialog.locator('[data-pe-req-add]').click();
    await at(2, 'kind').selectOption('answer');
    await at(2, 'line').fill('Risky');
    await at(2, 'answer_type').selectOption('choice');
    await dialog.locator('[data-pe-si="0"][data-pe-ri="2"][data-pe-yes-no]').click();
    assert.deepEqual(await at(2, 'choice').evaluateAll(els => els.map(e => (e as HTMLInputElement).value)), ['Yes', 'No']);
    assert.equal(await at(1, 'max').inputValue(), '100', 'a redraw keeps what was typed');

    await dialog.locator('[data-pe-save]').click();
    await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
    assert.equal(await dialog.locator('[data-pe-error]:not([hidden])').count(), 0);
    assert.equal(saves.length, 1);
    const sent = saves[0]!.stages[0]!.requirements;
    assert.deepEqual(sent[1], {kind: 'answer', source: null, condition: null, can_push_past: true, who_oks: 'none', person_id: null,
      line: 'Confidence', choices: null, settings: {type: 'number', name: 'confidence', question: 'How sure are you that it works?', min: 0, max: 100}});
    assert.deepEqual(sent[2], {kind: 'answer', source: null, condition: null, can_push_past: true, who_oks: 'none', person_id: null,
      line: 'Risky', choices: ['Yes', 'No'], settings: {type: 'choice', name: 'risky'}});

    // Opened again on the saved record: the same fields.
    await dialog.locator('[data-pe-close]').click();
    await dialog.waitFor({state: 'detached'});
    await page.locator('[data-pv-settings="spec"]').click();
    await dialog.locator('[data-pe-pf="goal"]').waitFor();
    assert.equal(await at(1, 'kind').inputValue(), 'answer');
    assert.equal(await at(1, 'line').inputValue(), 'Confidence');
    assert.equal(await at(1, 'answer_name').inputValue(), 'confidence');
    assert.equal(await at(1, 'question').inputValue(), 'How sure are you that it works?');
    assert.equal(await at(1, 'answer_type').inputValue(), 'number');
    assert.deepEqual([await at(1, 'min').inputValue(), await at(1, 'max').inputValue()], ['0', '100']);
    assert.equal(await at(2, 'answer_type').inputValue(), 'choice');
    assert.deepEqual(await at(2, 'choice').evaluateAll(els => els.map(e => (e as HTMLInputElement).value)), ['Yes', 'No']);
    assert.deepEqual(await dialog.locator('.pe-req-name').allInnerTexts(), ['Owner says done', 'Confidence', 'Risky']);

    // A release requirement counts RCs by default; switching it off must survive save and reopening on either width.
    const shot = async (ri: number, name: string) => {
      const dir = process.env.TICO_UI_SHOTS;
      if (!dir) return;
      fs.mkdirSync(dir, {recursive: true});
      await dialog.locator(`.pe-req[data-pe-ri="${ri}"]`).screenshot({path: path.join(dir, name + '.png')});
    };
    for (const [i, width] of [1200, 390].entries()) {
      await page.setViewportSize({width, height: 900});
      const ri = 3 + i;
      await dialog.locator('[data-pe-req-add]').click();
      await at(ri, 'kind').selectOption('in_release');
      const prereleases = dialog.locator(`[data-pe-si="0"][data-pe-ri="${ri}"][data-pe-rs="prereleases"]`);
      assert.equal(await prereleases.isChecked(), true, `${width}: an omitted setting counts prereleases`);
      await shot(ri, `prereleases-${width}-default`);
      await prereleases.uncheck();
      const count = saves.length;
      await dialog.locator('[data-pe-save]').click();
      await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
      assert.equal(saves.length, count + 1);
      assert.deepEqual(saves[count]!.stages[0]!.requirements[ri]!['settings'], {prereleases: false}, `${width}: save the opt-out`);
      await dialog.locator('[data-pe-close]').click();
      await dialog.waitFor({state: 'detached'});
      await page.reload();
      await page.locator('#task-pipeline-edit').click();
      await dialog.locator('.pe-stage > summary').click();
      await dialog.locator(`[data-pe-ri="${ri}"].pe-req > summary`).click();
      assert.equal(await prereleases.isChecked(), false, `${width}: the saved opt-out reloads as off`);
      await shot(ri, `prereleases-${width}-opt-out`);
      await prereleases.check();
      await dialog.locator('[data-pe-save]').click();
      await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
      assert.deepEqual(saves[count + 1]!.stages[0]!.requirements[ri]!['settings'], {prereleases: true}, `${width}: counting can be enabled again`);
    }

    assert.deepEqual(errors, []);
    console.log('PASS: typed answers and release opt-outs survive editor saves and reloads on desktop and phone.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
