// Synthetic replay of the Recorder's Updates row and Move to Applications prompt. No server is contacted.
// Run from the repository root: node recorder/tests/updates.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');

async function open(context, query) {
  const page = await context.newPage();
  const root = path.resolve(__dirname, '../../recorder/ui');
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, 'http://recorder.example');
    const file = path.resolve(root, '.' + url.pathname);
    assert.ok(file.startsWith(root + path.sep));
    await route.fulfill({path: file});
  });
  await page.goto(`http://recorder.example/preview/index.html?${query}`);
  return page;
}

async function replay(browser) {
  const context = await browser.newContext();
  try {
    // Check for updates: Checking…, Downloading, then Update ready with Restart.
    let page = await open(context, 'scene=library');
    await page.locator('.topbar [data-act="settings"]').last().click();
    const row = page.locator('#updates .update-row');
    await row.getByText('Version 0.5.143').waitFor();
    await row.getByRole('button', {name: 'Check for updates'}).click();
    await row.getByText('Checking…').waitFor();
    await row.getByText('Update ready · 0.5.150').waitFor();
    await row.getByRole('button', {name: 'Restart'}).waitFor();
    await page.close();

    // Up to date.
    page = await open(context, 'scene=library&latest');
    await page.locator('.topbar [data-act="settings"]').last().click();
    await page.locator('#updates').getByRole('button', {name: 'Check for updates'}).click();
    await page.locator('#updates').getByText('Up to date').waitFor();
    await page.close();

    // A failed check says so.
    page = await open(context, 'scene=update-error');
    await page.locator('.topbar [data-act="settings"]').last().click();
    await page.locator('#updates').getByText("Couldn't check for updates").waitFor();
    await page.close();

    // A copy that cannot update: the launch prompt and the row offer Move to Applications; a newer copy asks first.
    page = await open(context, 'scene=update-move&newer');
    const banner = page.locator('.update-banner');
    await banner.getByText("Can't update from here").waitFor();
    await banner.getByRole('button', {name: 'Move to Applications'}).click();
    await banner.getByText('Applications has 0.5.150').waitFor();
    await banner.getByRole('button', {name: 'Replace'}).click();
    assert.deepEqual(await page.evaluate(() => window.moves), ['copy', 'replace']);
    await page.locator('.topbar [data-act="settings"]').last().click();
    const moveRow = page.locator('#updates');
    await moveRow.getByText("Can't update from here").waitFor();
    assert.equal(await moveRow.getByRole('button', {name: 'Check for updates'}).count(), 0);
    // Dismissed once, the prompt stays away on the next launch; Settings still offers the move.
    await banner.getByRole('button', {name: 'Dismiss'}).click();
    await banner.waitFor({state: 'hidden'});
    await page.close();
    page = await open(context, 'scene=update-move');
    await page.locator('.topbar [data-act="settings"]').last().click();
    await page.locator('#updates').getByRole('button', {name: 'Move to Applications'}).click();
    assert.deepEqual(await page.evaluate(() => window.moves), ['copy']);
    assert.equal(await page.locator('.update-banner').isVisible(), false);
    console.log('PASS: Check for updates states, Up to date, check error, Move to Applications (ask on newer, dismiss once). 0 failures.');
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
