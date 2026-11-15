// Journey 8 on a real hub (redesign TESTING.md §L3): Tools lists the tool a computer reported (the Engineering
// Manager's Acme Billing, read and refund, from the runner's heartbeat) under In use, with the face of who has it, and
// its row opens the tool's own page: who has it (the Engineering Manager, free to change things there), then its page.
// The old Tools address (#/integrations) and the old Keys address lead to Tools. Desktop only: nothing here differs on a
// phone, and e2e-tools.cts runs Tools on a phone.
import type * as RE from './support/redesign-e2e.cts';
const assert = require('node:assert/strict');
const {run, open, ok, sideways, shot, t} = require('./support/redesign-e2e.cts') as RE.Api;

run('redesign-e2e-tools', ['org', 'toured', 'connection'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/integrations'});
  await page.waitForFunction(() => location.hash === '#/tools', null, {timeout: t(5000)});
  const tools = await ok(page, 'GET', 'tools');
  assert.ok(JSON.stringify(tools).includes('acme-billing'), `${tag}: the hub lists it`);
  const row = page.locator('tr[data-tl-row$="acme-billing"]');
  await row.waitFor({timeout: t(15000)});
  assert.match(await row.innerText(), /acme|billing/i);
  assert.equal(await row.locator('.tt-face').count(), 1, `${tag}: one teammate has it`);
  assert.equal(await row.evaluate(el => { let x = el.previousElementSibling; while (x && !(x as HTMLElement).dataset.tlGrp) x = x.previousElementSibling; return (x as HTMLElement | null)?.dataset.tlGrp; }),
    'In use', `${tag}: under In use`);
  // The rail marks the page once config has rearranged it (redesign.js), which may land after the list.
  await page.locator('#side a[data-nav="tools"].cur').waitFor({state: 'attached', timeout: t(5000)})
    .catch(() => assert.fail(`${tag}: Tools is current (${JSON.stringify(page.errors)})`));
  const h = await row.evaluate(el => el.getBoundingClientRect().height);
  assert.ok(h < 64, `${tag}: one dense row (${h}px)`);
  assert.equal(await sideways(page), false);
  if (phone) await shot(page, `${tag}-01-list`);

  await row.locator('.au-nm').click();
  await page.waitForFunction(() => location.hash === '#/tools/acme-billing', null, {timeout: t(5000)});
  await page.locator('#int-detail h1').filter({hasText: /acme.billing/i}).waitFor({timeout: t(5000)});
  const access = page.locator('.lc-access .lc-who-row');
  await access.first().waitFor();
  assert.match(await access.first().innerText(), /engineering.manager/i, `${tag}: who has it`);
  assert.equal(await access.first().locator('.pill').innerText(), 'Freely', `${tag}: refund is a write`);
  assert.equal(await page.locator('#int-detail .int-meta dd').evaluateAll(dd => dd.filter(d => !d.textContent!.trim()).length), 0,
    `${tag}: no label without a value`);
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-02-tool`);
  // The old Keys address of the tool lands on its page.
  await page.evaluate(() => { location.hash = '#/tools/keys/acme-billing'; });
  await page.waitForFunction(() => location.hash === '#/tools/acme-billing', null, {timeout: t(5000)});
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
}, {only: 'desktop'});
