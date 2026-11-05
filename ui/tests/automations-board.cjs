// The flow board (ui/app/automations-flow.js auBoardHTML) against the fake hub: an inbox with destinations shows its
// rules, questions, LLM and destinations, a flow without them keeps the step list; a rule's pop-up adds an action and
// saves both (with the destinations unchanged), a rule moved on saves next "rule"; a proposal's diff shows destination,
// `to` and move-on changes (by id when two destinations share a name); a webhook board offers Sort only and no mail
// actions; deleting down to one destination saves Sort as one routing rule, and a second one again (or a draft) starts
// with an instruction the hub accepts; a switched-off Sort keeps the step list; the step list's Stop here keeps next;
// an action's label or reply text shows and saves under the name that runs;
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

// Save the open editor and wait for the new version, whatever its number.
async function saveAny(page) {
  const was = await ver(page);
  await page.locator('.au-save').click();
  await page.waitForFunction(v => document.querySelector('.au-ver')?.textContent !== v && document.querySelector('.au-ed').hidden, was);
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
      assert.ok(added.some(l => /receipt.*→ Engineering \(eng\)$/.test(l)), 'the new destination shows');
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

    // A webhook board: its fields as code, Sort only (a draft or reply needs a message), and no mail actions to pick.
    {
      const {page, errors} = await open(browser, DESKTOP, 'au_hook');
      await page.waitForSelector('.au-board');
      assert.equal(await page.locator('[data-board="rule:0"] code').first().textContent(), 'type');
      assert.equal(await page.locator('[data-bmode], .au-bseg').count(), 0, 'no draft or reply modes off mail');
      assert.equal(await page.locator('.au-dest:not(.au-dest-add)').count(), 2);
      assert.equal(await page.locator('.au-branch path').count(), 3);
      await shot(page, 'desktop-board-webhook');
      await page.locator('[data-board="llm"]').click();
      await page.waitForSelector('#au-root.ed-pop .au-ed:not([hidden])');
      assert.equal(await page.locator('.au-ed [data-op="bmode"]').count(), 0, 'no mode switch in the pop-up either');
      await page.locator('.au-ed [data-op="close"]').click();
      await page.locator('[data-board="rule:0"]').click();
      await page.locator('[data-op="add"][data-at="item.then"]').click();
      const acts = await page.locator('[data-act="item.then.0"] option').evaluateAll(os => os.map(o => o.value));
      assert.equal(acts[0], 'create_task', 'a new action off mail is a task');
      for (const mail of ['archive', 'label', 'star', 'leave_for_you', 'forward', 'draft_reply', 'send_reply', 'unsubscribe'])
        assert.equal(acts.includes(mail), false, mail + ' needs a message');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Deleting down to one destination: Sort saves as a single rule sending everything there, with no decide step.
    {
      const {page, errors, writes} = await open(browser, DESKTOP, 'au_hook');
      await page.waitForSelector('.au-board');
      await page.locator('[data-board="dest:1"]').click();
      page.once('dialog', d => d.accept());
      await page.locator('.au-ed [data-op="bdel"]').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v3');
      const w = writes.at(-1);
      assert.equal(w.path, '/api/v2/automations/au_hook');
      assert.deepEqual(w.body.destinations.map(d => d.id), ['ops']);
      assert.deepEqual(w.body.steps.map(x => [x.id, x.type]), [['f1', 'filter'], ['sf', 'filter']]);
      assert.deepEqual(w.body.steps[1].rules, [{when: [], match: 'all', then: [], to: 'ops', next: 'end', stop: true}]);
      await page.waitForSelector('.au-board');
      assert.equal(await page.locator('.au-dest:not(.au-dest-add)').count(), 1);
      await page.locator('[data-board="llm"]').click();
      await page.waitForSelector('#au-root.ed-pop .au-ed:not([hidden])');
      assert.equal(await page.locator('.au-ed [data-b="item.default"]').count(), 0, 'no unsure pick with one destination');
      assert.equal(await page.locator('.au-ed [data-b="item.prompt"]').count(), 0, 'nothing to ask with one destination');
      await page.locator('.au-ed [data-op="close"]').click();
      await page.locator('[data-board="dest:0"]').click();
      assert.equal(await page.locator('.au-ed [data-op="bdel"]').count(), 0, 'the last destination stays');
      await page.locator('.au-ed [data-op="close"]').click();
      // A second destination again: Sort asks once more, with a starting instruction the hub accepts, at the board's level.
      await page.locator('.au-dest-add').click();
      await page.fill('[data-b="item.name"]', 'Archive');
      const back = await save(page, writes, 'au_hook', 4);
      assert.deepEqual(back.steps.map(x => x.type), ['filter', 'decide', 'filter']);
      assert.equal(back.steps[1].level, 'high');
      assert.deepEqual(back.steps[1].questions.to.instructions, 'Pick where this goes.');
      assert.deepEqual(Object.keys(back.steps[1].questions.to.criteria), ['ops', 'archive']);
      assert.equal(back.steps[2].id, 'sf');
      await page.locator('[data-board="llm"]').click();
      assert.equal(await page.locator('.au-ed [data-b="item.prompt"]').inputValue(), 'Pick where this goes.', 'the person can edit it');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // One destination on mail, switched to Sort + draft: the llm step gets a starting prompt the hub accepts.
    {
      const one = fx => { tweak(fx); const a = fx.automations.find(x => x.id === 'au_board');
        a.steps.splice(3, 2, {id: 'sf', type: 'filter', enabled: true, rules: [{when: [], match: 'all', then: [], to: 'me', next: 'end', stop: true}]}); };
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_board', tweak: one});
      await page.waitForSelector('.au-board');
      await page.locator('[data-bmode="draft"]').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v4');
      const turn = writes.at(-1).body.steps.at(-1);
      assert.equal(turn.type, 'llm');
      assert.equal(turn.prompt, 'Pick where this message goes.');
      assert.deepEqual(turn.choose, {to: ['me'], default: 'me', mode: 'draft'});
      assert.equal(writes.at(-1).body.steps[1].level, 'medium', 'a kept decide keeps its level');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Sort whose unsure destination isn't one of its answers, switched to a draft: the default joins choose.to, which
    // the hub requires (and the fake refuses without).
    {
      const outside = fx => { tweak(fx); const a = fx.automations.find(x => x.id === 'au_board');
        delete a.steps[3].questions.to.criteria.me; a.steps[4].rules.splice(0, 1); };
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_board', tweak: outside});
      await page.waitForSelector('.au-board');
      const status = await page.evaluate(async steps => (await fetch('/api/v2/automations/au_board', {method: 'PATCH',
        headers: {'content-type': 'application/json'}, body: JSON.stringify({steps})})).status,
        [{id: 'l1', type: 'llm', out: 'l1', prompt: 'x', choose: {to: ['legal'], default: 'me', mode: 'draft'}}]);
      assert.equal(status, 422, 'the fake refuses a default outside to');
      await page.locator('[data-bmode="draft"]').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v4');
      assert.deepEqual(writes.at(-1).body.steps.at(-1).choose, {to: ['legal', 'eng', 'archive', 'me'], default: 'me', mode: 'draft'});
      assert.deepEqual(errors, []);
      await page.close();
    }

    // The step list's Filter: Stop here reads where a rule goes (its own next, else stop), and a save keeps each next.
    {
      const rules = fx => { tweak(fx); const f = fx.automations.find(x => x.id === 'au_inbox').steps[0], w = v => [{field: 'message.subject', op: 'contains', value: v}];
        f.rules = [{when: w('a'), match: 'all', then: ['archive'], next: 'end'}, {when: w('b'), match: 'all', then: ['archive'], next: 'end', stop: false},
          {when: w('c'), match: 'all', then: ['archive'], next: 'rule', stop: false}]; };
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_inbox', tweak: rules});
      await page.locator('.au-card[data-open="f1"]').click();
      const box = i => page.locator(`[data-b="step.rules.${i}.stop"]`);
      assert.deepEqual([await box(0).isChecked(), await box(1).isChecked(), await box(2).isChecked()], [true, true, false]);
      const n = writes.length;
      await saveAny(page);
      assert.equal(writes.length, n + 1);
      let r = writes.at(-1).body.steps[0].rules;
      assert.deepEqual(r.map(x => [x.next, 'stop' in x ? x.stop : 'none']), [['end', 'none'], ['end', true], ['rule', false]]);
      // Unstopped, a rule goes on to the next step; stopped, it ends.
      await page.locator('.au-card[data-open="f1"]').click();
      await box(0).uncheck();
      await box(2).check();
      await saveAny(page);
      r = writes.at(-1).body.steps[0].rules;
      assert.deepEqual(r.map(x => [x.next, x.stop]), [['step', false], ['end', true], ['end', true]]);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A switched-off Sort keeps the step list: the board has no off switch and a save would turn it back on.
    {
      const off = fx => { tweak(fx); const h = fx.automations.find(x => x.id === 'au_hook'); h.steps[1].enabled = h.steps[2].enabled = false; };
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_hook', tweak: off});
      await page.waitForSelector('.au-card[data-open="sd"]');
      assert.equal(await page.locator('.au-board').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Two destinations with one name: a proposal moving a rule from one to the other shows in the diff by id.
    {
      const same = fx => { tweak(fx); fx.automations.find(x => x.id === 'au_hook').destinations[1].name = 'Ops'; };
      const {page, errors, db} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_hook', tweak: same});
      await page.waitForSelector('.au-board');
      const flow = JSON.parse(JSON.stringify({trigger: FX.webhook.trigger, steps: FX.webhook.steps, destinations: FX.webhook.destinations}));
      flow.destinations[1].name = 'Ops';
      flow.steps[0].rules[0].to = 'log';
      db.versions.au_hook.push({n: 3, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending', summary: 'Refunds to log', flow});
      await page.locator('.au-ver').click();
      const card = page.locator('.au-prop[data-n="3"]');
      await card.waitFor();
      await card.getByRole('button', {name: 'View diff'}).click();
      const added = await card.locator('.au-diff .add').allTextContents(), gone = await card.locator('.au-diff .del').allTextContents();
      assert.equal(added.length, 1, added.join('\n'));
      assert.match(added[0], /refund.*→ Ops \(log\)$/);
      assert.match(gone[0], /refund.*→ Ops \(ops\)$/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // An action's text under another name the hub reads first (label or tag before name, text after body): both editors
    // show what runs, and an edit saves under one name so the edit is what runs.
    {
      const aliased = fx => { tweak(fx);
        const b = fx.automations.find(x => x.id === 'au_board');
        b.steps[0].rules[2].then = [{do: 'label', args: {label: 'Later', name: 'Unused'}}, {do: 'forward', args: {webhook: 'wh_1'}}];
        b.destinations[3].then = [{do: 'call_webhook', args: {webhook: 'orders', data: []}}, {do: 'call_webhook', args: {webhook: 'refunds', data: ''}}];
        b.steps[2].rules[1].then = [{do: 'label', args: {tag: 'Old'}}, {do: 'draft_reply', args: {text: 'Hi'}}];
        fx.automations.find(x => x.id === 'au_inbox').steps.push(
          {id: 'lb1', type: 'action', enabled: true, do: 'label', args: {label: 'Later'}},
          {id: 'lb2', type: 'action', enabled: true, do: 'label', args: {tag: 'Old', name: 'Unused'}}); };
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_board', tweak: aliased});
      await page.waitForSelector('.au-board');
      assert.match(await page.locator('[data-board="rule:2"]').innerText(), /Later/);
      await page.locator('[data-board="rule:2"]').click();
      assert.equal(await page.inputValue('[data-b="item.then.0.args.name"]'), 'Later');
      await page.fill('[data-b="item.then.0.args.name"]', 'Receipts');
      // A forward to a webhook shows it (it is what runs); an address replaces it.
      assert.equal(await page.inputValue('[data-b="item.then.1.args.to"]'), 'wh_1');
      await page.fill('[data-b="item.then.1.args.to"]', 'ops@acme.example');
      let body = await save(page, writes, 'au_board', 4);
      assert.deepEqual(body.steps[0].rules[2].then, [{do: 'label', args: {name: 'Receipts'}}, {do: 'forward', args: {to: 'ops@acme.example'}}]);
      await page.locator('[data-board="q:1"]').click();
      assert.equal(await page.inputValue('[data-b="item.then.0.args.name"]'), 'Old');
      assert.equal(await page.inputValue('[data-b="item.then.1.args.body"]'), 'Hi');
      await page.fill('[data-b="item.then.0.args.name"]', 'New');
      await page.fill('[data-b="item.then.1.args.body"]', 'Hello');
      body = await save(page, writes, 'au_board', 5);
      assert.deepEqual(body.steps[2].rules[1].then, [{do: 'label', args: {name: 'New'}}, {do: 'draft_reply', args: {body: 'Hello'}}]);
      // Renaming a destination keeps its webhooks' payloads, empty ones included.
      await page.locator('[data-board="dest:3"]').click();
      await page.fill('[data-b="item.name"]', 'Done');
      body = await save(page, writes, 'au_board', 6);
      assert.deepEqual(body.destinations[3].then, [{do: 'call_webhook', args: {webhook: 'orders', data: []}}, {do: 'call_webhook', args: {webhook: 'refunds', data: ''}}]);
      // The step list's Label step.
      await page.evaluate(() => { location.hash = '#/automations/au_inbox'; });
      for (const [id, was, now] of [['lb1', 'Later', 'Receipts'], ['lb2', 'Old', 'New']]) {
        await page.locator(`.au-card[data-open="${id}"]`).click();
        assert.equal(await page.inputValue('[data-b="step.args.name"]'), was);
        await page.fill('[data-b="step.args.name"]', now);
        await saveAny(page);
        assert.deepEqual(writes.at(-1).body.steps.find(x => x.id === id).args, {name: now});
      }
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
