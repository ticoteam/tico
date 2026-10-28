// Strategy › Org (ui/app/redesign-org.js) against the fake hub in support/strategy-fake.cjs (the
// Acme example): the chart top down with the owner on top, groups as outlines that fold (and stay folded), helpers off
// it, pinch and the zoom buttons; a teammate's sheet with its group and Move to…; the owner, and a lead inside the
// groups it leads, move at once (PATCH /api/v2/groups), anyone else sees the group and no Move to…; no proposals and no
// approvals; the Strategy Bot's starters ask for org changes. A phone first, then a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, PHONE, DESKTOP, sideways, shot} = require('./support/strategy-fake.cjs');

const card = ref => `.rdo-card[data-rdo-ref="${ref}"]`;
const inGroup = (page, ref, gid) => page.locator(`[data-rdo-group="${gid}"] ${card(ref)}`).count().then(n => n === 1);
const zoomOf = page => page.locator('#rdo-cv').evaluate(el => Number(el.style.zoom) || 1);

async function chart(page, tag) {
  await page.locator(card('bot:builder')).waitFor();
  assert.equal(await page.locator('.rdo-card.top').innerText().then(t => t.split('\n')[0]), 'Ana Silva', tag + ': the owner on top');
  for (const helper of ['bot:botops', 'bot:librarian', 'bot:coo', 'bot:mail-sorter'])
    assert.equal(await page.locator(card(helper)).count(), 0, `${tag}: ${helper} is off the chart`);
  assert.equal(await page.locator(card('bot:goal-manager')).count(), 1, tag + ': the Strategy Bot is on it');
  assert.ok(await inGroup(page, 'bot:builder', 'eng'), tag + ': Builder in Engineering');
  assert.equal(await page.locator('[data-rdo-group="eng"] [data-rdo-group="web"]').count(), 1, tag + ': Web nests in Engineering');
  assert.equal(await page.locator('[data-rdo-group="eng"] .rdo-card').first().getAttribute('data-rdo-ref'), 'bot:eng-lead', tag + ': the lead first');
  assert.equal(await sideways(page), false, tag + ': no sideways scroll');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A phone, the owner: the chart, folding, pinch, a move.
    {
      const tag = 'phone owner';
      const {page, errors, writes} = await openStrategy(browser);
      await chart(page, tag);
      // Opens readable (names 12px or more), centred on the owner, the frame down to the tab bar, the page still.
      const look = await page.evaluate(() => {
        const pane = document.querySelector('#rdo-pane'), cv = document.querySelector('#rdo-cv');
        const name = parseFloat(getComputedStyle(cv.querySelector('.rdo-card b')).fontSize) * Number(cv.style.zoom);
        const top = document.querySelector('.rdo-card.top').getBoundingClientRect(), box = pane.getBoundingClientRect();
        const bar = document.querySelector('#mobile-nav').getBoundingClientRect().top;
        return {name, off: Math.abs(top.left + top.width / 2 - (box.left + box.width / 2)), gap: bar - box.bottom,
          wider: pane.scrollWidth > pane.clientWidth, still: document.querySelector('#main').scrollHeight <= document.querySelector('#main').clientHeight + 1};
      });
      assert.ok(look.name >= 12, `${tag}: names read at ${look.name}px`);
      assert.ok(look.off < 4, `${tag}: centred on the owner (${look.off}px off)`);
      assert.ok(look.gap >= 0 && look.gap <= 24, `${tag}: the frame runs to the tab bar (${look.gap}px short)`);
      assert.ok(look.wider, tag + ': the chart runs wider than the screen and pans');
      assert.ok(look.still, tag + ': the page itself does not scroll');
      await shot(page, 'phone-org');

      // Fold Engineering: its teammates leave, the tab counts them, and it stays folded on the next visit.
      await page.locator('[data-rdo-fold="eng"]').click();
      assert.equal(await page.locator(card('bot:builder')).isVisible(), false, tag + ': folded');
      assert.equal(await page.locator('[data-rdo-fold="eng"] .rdo-n').innerText(), '5', tag + ': the tab counts who is inside');
      assert.equal(await page.locator('[data-rdo-fold="eng"]').getAttribute('aria-expanded'), 'false');
      await shot(page, 'phone-org-folded');
      await page.reload();
      await page.locator('[data-rdo-fold="eng"]').waitFor();
      assert.equal(await page.locator(card('bot:builder')).isVisible(), false, tag + ': still folded after a reload');
      await page.locator('[data-rdo-fold="eng"]').click();
      await page.locator(card('bot:builder')).waitFor();

      // Pinch out with two fingers, then the buttons.
      const before = await zoomOf(page), box = await page.locator('#rdo-pane').boundingBox();
      const cdp = await page.context().newCDPSession(page);
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      const touch = d => [{x: cx - d, y: cy, id: 1}, {x: cx + d, y: cy, id: 2}];
      await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: touch(40)});
      for (const d of [60, 80, 100]) await cdp.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: touch(d)});
      await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
      const pinched = await zoomOf(page);
      assert.ok(pinched > before * 1.5, `${tag}: a pinch zooms in (${before} → ${pinched})`);
      await page.locator('[data-rdo-zoom="out"]').click();
      assert.ok(await zoomOf(page) < pinched, tag + ': − zooms out');
      assert.equal(await sideways(page), false, tag + ': zoomed, the chart pans inside its pane');

      // The owner moves Builder into Web at once.
      await page.locator(card('bot:builder')).click();
      const sheet = page.locator('dialog.rd-sheet[open]');
      await sheet.waitFor();
      assert.equal(await sheet.locator('[data-rdo-home]').innerText(), 'Engineering', tag + ': the sheet names its group');
      const options = await sheet.locator('select[name="to"] option').allInnerTexts();
      assert.deepEqual(options.map(t => t.trim()), ['No group', 'Engineering', 'Web', 'Sales', 'Outbound'], tag + ': every group, and No group');
      await sheet.locator('select[name="to"]').selectOption('web');
      assert.equal(await sheet.locator('[type=submit]').innerText(), 'Move', tag + ': the owner moves');
      await shot(page, 'phone-org-sheet');
      await sheet.locator('[type=submit]').click();
      await sheet.waitFor({state: 'detached'});
      await page.waitForFunction(() => document.querySelector('[data-rdo-group="web"] [data-rdo-ref="bot:builder"]'));
      assert.deepEqual(writes.filter(w => w.path.startsWith('/api/v2/groups') || w.path.startsWith('/api/v2/approvals')), [
        {method: 'PATCH', path: '/api/v2/groups/web', body: {add: {bots: ['builder']}}},
      ], tag + ': one group change, no approval');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A phone, a teammate who leads nothing: the same chart; the sheet names the group and has no Move to….
    {
      const tag = 'phone member';
      const {page, errors, writes} = await openStrategy(browser, {viewer: 'cara'});
      await chart(page, tag);
      await page.locator(card('bot:sdr')).click();
      const sheet = page.locator('dialog.rd-sheet[open]');
      await sheet.waitFor();
      assert.equal(await sheet.locator('[data-rdo-home]').innerText(), 'Sales', tag + ': the group');
      assert.equal(await sheet.locator('select[name="to"], [type=submit]').count(), 0, tag + ': no Move to…');
      await shot(page, 'phone-org-sheet-member');
      await sheet.locator('[data-close]').first().click();
      assert.deepEqual(writes.filter(w => w.path.startsWith('/api/v2/groups') || w.path.startsWith('/api/v2/approvals')), [], tag + ': nothing written');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A phone, a lead who is not an admin: Move to… offers the groups he leads, and the move happens at once (PATCH,
    // audited by the hub); a teammate outside them has no Move to….
    {
      const tag = 'phone lead';
      const {page, errors, writes} = await openStrategy(browser, {viewer: 'ben'});
      await chart(page, tag);
      const ask = async (ref, to, label) => {
        await page.locator(card(ref)).click();
        const sheet = page.locator('dialog.rd-sheet[open]');
        await sheet.locator('select[name="to"]').selectOption(to);
        assert.equal(await sheet.locator('[type=submit]').innerText(), label, `${tag}: ${ref} to ${to} is ${label}`);
        await sheet.locator('[type=submit]').click();
        await sheet.waitFor({state: 'detached'});
      };
      await page.locator(card('bot:sdr')).click();
      const options = await page.locator('dialog.rd-sheet[open] select[name="to"] option').allInnerTexts();
      assert.deepEqual(options.map(t => t.trim()), ['Sales', 'Outbound'], tag + ': only the groups he leads');
      await page.locator('dialog.rd-sheet[open] [data-close]').first().click();
      await ask('bot:sdr', 'outbound', 'Move');
      await page.waitForFunction(() => document.querySelector('[data-rdo-group="outbound"] [data-rdo-ref="bot:sdr"]'));
      await page.locator(card('bot:builder')).click();
      assert.equal(await page.locator('dialog.rd-sheet[open] select[name="to"]').count(), 0, tag + ': no Move to… outside his groups');
      await page.locator('dialog.rd-sheet[open] [data-close]').first().click();
      assert.deepEqual(writes.filter(w => w.path.startsWith('/api/v2/groups') || w.path.startsWith('/api/v2/approvals')), [
        {method: 'PATCH', path: '/api/v2/groups/outbound', body: {add: {bots: ['sdr']}}},
      ], tag + ': a direct move inside Sales');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A computer, the owner: the same chart, wider.
    {
      const tag = 'desktop owner';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP});
      await chart(page, tag);
      const pane = await page.locator('#rdo-pane').boundingBox(), cv = await page.locator('#rdo-cv').boundingBox();
      assert.ok(cv.width <= pane.width + 1, tag + ': the whole chart fits the pane');
      await page.locator(card('person:cara')).click();
      await page.locator('dialog[open] [data-rdo-home]').waitFor();
      assert.equal(await page.locator('dialog[open] [data-rdo-home]').innerText(), 'Engineering', tag + ': a person has a home group too');
      await shot(page, 'desktop-org-sheet');
      await page.locator('dialog[open] [data-close]').first().click();
      await shot(page, 'desktop-org');
      // The Strategy Bot beside it offers org starters here, and its usual ones on Goals.
      const starters = () => page.locator('#sbot [data-gm-starter]').allInnerTexts();
      await page.locator('#sbot [data-gm-starter]').first().waitFor();
      assert.deepEqual(await starters(), ['Suggest org changes', 'Who should lead each group?', 'Fix reporting lines'], tag + ': org starters');
      await page.evaluate(() => { location.hash = '#/strategy/goals'; });
      await page.waitForFunction(() => document.querySelector('#sbot [data-gm-starter]')?.textContent === 'Set a goal');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    console.log('strategy org ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
