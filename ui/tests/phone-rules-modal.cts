// A native modal makes the page inert even at the first, transparent frame of its fade.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {chromium}: typeof import('playwright') = require('playwright');
const {audit} = require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api;

(async () => {
  const channel = process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL;
  const browser = await chromium.launch({headless: true, ...(channel ? {channel} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 400, height: 844}, isMobile: true, hasTouch: true});
    await page.setContent(`<meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        :root { --dur: 200ms; --ease: linear; }
        button { min-width: 40px; min-height: 40px; }
        #task-new { position: fixed; top: 14px; right: 12px; }
        dialog { position: fixed; inset: 0; width: 100%; height: 100%; max-width: none; max-height: none; margin: 0; padding: 0; border: 0; }
        #cover { position: fixed; inset: 0; margin: 0; width: 100%; height: 100%; border: 0; padding: 0; }
      </style>
      <button id="task-new" class="primary">New task</button>
      <dialog id="task-modal" aria-label="Task"><button>Close</button></dialog>
      <div id="cover" popover="manual">Cover</div>`);
    await page.addStyleTag({content: fs.readFileSync(path.join(__dirname, '../styles/base.css'), 'utf8')});
    await page.evaluate(() => {
      const modal = document.querySelector<HTMLDialogElement>('#task-modal')!;
      modal.showModal();
      // Hold the exact frame that races the phone walk; no wall-clock timing or slow server is needed.
      for (const animation of modal.getAnimations()) { animation.pause(); animation.currentTime = 0; }
    });
    assert.equal(await page.locator('#task-modal').evaluate(el => getComputedStyle(el).opacity), '0');
    assert.deepEqual(await page.evaluate(audit, 'as the screen opens'), [],
      'the fading native modal scopes the audit; New task underneath is inert');

    // A non-modal dialog does not make the rest of the page inert; a cover over it is still a failure.
    await page.evaluate(() => {
      const modal = document.querySelector<HTMLDialogElement>('#task-modal')!;
      modal.close(); modal.show();
      for (const animation of modal.getAnimations()) animation.finish();
      document.querySelector<HTMLElement>('#cover')!.showPopover();
    });
    assert.ok((await page.evaluate(audit, 'as the screen opens')).some(b => b.rule === 2 && b.element.includes('#cover')),
      'a control drawn over the modal is still a failure');

    await page.evaluate(() => document.querySelector<HTMLDialogElement>('#task-modal')!.close());
    assert.ok((await page.evaluate(audit, 'as the screen opens')).some(b => b.rule === 7 && b.element.includes('#task-new')),
      'a fixed cover over New task remains a failure when no modal is open');
    console.log('Phone modal audit: transparent opening, modal cover and primary action cover passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
