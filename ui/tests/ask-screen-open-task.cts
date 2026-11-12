// A task that waits on the person's own answer opens on its ask (the ask screen, ui/app/decide.ts), from wherever it is
// opened; the ask screen links to the task's own page; and a note can carry screenshots and files.
// - opened by a #/task/ link, a list row's open (taskModalOpen) or a link in chat: the ask screen on that ask, not the task page;
// - a task waiting on someone else opens the task page as before;
// - "Open task #N ↗" opens the task page, which stays open; closing it leaves no ask screen behind;
// - Notes is 7 lines in a 420 px column, and grows with its text before it scrolls;
// - a pasted screenshot and a picked file show under the note with ✕; copied text pastes as text;
// - Send back waits for the uploads ("Uploading 1 of 2…"), names a failed one with Retry and sends nothing, then sends
//   the note ending with each file's name and id;
// - on a 390 px phone Notes starts at 4 lines and nothing scrolls sideways.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const card = (page: Page) => page.locator('#need-card[open]');
const taskPage = (page: Page) => page.locator('#task-modal[open] .tmodal-title');
const lineHeight = (page: Page) => page.locator('#dc-notes').evaluate(el => parseFloat(getComputedStyle(el).lineHeight) || 20);
/** Pastes into the focused note what a clipboard holds: files, text, or both. */
const paste = (page: Page, files: {name: string; type: string}[], text = '') => page.locator('#dc-notes').evaluate((el, {files, text}) => {
  const dt = new DataTransfer();
  for (const f of files) dt.items.add(new File([new Uint8Array([137, 80, 78, 71])], f.name, {type: f.type}));
  if (text) dt.setData('text/plain', text);
  (el as HTMLTextAreaElement).focus();
  const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
  el.dispatchEvent(ev);
  return ev.defaultPrevented;
}, {files, text});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Opened by its address (a link, a notification): the ask screen on t-1's OK, with the same top bar as from Needs you.
    {
      const {page, errors} = await open(browser, {hash: '#/task/t-1'});
      try {
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Approve the spec for Checkout flow');
        assert.match(await card(page).locator('.dc-count').innerText(), /^\d of 3$/, 'walks the whole of Needs you, as from Needs you');
        assert.equal(await page.locator('#task-modal[open]').count(), 0, 'the task page does not open under it');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A list row or a board card opens a task with taskModalOpen (tasks-page.ts); a chat link with taskModalShow.
    {
      const {page, errors} = await open(browser);
      try {
        await page.evaluate(() => { void taskModalOpen('t-2'); });
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Which plan do new signups get?');
        await card(page).locator('[data-dc="close"]').click();
        // Not waiting on Ana: the task page, as today.
        await page.evaluate(() => { void taskModalOpen('t-4'); });
        await taskPage(page).waitFor({timeout: 5000});
        assert.equal(await card(page).count(), 0);
        await page.keyboard.press('Escape');
        // A link to the task in a message.
        await page.evaluate(() => {
          const md = document.createElement('div'); md.className = 'md'; md.style.cssText = 'position:fixed;top:200px;left:700px;z-index:9';
          md.innerHTML = '<a href="#/task/t-1">Checkout flow</a>';
          document.body.appendChild(md);
        });
        await page.locator('.md a[href="#/task/t-1"]').click();
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Approve the spec for Checkout flow');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Open task ↗: the task page opens and stays open; closing it leaves no ask screen behind.
    {
      const {page, errors} = await open(browser);
      try {
        await page.evaluate(() => { void taskModalOpen('t-1'); });
        await card(page).waitFor({timeout: 5000});
        const link = card(page).locator('[data-dc="task"]');
        assert.equal(await link.innerText(), 'Open task #1 ↗');
        await link.click();
        await taskPage(page).waitFor({timeout: 5000});
        assert.equal(await taskPage(page).innerText(), 'Checkout flow');
        await page.waitForTimeout(400);
        assert.equal(await card(page).count(), 0, 'the ask screen does not come back over the task page');
        assert.equal(await taskPage(page).count(), 1, 'the task page stays open');
        // Its Answer goes back to the ask.
        await page.locator('#task-modal[open] [data-task-answer]').click();
        await card(page).waitFor({timeout: 5000});
        await card(page).locator('[data-dc="close"]').click();
        await page.locator('#task-modal [data-modal-close]').click();
        await page.waitForTimeout(200);
        assert.equal(await card(page).count() + await page.locator('#task-modal[open]').count(), 0, 'back where it started');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Notes: 420 px column, 7 lines, grows before it scrolls. Screenshots and files: shown with ✕, uploaded to the task,
    // waited for, named when one fails, and listed by name and id at the end of the note that is sent.
    {
      let gate: () => void = () => {};
      let fail = true, uploads = 0;
      const {page, errors, posts} = await open(browser, {viewport: {width: 1040, height: 760}, beforeGoto: async p => {
        await p.route(u => /^\/api\/v2\/tasks\/t-1\/files$/.test(u.pathname), async route => {
          if (route.request().method() !== 'POST') return route.fallback();
          const n = ++uploads, body = route.request().postDataBuffer()?.toString('latin1') || '';
          const name = /filename="([^"]+)"/.exec(body)?.[1] || 'file';
          if (name.startsWith('notes')) {
            if (fail) { fail = false; return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({error: 'internal', detail: 'Disk full'})}); }
          } else await new Promise<void>(resolve => { gate = resolve; });
          return route.fulfill({contentType: 'application/json', body: JSON.stringify({file_id: `file-${n}`, version: 1, file: {name}})});
        });
      }});
      try {
        await page.evaluate(() => { void taskModalOpen('t-1'); });
        await card(page).waitFor({timeout: 5000});
        const notes = page.locator('#dc-notes'), lh = await lineHeight(page);
        const side = await page.locator('.decide .dc-side').boundingBox();
        assert.ok(side && Math.abs(side.width - 420) <= 1, `the answer column is 420 px wide, got ${side?.width}`);
        await notes.fill('x'.repeat(300));
        const small = (await notes.boundingBox())!;
        assert.ok(small.width >= 420 - 2 * 20 - 2, `Notes is the column wide minus padding, got ${small.width}`);
        assert.ok(small.height >= 7 * lh, `Notes is at least 7 lines tall, got ${small.height} for ${lh} px lines`);
        await notes.fill('word '.repeat(200));
        const big = (await notes.boundingBox())!;
        assert.ok(big.height > small.height + lh, `Notes grew with 1000 characters, ${small.height} -> ${big.height}`);

        // Text on the clipboard pastes as text: the page does not take it.
        assert.equal(await paste(page, [], 'plain words'), false);
        // A screenshot pasted, then a file picked with 📎.
        await paste(page, [{name: 'image.png', type: 'image/png'}]);
        await page.locator('.dc-attach input[type=file]').setInputFiles({name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello')});
        const rows = card(page).locator('.dc-file');
        await page.waitForFunction(() => document.querySelectorAll('.dc-file').length === 2);
        assert.match(await rows.nth(0).locator('.dc-file-name').innerText(), /^screenshot-\d{8}-\d{6}\.png$/);
        assert.equal(await rows.nth(1).locator('.dc-file-name').innerText(), 'notes.txt');
        assert.equal(await rows.locator('[data-dc="file-drop"]').count(), 2, 'each file has ✕');

        // The picked file failed: Send back names it, with Retry, and sends nothing.
        await page.locator('.dc-file[data-state="failed"]').waitFor({timeout: 5000});
        await notes.fill('Needs a mobile layout');
        await card(page).locator('[data-dc="sendback"]').click();
        await card(page).locator('.err', {hasText: 'notes.txt did not upload'}).waitFor({timeout: 5000});
        assert.equal(posts.filter(x => x.p.endsWith('/answer')).length, 0, 'nothing sent while a file failed');
        assert.equal(await notes.inputValue(), 'Needs a mobile layout', 'the note stays');
        await card(page).locator('[data-dc="file-retry"]').click();
        await page.locator('.dc-file[data-state="done"]').waitFor({timeout: 5000});

        // The screenshot is still uploading: Send back waits and says so, then sends.
        await card(page).locator('[data-dc="sendback"]').click();
        await card(page).locator('[data-dc-status]', {hasText: 'Uploading 2 of 2…'}).waitFor({timeout: 5000});
        assert.equal(posts.filter(x => x.p.endsWith('/answer')).length, 0, 'nothing sent while a file uploads');
        gate();
        await page.waitForFunction(() => document.querySelector('#need-card .dc-q')?.textContent !== 'Approve the spec for Checkout flow', null, {timeout: 5000});
        const sent = posts.filter(x => x.p.endsWith('/tasks/t-1/asks/r1/answer'));
        assert.equal(sent.length, 1);
        assert.equal(sent[0]!.body['value'], 'no');
        const note = String(sent[0]!.body['note']);
        assert.match(note, /^Needs a mobile layout\n\nFiles:\n- screenshot-\d{8}-\d{6}\.png \(file-1\)\n- notes\.txt \(file-3\)$/, note);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A phone: Notes starts at 4 lines, the buttons are on screen, nothing scrolls sideways.
    {
      const tasks = fixtures();
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, tasks});
      try {
        await page.evaluate(() => { void taskModalOpen('t-1'); });
        await card(page).waitFor({timeout: 5000});
        const notes = page.locator('#dc-notes');
        assert.equal(await notes.getAttribute('rows'), '4');
        await notes.fill('A note typed on a phone. '.repeat(8));
        await paste(page, [{name: 'image.png', type: 'image/png'}]);
        await page.locator('.dc-file').waitFor();
        for (const what of ['approve', 'sendback', 'skip']) {
          const box = await card(page).locator(`[data-dc="${what}"]`).boundingBox();
          assert.ok(box && box.y >= 0 && box.y + box.height <= 844, `${what} is on screen`);
        }
        const sideways = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.querySelector('#need-card')!.scrollWidth) - innerWidth);
        assert.ok(sideways <= 0, `nothing scrolls sideways (${sideways} px)`);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('PASS: a task waiting on you opens on its ask; Open task ↗; a bigger note with screenshots and files.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
