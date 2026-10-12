// A send approval in Needs you: recipients, subject, draft and each attached file readable (name, size, short hash),
// the exact payload folds under Details, and a payload value is only ever text. A file links only to the version the
// server verified (`attachment_files`), pinned with ?v=, and opens that version in the viewer; a payload file_id the
// server did not verify is no link and says so. A Support reply to publish, on a 390px phone, says which ticket and
// shows its whole text wrapped (in Needs you and in Decide), its HTML-looking text stays text, and the exact payload
// (with the content hash) folds under Exact payload. A field the facts list does not name gets its own row, and a
// publish without its text says so.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const HASH = 'a'.repeat(12) + 'b'.repeat(52), HASH2 = 'c'.repeat(64);
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const approval = {id: 'ap-1', kind: 'approval', what: 'send', title: 'Approve this send', requester: 'bot:support',
  requested_by: 'bot:support', created: new Date().toISOString(), conversation_id: 'c-1',
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
  requested_by: 'bot:support', created: new Date().toISOString(),
  payload: {content_sha256: '355cf' + 'e'.repeat(59), text: REPLY, url: 'hq-ticket:TK-TGYWKV5A'}};
async function phonePublish(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks'});
  await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [publish]})}));
  await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
  await page.locator('#task-view [data-view="foryou"]').click();
  const card = page.locator('[data-group="approvals"] details.req');
  await card.locator('> summary').click();
  // Readable, whole and inside the phone's width, in Needs you and then on Decide's card.
  const check = async (box, where) => {
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
  await check(card.locator('.approval-payload'), 'Needs you');
  await page.evaluate(() => { location.hash = '#/decide'; });
  const dc = page.locator('.decide .dc-card');
  await dc.waitFor();
  assert.equal(await dc.locator('.dc-q').textContent(), 'Publish this?');
  await check(dc.locator('.approval-payload'), 'Decide');
  if (process.env.APPROVAL_SHOT) await dc.screenshot({path: process.env.APPROVAL_SHOT.replace(/(\.png)?$/, '-publish.png')});
  // A web address is a link only when it is http(s); a spend and a merge show their fields; a field the list does not
  // name gets a row of its own (an object or array as its JSON); a publish without its text says only its hash came;
  // any other kind shows its JSON. Each is drawn on Decide's card at 390px and stays inside it.
  const NO_TEXT = 'not included; only a hash of it is in the payload';
  const shown = await page.evaluate(() => [
    {what: 'publish', payload: {url: 'https://acme.example/blog/launch', content_sha256: 'f'.repeat(64)}},
    {what: 'publish', payload: {url: 'javascript:alert(1)', content_sha256: 'f'.repeat(64), text: ''}},
    {what: 'spend', payload: {amount: '$120', account: 'Ads', what: 'Launch week boost'}},
    {what: 'spend', payload: {amount: {value: 120, currency: 'USD'}, account: 'Ads', what: 'Boost', currency: 'USD',
      vendor: '<img src=x onerror="window.pwned=1">', seats: 3, tags: ['launch', 'q4'], note: null}},
    {what: 'merge', payload: {repo: 'acme/site', pr: 42}},
    {what: 'other', payload: {x: 1}}].map(it => {
    const node = approvalPayloadNode(it), body = document.querySelector('.decide .dc-body');
    body.replaceChildren(node);
    const fits = [...node.querySelectorAll('.approval-facts dd')].every(dd => dd.getBoundingClientRect().right <= innerWidth && dd.scrollWidth <= dd.clientWidth);
    return {facts: [...node.querySelectorAll('.approval-facts > *')].map(x => x.textContent), fits,
      links: [...node.querySelectorAll('a')].map(a => a.getAttribute('href')), folded: !!node.querySelector('details.approval-raw pre'),
      markup: node.querySelectorAll('img').length};
  }));
  const ok = {links: [], folded: true, fits: true, markup: 0};
  assert.deepEqual(shown, [
    {...ok, facts: ['Where', 'https://acme.example/blog/launch', 'Text', NO_TEXT], links: ['https://acme.example/blog/launch']},
    {...ok, facts: ['Where', 'javascript:alert(1)', 'Text', NO_TEXT]},
    {...ok, facts: ['Amount', '$120', 'Account', 'Ads', 'For', 'Launch week boost']},
    {...ok, facts: ['Amount', '{"value":120,"currency":"USD"}', 'Account', 'Ads', 'For', 'Boost', 'currency', 'USD',
      'vendor', '<img src=x onerror="window.pwned=1">', 'seats', '3', 'tags', '["launch","q4"]', 'note', 'null']},
    {...ok, facts: ['Repository', 'acme/site', 'Pull request', '#42']},
    {...ok, facts: [], folded: false}]);
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  assert.deepEqual(errors, []);
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
    const card = page.locator('[data-group="approvals"] details.req');
    await card.locator('> summary').click();
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
    await card.locator('[data-v2-approval="ap-1"][data-v2-decision="approved"]').click();
    for (let i = 0; i < 50 && !decided.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(decided, [{decision: 'approved'}]);
    assert.deepEqual(errors, []);
    await page.close();
    await phonePublish(browser);
    console.log('Needs you approvals: send payload readable, files open, HTML stays text, Approve posts; publish readable at 390px');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
