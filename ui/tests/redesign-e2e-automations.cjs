// Journey 2 on a real hub (redesign TESTING.md §L3): Dana's Automations list, open one, switch it off and on from its header, run it
// with a sample message (⋯ Test run), and the run lands open in Runs with each step's real output: the Filter's rule,
// the Decide's answer from the hub's judge (a steady stand-in, no model), the note. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, ok, automationNamed, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const enabled = async page => (await automationNamed(page, 'Inbox triage')).enabled;
// The hub's answer, once the switch's write has landed (or after about three seconds).
const settled = async (page, want) => { for (let i = 0; i < 20 && await enabled(page) !== want; i++) await page.waitForTimeout(150); return enabled(page); };

run('redesign-e2e-automations', ['org', 'toured', 'automations'], async ({hub, browser, phone, tag}) => {
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
  await box.fill(JSON.stringify({message: {from: 'promo@deals.example', subject: 'Sale', body: '50% off'}}));
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
});
