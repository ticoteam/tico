// Automations step editors (ui/app/automations-editors.js) against the fake hub in support/automations-fake.cjs: Filter
// rules, Decide (set, level, questions, the last run's bars) and LLM (runs as, prompt) each save the flow with one PATCH
// that makes a new version; Versions restores one; on a computer the spend chip, "+" between steps (no Paths except at the
// end of a list), a step's own switch and Delete step. A phone edits in a sheet from the bottom; a computer in the right panel.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

async function flow(browser, viewport) {
  const o = await openAutomations(browser, {viewport, hash: '#/automations/au_inbox'});
  await o.page.waitForSelector('.au-card[data-open="f1"]');
  return o;
}
const ver = page => page.locator('.au-ver').innerText();
async function saved(page, writes, n) {
  await page.locator('.au-save').click();
  await page.waitForFunction(v => document.querySelector('.au-ver')?.textContent === v && document.querySelector('.au-ed').hidden, 'v' + n);
  const w = writes.at(-1);
  assert.equal(w.method, 'PATCH');
  assert.equal(w.path, '/api/v2/automations/au_inbox');
  return w.body;
}

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: Filter, Decide, LLM, then Versions, each in a sheet.
    {
      const {page, errors, writes} = await flow(browser, PHONE);
      const end = await page.locator('.au-add-end').first().boundingBox(), plus = await page.locator('[data-add="root|1"]').boundingBox();
      assert.ok(end.height >= 40 && plus.width >= 28, 'thumb-sized "+" and Add step');
      await page.locator('.au-card[data-open="f1"]').click();
      await page.waitForSelector('#au-root.ed-open .au-ed:not([hidden])');
      const sheet = await page.locator('.au-side').boundingBox();
      assert.ok(Math.abs(sheet.y + sheet.height - PHONE.height) < 2 && sheet.y > 80, 'the editor is a sheet at the bottom');
      assert.equal(await page.locator('.au-scrim').isVisible(), true);
      assert.equal(await page.evaluate(() => document.activeElement?.closest('.au-ed') == null), true, 'no keyboard pops over the sheet');
      await page.locator('[data-op="add"][data-at="step.rules.0.when"]').click();
      await page.fill('[data-b="step.rules.0.when.1.field"]', 'message.subject');
      await page.selectOption('[data-b="step.rules.0.when.1.op"]', 'contains');
      await page.fill('[data-b="step.rules.0.when.1.value"]', 'invoice');
      await page.selectOption('[data-b="step.rules.0.match"]', 'all');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-edit-filter');
      let body = await saved(page, writes, 8);
      assert.deepEqual(body.steps[0].rules[0].when, [{field: 'message.from', op: 'contains', value: 'newsletter'}, {field: 'message.subject', op: 'contains', value: 'invoice'}]);
      assert.equal(body.steps[0].rules[0].match, 'all');
      assert.equal(body.steps[1].set, 'mail-triage@1');
      assert.equal('questions' in body.steps[1], false, 'a saved set goes back as the set alone');
      assert.match(await page.locator('.au-card[data-open="f1"]').innerText(), /subject has “invoice”/);

      // Decide: the set's questions, last run's bars; a new question makes them the step's own.
      await page.locator('.au-card[data-open="dc1"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.au-ed .au-eb').length === 4);
      assert.equal(await page.locator('[data-b="set"]').inputValue(), 'mail-triage@1');
      assert.equal(await page.locator('[data-b="qs.0.opts"]').inputValue(), 'needs-owner, reply, route, archive, watch');
      assert.match(await page.locator('.au-ed').innerText(), /reply 82%[\s\S]*money 12%/);
      await shot(page, 'phone-edit-decide');
      await page.selectOption('[data-b="step.level"]', 'high');
      await page.locator('[data-op="add"][data-at="qs"]').click();
      await page.fill('[data-b="qs.4.id"]', 'urgent');
      assert.equal(await page.locator('[data-b="set"]').inputValue(), '');
      // The hub's judge needs what to ask: an empty one is stopped here, not sent.
      const before = writes.length;
      await page.locator('.au-ed [data-op="save"]').click();
      await page.waitForSelector('.toast:has-text("needs what to ask")');
      assert.equal(writes.length, before);
      await page.fill('[data-b="qs.4.ask"]', 'Is it urgent?');
      body = await saved(page, writes, 9);
      const d = body.steps[1];
      assert.equal(d.level, 'high');
      assert.equal('set' in d, false);
      assert.deepEqual(Object.keys(d.questions), ['bucket', 'is_ask', 'money', 'legal', 'urgent']);
      assert.deepEqual(Object.keys(d.questions.bucket.criteria), ['needs-owner', 'reply', 'route', 'archive', 'watch']);
      assert.deepEqual(d.questions.urgent, {type: 'noul', instructions: 'Is it urgent?'});
      assert.equal(d.questions.money.instructions, 'Is it about money?');
      assert.match(await page.locator('.au-card[data-open="dc1"]').innerText(), /Custom questions[\s\S]*urgent/);

      // LLM inside a path: who it runs as, and the prompt.
      await page.locator('.au-card[data-open="l1s"]').click();
      await page.waitForSelector('[data-b="step.prompt"]');
      assert.equal(await page.locator('[data-b="step.as"]').inputValue(), 'assistant:dana');
      assert.match(await page.locator('[data-b="step.as"] option:checked').innerText(), /Dana’s Assistant/);
      await page.fill('[data-b="step.prompt"]', 'Draft a short reply. Never promise dates.');
      await shot(page, 'phone-edit-llm');
      body = await saved(page, writes, 10);
      assert.equal(body.steps[2].paths[0].steps[0].prompt, 'Draft a short reply. Never promise dates.');
      assert.match(await page.locator('.au-card[data-open="l1s"]').innerText(), /Never promise dates/);

      // Versions: the current one says so; an older one restores as a new version.
      await page.locator('.au-ver').click();
      await page.waitForSelector('.au-vr');
      assert.match(await page.locator('.au-vr').first().innerText(), /v10[\s\S]*Current/);
      await shot(page, 'phone-versions');
      await page.locator('.au-vr:has-text("v7") [data-op="restore"]').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v11');
      assert.deepEqual(writes.at(-1), {method: 'POST', path: '/api/v2/automations/au_inbox/versions/7/restore', body: {}});
      assert.match(await page.locator('.au-card[data-open="dc1"]').innerText(), /mail-triage v1/);
      await page.locator('.au-scrim').click({position: {x: 10, y: 10}});
      await page.waitForFunction(() => !document.querySelector('#au-root').classList.contains('ed-open'));
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A computer: the editor is the right panel; spend, "+", a step's switch, Delete step.
    {
      const {page, errors, writes} = await flow(browser, DESKTOP);
      await page.locator('.au-card[data-open="l1s"]').click();
      await page.waitForSelector('.au-ed:not([hidden]) [data-b="step.prompt"]');
      assert.ok((await page.locator('.au-side').boundingBox()).x > 1000, 'the editor sits on the right');
      assert.equal(await page.locator('.au-scrim').isVisible(), false);
      assert.equal(await page.locator('.au-botp').isVisible(), false, 'the editor takes the bot’s place');
      await shot(page, 'desktop-edit-llm');
      await page.locator('.au-ed [data-op="close"]').click();
      assert.equal(await page.locator('.au-botp').isVisible(), true);

      // Opened and saved untouched, the spend editor keeps the limit (it once drew blank and saved none).
      await page.locator('[data-spend]').click();
      assert.equal(await page.locator('[data-b="value"]').inputValue(), '5');
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-ed').hidden);
      assert.deepEqual(writes.at(-1).body, {spend_cap: {per_30m: 5}});
      await page.locator('[data-spend]').click();
      await page.fill('[data-b="value"]', '3.5');
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelector('[data-spend]')?.textContent.includes('$3.50 / 30 min'));
      assert.deepEqual(writes.at(-1).body, {spend_cap: {per_30m: 3.5}});
      assert.equal(await ver(page), 'v7', 'a spend limit is not a new version');
      await page.locator('[data-spend]').click();
      await page.locator('[data-op="nocap"]').click();
      await page.waitForFunction(() => document.querySelector('[data-spend]')?.textContent.includes('Bot’s limit'));
      assert.deepEqual(writes.at(-1).body, {spend_cap: null});

      // "+": none after Paths; Paths only at the end of a list without one.
      assert.equal(await page.locator('[data-add="root|2"]').count(), 1);
      assert.equal(await page.locator('[data-add="root|3"]').count(), 0);
      await page.locator('[data-add="root|0"]').click();
      assert.equal(await page.locator('.au-menu [data-ty="paths"]').count(), 0);
      assert.equal(await page.locator('.au-menu [data-ty="filter"]').count(), 1);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.au-menu').isVisible(), false);
      // Between steps a small "+"; the end of each list a labelled Add step.
      assert.equal(await page.locator('[data-add="root|0"]').getAttribute('aria-label'), 'Add step');
      assert.equal(await page.locator('.au-add-end[data-add="p1:0|2"]').textContent(), '+Add step');
      assert.equal(await page.locator('.au-add-end[data-add^="root|"]').count(), 0, 'Paths end the root list');
      await page.locator('[data-add="p1:0|2"]').click();
      assert.equal(await page.locator('.au-menu [data-ty="paths"]').count(), 1);
      await shot(page, 'desktop-add-menu');
      await page.locator('.au-menu [data-ty="action"]').click();
      await page.selectOption('[data-b="step.do"]', 'label');
      await page.fill('[data-b="step.args.label"]', 'Later');
      assert.equal(await page.locator('[data-op="del"]').count(), 0, 'a new step has nothing to delete');
      let body = await saved(page, writes, 8);
      const added = body.steps[2].paths[0].steps;
      assert.equal(added.length, 3);
      assert.deepEqual({type: added[2].type, do: added[2].do, args: added[2].args, enabled: added[2].enabled}, {type: 'action', do: 'label', args: {label: 'Later'}, enabled: true});

      await page.locator('[data-step="dc1"]').click();
      await page.waitForFunction(() => document.querySelector('.au-card[data-open="dc1"]').classList.contains('off'));
      assert.equal(writes.at(-1).body.steps[1].enabled, false);
      assert.equal(await ver(page), 'v9');

      await page.locator('.au-card[data-open="a3"]').click();
      await page.locator('[data-op="del"]').click();
      await page.waitForFunction(() => !document.querySelector('.au-card[data-open="a3"]'));
      assert.deepEqual(writes.at(-1).body.steps[2].otherwise.steps, []);
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations edit ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
