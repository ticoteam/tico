// A send approval in Needs you: one line in the list, and its card (a tap on the line) shows recipients, subject,
// draft and each attached file readable (name, size, short hash),
// the exact payload folds under Details, and a payload value is only ever text. A file links only to the version the
// server verified (`attachment_files`), pinned with ?v=, and opens that version in the viewer; a payload file_id the
// server did not verify is no link and says so. A Support reply to publish, on a 390px phone, says which ticket and
// shows its whole text wrapped on its card, its HTML-looking text stays text, and the exact payload
// (with the content hash) folds under Exact payload. A field the facts list does not name gets its own row, and a
// publish without its text says so. A bot's report to Tico support reads "Send this to Tico support?" in both places,
// with where it goes, what goes with it and its whole message wrapped as text. A task cap and a spend limit read as
// facts with names, not ids. An approval says how long its exact
// payload has been ready and whose decision it is ("Ready 2 h 10 min, waiting on you", or the person's name).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const HASH = 'a'.repeat(12) + 'b'.repeat(52), HASH2 = 'c'.repeat(64);
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const approval = {id: 'ap-1', kind: 'approval', what: 'send', title: 'Approve this send', requester: 'bot:support',
  requested_by: 'bot:support', created_at: new Date().toISOString(), conversation_id: 'c-1',
  payload: {to: ['ava@creator.example'], cc: [], subject: '<img src=x onerror="window.pwned=1">Contract', mailbox: 'ana@acme.example',
    draft: 'r-882', body_sha256: 'd'.repeat(64), attachments: [
      {name: 'chart.png', size: 219136, sha256: HASH, file_id: 'f-chart-0001'},
      {name: '<b>notes</b>.pdf', size: 900, sha256: HASH2, file_id: 'f-notes-0002'},
      {name: 'plain.txt', size: 12, sha256: HASH2}]},
  attachment_files: [{file_id: 'f-chart-0001', version: 1}, null, null]};
const REPLY = 'You asked how BotOps can restart a stuck runner without losing the queue. ' +
  'Open Settings, then Computers, pick the computer and choose Restart runner; queued_runs_survive_a_restart_because_they_live_in_hub_db_not_in_the_runner.\n\n' +
  '<img src=x onerror="window.pwned=1"> is how the ticket quoted it, and that stays text.\nThanks, Support';
const publish = {id: 'ap-pub', kind: 'approval', what: 'publish', title: 'Approve this publish', requester: 'bot:support',
  requested_by: 'bot:support', created_at: new Date().toISOString(),
  payload: {content_sha256: '355cf' + 'e'.repeat(59), text: REPLY, url: 'hq-ticket:TK-TGYWKV5A'},
  ready_since: new Date(Date.now() - (130 * 60 + 5) * 1000).toISOString()};
const PUBLISH_CLOCK = new Date();
async function phonePublish(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', clock: PUBLISH_CLOCK});
  publish.decision_owner = 'human:' + await page.evaluate(() => S.me.id);
  await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [publish]})}));
  await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
  await page.locator('#task-view [data-view="foryou"]').click();
  const row = page.locator('[data-need-card="approval:ap-pub"]');
  assert.equal(await row.locator('.tl-age').textContent(), '2h');
  await row.click();
  const card = page.locator('#need-card .dc-card');
  // How long its exact payload has been ready, and whose decision it is; readable, whole and inside the phone's width.
  const check = async (box, where, minutes = 10) => {
    assert.equal(await box.locator('.approval-ready').textContent(), `Ready 2 h ${minutes} min, waiting on you`, where);
    const facts = await box.locator('.approval-facts').evaluate(dl => [...dl.children].map(x => x.textContent));
    assert.deepEqual(facts, ['Where', 'Support ticket TK-TGYWKV5A', 'Text', REPLY], where);
    const text = box.locator('.approval-facts dd.approval-text');
    assert.ok(await text.isVisible(), where);
    const fit = await text.evaluate(dd => {
      const r = dd.getBoundingClientRect();
      return {inside: r.left >= 0 && r.right <= innerWidth, wraps: dd.scrollWidth <= dd.clientWidth, lines: r.height / parseFloat(getComputedStyle(dd).lineHeight)};
    });
    assert.deepEqual([fit.inside, fit.wraps], [true, true], `${where}: the text fits 390px`);
    assert.ok(fit.lines > 4, `${where}: the text wraps onto several lines`);
    assert.deepEqual(await box.locator('img, a').count(), 0, `${where}: the payload's markup stays text`);
    const raw = box.locator('details.approval-raw');
    assert.equal(await raw.locator('summary').textContent(), 'Exact payload');
    assert.equal(await raw.evaluate(d => d.open), false, `${where}: the exact payload is folded`);
    assert.deepEqual(JSON.parse(await raw.locator('pre').textContent()), publish.payload);
    await raw.locator('summary').click();
    const pre = await raw.locator('pre').evaluate(el => ({wraps: el.scrollWidth <= el.clientWidth, right: el.getBoundingClientRect().right}));
    assert.ok(pre.wraps && pre.right <= 390, `${where}: the exact payload wraps at 390px`);
  };
  const dc = card;
  assert.equal(await dc.locator('.dc-q').textContent(), 'Publish this?');
  await check(dc.locator('.approval-payload'), 'card');
  // A minute on, the card still open: its line moves on where it stands, and an ordinary refresh keeps it (and the card
  // open) rather than putting back the first age.
  const ready = () => card.locator('.approval-payload .approval-ready').textContent();
  await page.clock.runFor(60000);
  assert.equal(await ready(), 'Ready 2 h 11 min, waiting on you');
  await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
  assert.equal(await ready(), 'Ready 2 h 11 min, waiting on you');
  assert.equal(await page.locator('#need-card').evaluate(d => d.open), true, 'the card stays open');
  await page.clock.runFor(60000);
  assert.equal(await dc.locator('.approval-ready').textContent(), 'Ready 2 h 12 min, waiting on you');
  if (process.env.APPROVAL_SHOT) await dc.screenshot({path: process.env.APPROVAL_SHOT.replace(/(\.png)?$/, '-publish.png')});
  // A web address is a link only when it is http(s); a spend and a merge show their fields; a field the list does not
  // name gets a row of its own (an object or array as its JSON); a publish without its text (or with only whitespace)
  // says only its hash came; any other kind shows its JSON. Each is drawn on the card at 390px and stays inside it.
  const NO_TEXT = 'not included; only a hash of it is in the payload';
  const shown = await page.evaluate(() => [
    {what: 'publish', payload: {url: 'https://acme.example/blog/launch', content_sha256: 'f'.repeat(64)}},
    {what: 'publish', payload: {url: 'javascript:alert(1)', content_sha256: 'f'.repeat(64), text: ''}},
    {what: 'publish', payload: {url: 'hq-ticket:TK-0000AAAA', content_sha256: 'f'.repeat(64), text: ' \n\t '}},
    {what: 'spend', payload: {amount: '$120', account: 'Ads', what: 'Launch week boost'}},
    {what: 'spend', payload: {amount: {value: 120, currency: 'USD'}, account: 'Ads', what: 'Boost', currency: 'USD',
      vendor: '<img src=x onerror="window.pwned=1">', seats: 3, tags: ['launch', 'q4'], note: null}},
    {what: 'merge', payload: {repo: 'acme/site', pr: 42}},
    {what: 'other', payload: {x: 1}},
    {what: 'merge', payload: {repo: 'acme/site', pr: 43}, decision_owner: 'human:sam', decision_owner_name: 'Sam',
      ready_since: new Date(Date.now() - 61 * 60000).toISOString()}].map(it => {
    const node = approvalPayloadNode(it), body = document.querySelector('.decide .dc-body');
    body.replaceChildren(node);
    const fits = [...node.querySelectorAll('.approval-facts dd')].every(dd => dd.getBoundingClientRect().right <= innerWidth && dd.scrollWidth <= dd.clientWidth);
    return {facts: [...node.querySelectorAll('.approval-facts > *')].map(x => x.textContent), fits,
      links: [...node.querySelectorAll('a')].map(a => a.getAttribute('href')), folded: !!node.querySelector('details.approval-raw pre'),
      markup: node.querySelectorAll('img').length, ...(it.ready_since ? {ready: node.querySelector('.approval-ready').textContent} : {})};
  }));
  const ok = {links: [], folded: true, fits: true, markup: 0};
  assert.deepEqual(shown, [
    {...ok, facts: ['Where', 'https://acme.example/blog/launch', 'Text', NO_TEXT], links: ['https://acme.example/blog/launch']},
    {...ok, facts: ['Where', 'javascript:alert(1)', 'Text', NO_TEXT]},
    {...ok, facts: ['Where', 'Support ticket TK-0000AAAA', 'Text', NO_TEXT]},
    {...ok, facts: ['Amount', '$120', 'Account', 'Ads', 'For', 'Launch week boost']},
    {...ok, facts: ['Amount', '{"value":120,"currency":"USD"}', 'Account', 'Ads', 'For', 'Boost', 'currency', 'USD',
      'vendor', '<img src=x onerror="window.pwned=1">', 'seats', '3', 'tags', '["launch","q4"]', 'note', 'null']},
    {...ok, facts: ['Repository', 'acme/site', 'Pull request', '#42']},
    {...ok, facts: [], folded: false},
    {...ok, facts: ['Repository', 'acme/site', 'Pull request', '#43'], ready: 'Ready 1 h 1 min, waiting on Sam'}]);
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  assert.deepEqual(errors, []);
}
const REPORT = 'hub task update has failed since 0.2.17 with: refused (version); a fresh read gives the same error. ' +
  'Tried: hub task show, then the update with the new version; still refused. <script>window.pwned=1</script> stays text.';
const report = {id: 'ap-sup', kind: 'approval', what: 'support', title: 'Send this to Tico support?', requester: 'bot:support',
  requested_by: 'bot:support', created_at: new Date().toISOString(), support: {to: 'updates.tico.team'},
  payload: {message: REPORT, content_sha256: 'e'.repeat(64)}};
async function phoneSupport(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks'});
  await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [report]})}));
  await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
  await page.locator('#task-view [data-view="foryou"]').click();
  const row = page.locator('[data-need-card="approval:ap-sup"]');
  assert.match(await row.locator('.tl-title').textContent(), /^Support: Send this to Tico support\?$/);
  await row.click();
  const facts = ['To', 'Tico support (updates.tico.team)', 'With it', "The Tico version and this install's ID", 'Message', REPORT];
  const check = async (box, where) => {
    assert.deepEqual(await box.locator('.approval-facts').evaluate(dl => [...dl.children].map(x => x.textContent)), facts, where);
    const fit = await box.locator('.approval-facts dd.approval-text').evaluate(dd => {
      const r = dd.getBoundingClientRect();
      return {inside: r.left >= 0 && r.right <= innerWidth, wraps: dd.scrollWidth <= dd.clientWidth};
    });
    assert.deepEqual(fit, {inside: true, wraps: true}, `${where}: the message wraps inside 390px`);
    assert.equal(await box.locator('script, img').count(), 0, `${where}: the message stays text`);
  };
  const dc = page.locator('#need-card .dc-card');
  assert.equal(await dc.locator('.dc-q').textContent(), 'Send this to Tico support?');
  await check(dc.locator('.approval-payload'), 'card');
  // Without the IDs, the card says only the message goes.
  const bare = await page.evaluate(r => [...approvalPayloadNode({...r, payload: {...r.payload, include_ids: false}})
    .querySelectorAll('.approval-facts > *')].map(x => x.textContent), report);
  assert.deepEqual(bare, ['To', 'Tico support (updates.tico.team)', 'With it', 'Nothing else: only the message', 'Message', REPORT]);
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  assert.deepEqual(errors, []);
  await page.close();
}
// The redesign's approvals (CONTRACT §5: task_cap, spend_limit) read as facts with names, the exact payload folded, and
// fit a 390px phone; neither falls back to the bare JSON. ‹ and › walk from one card to the next.
async function phoneRedesignKinds(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks'});
  const at = new Date().toISOString(), ap = (id, what, payload) => ({id, kind: 'approval', what, title: 'Approve this ' + what,
    requester: 'bot:analyst', requested_by: 'bot:analyst', created_at: at, payload});
  const items = [ap('ap-cap', 'task_cap', {sender: 'bot:engineer', receiver: 'bot:support', per_day: 40}),
    ap('ap-lim', 'spend_limit', {bot: 'bot:engineer', daily_usd: 25, monthly_usd: 400})];
  await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
  await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
  await page.locator('#task-view [data-view="foryou"]').click();
  const want = {'ap-cap': ['Task cap: Engineer → Support', ['From', 'To', 'Per day']], 'ap-lim': ['Spend limit: Engineer', ['For', 'Day', 'Month']]};
  assert.deepEqual(await page.locator('[data-need-card] .tl-title').allTextContents(), Object.values(want).map(([t]) => 'Analyst: ' + t),
    'a title that names it');
  await page.locator('[data-need-card="approval:ap-cap"]').click();
  for (const [id, [, labels]] of Object.entries(want)) {
    const card = page.locator(`#need-card .dc-card[data-key="approval:${id}"]`);
    await card.waitFor();
    assert.deepEqual(await card.locator('.approval-facts dt').allTextContents(), labels, id + ': its facts');
    assert.match(await card.locator('.approval-facts').textContent(), /Engineer/, id + ': names, not ids');
    assert.equal(await card.locator('details.approval-raw').evaluate(d => d.open), false, id + ': the payload folded');
    const fits = el => el.getBoundingClientRect().right <= innerWidth && el.scrollWidth <= el.clientWidth + 1;
    assert.equal(await card.evaluate(fits), true, id + ': fits 390px');
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    if (id === 'ap-cap') await page.locator('#need-card [data-dc="next"]').click();
  }
  assert.equal(await page.locator('#need-card [data-dc="next"]').isDisabled(), true, 'the last card has no next');
  assert.deepEqual(errors, []);
  await page.close();
}
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const decided = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [approval]})}));
    const fetched = [];
    await page.route('**/api/v2/files/f-*', route => {
      fetched.push(new URL(route.request().url()).pathname + new URL(route.request().url()).search);
      return route.fulfill({contentType: 'image/png', body: PNG, headers: {'content-disposition': 'inline; filename="chart.png"'}});
    });
    await page.route('**/api/v2/approvals/ap-1', route => { decided.push(route.request().postDataJSON()); return route.fulfill({contentType: 'application/json', body: '{}'}); });
    await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    // One line in the list; nothing unfolds there. A tap opens its card, a modal on a wide screen.
    const row = page.locator('[data-group="asks"] [data-need-card="approval:ap-1"]');
    assert.equal(await page.locator('#task-body details.req').count(), 0, 'no dropdown rows');
    await row.click();
    const card = page.locator('#need-card .dc-card');
    const size = await page.locator('#need-card').evaluate(d => { const r = d.getBoundingClientRect(); return {w: r.width, h: r.height, modal: d.matches(':modal')}; });
    assert.ok(size.modal && size.w <= 480 && size.h < 900, `a modal on a wide screen: ${JSON.stringify(size)}`);
    const box = card.locator('.approval-payload');
    const facts = await box.locator('.approval-facts').evaluate(dl => [...dl.children].map(x => x.textContent));
    assert.deepEqual(facts, ['From', 'ana@acme.example', 'To', 'ava@creator.example',
      'Subject', '<img src=x onerror="window.pwned=1">Contract', 'Draft', 'r-882']);
    const files = await box.locator('.approval-files li').evaluateAll(lis => lis.map(li => ({
      name: li.querySelector('.approval-file-name').textContent, link: li.querySelector('a')?.getAttribute('href') ?? null,
      size: li.querySelector('.approval-file-size').textContent, hash: li.querySelector('.approval-file-hash').textContent,
      title: li.querySelector('.approval-file-hash').title, icon: !!li.querySelector('svg.approval-file-icon'),
      warn: li.querySelector('.approval-file-warn')?.textContent ?? null})));
    const warn = "Not openable: the file on the task doesn't match the approved file";
    assert.deepEqual(files, [
      {name: 'chart.png', link: '/api/v2/files/f-chart-0001?v=1', size: '214 KB', hash: 'aaaaaaaaaaaa', title: 'sha256 ' + HASH, icon: true, warn: null},
      {name: '<b>notes</b>.pdf', link: null, size: '900 B', hash: 'cccccccccccc', title: 'sha256 ' + HASH2, icon: true, warn},
      {name: 'plain.txt', link: null, size: '12 B', hash: 'cccccccccccc', title: 'sha256 ' + HASH2, icon: true, warn: null}]);
    // Any picture is the viewer's own thumbnail of the linked image; the payload's markup stays text.
    assert.deepEqual(await card.locator('img, b').evaluateAll(xs => xs.filter(x => !x.closest('.inline-thumb')).map(x => x.outerHTML)), []);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    const raw = card.locator('details.approval-raw');
    assert.equal(await raw.evaluate(d => d.open), false, 'the raw JSON is folded');
    assert.deepEqual(JSON.parse(await raw.locator('pre').textContent()), approval.payload);
    if (process.env.APPROVAL_SHOT) await card.screenshot({path: process.env.APPROVAL_SHOT});
    await box.locator('a.approval-file-name').click();
    await page.locator('#doc-viewer .viewer-img').waitFor();
    assert.equal(await page.locator('#doc-viewer h2').textContent(), 'chart.png');
    assert.deepEqual([...new Set(fetched)], ['/api/v2/files/f-chart-0001?v=1'], 'the viewer opens the verified version only');
    await page.keyboard.press('Escape');
    await page.locator('#need-card [data-dc="yes"]').click();
    // Answered: out of the list at once, and sent when its Undo runs out (or the card closes).
    await row.waitFor({state: 'detached'});
    await page.locator('#need-card [data-dc="close"]').click();
    for (let i = 0; i < 100 && !decided.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(decided, [{decision: 'approved'}]);
    assert.deepEqual(errors, []);
    await page.close();
    await phonePublish(browser);
    await phoneSupport(browser);
    await phoneRedesignKinds(browser);
    console.log('Needs you approvals: one line each; the card shows the send payload readable, files open, HTML stays text, Approve posts; publish, support, task cap and spend limit readable at 390px');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
