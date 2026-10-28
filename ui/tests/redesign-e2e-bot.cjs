// Journey 5 on a real hub (redesign TESTING.md §L3): a bot proposes a change to Dana's "Inbox triage" (seeded as BotOps
// writes it for Builder Claude, whose rights do not reach it: the PATCH leaves a pending version; no model runs). Dana
// sees the proposal card in the BotOps panel, opens its diff and turns it on; the hub's live flow then archives receipts
// first and no proposal is left. On a phone BotOps has its own screen. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, ok, automationNamed, sideways, shot, t} = require('./support/redesign-e2e.cjs');

run('redesign-e2e-bot', ['org', 'toured', 'automations', 'bot_proposal'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  const triage = await automationNamed(page, 'Inbox triage');
  const pending = (await ok(page, 'GET', `automations/${triage.id}/versions?status=pending`)).versions;
  assert.equal(pending.length, 1, 'one proposal on the hub');
  const n = pending[0].n;
  assert.equal(triage.steps[0].rules.length, 1, 'not live yet');

  await page.locator(`.au-row[data-row="${triage.id}"] a`).click();
  await page.locator('.au-hd .au-name:text("Inbox triage")').waitFor();
  await page.locator('.au-pane .au-card[data-open="sort"]').waitFor();
  if (phone) {
    assert.equal(await page.locator('.au-botp').isVisible(), false);
    await page.locator('.au-botbtn').click();
    await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'bot');
  }
  assert.equal(await page.locator('.au-botp').isVisible(), true);
  const card = page.locator(`.au-prop[data-n="${n}"]`);
  await card.waitFor({timeout: t(10000)});
  assert.match(await card.innerText(), /Inbox triage[\s\S]*Archive receipts first/);
  await card.getByRole('button', {name: 'View diff'}).click();
  assert.deepEqual(await card.locator('.au-diff .add').allTextContents(), ['+ filter subject has “receipt” → Archive']);
  assert.equal(await card.locator('.au-diff .del').count(), 0);
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-01-proposal`);

  await card.getByRole('button', {name: 'Turn on'}).click();
  await page.locator(`.au-prop[data-n="${n}"] .pill.ok`).waitFor({timeout: t(10000)});
  assert.equal(await card.getByRole('button', {name: 'Turn on'}).count(), 0);
  await shot(page, `${tag}-02-turned-on`);

  const live = await automationNamed(page, 'Inbox triage');
  assert.equal(live.steps[0].rules.length, 2, `${tag}: live on the hub`);
  assert.deepEqual(live.steps[0].rules[0].when, [{field: 'message.subject', op: 'contains', value: 'receipt'}]);
  assert.deepEqual((await ok(page, 'GET', `automations/${triage.id}/versions?status=pending`)).versions, [], 'nothing pending');

  if (phone) {
    await page.locator('.au-botp [data-bot-back]').click();
    await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'detail');
  }
  assert.equal(await page.locator('.au-ver').innerText(), 'v' + live.version);
  assert.match(await page.locator('.au-pane .au-card[data-open="sort"]').innerText(), /2 rules[\s\S]*subject has “receipt”/);
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-03-flow`);
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
