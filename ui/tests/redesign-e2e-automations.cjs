// Journeys 2-6 on one real hub per viewport (redesign TESTING.md §L3), and the Assistant's sweep and a Runs tab: what
// were redesign-e2e-visibility, -automations, -paths and -build, automations-assistant-routine and e2e-automations'
// Runs tab. They share the seeds (org, toured, automations; assistant adds the Assistant's sweep) and run in this
// order on one hub and one browser, each step opening its own pages: visibility (read-only), automations (Inbox triage's
// switch and Test runs), paths (Route mail's new path), build (a new automation and its versions), assistantRoutine
// (the sweep: its row, header, Test run; Omar is refused), runsTab (a scheduled automation's Runs tab, made last).
// Each keeps its own assertions and names itself in a failure ("[paths, phone] ..."). Phone, then desktop.
// redesign-e2e-bot (its own bot_proposal seed changes Inbox triage) stays a separate script.
const assert = require('node:assert/strict');
const {run, open, go, api, ok, automationNamed, sideways, shot, t} = require('./support/redesign-e2e.cjs');
const promoInput = require('./fixtures/redesign-automation-promo.json');

// ---- redesign-e2e-visibility
const visibility = (() => {
  const listed = page => page.locator('.au-row').allInnerTexts();
  return async ({hub, browser, phone, tag}) => {
    const dana = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
    await dana.locator('.au-row').first().waitFor({timeout: t(15000)});
    const triage = await automationNamed(dana, 'Inbox triage');
    assert.ok((await listed(dana)).some(x => x.includes('Standup notes')), `${tag}: Dana sees Priya's`);
    assert.ok(await automationNamed(dana, 'Standup notes'), 'and the hub lists it for her');
    await shot(dana, `${tag}-01-dana`);
    await dana.context().close();

    const priya = await open(browser, hub, 'priya', {phone, hash: '#/automations'});
    await priya.locator('.au-row').first().waitFor({timeout: t(15000)});
    const rows = await listed(priya);
    assert.ok(rows.some(x => x.includes('Standup notes')), `${tag}: her own`);
    for (const name of ['Inbox triage', 'Route mail', 'Weekly digest'])
      assert.ok(!rows.some(x => x.includes(name)), `${tag}: ${name} is absent for Priya`);
    const names = (await ok(priya, 'GET', 'automations')).automations.map(a => a.name);
    assert.ok(!names.includes('Inbox triage'), `${tag}: the hub leaves it out too: ${names}`);
    const read = await api(priya, 'GET', `automations/${triage.id}`);
    assert.ok([403, 404].includes(read.status), `${tag}: the hub refuses the read: ${read.status}`);
    assert.equal(await sideways(priya), false);
    await shot(priya, `${tag}-02-priya`);

    // Dana's address opens nothing of it for Priya.
    await go(priya, `#/automations/${triage.id}`);
    await priya.locator('.au-pane:has-text("Not shared with you")').waitFor({timeout: t(5000)});
    assert.equal(await priya.locator('.au-pane .err').count(), 0, `${tag}: a short line, not the hub's refusal`);
    assert.equal(await priya.locator('.au-pane .au-card[data-open="sort"]').count(), 0, `${tag}: no flow shown`);
    assert.notEqual(await priya.locator('.au-hd .au-name').innerText().catch(() => ''), 'Inbox triage');
    await shot(priya, `${tag}-03-priya-direct`);
    assert.deepEqual(priya.errors, [], `${tag}: no page errors`);
    await priya.context().close();
  };
})();

// ---- redesign-e2e-automations
const automations = (() => {
  const enabled = async page => (await automationNamed(page, 'Inbox triage')).enabled;
  const settled = async (page, want) => { for (let i = 0; i < 20 && await enabled(page) !== want; i++) await page.waitForTimeout(150); return enabled(page); };
  return async ({hub, browser, phone, tag}) => {
    const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
    await page.locator('tr.au-row').first().waitFor({timeout: t(15000)});
    const names = await page.locator('tr.au-row').allInnerTexts();
    for (const n of ['What wakes bots', 'Inbox triage', 'Route mail', 'Weekly digest', 'Standup notes'])
      assert.ok(names.some(x => x.includes(n)), `${tag}: ${n} listed: ${names}`);
    const triage = await automationNamed(page, 'Inbox triage');
    const wakes = await automationNamed(page, 'What wakes bots');
    assert.equal(await page.locator('tr.au-row .au-sw').count(), 0, `${tag}: no switches in the list`);
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-01-list`);

    // The top of the org may pause the read-only built-in: its header has the switch, and says it is fixed.
    assert.equal(wakes.can_edit, false);
    assert.equal(wakes.can_pause, true);
    await page.locator(`tr.au-row[data-row="${wakes.id}"] a`).click();
    await page.locator('.au-hd .au-name:text("What wakes bots")').waitFor();
    assert.equal(await page.locator('.au-hd [data-on-cur]').count(), 1, `${tag}: the built-in has a switch`);
    assert.match(await page.locator('.au-pane').innerText(), /Built-in: can’t be edited or deleted/);
    await page.locator('.au-crumb .au-up').click();

    // Open it, then off and on again from its header: the hub agrees each time.
    await page.locator(`tr.au-row[data-row="${triage.id}"] a`).click();
    await page.locator('.au-hd [data-on-cur]').waitFor();
    const sw = page.locator('.au-hd [data-on-cur]');
    await sw.click();
    await page.waitForFunction(() => !document.querySelector('.au-hd [data-on-cur]').checked);
    assert.equal(await settled(page, false), false, `${tag}: off on the hub`);
    await shot(page, `${tag}-02-off`);
    await sw.click();
    await page.waitForFunction(() => document.querySelector('.au-hd [data-on-cur]').checked);
    assert.equal(await settled(page, true), true, `${tag}: on again`);

    // The trigger and the three steps.
    await page.locator('.au-hd .au-name:text("Inbox triage")').waitFor();
    await page.locator('.au-card[data-open="sort"]').waitFor();
    assert.match(await page.locator('.au-trig').innerText(), /dana@acme\.example/);
    // The header keeps everything inside it and the name whole (a narrow pane wraps Flow/Runs to a second row).
    const head = await page.locator('.au-hd').evaluate(hd => {
      const box = hd.getBoundingClientRect(), name = hd.querySelector('.au-name');
      const out = [...hd.querySelectorAll('.au-name, .au-sw, .au-ver, .au-more, .au-chip, .au-seg')]
        .filter(el => el.checkVisibility() && el.getBoundingClientRect().right > box.right + 1).map(el => el.className);
      return {out, cut: name.scrollWidth > name.clientWidth + 1};
    });
    assert.deepEqual(head, {out: [], cut: false}, `${tag}: the header fits`);
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-03-flow`);

    // ⋯ Test run with a sample message: it runs on the hub and opens in Runs.
    await page.locator('[data-more]').click();
    await page.locator('.au-menu [data-mi="run"]').click();
    const box = page.locator('.au-ed textarea[data-b="text"]');
    await box.waitFor();
    assert.match(await box.inputValue(), /sam@acme\.example/, 'a sample shaped like the trigger');
    await shot(page, `${tag}-04-test-run`);
    await box.fill(JSON.stringify({message: {from: 'sam@acme.example', subject: 'Lunch Friday?', body: 'Are you free?'}}, null, 2));
    await page.locator('.au-ed [data-op="save"]').click();
    await page.waitForURL(/\/runs$/);
    await page.locator('.au-rx .au-sl').first().waitFor({timeout: t(15000)});
    const runs = (await ok(page, 'GET', `automations/${triage.id}/runs`)).runs;
    assert.equal(runs.length, 1, `${tag}: one run on the hub`);
    assert.equal(await page.locator('.au-rr').count(), 1);
    assert.equal(await page.locator('.au-rr').first().getAttribute('aria-expanded'), 'true', 'the new run opens');
    const lines = await page.locator('.au-rx .au-sl').allInnerTexts();
    assert.equal(lines.length, 3, `${tag}: a line per step: ${lines}`);
    assert.match(lines[0], /Filter|Sort/i);
    assert.match(lines[1], /reply 82%/, `${tag}: the judge's answer: ${lines[1]}`);
    assert.match(lines[2], /Triaged|note|Tell/i, `${tag}: the note: ${lines[2]}`);
    for (const h of await page.locator('.au-rx .au-sl').evaluateAll(els => els.map(e => e.getBoundingClientRect().height)))
      assert.ok(h < 64, `${tag}: a step line stays short (${h}px)`);
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-05-run-open`);

    // A promo is archived by the Filter's rule.
    await go(page, `#/automations/${triage.id}`);
    await page.locator('.au-card[data-open="trigger"]').waitFor();
    await page.locator('[data-more]').click();
    await page.locator('.au-menu [data-mi="run"]').click();
    await box.fill(JSON.stringify(promoInput));
    await page.locator('.au-ed [data-op="save"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.au-rr').length === 2, null, {timeout: t(15000)});
    await page.locator('.au-rx .au-sl').first().waitFor();
    const promo = await page.locator('.au-rx .au-sl').allInnerTexts();
    assert.match(promo[0], /promo.*Archive · waiting/, `${tag}: the rule archived it, queued: ${promo}`);
    // The hub's clock is frozen, so both runs start at the same instant and their order is a tie: read the open one.
    assert.equal(await page.locator('.au-rr[aria-expanded="true"]').locator('.au-ro').innerText(), 'Waiting');
    await shot(page, `${tag}-06-run-promo`);
    assert.deepEqual(page.errors, [], `${tag}: no page errors`);
    await page.context().close();
  };
})();

// ---- redesign-e2e-paths
const paths = (() => {
  const selectedDot = page => page.evaluate(() => [...document.querySelectorAll('.au-dots [data-dot]')]
    .findIndex(d => d.getAttribute('aria-selected') === 'true'));
  return async ({hub, browser, phone, tag}) => {
    const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
    const route = await automationNamed(page, 'Route mail');
    await page.locator(`.au-row[data-row="${route.id}"] a`).click();
    await page.locator('.au-hd .au-name:text("Route mail")').waitFor();
    const strip = page.locator('.au-paths');
    await strip.waitFor();
    assert.deepEqual(await page.locator('.au-pth .au-pname').allInnerTexts(), ['Reply', 'Archive', 'Otherwise']);

    if (phone) {
      assert.equal(await page.locator('.au-dots [data-dot]').count(), 3);
      assert.equal(await selectedDot(page), 0);
      // A swipe left: the next card comes in and its dot lights.
      await strip.evaluate(el => el.scrollBy({left: el.clientWidth * 0.8}));
      await page.waitForFunction(() => document.querySelectorAll('.au-dots [data-dot]')[1].getAttribute('aria-selected') === 'true',
        null, {timeout: t(5000)});
      await shot(page, `${tag}-01-swiped`);
      await page.locator('.au-dots [data-dot="other"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.au-dots [data-dot]')[2].getAttribute('aria-selected') === 'true');
    } else {
      assert.equal(await page.locator('.au-dots').isVisible(), false);
      const tops = await page.locator('.au-pth').evaluateAll(cols => cols.map(c => Math.round(c.getBoundingClientRect().top)));
      assert.equal(new Set(tops).size, 1, 'the paths sit side by side');
      await shot(page, `${tag}-01-paths`);
    }
    assert.equal(await sideways(page), false);

    // A new path with a condition on the Decide answer.
    await page.locator('.au-pname[data-path="0"]').click();
    await page.locator('.au-ed [data-b="step.paths.0.name"]').waitFor();
    await page.locator('[data-op="add"][data-at="step.paths"]').click();
    await page.fill('[data-b="step.paths.2.name"]', 'Sure reply');
    await page.locator('[data-op="add"][data-at="step.paths.2.when"]').click();
    await page.fill('[data-b="step.paths.2.when.0.field"]', 'd1.bucket');
    await page.selectOption('[data-b="step.paths.2.when.0.op"]', 'is');
    await page.fill('[data-b="step.paths.2.when.0.value"]', 'reply');
    await page.fill('[data-b="step.paths.2.when.0.min"]', '90');
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-02-path-sheet`);
    await page.locator('.au-ed .au-save').click();
    await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v2', null, {timeout: t(10000)});

    const fork = (await automationNamed(page, 'Route mail')).steps.find(s => s.type === 'paths');
    assert.deepEqual(fork.paths.map(p => p.name), ['Reply', 'Archive', 'Sure reply'], `${tag}: the hub's paths`);
    assert.deepEqual(fork.paths[2], {name: 'Sure reply', when: [{field: 'd1.bucket', op: 'is', value: 'reply', min: 0.9}], steps: []});
    assert.equal(fork.paths[0].steps.length, 1, 'the other paths keep their steps');
    assert.deepEqual(fork.otherwise.steps.map(s => s.id), ['o']);
    assert.match(await page.locator('.au-pth[data-col="2"]').innerText(), /Sure reply[\s\S]*bucket = reply ≥ 90%/);
    if (phone) assert.equal(await page.locator('.au-dots [data-dot]').count(), 4);
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-03-saved`);

    // A test run: reply at 82% takes Reply, not Sure reply.
    const out = await ok(page, 'POST', `automations/${route.id}/run`,
      {input: {message: {from: 'sam@acme.example', subject: 'Lunch?', body: 'Free Friday?'}}});
    const went = out.run.steps.find(s => s.type === 'paths');
    assert.equal(went.outputs.path, 'Reply', `${tag}: the Reply path: ${JSON.stringify(went.outputs)}`);
    assert.deepEqual(page.errors, [], `${tag}: no page errors`);
    await page.context().close();
  };
})();

// ---- redesign-e2e-build
const build = (() => {
  async function add(page, type) {
    await page.locator('.au-pane .au-add').last().click();
    await page.locator(`.au-menu [data-ty="${type}"]`).click();
    await page.locator('.au-ed:not([hidden]) .au-save').waitFor();
  }
  async function save(page, version) {
    await page.locator('.au-ed .au-save').click();
    await page.waitForFunction(v => document.querySelector('.au-ver')?.textContent === v
      && document.querySelector('.au-ed').hidden, 'v' + version, {timeout: t(10000)});
  }
  return async ({hub, browser, phone, tag}) => {
    const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
    // Ask sits beside + New on a computer; a phone has the tab bar's Assistant instead.
    await page.locator('.au-new').waitFor();
    assert.equal(await page.locator('.au-ph [data-ask-open]').isVisible(), !phone, `${tag}: Ask beside + New on a computer only`);
    await page.locator('.au-new').click();
    await page.locator('.au-newm [data-new="schedule"]').click();
    await page.locator('.au-hd .au-name:text("New automation")').waitFor({timeout: t(10000)});
    await page.locator('.au-card[data-open="trigger"]').waitFor();
    assert.equal(await page.locator('.au-pane .au-card[data-open]:not([data-open="trigger"])').count(), 0, 'no steps yet');
    assert.equal(await page.locator('.au-hd .au-sw').isChecked(), false, `${tag}: a new one starts off`);
    const made = await automationNamed(page, 'New automation');
    assert.ok(made, `${tag}: the hub has it`);
    assert.equal(made.version, 1);
    assert.equal(made.enabled, false);
    assert.equal(made.owner_id, 'person:dana');
    assert.equal(made.trigger.type, 'schedule');
    assert.equal(made.trigger.cron, '0 9 * * 1-5');
    assert.deepEqual(made.steps, []);
    await shot(page, `${tag}-00-blank`);

    // Filter: a rule on the subject, archive.
    await add(page, 'filter');
    await page.fill('[data-b="step.rules.0.when.0.field"]', 'item.title');
    await page.fill('[data-b="step.rules.0.when.0.value"]', 'draft');
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-01-filter-sheet`);
    await save(page, 2);

    // Decide: what to ask is required; one yes/no question.
    await add(page, 'decide');
    await page.fill('[data-b="qs.0.id"]', 'worth_it');
    await page.locator('.au-ed .au-save').click();
    await page.locator('.toast:has-text("needs what to ask")').waitFor();
    await page.fill('[data-b="qs.0.ask"]', 'Is there enough news for a digest?');
    await shot(page, `${tag}-02-decide-sheet`);
    await save(page, 3);

    // LLM: Dana's Assistant writes it.
    await add(page, 'llm');
    await page.fill('[data-b="step.prompt"]', 'Write this week’s digest in five bullets.');
    await shot(page, `${tag}-03-llm-sheet`);
    await save(page, 4);
    assert.equal(await sideways(page), false);
    await shot(page, `${tag}-04-flow`);

    const built = (await ok(page, 'GET', `automations/${made.id}`));
    const flow = built.automation || built;
    assert.deepEqual(flow.steps.map(s => s.type), ['filter', 'decide', 'llm'], `${tag}: the hub has the three steps`);
    assert.deepEqual(flow.steps[0].rules[0].when, [{field: 'item.title', op: 'contains', value: 'draft'}]);
    assert.deepEqual(flow.steps[0].rules[0].then, [{do: 'archive', args: {}}]);
    assert.deepEqual(flow.steps[1].questions, {worth_it: {type: 'noul', instructions: 'Is there enough news for a digest?'}});
    assert.equal(flow.steps[2].prompt, 'Write this week’s digest in five bullets.');
    assert.match(await page.locator('.au-pane .au-card').allInnerTexts().then(x => x.join('\n')), /worth_it[\s\S]*five bullets/);

    // History: v1..v4, v4 current; restore v1 makes v5 with no steps.
    await page.locator('.au-ver').click();
    await page.locator('.au-vr').first().waitFor();
    const rows = await page.locator('.au-vr strong').allInnerTexts();
    assert.deepEqual(rows, ['v4', 'v3', 'v2', 'v1']);
    assert.match(await page.locator('.au-vr').first().innerText(), /Current/);
    for (const h of await page.locator('.au-vr').evaluateAll(els => els.map(e => e.getBoundingClientRect().height)))
      assert.ok(h < 52, `${tag}: a version is one row (${h}px)`);
    await shot(page, `${tag}-05-history`);
    await page.locator('.au-vr:has(strong:text-is("v1")) [data-op="restore"]').click();
    await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v5', null, {timeout: t(10000)});
    await page.locator('.au-vr strong:text-is("v5")').waitFor();
    await shot(page, `${tag}-06-restored`);
    const after = await automationNamed(page, 'New automation');
    assert.equal(after.version, 5);
    assert.deepEqual(after.steps, [], `${tag}: v1's empty flow is back`);
    await page.locator('.au-ed [data-op="close"]').click();
    assert.equal(await page.locator('.au-pane .au-card[data-open]:not([data-open="trigger"])').count(), 0);
    assert.deepEqual(page.errors, [], `${tag}: no page errors`);
    await page.context().close();
  };
})();

// ---- automations-assistant-routine
const assistantRoutine = (() => {
  const ASSISTANT = 'bot:coo';
  return async ({hub, browser, phone, tag}) => {
    // The sweep's title comes from the catalog (a template refresh renames it), so the test finds it by its owner.
    const dana = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
    await dana.locator('tr.au-row').first().waitFor({timeout: t(10000)});
    const sweeps = (await ok(dana, 'GET', 'automations?limit=500')).automations.filter(a => a.owner_id === ASSISTANT);
    assert.equal(sweeps.length, 1, `${tag}: the Assistant has one routine on Automations`);
    const sweep = sweeps[0];
    assert.equal(sweep.trigger.type, 'schedule', `${tag}: a schedule`);

    // The list row: its title, its schedule words, on (not greyed out).
    const row = dana.locator(`tr.au-row[data-row="${sweep.id}"]`);
    await row.waitFor({timeout: t(10000)});
    assert.equal(await row.locator('.au-nm').innerText(), sweep.name, `${tag}: its title on the row`);
    assert.match(await row.locator('td.au-tt').innerText(), /\b8\b|08:00|8:00/, `${tag}: its time on the row`);
    assert.doesNotMatch(await row.getAttribute('class'), /\boff\b/, `${tag}: on`);
    await shot(dana, `${tag}-assistant-routine-list`);

    // Opened: the Assistant's name over it, the switch on, the spend limit and the options menu (owner's controls).
    await row.locator('a').first().click();
    await dana.locator('.au-hd .au-name').filter({hasText: sweep.name}).waitFor({timeout: t(10000)});
    assert.equal(await dana.locator('.au-hd .au-sw').isChecked(), true, `${tag}: the switch is on`);
    assert.equal(await dana.locator('.au-hd [data-spend]').count(), 1, `${tag}: the spend limit is editable`);
    assert.equal(await dana.locator('.au-hd [data-more]').count(), 1, `${tag}: the options menu`);
    await shot(dana, `${tag}-assistant-routine-open`);

    // Test run from the options menu: the editor's Run makes a run, and the routine's Runs list has it.
    await dana.locator('.au-hd [data-more]').click();
    await dana.locator('.au-menu [data-mi="run"]').click();
    await dana.locator('.au-ed .au-save').click();
    const end = Date.now() + t(20000);
    let runs = [];
    while (Date.now() < end) {
      runs = (await ok(dana, 'GET', `automations/${sweep.id}/runs?limit=25`)).runs;
      if (runs.length) break;
      await dana.waitForTimeout(200);
    }
    assert.ok(runs.length >= 1, `${tag}: Test run made a run`);
    assert.deepEqual(dana.errors, [], `${tag}: no page errors`);

    // Omar is a peer of the Engineering Lead, not above the Assistant: no row, and the routine is refused by id.
    const omar = await open(browser, hub, 'omar', {phone, hash: '#/automations'});
    await omar.waitForTimeout(t(1000));
    assert.equal(await omar.locator(`tr.au-row[data-row="${sweep.id}"]`).count(), 0, `${tag}: Omar does not list it`);
    assert.ok(!(await ok(omar, 'GET', 'automations?limit=500')).automations.some(a => a.id === sweep.id), `${tag}: nor the API`);
    assert.equal((await api(omar, 'GET', `automations/${sweep.id}`)).status, 403, `${tag}: refused by id`);
    assert.deepEqual(omar.errors, [], `${tag}: no page errors for Omar`);
  };
})();

// ---- e2e-automations: the automation's own Runs tab, for a scheduled automation Dana made and fired by hand.
const runsTab = async ({hub, browser, phone, tag}) => {
  const NAME = 'Friday status check';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  const made = await ok(page, 'POST', 'automations', {name: NAME, owner: 'person:dana', enabled: true, builtin: false,
    trigger: {type: 'schedule', cron: '0 16 * * 5', tz: 'UTC'}, steps: []});
  const id = made.id || made.automation?.id;
  assert.ok(id, `${tag}: the create returns the automation id`);
  await go(page, `#/automations/${id}`);
  await page.locator('#main', {hasText: NAME}).first().waitFor({timeout: t(20000)});
  await ok(page, 'POST', `automations/${id}/run`, {input: {}});
  const runs = await ok(page, 'GET', `automations/${id}/runs`);
  assert.ok((runs.runs || runs.items || []).length >= 1, `${tag}: the hub has a run`);
  await page.reload();
  await page.locator('#main', {hasText: NAME}).first().waitFor({timeout: t(20000)});
  // The automation's own Runs tab (the list's Automations | Runs tabs are in the page, hidden behind it).
  await page.locator('#main button:visible, #main a:visible, #main [role=tab]:visible', {hasText: /^Runs$/}).first().click();
  await page.waitForFunction(() => { const text = document.querySelector('#main')?.innerText || '';
    return /\d{1,2}:\d{2} [AP]M/.test(text) && /Done/.test(text); }, null, {timeout: t(30000)})
    .catch(() => assert.fail(`${tag}: the run row draws its time and outcome`));
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  assert.equal(await sideways(page), false, `${tag}: automations scroll sideways`);
  await page.context().close();
};

const STEPS = {visibility, automations, paths, build, assistantRoutine, runsTab};

run('redesign-e2e-automations', ['org', 'toured', 'automations', 'assistant'], async ctx => {
  for (const [name, step] of Object.entries(STEPS)) {
    try { await step(ctx); }
    catch (error) { error.message = `[${name}, ${ctx.tag}] ${error.message}`; throw error; }
  }
});
