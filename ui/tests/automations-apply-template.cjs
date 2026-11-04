// Apply template (ui/app/automations-new.js) against the fake hub in support/automations-fake.cjs: offered in the ⋯ menu
// of an October inbox only (one llm step, no choose), it shows the filled template's destinations, then PATCHes the
// template's rules, questions and destinations with the old step as the LLM (its prompt as the instructions, its id,
// out and model kept, Sort + draft as the owner) and the automation renders as the board. A group's inbox gets Sort
// with the old prompt; a refused fill shows in the dialog and saves nothing; the dialog fits a phone.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, filledTemplate, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

const PROMPT = 'Read my mail. Draft replies to customers; leave anything about money for me.';
const old = (id, owner, inbox) => ({id, name: 'Inbox ' + id, owner_id: owner, enabled: true, builtin: false, version: 3,
  trigger: {type: 'message', inbox},
  steps: [{id: 'read', type: 'llm', as: owner.startsWith('person:') ? 'assistant:' + owner.slice(7) : undefined, prompt: PROMPT,
    out: 'triage', model: 'claude-sonnet'}]});
const tweak = fx => fx.automations.push(old('au_old', 'person:dana', 'dana@acme.example'),
  old('au_team', 'group:engineering', 'slack:#eng'), old('au_omar', 'person:omar', 'omar@acme.example'));
const open = (browser, id, viewport = DESKTOP) => openAutomations(browser, {viewport, hash: '#/automations/' + id, tweak});
async function openApply(page) {
  await page.locator('[data-more]').click();
  await page.locator('.au-menu [data-mi="apply"]').click();
  await page.waitForSelector('dialog.au-nd-apply[open] [data-tid="founder"]');
}

(async () => {
  const browser = await chromium.launch();
  try {
    // A person's October inbox: Founder, then Apply.
    {
      const {page, errors, writes} = await open(browser, 'au_old');
      await page.waitForSelector('.au-card[data-open="read"]');
      assert.equal(await page.locator('.au-board').count(), 0, 'one llm step is a step list');
      await openApply(page);
      assert.equal(await page.locator('.au-nd [data-go]').isDisabled(), true, 'nothing to apply before a pick');
      await page.locator('.au-nd [data-tid="founder"]').click();
      await page.waitForSelector('.au-nd .au-nd-dests li');
      assert.deepEqual(await page.locator('.au-nd .au-nd-dests b').allInnerTexts(), ['Me', 'Legal', 'Engineering', 'Archive']);
      await shot(page, 'apply-template-desktop');
      await page.locator('.au-nd [data-go]').click();
      await page.waitForSelector('.au-board');
      assert.equal(await page.locator('dialog.au-nd').count(), 0);
      const w = writes.filter(x => x.method === 'PATCH');
      assert.equal(w.length, 1);
      assert.equal(w[0].path, '/api/v2/automations/au_old');
      const {body} = w[0], filled = filledTemplate('founder', 'person:dana', 'dana@acme.example');
      for (const k of ['name', 'trigger', 'enabled', 'owner']) assert.ok(!(k in body), `${k} stays as it was`);
      assert.deepEqual(body.destinations, filled.destinations);
      assert.deepEqual(body.steps.map(s => s.id), ['rules', 'ask', 'route', 'read']);
      assert.deepEqual(body.steps[1].questions, filled.steps[1].questions);
      const llm = body.steps[3];
      assert.equal(llm.prompt, PROMPT, 'the old prompt is the instructions');
      assert.equal(llm.out, 'triage');
      assert.equal(llm.model, 'claude-sonnet');
      assert.equal(llm.as, 'assistant:dana');
      assert.deepEqual(llm.choose, {to: ['me', 'legal', 'eng', 'archive'], default: 'me', mode: 'draft'});
      // The board: the template's rules, question and destinations, the LLM on Sort + draft with the old prompt.
      assert.equal(await page.locator('.au-br[data-board^="rule:"]').count(), 1);
      assert.equal(await page.locator('.au-br[data-board^="q:"]').count(), 1);
      assert.equal(await page.locator('.au-dest[data-board^="dest:"]:not(.au-dest-add)').count(), 4);
      assert.equal(await page.locator('[data-bmode="draft"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.au-bpr').innerText(), PROMPT);
      assert.equal(await page.locator('.au-ver').innerText(), 'v4', 'a new version');
      // A board isn't an October inbox: no Apply template.
      await page.locator('[data-more]').click();
      assert.equal(await page.locator('.au-menu [data-mi="apply"]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A group's inbox: Sort, the old prompt as its instructions; no llm step left.
    {
      const {page, errors, writes} = await open(browser, 'au_team');
      await page.waitForSelector('.au-card[data-open="read"]');
      await openApply(page);
      await page.locator('.au-nd [data-tid="support"]').click();
      await page.waitForSelector('.au-nd .au-nd-dests li');
      await page.locator('.au-nd [data-go]').click();
      await page.waitForSelector('.au-board');
      const {body} = writes.find(x => x.method === 'PATCH');
      assert.equal(body.steps.filter(s => s.type === 'llm').length, 0);
      const sort = body.steps.find(s => s.type === 'decide' && s.questions.route);
      assert.equal(sort.questions.route.instructions, PROMPT);
      assert.deepEqual(Object.keys(sort.questions.route.criteria), ['me', 'legal', 'eng', 'archive']);
      assert.equal(await page.locator('[data-bmode="sort"]').count(), 0, 'Sort only: no mode to switch');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Omar's inbox, opened by Dana: the hub refuses the fill, the dialog says so and nothing is saved.
    {
      const {page, errors, writes} = await open(browser, 'au_omar');
      await page.waitForSelector('.au-card[data-open="read"]');
      await openApply(page);
      await page.locator('.au-nd [data-tid="founder"]').click();
      await page.waitForSelector('.au-nd [data-error]:not([hidden])');
      assert.match(await page.locator('.au-nd [data-error]').innerText(), /may not make an automation for person:omar/);
      assert.equal(await page.locator('.au-nd [data-go]').isDisabled(), true);
      assert.equal(writes.length, 0, 'a refused fill saves nothing');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Not offered on an automation that isn't one llm step.
    {
      const {page} = await open(browser, 'au_inbox');
      await page.waitForSelector('.au-card[data-open="trigger"]');
      await page.locator('[data-more]').click();
      await page.waitForSelector('.au-menu [data-mi="versions"]');
      assert.equal(await page.locator('.au-menu [data-mi="apply"]').count(), 0);
      await page.close();
    }

    // A phone: the dialog fits, nothing scrolls sideways.
    {
      const {page, errors} = await open(browser, 'au_old', PHONE);
      await page.waitForSelector('.au-card[data-open="read"]');
      await openApply(page);
      await page.locator('.au-nd [data-tid="founder"]').click();
      await page.waitForSelector('.au-nd .au-nd-dests li');
      const box = await page.locator('dialog.au-nd').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width + 1, 'the dialog fits the phone');
      assert.ok(await page.locator('.au-nd [data-go]').isVisible());
      assert.equal(await sideways(page), false);
      await shot(page, 'apply-template-phone');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations-apply-template: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
