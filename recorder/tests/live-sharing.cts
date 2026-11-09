// Synthetic replay of the real Recorder window. No server, model or bot is contacted.
// Run from the repository root: node recorder/tests/live-sharing.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
async function replay(browser) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const root = path.resolve(__dirname, '../../recorder/ui');
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      assert.equal(url.origin, 'http://recorder.example');
      const file = path.resolve(root, '.' + url.pathname);
      assert.ok(file.startsWith(root + path.sep));
      await route.fulfill({path: file});
    });
    // Disable preview polling: a choice must redraw through the action's own refresh.
    await page.addInitScript(() => { window.setInterval = () => 0; });
    await page.goto('http://recorder.example/preview/index.html?scene=live');
    await page.evaluate(() => {
      const m = window.__PREVIEW__.state.meetings.m2;
      m.live_url = 'https://acme.example/#/meetings?live=synthetic-room';
      m.people.org = 'Acme <test>';
      m.people.team = false;
      const invoke = window.__TAURI__.core.invoke;
      window.opened = [];
      window.__TAURI__.core.invoke = (cmd, args) => {
        if (cmd === 'open_url') { window.opened.push(args.url); return Promise.resolve({}); }
        return invoke(cmd, args);
      };
    });
    await page.locator('.row[data-id="m2"]').click();
    await page.getByRole('button', {name: 'Open in Tico', exact: true}).click();
    assert.deepEqual(await page.evaluate(() => window.opened), ['https://acme.example/#/meetings?live=synthetic-room']);
    // Who can join lives in the Share sheet; a live change applies at once and redraws without polling.
    await page.locator('[data-act="share-open"]').click();
    const sheet = page.locator('#sheet.share');
    const radio = team => sheet.locator(`[data-act="share-team"][data-team="${team}"]`);
    await radio(false).and(page.locator('[aria-checked="true"]')).waitFor();
    await radio(true).click();
    await radio(true).and(page.locator('[aria-checked="true"]')).waitFor();
    await page.locator('[data-act="share-open"]').filter({hasText: 'Anyone on Acme <test>'}).waitFor();
    assert.equal(await page.locator('#app test').count(), 0);
    await radio(false).click();
    await radio(false).and(page.locator('[aria-checked="true"]')).waitFor();
    console.log('PASS: synthetic Open in Tico click; Only people I add → Anyone → Only people I add without polling; escaped org name. 0 failures.');
  } finally { await context.close(); }
}
module.exports = {replay};
if (require.main === module) {
  (async () => {
    const browser = await chromium.launch({headless: true});
    try { await replay(browser); }
    finally { await browser.close(); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
