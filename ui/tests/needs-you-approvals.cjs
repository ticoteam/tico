// A send approval in Needs you: recipients, subject, draft and each attached file readable (name, size, short hash),
// a file on the task opens in the viewer, the exact payload folds under Details, and a payload value is only ever text.
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
      {name: '<b>notes</b>.pdf', size: 900, sha256: HASH2, file_id: '../../api/me'}]}};
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const decided = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [approval]})}));
    await page.route('**/api/v2/files/f-chart-0001', route => route.fulfill({contentType: 'image/png', body: PNG,
      headers: {'content-disposition': 'inline; filename="chart.png"'}}));
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
      title: li.querySelector('.approval-file-hash').title, icon: !!li.querySelector('svg.approval-file-icon')})));
    assert.deepEqual(files, [
      {name: 'chart.png', link: '/api/v2/files/f-chart-0001', size: '214 KB', hash: 'aaaaaaaaaaaa', title: 'sha256 ' + HASH, icon: true},
      {name: '<b>notes</b>.pdf', link: null, size: '900 B', hash: 'cccccccccccc', title: 'sha256 ' + HASH2, icon: true}]);
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
    await page.keyboard.press('Escape');
    await card.locator('[data-v2-approval="ap-1"][data-v2-decision="approved"]').click();
    for (let i = 0; i < 50 && !decided.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(decided, [{decision: 'approved'}]);
    assert.deepEqual(errors, []);
    console.log('Needs you approvals: send payload readable, files open, HTML stays text, Approve posts');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
