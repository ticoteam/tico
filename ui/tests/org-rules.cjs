// The Rules page (ui/app/org-rules.js) on a real hub seeded with the scenario org and its groups: Dana adds an internal
// rule with a Who and a With and sees it listed, edits it in the side panel, a stale save is refused and the panel
// reloads, the Engineering Lead's Rules tab shows the rule and its settings, a due date is added and marked
// satisfied, and the Engineering Lead's suggestions are added all at once. With RULES_SHOTS=<dir>, it also saves each
// screen at 1440 and 390, light and dark, into that directory.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

// A write as the page's own teammate, the way the app sends one.
const api = (page, method, p, body) => page.evaluate(async ([method, p, body]) => {
  const r = await fetch('/api' + p, {method, cache: 'no-store', headers: {'content-type': 'application/json', 'Idempotency-Key': crypto.randomUUID()},
                                     body: body === undefined ? undefined : JSON.stringify(body)});
  const json = await r.json().catch(() => ({}));
  return {status: r.status, json};
}, [method, p, body]);
const ok = async (page, method, p, body) => {
  const {status, json} = await api(page, method, p, body);
  assert.ok(status < 300, `${method} ${p}: ${status} ${JSON.stringify(json)}`);
  return json;
};
const ready = page => page.waitForFunction(() => {
  const who = document.querySelector('#account .account-email')?.textContent || '';
  return who !== '' && who !== 'Signing in…' && document.querySelector('#main')?.children.length > 0;
}, null, {timeout: t(20000)});
const go = async (page, hash) => { await page.evaluate(h => { location.hash = h; }, hash); };
const day = n => { const d = new Date(Date.now() + n * 86400000); return d.toISOString().slice(0, 10); };

(async () => {
  const hub = await startHub({seeds: ['org', 'groups', 'toured']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

    // The Engineering Lead suggests three rules from a vendor: a bot's rule always waits in Suggested.
    const bot = await pageAs(browser, hub, 'engineering-lead');
    const source = (await ok(bot, 'POST', '/v2/rule-sources', {name: 'Northwind Supply', kind: 'vendor'})).source;
    for (const text of ['Send invoices within 30 days', 'Keep delivery records for 2 years', 'Report outages within 24 hours']) {
      const made = await ok(bot, 'POST', '/v2/rules', {kind: 'external', text, who: {bots: ['engineering-lead']}, source_id: source.id});
      assert.equal(made.status, 'suggested');
    }

    const page = await pageAs(browser, hub, 'dana');
    page.on('dialog', d => d.accept());
    await ready(page);

    // Add an internal rule: Who the Engineering Lead, With Engineering.
    await go(page, '#/rules');
    await page.locator('.ru-ph [data-ru-add]').click();
    const modal = page.locator('#ru-add');
    await modal.locator('textarea[name=text]').fill('Ask before a production deploy');
    await modal.locator('[data-scope="who"] [data-seg="bots"]').click();
    await modal.locator('[data-scope="who"] .ru-addsel').selectOption('engineering-lead');
    await modal.locator('[data-scope="with"] [data-seg="groups"]').click();
    await modal.locator('[data-scope="with"] .ru-addsel').selectOption({label: 'Engineering'});
    await modal.locator('button[type=submit]').click();
    const row = page.locator('.ru-int a.ru-row', {hasText: 'Ask before a production deploy'});
    await row.waitFor({timeout: t(10000)});
    const ruleId = await row.getAttribute('data-rule');
    let rule = await ok(page, 'GET', '/v2/rules/' + ruleId);
    assert.equal(rule.status, 'active');
    assert.deepEqual(rule.who.bots.map(b => b.id), ['engineering-lead']);
    assert.equal(rule.with.groups.length, 1);
    assert.ok(rule.can_edit);

    // Edit it in the side panel.
    const side = page.locator('#ru-side');
    await page.waitForFunction(id => document.querySelector(`#ru-side form[data-rule="${id}"]`), ruleId, {timeout: t(10000)});
    await side.locator('textarea[name=description]').fill('Production means the live site.');
    await side.locator('[data-ru-seg="if_broken"] [data-v="warn"]').click();
    await page.evaluate(() => { document.querySelector('#ru-side form').dataset.old = '1'; });   // the panel redraws after a save
    await side.locator('button[type=submit]').click();
    await page.waitForFunction(async id => {
      const r = await (await fetch('/api/v2/rules/' + id, {cache: 'no-store'})).json();
      return r.if_broken === 'warn' && r.version === 2;
    }, ruleId, {timeout: t(10000)});
    rule = await ok(page, 'GET', '/v2/rules/' + ruleId);
    assert.equal(rule.description, 'Production means the live site.');

    // A save on a version someone else has moved past is refused; the panel reloads what is stored now.
    await side.locator(`form[data-rule="${ruleId}"]:not([data-old]) textarea[name=description]`).waitFor({timeout: t(10000)});
    await ok(page, 'POST', '/v2/rules/' + ruleId, {version: rule.version, text: 'Ask Omar before a production deploy'});
    await side.locator('textarea[name=description]').fill('A stale edit');
    await side.locator('button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#ru-side textarea[name=text]')?.value === 'Ask Omar before a production deploy'
      && document.querySelector('#ru-side .ru-status')?.textContent.trim() !== '', null, {timeout: t(10000)});
    rule = await ok(page, 'GET', '/v2/rules/' + ruleId);
    assert.equal(rule.version, 3);
    assert.equal(rule.description, 'Production means the live site.', 'the stale save wrote nothing');

    // Add a due date from the rule's panel, then mark it satisfied.
    await side.locator('.ru-adddue').click();
    await page.waitForFunction(() => document.querySelector('#ru-side input[name=due]'), null, {timeout: t(10000)});
    await side.locator('input[name=due]').fill(day(5));
    await side.locator('select[name=repeats]').selectOption('yearly');
    await side.locator('select[name=owner]').selectOption('human:dana');
    await side.locator('button[type=submit]').click();
    const satisfy = side.locator('[data-ru-satisfy]');
    await satisfy.waitFor({timeout: t(10000)});
    const dueId = new URLSearchParams((await page.evaluate(() => location.hash)).split('?')[1]).get('due');
    assert.ok(dueId);
    await satisfy.click();
    await page.locator('#ru-side .ru-mark').waitFor({timeout: t(10000)});   // its History has the mark
    const due = (await ok(page, 'GET', '/v2/rules/due?next=400d')).due.find(d => d.id === dueId);
    assert.equal(due.status, 'upcoming', 'a yearly due date moves on to next year');
    assert.equal(due.history[0].satisfied_by, 'human:dana');

    // The Engineering Lead's Rules tab: the rule, then its settings as sentences.
    await go(page, '#/bot/engineering-lead/rules');
    await page.locator(`#bot-rules .ru-row[data-rule="${ruleId}"]`).waitFor({timeout: t(15000)});
    assert.ok(await page.locator('#bot-rules .ru-setting').count() > 0, 'its settings are listed');

    // Suggested: add all three at once.
    await go(page, '#/rules/suggested');
    const group = page.locator(`.ru-sg[data-group="${source.id}"]`);
    await group.locator('[data-ru-add-all]').click();
    await group.waitFor({state: 'detached', timeout: t(15000)});
    const counts = await ok(page, 'GET', '/v2/rules/counts');
    assert.equal(counts.suggested, 0);
    assert.equal(counts.external, 3);
    await page.context().close();

    if (process.env.RULES_SHOTS) await shots(browser, hub, bot, {ruleId, dueId});
    await bot.context().close();
    console.log('org-rules: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });

// Each screen at 1440 and 390, light and dark: Internal with the rule's panel, External, Due with its panel,
// Suggested, Add rule and the bot's Rules tab.
async function shots(browser, hub, bot, {ruleId, dueId}) {
  const dir = process.env.RULES_SHOTS;
  fs.mkdirSync(dir, {recursive: true});
  // More to look at: a second internal rule, and suggestions from a customer waiting in Suggested.
  const dana = await pageAs(browser, hub, 'dana');
  await ok(dana, 'POST', '/v2/rules', {kind: 'internal', text: 'Reply to customers within one business day', who: {groups: [(await ok(dana, 'GET', '/v2/rules/' + ruleId)).with.groups[0].id]}});
  await ok(dana, 'POST', '/v2/rules', {kind: 'internal', text: 'Log every refund in the ledger', who: {all_bots: true}, if_broken: 'escalate'});
  await dana.context().close();
  const customer = (await ok(bot, 'POST', '/v2/rule-sources', {name: 'Contoso Retail', kind: 'customer'})).source;
  for (const text of ['Ship orders within 2 business days', 'Use the customer portal for returns'])
    await ok(bot, 'POST', '/v2/rules', {kind: 'external', text, who: {all_bots: true}, source_id: customer.id});
  const screens = [
    ['internal', `#/rules?rule=${ruleId}`, `#ru-side form[data-rule="${ruleId}"]`],
    ['external', '#/rules/external', '.ru-ext a.ru-row'],
    ['due', `#/rules/due?due=${dueId}`, '#ru-side .ru-mark'],
    ['suggested', '#/rules/suggested', '.ru-sg .ru-prop'],
    ['add-rule', '#/rules', '#ru-add[open]', async page => { await page.locator('.ru-ph [data-ru-add]').click(); }],
    ['bot-rules', '#/bot/engineering-lead/rules', '#bot-rules .ru-setting'],
  ];
  for (const theme of ['light', 'dark']) {
    for (const phone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        await ready(page);
        await page.evaluate(v => window.ticoTheme.set(v), theme);
        // The scenario's runner notice would cover the rail in every picture.
        await page.locator('.needs-you [data-snooze]').click({timeout: t(3000)}).catch(() => {});
        for (const [name, hash, wait, act] of screens) {
          await go(page, '#/overview');
          await go(page, hash);
          if (act) { await page.locator('.ru-ph [data-ru-add]').waitFor({timeout: t(10000)}); await act(page); }
          await page.locator(wait).first().waitFor({timeout: t(15000)});
          await page.waitForTimeout(400);
          await page.screenshot({path: path.join(dir, `${name}-${phone ? 390 : 1440}-${theme}.png`)});
          await page.keyboard.press('Escape');
        }
      } finally {
        await page.context().close();
      }
    }
  }
}
