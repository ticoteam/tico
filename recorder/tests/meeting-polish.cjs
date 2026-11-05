// Recorder meeting polish: task search that follows the typing, a type filter, obvious picks, @me in Add a task,
// Enter to add, the transcript's copy button, and Stop as the one end action.
require('../../ui/tests/support/browser.cjs');
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');

async function open(browser, scene, errors) {
  const page = await browser.newPage();
  page.on('pageerror', e => errors.push(e.message));
  const root = path.resolve(__dirname, '../ui');
  await page.route('http://recorder.example/**', route => {
    const file = path.resolve(root, '.' + new URL(route.request().url()).pathname);
    assert.ok(file.startsWith(root + path.sep));
    return route.fulfill({path:file});
  });
  await page.goto(`http://recorder.example/preview/index.html?scene=${scene}`);
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text => { window.copiedText = text; }}, configurable:true});
  });
  return page;
}
const rows = page => page.locator('#sheet [data-act="task-pick"] .grow').allInnerTexts();

(async () => {
  const browser = await chromium.launch({headless:true});
  const errors = [];
  try {
    // ---- Link tasks ----
    let page = await open(browser, 'library', errors);
    await page.click('.row[data-id="m1"]');
    await page.getByRole('button', {name:'Link', exact:true}).click();
    await page.locator('#sheet [data-act="task-pick"]').first().waitFor();
    assert.equal((await rows(page)).length, 4);
    // A slow reply for an older query never lands over a newer one, and rows dim the moment typing starts.
    await page.evaluate(() => {
      const original = window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke = async (cmd, args) => {
        if (cmd === 'task_picker' && args.query === 'pric') await new Promise(r => setTimeout(r, 900));
        return original(cmd, args);
      };
    });
    const search = page.getByRole('textbox', {name:'Search tasks'});
    await search.pressSequentially('pric');
    assert.equal(await page.locator('#sheet .pop-list.stale').count(), 1);
    await page.waitForTimeout(400);
    await search.pressSequentially('ing rounding');
    await page.waitForFunction(() => !document.querySelector('#sheet .pop-list.stale'));
    await page.waitForTimeout(1000);
    assert.deepEqual(await rows(page), ['#45 Fix pricing rounding · open']);
    assert.equal(await search.inputValue(), 'pricing rounding');
    // A bare number finds that task.
    await search.fill('43');
    await page.waitForFunction(() => document.querySelectorAll('#sheet [data-act="task-pick"]').length === 1 && !document.querySelector('#sheet .pop-list.stale'));
    assert.deepEqual(await rows(page), ['#43 Test recovery · review']);
    // Type filter.
    await search.fill('pricing');
    await page.waitForFunction(() => document.querySelectorAll('#sheet [data-act="task-pick"]').length === 3 && !document.querySelector('#sheet .pop-list.stale'));
    await page.getByRole('combobox', {name:'Type'}).selectOption('bug');
    await page.waitForFunction(() => document.querySelectorAll('#sheet [data-act="task-pick"]').length === 1);
    assert.deepEqual(await rows(page), ['#45 Fix pricing rounding · open']);
    // Picks are marked, counted on Link, and stay listed while the search moves on.
    const link = page.locator('#sheet [data-act="task-picker-link"]');
    assert.equal(await link.innerText(), 'Link');
    assert.ok(await link.isDisabled());
    await page.click('#sheet [data-id="t45"]');
    assert.equal(await page.locator('#sheet [data-id="t45"].on[aria-pressed="true"]').count(), 1);
    assert.equal(await link.innerText(), 'Link 1');
    await page.getByRole('combobox', {name:'Type'}).selectOption('');
    await search.fill('property');
    await page.waitForFunction(() => document.querySelector('#sheet [data-id="t44"]') && !document.querySelector('#sheet .pop-list.stale'));
    assert.deepEqual(await rows(page), ['#45 Fix pricing rounding · open', '#44 Property manager bots: pricing page · doing']);
    await page.click('#sheet [data-id="t44"]');
    assert.equal(await link.innerText(), 'Link 2');
    await page.getByRole('button', {name:'Cancel', exact:true}).click();

    // ---- Add a task: @me, Enter adds ----
    await page.getByRole('button', {name:'Add', exact:true}).click();
    const add = page.getByRole('textbox', {name:'Add a task', exact:true});
    await add.pressSequentially('Send notes to @me');
    await page.locator('#mention [data-opt]').first().waitFor();
    assert.equal(await page.locator('#mention [data-opt] .grow').first().innerText(), 'Tina Alvarez');
    await page.keyboard.press('Enter');
    assert.equal(await add.inputValue(), 'Send notes to @Tina Alvarez ');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.__PREVIEW__.state.meetings.m1.actions.items.some(i => i.title === 'Send notes to @Tina Alvarez'));
    assert.equal(await add.inputValue(), '');
    // With nothing typed after @, the person is first too.
    await add.pressSequentially('@');
    assert.equal(await page.locator('#mention [data-opt] .grow').first().innerText(), 'Tina Alvarez');
    await page.keyboard.press('Escape');

    // ---- Copy transcript from the header ----
    const copy = page.locator('.side-head [data-act="copy-transcript"]');
    assert.equal(await copy.getAttribute('aria-label'), 'Copy transcript');
    await copy.click();
    await page.waitForFunction(() => window.copiedText);
    const copied = await page.evaluate(() => window.copiedText);
    assert.match(copied, /^\[\d+:\d\d\] \S/);
    await page.locator('.side-head [data-act="copy-transcript"][aria-label="Copied"]').waitFor();
    await page.close();

    // ---- Stop, not shared: the recording stops and Finish asks where it goes ----
    page = await open(browser, 'recording', errors);
    await page.click('.row[data-id="m0"]');
    await page.waitForSelector('.title-input');
    assert.equal(await page.locator('.topbar [data-act="finish-open"]').count(), 0);
    await page.click('[data-act="stop"]');
    await page.waitForSelector('#sheet.finish');
    assert.equal(await page.evaluate(() => window.__PREVIEW__.state.status.recording), null);
    await page.keyboard.press('Escape');
    // Stopped but not saved: Finish stays so nothing is stuck.
    await page.locator('.topbar [data-act="finish-open"]').waitFor();
    await page.close();

    // ---- Stop, shared live: it is saved where it is shared, in one step ----
    page = await open(browser, 'live', errors);
    await page.evaluate(() => {
      const s = window.__PREVIEW__.state;
      s.status.recording = {id:'m2', elapsed_ms:1000, phase:'recording', error:'', sources:[], stream:[]};
      Object.assign(s.meetings.m2, {state:'recording', recording_here:true});
      const original = window.__TAURI__.core.invoke;
      window.__TAURI__.core.invoke = async (cmd, args) => {
        if (cmd === 'save_meeting') window.saved = args;
        return original(cmd, args);
      };
    });
    await page.click('.row[data-id="m2"]');
    await page.waitForSelector('.title-input');
    assert.equal(await page.locator('.topbar [data-act="finish-open"]').count(), 0);
    await page.click('[data-act="stop"]');
    await page.waitForSelector('.topbar .badge:has-text("In Tico")', {timeout:8000});
    const saved = await page.evaluate(() => window.saved);
    assert.equal(saved.destination, 'team');
    assert.equal(saved.audience, 'team');
    assert.equal(await page.locator('#sheet.finish').count(), 0);
    await page.close();

    assert.deepEqual(errors, []);
    console.log('Recorder: task search, picks, @me, Enter, copy transcript and Stop passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
