import type {Route} from 'playwright';
// Offline browser check of a stage's typed answer in the pipeline editor (docs/dev/pipelines-v3.md kind 6, C30): To leave is
// a list of requirement rows; + Requirement opens one plain pick list (Esc closes it and + Requirement has the focus
// back; what is not ready yet is listed, off, with Soon); a pick with something to choose opens its editor under its
// row, the focus in its first field; one editor is open at a time and Remove is in it; an answer's name follows its
// title; a number keeps its Min and Max, a choice its choices (Yes / No in one click). A save sends them as the record
// stores them (kind answer, settings name/type/min/max/question, choices), and the editor opened again on the saved
// record shows the same fields. The release switch defaults on; an explicit opt-out survives save/reload, and can be
// enabled again on desktop and phone.
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
    const pick = page.locator('#pe-req-pick:popover-open');
    /** + Requirement, then a pick. */
    const add = async (what: string) => {
      await dialog.locator('[data-pe-req-add]').click();
      await pick.waitFor();
      await pick.locator(`[data-pe-req-kind="${what}"]`).click();
      await page.locator('#pe-req-pick:popover-open').waitFor({state: 'detached'}).catch(() => {});
    };
    const row = (ri: number) => dialog.locator(`[data-pe-leave-row="req:${ri}"]`);
    /** A requirement's editor, open under its row (one is open at a time). */
    const open = async (ri: number) => {
      const b = row(ri).locator('.sr-open');
      if (await b.getAttribute('aria-expanded') !== 'true') await b.click();
      await row(ri).locator('.pe-row-ed').waitFor();
    };
    const rows = () => dialog.locator('[data-pe-leave-row] .sr-text').allInnerTexts();
    await page.locator('[data-pv-settings="spec"]').click();
    // The stage's header opens that stage's editor alone (style guide, Pipeline stage): its fields are the body, nothing to
    // collapse; its requirements are open.
    await dialog.locator('.pe-stage-one[data-pe-si="0"]').waitFor();
    assert.equal(await dialog.locator('details').count(), 0, 'nothing to collapse');
    assert.deepEqual(await rows(), ['Owner says done']);
    assert.equal(await dialog.locator('.pe-row-ed').count(), 0, 'rows, their editors shut');

    // + Requirement's pick list: one list in plain words; Esc closes it and + Requirement has the focus back.
    await dialog.locator('[data-pe-req-add]').click();
    await pick.waitFor();
    assert.equal(await pick.locator('.tl-menu-h').count(), 0, 'no group headings');
    assert.deepEqual((await pick.locator('[data-pe-req-kind]').allTextContents()).slice(0, 3), ['OK from anyone', 'OK from a person…', 'A page, checked by Decision model…'],
      'by hand is an OK from anyone; a bot\'s OK only where there is a bot');
    assert.equal(await pick.locator('[data-pe-req-kind="answer"]').count(), 1);
    assert.equal(await pick.locator('[data-pe-req-kind="owner_done"]').count(), 0, 'one Owner says done is enough: not offered again');
    // not ready yet: listed, off, each with a small Soon
    assert.deepEqual(await pick.locator('.pe-mi-soon').evaluateAll(els => els.map(e => [e.firstChild?.textContent, e.querySelector('.pill.soon')?.textContent, (e as HTMLButtonElement).disabled])),
      [['A question Decision model answers…', 'Soon', true], ['In your own words…', 'Soon', true]]);
    // the list never covers its button and stays in the window
    const placed = await page.evaluate(() => {
      const b = document.querySelector('#pe-dialog [data-pe-req-add]')!.getBoundingClientRect(), l = document.querySelector('#pe-req-pick')!.getBoundingClientRect();
      return {covers: l.left < b.right && l.right > b.left && l.top < b.bottom && l.bottom > b.top, inside: l.top >= 0 && l.bottom <= innerHeight && l.left >= 0 && l.right <= innerWidth};
    });
    assert.deepEqual(placed, {covers: false, inside: true});
    assert.equal(await pick.locator('.tl-mi').first().evaluate(el => el === document.activeElement), true, 'the first item has the focus');
    await page.keyboard.press('ArrowDown');
    assert.equal(await pick.locator('.tl-mi').nth(1).evaluate(el => el === document.activeElement), true, 'arrow keys move');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#pe-req-pick:popover-open'));
    assert.equal(await dialog.isVisible(), true, 'Esc closed the list, not the editor');
    await page.waitForFunction(() => document.activeElement?.hasAttribute('data-pe-req-add') ?? false);
    // a second click on + Requirement closes the list it opened
    await dialog.locator('[data-pe-req-add]').click();
    await pick.waitFor();
    await dialog.locator('[data-pe-req-add]').click();
    await page.waitForTimeout(150);
    assert.equal(await page.locator('#pe-req-pick:popover-open').count(), 0, 'a second click closes it');
    // Save is the dialog's footer: it stays in view while the body scrolls
    const foot = await page.evaluate(() => {
      const save = document.querySelector('#pe-dialog [data-pe-save]')!, d = document.querySelector('#pe-dialog')!.getBoundingClientRect(), r = save.getBoundingClientRect();
      return {inFoot: !!save.closest('.tmodal-foot'), inBody: !!save.closest('#pe-body'), shown: r.bottom <= d.bottom && r.top >= d.top};
    });
    assert.deepEqual(foot, {inFoot: true, inBody: false, shown: true});
    // the title follows the stage's name as it is typed (its only other place in this editor)
    await dialog.locator('[data-pe-sf="name"]').fill('Specs');
    assert.equal(await page.locator('#pe-title').innerText(), 'Product › Specs');
    await dialog.locator('[data-pe-sf="name"]').fill('Spec');

    // A number answer: the pick opens its editor under its row, the focus in its first field; the name follows the title.
    await add('answer');
    await page.waitForFunction(() => document.activeElement?.matches('[data-pe-ri="1"][data-pe-rf="line"]') ?? false);
    assert.deepEqual(await rows(), ['Owner says done', 'An answer']);
    await at(1, 'line').fill('Confidence');
    assert.deepEqual(await rows(), ['Owner says done', 'Answer: Confidence'], 'the row says it as it is typed');
    assert.equal(await at(1, 'answer_name').inputValue(), 'confidence', 'the name follows the title');
    // the answer's key reads "Saved as", not a second Name
    assert.equal(await row(1).locator('.pe-f:has([data-pe-rf="answer_name"]) > span').innerText(), 'Saved as');
    assert.equal(await at(1, 'person_id').locator('option:checked').innerText(), 'Task owner');
    await at(1, 'question').fill('How sure are you that it works?');
    await at(1, 'answer_type').selectOption('number');
    await at(1, 'min').fill('0');
    await at(1, 'max').fill('100');

    // A choice: Yes / No in one click. One editor is open at a time: the new one's.
    await add('answer');
    assert.equal(await dialog.locator('.pe-row-ed').count(), 1, 'one editor open');
    await at(2, 'line').fill('Risky');
    await at(2, 'answer_type').selectOption('choice');
    await dialog.locator('[data-pe-si="0"][data-pe-ri="2"][data-pe-yes-no]').click();
    assert.deepEqual(await at(2, 'choice').evaluateAll(els => els.map(e => (e as HTMLInputElement).value)), ['Yes', 'No']);
    await open(1);
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

    // Opened again on the saved record: the same rows and fields.
    await dialog.locator('[data-pe-close]').click();
    await dialog.waitFor({state: 'detached'});
    await page.locator('[data-pv-settings="spec"]').click();
    await dialog.locator('.pe-stage-one[data-pe-si="0"]').waitFor();
    assert.deepEqual(await rows(), ['Owner says done', 'Answer: Confidence', 'Answer: Risky']);
    await open(1);
    assert.equal(await at(1, 'line').inputValue(), 'Confidence');
    assert.equal(await at(1, 'answer_name').inputValue(), 'confidence');
    assert.equal(await at(1, 'question').inputValue(), 'How sure are you that it works?');
    assert.equal(await at(1, 'answer_type').inputValue(), 'number');
    assert.deepEqual([await at(1, 'min').inputValue(), await at(1, 'max').inputValue()], ['0', '100']);
    await open(2);
    assert.equal(await at(2, 'answer_type').inputValue(), 'choice');
    assert.deepEqual(await at(2, 'choice').evaluateAll(els => els.map(e => (e as HTMLInputElement).value)), ['Yes', 'No']);

    // A kind with nothing to choose (Checks pass) is added as its row, shut, one line; Remove is in its editor, and the
    // focus goes back to + Requirement.
    await add('checks_pass');
    assert.deepEqual(await rows(), ['Owner says done', 'Answer: Confidence', 'Answer: Risky', 'Checks pass']);
    assert.equal(await dialog.locator('.pe-row-ed').count(), 0, 'added shut');
    const tall = await row(3).evaluate(el => el.getBoundingClientRect().height);
    assert.ok(tall < 50, `Checks pass is one line tall: ${tall}`);
    await open(3);
    await dialog.locator('[data-pe-row-x="req:3"]').click();
    assert.equal(await dialog.locator('[data-pe-leave-row]').count(), 3);
    await page.waitForFunction(() => document.activeElement?.hasAttribute('data-pe-req-add') ?? false);

    // A release requirement counts RCs by default; switching it off must survive save and reopening on either width.
    const shot = async (ri: number, name: string) => {
      const dir = process.env.TICO_UI_SHOTS;
      if (!dir) return;
      fs.mkdirSync(dir, {recursive: true});
      await row(ri).screenshot({path: path.join(dir, name + '.png')});
    };
    for (const [i, width] of [1200, 390].entries()) {
      await page.setViewportSize({width, height: 900});
      const ri = 3 + i;
      await add('in_release');
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
      await page.locator('#task-menu').click();
      await page.locator('#task-mi-edit').click();
      // the whole pipeline's editor: the stage opens, then the requirement's editor
      await dialog.locator('.pe-stage > summary').click();
      await open(ri);
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
