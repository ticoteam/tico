// The flow board (ui/app/automations-flow.js auBoardHTML) against the fake hub: an inbox with destinations shows its
// rules, questions, LLM and destinations, a flow without them keeps the step list; a rule's pop-up adds an action and
// saves both (with the destinations unchanged), a rule moved on saves next "rule"; a proposal's diff shows destination,
// `to` and move-on changes; a webhook board saves a mode switch;
// a phone stacks the board with no sideways scroll.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

const FX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/automations-board.json'), 'utf8'));
const tweak = fx => { fx.automations.push(JSON.parse(JSON.stringify(FX.inbox)), JSON.parse(JSON.stringify(FX.webhook))); };
const open = (browser, viewport, id = 'au_board') => openAutomations(browser, {viewport, hash: '#/automations/' + id, tweak});
const ver = page => page.locator('.au-ver').innerText();
async function save(page, writes, id, n) {
  await page.locator('.au-save').click();
  await page.waitForFunction(v => document.querySelector('.au-ver')?.textContent === v && document.querySelector('.au-ed').hidden, 'v' + n);
  const w = writes.at(-1);
  assert.equal(w.method, 'PATCH');
  assert.equal(w.path, '/api/v2/automations/' + id);
  return w.body;
}

(async () => {
  const browser = await chromium.launch();
  try {
    // A computer: the board, then a rule's pop-up.
    {
      const {page, errors, writes} = await open(browser, DESKTOP);
      await page.waitForSelector('.au-board');
      assert.equal(await page.locator('.au-card').count(), 0, 'no step cards on a board');
      assert.equal(await page.locator('.au-br[data-board^="rule:"]').count(), 3);
      assert.equal(await page.locator('.au-br[data-board^="q:"]').count(), 2);
      assert.equal(await page.locator('.au-dest[data-board^="dest:"]:not(.au-dest-add)').count(), 4);
      assert.equal(await page.locator('.au-dest-add').count(), 1);
      // Rule 1: Star, then Legal; rule 2 moves on (dashed); the LLM is on Sort and branches into all four.
      assert.equal(await page.locator('[data-board="rule:0"] .au-bchip').count(), 2);
      assert.equal(await page.locator('[data-board="rule:0"] .au-bchip .nav-icon').first().textContent(), 'star');
      assert.equal(await page.locator('[data-board="rule:1"] .au-bchip.mv').count(), 1);
      assert.equal(await page.locator('[data-board="rule:1"] .au-bw.mv').count(), 1);
      assert.equal(await page.locator('[data-bmode="sort"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.au-branch path').count(), 5, 'a trunk and a drop into each destination');
      const [llm, dests] = [await page.locator('.au-bllm').boundingBox(), await page.locator('.au-dests').boundingBox()];
      assert.ok(dests.y > llm.y + llm.height, 'the destinations sit under the LLM');
      const [cond, chain] = [await page.locator('[data-board="rule:0"] .au-bc').boundingBox(), await page.locator('[data-board="rule:0"] .au-bch').boundingBox()];
      assert.ok(chain.x >= cond.x + cond.width, 'a chain sits right of its rule on a computer');
      await shot(page, 'desktop-board');

      // Rule 3 (receipt → Archive): + Action, Create a task, Save. Mail actions stay first; the destinations go back as they were.
      await page.locator('[data-board="rule:2"]').click();
      await page.waitForSelector('#au-root.ed-pop .au-ed:not([hidden])');
      const pop = await page.locator('.au-side').boundingBox();
      assert.ok(pop.width <= 521 && Math.abs(pop.x + pop.width / 2 - DESKTOP.width / 2) < 2, 'a pop-up over the board');
      assert.equal(await page.locator('[data-board="rule:2"]').getAttribute('class').then(c => c.includes('sel')), true);
      await page.locator('[data-op="add"][data-at="item.then"]').click();
      await page.selectOption('[data-act="item.then.1"]', 'create_task');
      await page.fill('[data-b="item.then.1.args.title"]', 'Check the receipt');
      await page.selectOption('[data-b="item.then.1.args.owner"]', 'person:omar');
      await shot(page, 'desktop-board-rule');
      let body = await save(page, writes, 'au_board', 4);
      assert.deepEqual(body.steps[0].rules[2].then, [{do: 'archive', args: {}}, {do: 'create_task', args: {title: 'Check the receipt', owner: 'person:omar'}}]);
      assert.equal(body.steps[0].rules[2].next, 'end');
      assert.deepEqual(body.destinations, FX.inbox.destinations, 'the destinations go back unchanged');
      assert.deepEqual(body.steps.map(s => s.id), ['f1', 'dq', 'fq', 'sd', 'sf']);
      assert.deepEqual(body.steps.slice(1), FX.inbox.steps.slice(1), 'the other steps go back unchanged');
      assert.equal(await page.locator('[data-board="rule:2"] .au-bchip').count(), 2);

      // A non-mail action put first goes after the mail ones on save.
      await page.locator('[data-board="rule:2"]').click();
      await page.selectOption('[data-act="item.then.0"]', 'wake_bot');
      await page.selectOption('[data-act="item.then.1"]', 'forward');
      await page.fill('[data-b="item.then.1.args.to"]', 'books@acme.example');
      body = await save(page, writes, 'au_board', 5);
      assert.deepEqual(body.steps[0].rules[2].then, [{do: 'forward', args: {to: 'books@acme.example'}}, {do: 'wake_bot', args: {}}]);

      // Rule 1 (legal words → Legal): Move on instead.
      await page.locator('[data-board="rule:0"]').click();
      await page.selectOption('[data-end]', 'rule');
      body = await save(page, writes, 'au_board', 6);
      const r0 = body.steps[0].rules[0];
      assert.deepEqual([r0.next, r0.stop, 'to' in r0], ['rule', false, false]);
      assert.equal(await page.locator('[data-board="rule:0"] .au-bw.mv').count(), 1);

      // A destination's pop-up: a new color and name, saved in destinations.
      await page.locator('[data-board="dest:3"]').click();
      await page.fill('[data-b="item.name"]', 'Done');
      await page.locator('.au-swatches input[value="teal"]').check();
      body = await save(page, writes, 'au_board', 7);
      assert.deepEqual(body.destinations[3], {...FX.inbox.destinations[3], name: 'Done', color: 'teal'});
      assert.match(await page.locator('[data-board="dest:3"]').innerText(), /Done/);

      // Versions name what moved.
      assert.equal(await ver(page), 'v7');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A bot's proposal that changes a destination's chain, sends a rule to a destination and moves another on: each
    // shows in the diff before Turn on.
    {
      const {page, errors, db} = await open(browser, DESKTOP);
      await page.waitForSelector('.au-board');
      const flow = JSON.parse(JSON.stringify({trigger: FX.inbox.trigger, steps: FX.inbox.steps, destinations: FX.inbox.destinations}));
      flow.destinations[2].then = [{do: 'wake_bot', args: {bot: 'bot:support'}}];
      Object.assign(flow.steps[0].rules[2], {to: 'eng', next: 'end', stop: true});
      flow.steps[0].rules[0] = {...flow.steps[0].rules[0], next: 'rule', stop: false};
      delete flow.steps[0].rules[0].to;
      db.versions.au_board.push({n: 4, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending', summary: 'Route receipts', flow});
      await page.locator('.au-ver').click();
      const card = page.locator('.au-prop[data-n="4"]');
      await card.waitFor();
      await card.getByRole('button', {name: 'View diff'}).click();
      const added = await card.locator('.au-diff .add').allTextContents();
      assert.equal(added.length, 3, added.join('\n'));
      assert.ok(added.some(l => /subpoena.*STARRED.*move on/.test(l)), 'move on shows');
      assert.ok(added.some(l => /receipt.*→ Engineering$/.test(l)), 'the new destination shows');
      assert.ok(added.some(l => /^\+ destination Engineering.*bot:support/.test(l)), 'the destination chain shows with its args');
      assert.equal(await card.locator('.au-diff .del').count(), 3);
      await shot(page, 'desktop-board-proposal');
      await card.getByRole('button', {name: 'Turn on'}).click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v4');
      await page.locator('.au-ed [data-op="close"]').click();
      assert.equal(await page.locator('[data-board="rule:0"] .au-bw.mv').count(), 1);
      assert.match(await page.locator('[data-board="rule:2"]').innerText(), /Engineering/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A flow without destinations keeps its step list.
    {
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_inbox', tweak});
      await page.waitForSelector('.au-card[data-open="f1"]');
      assert.equal(await page.locator('.au-board').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A webhook board: its fields as code, Sort + note on; switching to Sort saves a decide and a routing filter.
    {
      const {page, errors, writes} = await open(browser, DESKTOP, 'au_hook');
      await page.waitForSelector('.au-board');
      assert.equal(await page.locator('[data-board="rule:0"] code').first().textContent(), 'type');
      assert.equal(await page.locator('[data-bmode="draft"]').textContent(), 'Sort + note');
      assert.equal(await page.locator('[data-bmode="draft"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.au-dest:not(.au-dest-add)').count(), 2);
      assert.equal(await page.locator('.au-branch path').count(), 3);
      await shot(page, 'desktop-board-webhook');
      await page.locator('[data-bmode="sort"]').click();
      await page.waitForFunction(() => document.querySelector('[data-bmode="sort"]')?.getAttribute('aria-pressed') === 'true');
      const body = writes.at(-1).body;
      assert.deepEqual(body.steps.map(s => s.type), ['filter', 'decide', 'filter']);
      assert.deepEqual(body.steps[2].rules.map(r => r.to), ['ops', 'log', 'log']);
      assert.deepEqual(body.destinations, FX.webhook.destinations);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: the board stacks, each chain under its row, no sideways scroll; a pop-up is a sheet.
    {
      const {page, errors} = await open(browser, PHONE);
      await page.waitForSelector('.au-board');
      const [cond, chain] = [await page.locator('[data-board="rule:0"] .au-bc').boundingBox(), await page.locator('[data-board="rule:0"] .au-bch').boundingBox()];
      assert.ok(chain.y >= cond.y + cond.height - 1, 'a chain goes under its rule');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-board');
      await page.locator('[data-board="dest:0"]').click();
      await page.waitForSelector('#au-root.ed-open .au-ed:not([hidden])');
      const sheet = await page.locator('.au-side').boundingBox();
      assert.ok(Math.abs(sheet.y + sheet.height - PHONE.height) < 2, 'a sheet at the bottom');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-board-dest');
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('automations-board: ok');
})().catch(e => { console.error(e); process.exit(1); });
