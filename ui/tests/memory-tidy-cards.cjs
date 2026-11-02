// Standalone Nightly memory tidy cards use the usual review screen, not task/approval endpoints.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', baseTasks: []});
    const it = {id: 'tidy-1', kind: 'memory_tidy', bot: 'writer', requester: 'bot:writer', reviewer: 'human:ana',
      title: 'Nightly memory tidy: writer', model: 'codex:gpt-6.1-sol', cost_usd: .125, memory_commit: 'a'.repeat(40),
      diary: 'Merged duplicate rules.\nArchived old notes.\nChecks passed.', created: new Date().toISOString(),
      expires: new Date(Date.now() + 4 * 86400000).toISOString(), status: 'open', reason: '', keep_available: false};
    const diff = '-Old rule\n+<img src=x onerror="window.pwned=1">';
    let items = [it], reads = 0;
    const sent = [];
    await page.route('**/api/v2/needs-you*', r => r.fulfill({json: {items}}));
    await page.route('**/api/v2/memory-tidy/cards/tidy-1', r => { reads++; return r.fulfill({json: {card: {...it, diff}}}); });
    await page.route('**/api/v2/memory-tidy/cards/tidy-1/decide', r => {
      sent.push(r.request().postDataJSON()); items = [];
      return r.fulfill({json: {card: {...it, diff, status: 'discarded'}}});
    });
    await page.evaluate(async () => { await v2Refresh(); needsListsRedraw(); location.hash = '#/decide?item=tidy-1'; });
    const card = page.locator('#need-card .dc-card[data-key="memory_tidy:tidy-1"]');
    await card.waitFor();
    await card.locator('.dc-body details pre').filter({hasText: diff}).waitFor();
    const text = await card.innerText();
    assert.match(text, /Nightly memory tidy/);
    assert.match(text, /codex:gpt-6.1-sol · Estimated cost \$0.1250/);
    assert.ok(text.includes('a'.repeat(40)));
    assert.ok(text.includes(it.diary));
    assert.equal(await card.locator('.dc-body img').count(), 0, 'diff is escaped text');
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    await page.locator('#need-card .dc-actions button:has-text("Keep")').isDisabled().then(v => assert.ok(v));
    assert.equal(reads, 1, 'diff is fetched when opened');
    if (process.env.MEMORY_TIDY_SHOT) await page.screenshot({path: process.env.MEMORY_TIDY_SHOT});
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    const written = page.waitForResponse(r => r.url().endsWith('/memory-tidy/cards/tidy-1/decide'));
    await page.locator('#need-card .dc-actions [data-dc="no"]').click();
    await written;
    await page.waitForFunction(() => DEC_OUTBOX.size === 0);
    assert.deepEqual(sent, [{decision: 'discard'}]);
    assert.deepEqual(errors, []);
    console.log('memory-tidy-cards: passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
