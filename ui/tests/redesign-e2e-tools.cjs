// Journey 8 on a real hub (redesign TESTING.md §L3): Library › Tools lists the tool a computer reported (the
// Engineering Lead's Acme Billing, read and refund, from the runner's heartbeat) under In use, with how many have it, and
// its row opens the tool: who has it (the Engineering Lead, free to change things there), then its page. The old
// Tools address (#/integrations) leads here. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, ok, sideways, shot, t} = require('./support/redesign-e2e.cjs');

run('redesign-e2e-tools', ['org', 'toured', 'connection'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/integrations'});
  await page.waitForFunction(() => location.hash.startsWith('#/library/tools'), null, {timeout: t(5000)});
  const tools = await ok(page, 'GET', 'tools');
  assert.ok(JSON.stringify(tools).includes('acme-billing'), `${tag}: the hub lists it`);
  const row = page.locator('.lc-row[data-key="acme-billing"]');
  await row.waitFor({timeout: t(15000)});
  assert.match(await row.innerText(), /acme|billing/i);
  assert.equal(await row.locator('.lc-n').innerText(), '1', `${tag}: one teammate has it`);
  assert.match((await page.locator('.lc-gh').allInnerTexts())[0], /IN USE/);
  // The rail marks the page once config has rearranged it (redesign.js), which may land after the list.
  await page.locator('#side a[data-nav="tools"].cur').waitFor({state: 'attached', timeout: t(5000)})
    .catch(() => assert.fail(`${tag}: Tools is current (${JSON.stringify(page.errors)})`));
  const h = await row.evaluate(el => el.getBoundingClientRect().height);
  assert.ok(h < 64, `${tag}: one dense row (${h}px)`);
  assert.equal(await sideways(page), false);
  if (phone) await shot(page, `${tag}-01-list`);

  await row.click();
  await page.waitForFunction(() => location.hash.split('?')[0] === '#/library/tools/acme-billing', null, {timeout: t(5000)});
  // On a computer another tool is open beside the list first: wait for this one's page.
  await page.locator('#int-detail h1').filter({hasText: /acme.billing/i}).waitFor({timeout: t(5000)});
  const access = page.locator('.lc-access .lc-who-row');
  await access.first().waitFor();
  assert.match(await access.first().innerText(), /engineering.lead/i, `${tag}: who has it`);
  assert.equal(await access.first().locator('.pill').innerText(), 'Freely', `${tag}: refund is a write`);
  assert.equal(await page.locator('#int-detail .int-meta dd').evaluateAll(dd => dd.filter(d => !d.textContent.trim()).length), 0,
    `${tag}: no label without a value`);
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-02-tool`);
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
