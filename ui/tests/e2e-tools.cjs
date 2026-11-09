// Journey: Tools on a real hub, as Dana. The `tools` seed (evals/redesign/seeds.py) makes Close CRM on the proxy at a
// made-up address its fake vendor answers in the hub's process: a Read and write key, the Engineering Lead at Read and
// Omar at Read and write, one run of the Lead's that takes its proxy token from credential-runtime and makes two calls
// (a GET sent, a POST refused: at Read with only the full key, only GET goes through), then a read key (the Lead's Read
// moves to it) and Check now; and Ledger export, a key on computers. Here a shared mailbox is added through the API.
// The hub's answers: who sees which tool (Omar only the one he holds, and only his own row on it; Priya none), and no
// key value in any answer. The page: the old Library › Connections address lands on Tools; the list, its Needs you
// and Mail filters; Close CRM's levels, keys, check squares and recent calls; its Activity tab and Refused filter;
// Ledger export's Move to proxy; the mailbox's page. Phone only; TICO_UI_SHOTS=<dir> saves each in dark and light.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {journey, open, go, must, call, assertClean, sideways, t, SEEDS} = require('./support/e2e.cjs');

// The seed's made-up key values (evals/redesign/seeds.py TOOL_KEYS): no answer may carry one.
const KEYS = ['close-read-test-0001', 'close-full-test-0002', 'ledger-test-0003'];
const MAILBOX = 'sales@acme.example';

async function shots(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  for (const theme of ['dark', 'light']) {
    await page.evaluate(v => window.ticoTheme.set(v), theme);
    await page.evaluate(() => Promise.all(document.getAnimations()
      .filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({path: path.join(dir, `tools-${name}-${theme}.png`), fullPage: true, animations: 'disabled'});
  }
  await page.evaluate(() => window.ticoTheme.set('dark'));
}
const rowIds = page => page.locator('tr[data-tl-row]').evaluateAll(rows => rows.map(r => r.dataset.tlRow));

journey('e2e-tools', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';

  // ---- the hub's answers
  const box = (await must(hub, 'dana', 'POST', 'mailboxes', {address: MAILBOX, shared: true}, 'a shared mailbox')).mailbox;
  await must(hub, 'dana', 'POST', `mailboxes/${box.id}/accesses`, {grantee_type: 'bot', grantee_id: 'engineering-lead',
    level: 'read'}, 'the Lead reads it');
  const index = await must(hub, 'dana', 'GET', 'tool-index');
  const crm = index.rows.find(r => r.id === 'api:close-crm'), ledger = index.rows.find(r => r.id === 'api:ledger-export');
  assert.ok(crm && ledger, `${where}: both API tools listed: ${index.rows.map(r => r.id)}`);
  assert.deepEqual([crm.way, crm.status.text, crm.needs_you, crm.href], ['Proxy', 'Working', false, '#/tools/api/close-crm']);
  assert.deepEqual(crm.who.map(w => [w.kind, w.id]).sort(), [['bot', 'engineering-lead'], ['person', 'omar']]);
  assert.deepEqual([ledger.way, ledger.status.text, ledger.needs_you], ['On computers', 'Key on computers', true]);
  const mailRow = index.rows.find(r => r.id === 'mailbox:' + MAILBOX);
  assert.ok(mailRow && mailRow.href === '#/tools/mailbox/' + MAILBOX && mailRow.mailbox_id === box.id, `${where}: the mailbox row`);
  assert.ok(index.counts.needs_you >= 1 && index.counts.api >= 2 && index.counts.mail >= 1, JSON.stringify(index.counts));

  const tool = await must(hub, 'dana', 'GET', 'api-tools/close-crm');
  assert.deepEqual(tool.keys.map(k => k.access_level).sort(), ['read', 'read_write'], `${where}: a key a level`);
  assert.deepEqual(Object.fromEntries(tool.access.map(a => [a.subject, a.level])),
    {'bot:engineering-lead': 'read', 'human:omar': 'read_write'}, `${where}: levels`);
  assert.ok(tool.checks.length >= 2 && tool.checks.every(c => c.ok && c.status_code === 200), `${where}: Check now worked`);
  const calls = Object.fromEntries(tool.recent.map(c => [c.method, c]));
  assert.deepEqual([calls.GET?.result, calls.GET?.status_code, calls.GET?.path], ['sent', 200, '/api/v1/lead/?query=&_limit='],
    `${where}: the GET went through, its query values dropped`);
  assert.equal(calls.POST?.result, 'refused', `${where}: the Read bot's POST was refused`);
  assert.match(calls.POST.detail, /Read: only GET calls/, `${where}: the refusal names the setting`);
  const activity = await must(hub, 'dana', 'GET', 'api-tools/close-crm/activity?limit=50');
  assert.equal(activity.rows.filter(r => r.kind === 'call').length, 2, `${where}: two calls logged`);
  assert.equal(activity.rows.filter(r => r.kind === 'use').length, 0, `${where}: a proxied tool's calls are its uses`);
  const refused = await must(hub, 'dana', 'GET', 'api-tools/close-crm/activity?result=refused');
  assert.deepEqual(refused.rows.map(r => r.method), ['POST'], `${where}: the Refused filter`);
  const said = JSON.stringify([index, tool, activity, await must(hub, 'dana', 'GET', 'api-tools/ledger-export')]);
  for (const key of KEYS) assert.ok(!said.includes(key), `${where}: no answer carries a key value`);

  // Omar holds Close CRM only: he sees it, with his own row alone, and not Ledger export; Priya holds neither.
  const omar = await must(hub, 'omar', 'GET', 'tool-index');
  const omarApi = omar.rows.filter(r => r.kind === 'api');
  assert.deepEqual(omarApi.map(r => r.id), ['api:close-crm'], `${where}: Omar's API tools`);
  assert.deepEqual(omarApi[0].who.map(w => w.id), ['omar'], `${where}: Omar sees his own row only`);
  assert.equal((await call(hub, 'omar', 'GET', 'api-tools/ledger-export')).status, 404, `${where}: not Omar's tool`);
  const priya = await must(hub, 'priya', 'GET', 'tool-index');
  assert.deepEqual(priya.rows.filter(r => r.kind === 'api'), [], `${where}: Priya holds no API tool`);

  // ---- the page
  const page = await open(browser, hub, 'dana', {phone, hash: '#/library/connections'});
  await page.waitForFunction(() => location.hash === '#/tools', null, {timeout: t(5000)});
  await page.locator('tr[data-tl-row="api:close-crm"]').waitFor({timeout: t(15000)});
  // The first-result feedback question covers the page's corner; Dana skips it.
  const skip = page.locator('#feedback-ask:not([hidden]) [data-skip]');
  if (await skip.count()) await skip.click();
  const crmRow = page.locator('tr[data-tl-row="api:close-crm"]');
  assert.match(await crmRow.innerText(), /Close CRM[\s\S]*Proxy[\s\S]*Working/, `${where}: the Close CRM row`);
  assert.equal(await crmRow.locator('.tt-face').count(), 2, `${where}: two faces`);
  assert.equal(await sideways(page), false, `${where}: the list has no sideways scroll`);
  assertClean(page, `${where}: list`);
  await shots(page, `list-${where}`);
  await page.locator('[data-tl-show="needs"]').click();
  await page.waitForFunction(() => location.hash === '#/tools?show=needs');
  const needs = await rowIds(page);
  assert.ok(needs.includes('api:ledger-export') && !needs.includes('api:close-crm'), `${where}: Needs you: ${needs}`);
  await shots(page, `needs-${where}`);
  await page.locator('[data-tl-show="mail"]').click();
  assert.ok((await rowIds(page)).includes('mailbox:' + MAILBOX), `${where}: Mail and numbers lists the mailbox`);
  await page.locator('[data-tl-show=""]').click();

  // Close CRM: who can use it at which level, its keys, its checks and its calls.
  await crmRow.locator('.tt-st').click();
  await page.waitForFunction(() => location.hash === '#/tools/api/close-crm');
  await page.locator('#tool-access tr[data-ta-subject-row]').first().waitFor({timeout: t(10000)});
  const checked = sel => page.locator(`${sel}[aria-checked="true"]`).count();
  assert.equal(await checked('[data-ta-subject="bot:engineering-lead"][data-ta-level="read"]'), 1, `${where}: the Lead at Read`);
  assert.equal(await checked('[data-ta-subject="human:omar"][data-ta-level="read_write"]'), 1, `${where}: Omar at Read and write`);
  assert.deepEqual(await page.locator('#tool-keys [data-key-level]').evaluateAll(els => els.map(e => e.dataset.keyLevel).sort()),
    ['read', 'read_write'], `${where}: two keys`);
  assert.match(await page.locator('#tool-keys').innerText(), /Through Tico · logged/, `${where}: proxied`);
  assert.ok(await page.locator('#tool-check .tt-sq.ok').count() >= 2, `${where}: green squares`);
  assert.equal(await page.locator('#tool-check .tt-sq.fail').count(), 0, `${where}: no failed check`);
  const recent = await page.locator('#tool-recent').innerText();
  assert.match(recent, /GET \/api\/v1\/lead\/\?query=&_limit=/, `${where}: the GET in Recent`);
  assert.match(recent, /Refused/, `${where}: the refusal in Recent`);
  assert.match(await page.locator('#tool-limits').innerText(), /Not available yet/, `${where}: Limits placeholder`);
  assert.equal(await sideways(page), false, `${where}: the tool page has no sideways scroll`);
  assertClean(page, `${where}: Close CRM`);
  await shots(page, `api-${where}`);

  await page.locator('.tt-tabs a[data-ta-tab="activity"]').click();
  await page.waitForFunction(() => location.hash === '#/tools/api/close-crm/activity');
  await page.waitForFunction(() => document.querySelectorAll('#tool-api [data-ta-call]').length === 2, null, {timeout: t(10000)});
  await page.locator('[data-ta-show="refused"]').click();
  await page.waitForFunction(() => document.querySelectorAll('#tool-api [data-ta-call]').length === 1);
  assert.match(await page.locator('#tool-api [data-ta-call]').innerText(), /POST[\s\S]*Refused[\s\S]*only GET calls/, `${where}: why`);
  assert.equal(await sideways(page), false, `${where}: Activity has no sideways scroll`);
  assertClean(page, `${where}: Activity`);
  await shots(page, `activity-${where}`);

  // Ledger export: its key is on computers, and Move to proxy is offered (it has no address, so not done here).
  await go(page, '#/tools/api/ledger-export');
  await page.locator('#tool-keys [data-ta-delivery="computer"]').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('#tool-keys [data-ta-proxy]').count(), 1, `${where}: Move to proxy`);
  assert.equal(await page.locator('[data-ta-check]').count(), 0, `${where}: no Check now without a check path`);
  assert.match(await page.locator('#tool-api .tt-title').innerText(), /Key on computers/, `${where}: its status`);
  assertClean(page, `${where}: Ledger export`);
  await shots(page, `computer-${where}`);

  // The mailbox's page under Tools.
  await go(page, '#/tools/mailbox/' + MAILBOX);
  await page.locator('[data-ib-detail="mailbox"] [data-ib-detail-body] h1, [data-ib-detail="mailbox"] [data-ib-detail-body] h2')
    .first().waitFor({timeout: t(15000)});
  assert.match(await page.locator('#tools-inbox').innerText(), new RegExp(MAILBOX.replace('.', '\\.')), `${where}: the mailbox page`);
  assert.equal(await sideways(page), false, `${where}: the mailbox page has no sideways scroll`);
  assertClean(page, `${where}: mailbox`);
  await shots(page, `mailbox-${where}`);
  await page.context().close();
}, [...SEEDS, 'tools'], {only: 'phone'});   // the hub's answers do not depend on the width; the pages have one markup, and a phone is where sideways scroll shows
