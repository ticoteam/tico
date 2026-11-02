// Counsel on the Rules page (ui/app/org-rules.js, docs/rules-plan.md §2, §5) on a real hub with Counsel on and a vendor's
// terms in Files (evals/redesign/seeds.py `counsel`): Counsel is in no bot list and its bot page has no composer; Dana
// adds the terms on External and a private Counsel task opens (asked again, the same task); Counsel's suggestions, one
// with no Who, wait in Suggested with no quote on the rows; Add all adds only the rows with a Who; the one with no Who
// is added once Dana picks one; and its quote shows in the rule's side panel, linked to the file version it cites.
// With RULES_SHOTS=<dir>, it also saves Add document, Suggested and an external rule's panel at 1440 and 390, light and
// dark, into <dir>/p1c-*.png.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const QUOTE = 'Keep a record of each consent for five years.';
const OPT_OUT = 'Customer messages must include an opt-out line in every send.';
// One API call as a teammate, by token (or a run's own token, {token}): Counsel's run writes its suggestions this way.
async function send(hub, teammate, method, p, body) {
  const token = typeof teammate === 'string' ? hub.tokens[teammate] : teammate.token;
  const r = await fetch(hub.url + '/api' + p, {method, headers: {Authorization: 'Bearer ' + token,
    'content-type': 'application/json', ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})},
    body: body === undefined ? undefined : JSON.stringify(body)});
  return {status: r.status, json: await r.json().catch(() => ({}))};
}
async function call(hub, teammate, method, p, body) {
  const {status, json} = await send(hub, teammate, method, p, body);
  assert.ok(status < 300, `${method} ${p} as ${teammate.who || teammate}: ${status} ${JSON.stringify(json)}`);
  return json;
}
const ready = page => page.waitForFunction(() => {
  const who = document.querySelector('#account .account-email')?.textContent || '';
  return who !== '' && who !== 'Signing in…' && document.querySelector('#main')?.children.length > 0;
}, null, {timeout: t(20000)});
const go = async (page, hash) => { await page.evaluate(h => { location.hash = h; }, hash); };
// The rule panel has closed and the address no longer names a rule.
const closed = async page => {
  await page.locator('#ru-side').waitFor({state: 'hidden', timeout: t(15000)});
  assert.ok(!page.url().includes('rule='), 'the address drops the rule');
};
// Counsel's run on its read task, claimed and started as its computer would (backend/tests/test_counsel.py
// counsel_run): only that run cites the document it was asked to read.
async function counselRun(hub) {
  const runner = {token: hub.runner, who: 'the runner'};
  const {attempt} = await call(hub, runner, 'POST', '/v2/jobs/claim', {bot: 'tico-counsel', start_places: true});
  assert.ok(attempt, 'Counsel has a run to claim');
  await call(hub, runner, 'POST', `/v2/attempts/${attempt.id}/started`, {thread_id: 'local-thread'});
  return {token: attempt.token, who: 'Counsel run'};
}
// Counsel's suggestion on the terms it was asked to read, citing that version (its opt-out line unless named).
const suggest = (hub, read, text, who, cite = {section: '4. Messaging', quote: OPT_OUT}) => call(hub, read.run, 'POST',
  '/v2/rules', {kind: 'external', text, who, source_id: read.source.id,
                cite: {file_id: read.file.id, version: read.file.version, ...cite}});

(async () => {
  const hub = await startHub({seeds: ['org', 'groups', 'toured', 'counsel']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
    const page = await pageAs(browser, hub, 'dana');
    page.on('dialog', d => d.accept());
    await ready(page);

    // Counsel is a hidden helper: on the roster, but not on the Team list, and its bot page has no composer.
    assert.ok(await page.evaluate(() => S.emps.some(e => e.name === 'tico-counsel')), 'Counsel is on the roster');
    await page.locator('[data-org^="b:"]').first().waitFor({timeout: t(15000)});
    assert.equal(await page.locator('[data-org="b:tico-counsel"], #side a[href="#/bot/tico-counsel"]').count(), 0, 'Counsel is not on the Team list');
    await go(page, '#/bot/tico-counsel');
    await page.locator('#chat-composer [data-ask-open]').waitFor({timeout: t(15000)});
    assert.equal(await page.locator('#chat-composer textarea').count(), 0, 'no composer on its page');

    // Add document: the terms from Files, from a new vendor, with a description.
    await go(page, '#/rules/external');
    await page.locator('.ru-ph [data-ru-add-doc]').click();
    const modal = page.locator('#ru-doc');
    await modal.locator('.ru-pick', {hasText: 'Northwind Messaging Terms'}).click();
    await modal.locator('select[name=source]').selectOption('new');
    await modal.locator('input[name=source_name]').fill('Northwind');
    await modal.locator('[data-ru-seg="source_kind"] [data-v="vendor"]').click();
    await modal.locator('textarea[name=note]').fill('We just signed with Northwind for texts.');
    await modal.locator('button[type=submit]').click();
    const done = modal.locator('.ru-done');
    await done.waitFor({timeout: t(15000)});
    const taskId = await done.getAttribute('data-ru-read');
    const task = await call(hub, 'dana', 'GET', '/v2/tasks/' + taskId);
    assert.equal((task.task || task).owner, 'bot:tico-counsel', 'a Counsel task');
    assert.match(await modal.locator('[data-ru-task]').getAttribute('href'), new RegExp(taskId));
    await modal.locator('[data-ru-close]').first().click();

    // Asked again while that task is open: the same task, and the dialog says it is already being read.
    await page.locator('.ru-ph [data-ru-add-doc]').click();
    await modal.locator('.ru-pick', {hasText: 'Northwind Messaging Terms'}).click();
    await modal.locator('select[name=source]').selectOption({label: 'Northwind'});
    await modal.locator('button[type=submit]').click();
    await modal.locator(`.ru-done[data-ru-read="${taskId}"]`, {hasText: 'already'}).waitFor({timeout: t(15000)});
    await modal.locator('[data-ru-close]').first().click();

    // Screening holds a document: the dialog says so and links the task, with no "reading" and no long-document line.
    const receipt = await call(hub, 'dana', 'GET', '/v2/rule-sources');
    const northwind = receipt.sources.find(s => s.name === 'Northwind');
    await page.route('**/api/v2/rules/read', route => route.fulfill({json: {task_id: taskId,
      source: {id: northwind.id, name: northwind.name, kind: northwind.kind},
      file: {id: 'file-held', version: 1, title: 'Northwind Messaging Terms'}, suggested_who: {groups: []},
      truncated: true, screening: 'hold', existing: false}}));
    await page.locator('.ru-ph [data-ru-add-doc]').click();
    await modal.locator('.ru-pick', {hasText: 'Northwind Messaging Terms'}).click();
    await modal.locator('select[name=source]').selectOption({label: 'Northwind'});
    await modal.locator('button[type=submit]').click();
    await modal.locator(`.ru-held[data-ru-read="${taskId}"]`).waitFor({timeout: t(15000)});
    assert.equal(await modal.locator('.ru-done, .ru-warn').count(), 0, 'a held document is not being read');
    assert.match(await modal.locator('[data-ru-task]').getAttribute('href'), new RegExp(taskId));
    await modal.locator('[data-ru-close]').first().click();
    await page.unroute('**/api/v2/rules/read');

    // A link the server refuses: the dialog shows the server's reason, and stays open to fix it.
    const link = 'http://127.0.0.1/terms';
    const refused = await send(hub, 'dana', 'POST', '/v2/rules/read', {url: link, source_id: northwind.id});
    assert.equal(refused.json.error?.code, 'fetch');
    await page.locator('.ru-ph [data-ru-add-doc]').click();
    await modal.locator('input[name=find]').fill(link);
    await modal.locator('select[name=source]').selectOption({label: 'Northwind'});
    await modal.locator('button[type=submit]').click();
    await page.waitForFunction(want => document.querySelector('#ru-doc .ru-status')?.textContent === want,
      refused.json.error.detail, {timeout: t(15000)});
    await modal.locator('[data-ru-close]').first().click();

    // Counsel suggests three rules: one with no Who (no department holds Northwind's access), two with one.
    const read = {source: (await call(hub, 'dana', 'GET', '/v2/rule-sources')).sources.find(s => s.name === 'Northwind'),
                  file: {id: (await call(hub, 'dana', 'GET', '/v2/files?limit=50')).files.find(f => f.title === 'Northwind Messaging Terms').id, version: 1},
                  run: await counselRun(hub)};
    const open = await suggest(hub, read, 'Keep a consent record for five years.', {}, {section: '4. Messaging', quote: QUOTE});
    const logs = await suggest(hub, read, 'Delete raw logs after 30 days.', {bots: ['engineering-lead']}, {section: '5. Data', quote: 'Delete raw logs after 30 days.'});
    const optout = await suggest(hub, read, 'Every customer text has an opt-out line.', {bots: ['engineering-lead']});
    assert.ok([open, logs, optout].every(r => r.status === 'suggested'));

    // Suggested: no document name or quote on the rows; the row with no Who can't be added until one is picked.
    await go(page, '#/rules/suggested');
    const group = page.locator(`.ru-sg[data-group="${read.source.id}"]`);
    const row = group.locator(`.ru-prop[data-rule="${open.id}"]`);
    await row.waitFor({timeout: t(15000)});
    assert.ok(await row.locator('[data-ru-accept]').isDisabled(), 'Add waits for a Who');
    // Counsel is a helper, never a Who: not among the row's choices.
    const whoChoices = await row.locator('[data-ru-who] option').evaluateAll(os => os.map(o => o.value + ' ' + o.textContent));
    assert.ok(whoChoices.some(v => v.includes('engineering-lead')), 'bots are offered');
    assert.ok(!whoChoices.some(v => /counsel/i.test(v)), 'Counsel is not offered as Who');
    const groupText = await group.textContent();
    assert.ok(!groupText.includes(QUOTE) && !groupText.includes('Northwind Messaging Terms'), 'no quote or document on the rows');

    // The sentence opens the side panel, with the section and quote, linked to the version cited.
    await row.locator('.ru-pt').click();
    const quote = page.locator('#ru-side .ru-quote');
    await quote.waitFor({timeout: t(10000)});
    assert.ok((await quote.textContent()).includes(QUOTE));
    assert.equal(await quote.locator('.ru-qsrc').textContent(), '4. Messaging');
    const fileHref = `/api/v2/files/${read.file.id}?v=${read.file.version}`;
    assert.ok((await quote.locator('.ru-qsrc').getAttribute('href')).endsWith(fileHref), 'the quote links the version it cites');
    assert.ok(await page.locator('#ru-side [data-ru-accept-one]').isDisabled(), "the panel's Add waits for a Who too");
    const panelChoices = await page.locator('#ru-side [data-scope="who"]').evaluate(async el => {
      el.querySelector('[data-seg="bots"]').click();
      await new Promise(r => setTimeout(r, 0));
      return [...el.querySelectorAll('.ru-addsel option')].map(o => o.value + ' ' + o.textContent);
    });
    assert.ok(panelChoices.some(v => v.includes('engineering-lead')) && !panelChoices.some(v => /counsel/i.test(v)),
      "Counsel is not offered in the panel's Who");

    // Add all adds the two rows with a Who and leaves the one without waiting; its panel stays on it.
    await group.locator('[data-ru-add-all]', {hasText: 'Add all 2'}).click();
    await group.locator(`.ru-prop[data-rule="${logs.id}"]`).waitFor({state: 'detached', timeout: t(15000)});
    await group.locator(`.ru-prop[data-rule="${optout.id}"]`).waitFor({state: 'detached', timeout: t(15000)});
    assert.equal((await call(hub, 'dana', 'GET', '/v2/rules/' + open.id)).status, 'suggested');
    await page.locator(`#ru-side form[data-rule="${open.id}"]`).waitFor({timeout: t(5000)});

    // Pick a Who, and the row's Add works; the open panel on that rule closes rather than show the old suggestion.
    await row.locator('[data-ru-who]').selectOption('bots:engineering-lead');
    assert.ok(await row.locator('[data-ru-accept]').isEnabled());
    await row.locator('[data-ru-accept]').click();
    await group.waitFor({state: 'detached', timeout: t(15000)});
    await closed(page);
    const added = await call(hub, 'dana', 'GET', '/v2/rules/' + open.id);
    assert.equal(added.status, 'active');
    assert.deepEqual(added.who.bots.map(b => b.id), ['engineering-lead']);
    assert.equal(added.cite.quote, QUOTE);

    // The same for a row's Skip and for Add all, each with its rule's panel open.
    const skipped = await suggest(hub, read, 'Send texts only from the approved short code.', {bots: ['engineering-lead']});
    const bulk = await suggest(hub, read, 'Stop texting a number that replies STOP.', {bots: ['engineering-lead']});
    const bulk2 = await suggest(hub, read, 'Name the business in every text.', {bots: ['engineering-lead']});
    // Away and back, each tab drawn before the next, so Suggested reads the new rows.
    await go(page, '#/rules/external');
    await page.locator('.ru-tabs a.cur[data-ru-tab="external"]').waitFor({timeout: t(10000)});
    await go(page, '#/rules/suggested');
    await group.locator(`.ru-prop[data-rule="${skipped.id}"] .ru-pt`).click();
    await page.locator(`#ru-side form[data-rule="${skipped.id}"]`).waitFor({timeout: t(10000)});
    await group.locator(`.ru-prop[data-rule="${skipped.id}"] [data-ru-skip]`).click();
    await closed(page);
    assert.notEqual((await call(hub, 'dana', 'GET', '/v2/rules/' + skipped.id)).status, 'suggested');
    await group.locator(`.ru-prop[data-rule="${bulk.id}"] .ru-pt`).click();
    await page.locator(`#ru-side form[data-rule="${bulk.id}"]`).waitFor({timeout: t(10000)});
    await group.locator('[data-ru-add-all]', {hasText: 'Add all 2'}).click();
    await closed(page);
    for (const r of [bulk, bulk2]) assert.equal((await call(hub, 'dana', 'GET', '/v2/rules/' + r.id)).status, 'active');

    // The external rule's panel shows the quote, and its link opens that version in the viewer.
    await go(page, `#/rules/external?rule=${open.id}`);
    const extQuote = page.locator(`#ru-side form[data-rule="${open.id}"] .ru-quote`);
    await extQuote.waitFor({timeout: t(15000)});
    const [viewed] = await Promise.all([page.waitForRequest(r => r.url().endsWith(fileHref), {timeout: t(10000)}),
      extQuote.locator('.ru-qsrc').click()]);
    assert.ok(viewed);
    await page.keyboard.press('Escape');
    await page.context().close();

    // A teammate who can't read the file (the Release Manager, a bot with no access to the Engineering Lead's files)
    // sees the rule but not its quote.
    assert.deepEqual((await call(hub, 'release-manager', 'GET', '/v2/rules/' + open.id)).cite, {hidden: true});
    // The browser requires a person's sign-in. Restrict the source before using
    // a person outside its readers, preserving the bot's API privacy check too.
    const access = await call(hub, 'dana', 'GET', '/v2/bots/engineering-lead/access');
    await call(hub, 'dana', 'PUT', '/v2/bots/engineering-lead/access', {
      see: access.see, read: {people: ['dana']}, write: access.write, revision: access.revision,
    });
    assert.deepEqual((await call(hub, 'priya', 'GET', '/v2/rules/' + open.id)).cite, {hidden: true});
    const other = await pageAs(browser, hub, 'priya');
    await ready(other);
    await go(other, `#/rules/external?rule=${open.id}`);
    await other.locator(`#ru-side form[data-rule="${open.id}"]`).waitFor({timeout: t(20000)});
    assert.equal(await other.locator('#ru-side .ru-quote, #ru-side .ru-qsrc').count(), 0, 'no quote for a teammate who cannot read the file');
    await other.context().close();

    if (process.env.RULES_SHOTS) await shots(browser, hub, read, open.id);
    console.log('org-rules-counsel: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });

// Add document (the terms picked, a new From), Suggested with Counsel's rows, and the external rule's panel with its
// quote, at 1440 and 390, light and dark.
async function shots(browser, hub, read, ruleId) {
  const dir = process.env.RULES_SHOTS;
  fs.mkdirSync(dir, {recursive: true});
  await suggest(hub, read, 'Texts go out only between 8am and 9pm in the recipient’s time zone.', {});
  await suggest(hub, read, 'Delete raw message logs after 30 days.', {bots: ['engineering-lead']}, {section: '5. Data', quote: 'Delete raw logs after 30 days.'});
  const groups = await call(hub, 'dana', 'GET', '/v2/groups');
  const team = (Array.isArray(groups) ? groups : groups.groups || []).find(g => g.name === 'Engineering');
  await suggest(hub, read, 'Every marketing text says how to opt out.', team ? {groups: [team.id]} : {all_bots: true});
  const addDoc = async page => {
    await page.locator('.ru-ph [data-ru-add-doc]').click();
    const modal = page.locator('#ru-doc');
    await modal.locator('.ru-pick', {hasText: 'Northwind Messaging Terms'}).click();
    await modal.locator('select[name=source]').selectOption('new');
    await modal.locator('input[name=source_name]').fill('Contoso Retail');
    await modal.locator('[data-ru-seg="source_kind"] [data-v="customer"]').click();
    await modal.locator('textarea[name=note]').fill('Our signed order form with Contoso.');
  };
  const screens = [
    ['p1c-add-document', '#/rules/external', '#ru-doc[open] .ru-doc:not([hidden])', addDoc, '.ru-ph [data-ru-add-doc]'],
    ['p1c-suggested', '#/rules/suggested', '.ru-sg .ru-prop'],
    ['p1c-external-quote', `#/rules/external?rule=${ruleId}`, `#ru-side form[data-rule="${ruleId}"] .ru-quote`],
  ];
  for (const theme of ['light', 'dark']) {
    for (const phone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        await ready(page);
        await page.evaluate(v => window.ticoTheme.set(v), theme);
        await page.locator('.needs-you [data-snooze]').click({timeout: t(3000)}).catch(() => {});
        for (const [name, hash, wait, act, before] of screens) {
          await go(page, '#/overview');
          await go(page, hash);
          if (act) { await page.locator(before).waitFor({timeout: t(10000)}); await act(page); }
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
