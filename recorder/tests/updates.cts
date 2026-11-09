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
    assert.equal(await page.getByText('Move Tico Recorder to Applications so it can update.').count(), 0);
    await page.close();

    // First launch from a disk image: one prompt, Move or Not now. Not now leaves the smaller banner.
    const PROMPT = 'Move Tico Recorder to Applications so it can update.';
    page = await open(context, 'scene=update-first');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    const prompt = page.locator('.update-banner.update-prompt');
    await prompt.getByText(PROMPT).waitFor();
    assert.equal(await prompt.getByRole('button').count(), 2);
    await prompt.getByRole('button', {name: 'Not now'}).click();
    await prompt.waitFor({state: 'detached'});
    await page.locator('.update-banner').getByText("Can't update from here").waitFor();
    await page.locator('.update-banner').getByRole('button', {name: 'Move to Applications'}).waitFor();
    // A new launch asks again.
    await page.reload();
    await prompt.getByText(PROMPT).waitFor();
    await page.close();

    // Move uses the same path: a newer copy asks first.
    page = await open(context, 'scene=update-first&newer');
    await page.locator('.update-prompt').getByRole('button', {name: 'Move', exact: true}).click();
    await page.locator('.update-prompt').getByText('Applications has 0.5.150').waitFor();
    await page.locator('.update-prompt').getByRole('button', {name: 'Replace'}).click();
    assert.deepEqual(await page.evaluate(() => window.moves), ['copy', 'replace']);
    await page.close();

    // A recording starting first: the prompt goes and does not come back this launch.
    page = await open(context, 'scene=update-first');
    await page.locator('.update-prompt').getByText(PROMPT).waitFor();
    await page.evaluate(() => { window.__PREVIEW__.state.status.update.held = true; });
    await page.locator('.update-banner').waitFor({state: 'hidden'});
    await page.evaluate(() => { window.__PREVIEW__.state.status.update.held = false; });
    await page.locator('.update-banner').getByText("Can't update from here").waitFor();
    assert.equal(await page.locator('.update-prompt').count(), 0);
    await page.close();

    // No prompt and no banner for a copy that can update (in Applications).
    page = await open(context, 'scene=library');
    await page.waitForTimeout(700);
    assert.equal(await page.locator('.update-banner').isVisible(), false);
    await page.close();

    // Update ready while recording: no banner. Once idle: the notes line and Restart.
    page = await open(context, 'scene=update');
    await page.waitForTimeout(700);
    assert.equal(await page.locator('.update-banner').isVisible(), false);
    await page.evaluate(() => Object.assign(window.__PREVIEW__.state.status.update, {held: false, blocked: ''}));
    const ready = page.locator('.update-banner');
    await ready.getByText('Update ready').waitFor();
    await ready.locator('.update-note').getByText('Live text through your Tico.').waitFor();
    await ready.getByRole('button', {name: 'Restart'}).waitFor();
    // Shared live again (or sending): it goes away.
    await page.evaluate(() => { window.__PREVIEW__.state.status.update.held = true; });
    await ready.waitFor({state: 'hidden'});
    await page.close();

    // The build placeholder never shows as notes.
    page = await open(context, 'scene=update-ready&built');
    await page.locator('.update-banner').getByText('Update ready').waitFor();
    assert.equal(await page.locator('.update-note').count(), 0);
    assert.equal(await page.getByText(/Built from/).count(), 0);
    await page.close();
    page = await open(context, 'scene=update-ready');
    await page.locator('.update-note').getByText('Clearer task linking, @me, copy transcript and Check for updates.').waitFor();
    console.log('PASS: Check for updates states, Up to date, check error, Move to Applications (ask on newer, dismiss once), first-launch prompt (Not now, Move, passes on recording, never in Applications), quiet while held, notes vs Built from. 0 failures.');
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
