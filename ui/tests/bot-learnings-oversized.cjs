// Exercise the actual panel with current, stale and never-saved files, including its entry button.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    await page.setContent('<button id="bot-learn" hidden><span class="bl-n"></span></button>');
    await page.addStyleTag({path: path.join(__dirname, '../styles/bot-learnings.css')});
    await page.evaluate(() => {
      window.$ = (selector, root = document) => root.querySelector(selector);
      window.esc = value => String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
      window.md = text => `<p>${esc(text)}</p>`;
      window.BOT = {slug: 'ops'};
      window.post = async () => ({});
      window.toast = () => {};
    });
    await page.addScriptTag({path: path.join(__dirname, '../app/bot-learnings.js')});
    for (const width of [1100, 390]) {
      await page.setViewportSize({width, height: 800});
      for (const file of ['memory/learnings.md', 'memory/decisions.md']) {
        const tab = `[data-learn-view="${file}"]`;
        await page.evaluate(file => learnOpen('ops', {
          documents: {[file]: 'The saved copy'},
          document_status: {[file]: {oversized: true, updated_at: '2026-10-26T10:00:00Z'}},
        }), file);
        await page.locator(tab).click();
        assert.equal(await page.locator('.learn-warning').count(), 1);
        assert.ok((await page.locator('.learn-warning').innerText()).includes('2026'));
        assert.equal(await page.locator('.learn-body .md').innerText(), 'The saved copy');
        assert.ok(await page.locator('.learn-warning').evaluate(el => Boolean(el.compareDocumentPosition(el.nextElementSibling) & Node.DOCUMENT_POSITION_FOLLOWING)));
        await page.locator('[data-learn-close]').click();
        await page.evaluate(file => learnOpen('ops', {
          documents: {[file]: 'The current copy'}, document_status: {[file]: {oversized: false, updated_at: '2026-10-27T10:00:00Z'}},
        }), file);
        await page.locator(tab).click();
        assert.equal(await page.locator('.learn-warning').count(), 0);
        assert.equal(await page.locator('.learn-body .md').innerText(), 'The current copy');
        await page.locator('[data-learn-close]').click();
      }
    }
    // The button loads only status. Opening refreshes the actual file; More fetches only history.
    await page.evaluate(async () => {
      window.reads = [];
      const status = {'memory/learnings.md': {oversized: false, updated_at: '2026-10-27T10:00:00Z'}};
      const row = subject => ({subject, author: 'ops', when: '2026-10-27T10:00:00Z', files: ['memory/learnings.md']});
      window.ago = () => 'today';
      window.get = async url => {
        reads.push(url);
        if (url.includes('before=')) return {updates: [row('Older update')], next_before: null};
        if (url.includes('include_documents=false')) return {documents: {}, document_status: status};
        return {documents: {'memory/learnings.md': 'Latest copy fetched on open'}, document_status: status,
          updates: [row('Latest update')], next_before: '2026-10-26T10:00:00Z'};
      };
      await botLearningsLoad('ops');
    });
    assert.equal((await page.evaluate(() => reads)).length, 1);
    assert.ok((await page.evaluate(() => reads))[0].includes('include_documents=false'));
    assert.equal(await page.locator('#bot-learn').isVisible(), true, 'saved status exposes a file without downloading it');
    await page.locator('#bot-learn').click();
    await page.locator('[data-learn-more]').click();
    await page.locator('.learn-row').nth(1).waitFor();
    const reads = await page.evaluate(() => reads);
    assert.equal(reads.length, 3);
    assert.ok(!reads[1].includes('include_documents=false'), 'opening requests current contents');
    assert.ok(reads[2].includes('include_documents=false'), 'More does not re-download the documents');
    await page.locator('[data-learn-view="memory/learnings.md"]').click();
    assert.equal(await page.locator('.learn-body .md').innerText(), 'Latest copy fetched on open');
    await page.locator('[data-learn-close]').click();
    // No updates and no saved text: the warning alone still exposes the button and tab.
    await page.evaluate(async () => {
      window.get = async () => ({documents: {}, document_status: {'memory/learnings.md': {oversized: true, updated_at: ''}}});
      await botLearningsLoad('ops');
    });
    assert.equal(await page.locator('#bot-learn').isVisible(), true);
    await page.locator('#bot-learn').click();
    await page.locator('[data-learn-view="memory/learnings.md"]').click();
    assert.equal(await page.locator('.learn-warning').count(), 1);
    assert.equal(await page.locator('.learn-warning time').count(), 0);
    assert.equal(await page.locator('.learn-body .md').innerText(), '');
    await page.locator('[data-learn-close]').click();
    // Old servers return no metadata; existing documents remain usable without a warning.
    await page.evaluate(() => learnOpen('ops', {documents: {'memory/learnings.md': 'Old server'}}));
    await page.locator('[data-learn-view="memory/learnings.md"]').click();
    assert.equal(await page.locator('.learn-warning').count(), 0);
    console.log('bot-learnings-oversized: 4 stale/current view pairs, missing copy, old server and lazy loading/pagination passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
