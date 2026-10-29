// Strategy › Org (ui/app/redesign-org.js) against the fake hub in support/strategy-fake.cjs (the
// Acme example): the chart top down with the owner on top, groups as outlines that fold (and stay folded), helpers off
// it, pinch, the zoom toolbar above the frame and a first look that fits the width (or centres the owner) with nothing
// clipped at either end; a teammate's sheet with its group and Move to…; the owner, and a lead inside the
// groups it leads, move at once (PATCH /api/v2/groups), anyone else sees the group and no Move to…; no proposals and no
// approvals; the Strategy Bot's starters ask for org changes. A phone first, then a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, PHONE, DESKTOP, sideways, shot} = require('./support/strategy-fake.cjs');

const card = ref => `.rdo-card[data-rdo-ref="${ref}"]`;
const inGroup = (page, ref, gid) => page.locator(`[data-rdo-group="${gid}"] ${card(ref)}`).count().then(n => n === 1);
const zoomOf = page => page.evaluate(() => RDO.zoom);

// The first look, fitted: every card inside the frame across, the owner over the middle of its row, the toolbar above the
// frame and clear of it.
async function fitted(page, tag) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  const f = await page.evaluate(() => {
    const box = el => el.getBoundingClientRect(), pane = document.querySelector('#rdo-pane'), p = box(pane);
    const cards = [...document.querySelectorAll('#rdo-cv .rdo-card')].map(box);
    const owner = box(document.querySelector('.rdo-card.top')), row = box(document.querySelector('.rdo-row'));
    const bar = box(document.querySelector('.rdo-bar'));
    return {left: Math.min(...cards.map(r => r.left)) - (p.left + pane.clientLeft), right: p.left + pane.clientLeft + pane.clientWidth - Math.max(...cards.map(r => r.right)),
      off: Math.abs(owner.left + owner.width / 2 - (row.left + row.width / 2)), barOver: bar.bottom - p.top, barRight: p.right - bar.right,
      wider: pane.scrollWidth > pane.clientWidth + 1};
  });
  assert.ok(f.left >= 0 && f.right >= 0, `${tag}: no card clipped across (${f.left}px spare on the left, ${f.right}px on the right)`);
  assert.ok(f.off <= 2, `${tag}: the owner over the middle of the row (${f.off}px off)`);
  assert.ok(f.barOver <= 0 && f.barRight < 2, `${tag}: the zoom toolbar sits above the frame on the right (${f.barOver}, ${f.barRight})`);
  assert.ok(!f.wider, tag + ': fitted, nothing to pan across');
}

async function chart(page, tag) {
  await page.locator(card('bot:builder')).waitFor();
  assert.equal(await page.locator('.rdo-card.top').innerText().then(t => t.split('\n')[0]), 'Ana Silva', tag + ': the owner on top');
  for (const helper of ['bot:botops', 'bot:librarian', 'bot:coo', 'bot:mail-sorter', 'bot:goal-manager'])
    assert.equal(await page.locator(card(helper)).count(), 0, `${tag}: ${helper} is off the chart`);
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
        const name = parseFloat(getComputedStyle(cv.querySelector('.rdo-card b')).fontSize) * RDO.zoom;
        const top = document.querySelector('.rdo-card.top').getBoundingClientRect(), box = pane.getBoundingClientRect();
        const bar = document.querySelector('#mobile-nav').getBoundingClientRect().top;
        return {name, off: Math.abs(top.left + top.width / 2 - (box.left + box.width / 2)), gap: bar - box.bottom,
          wider: pane.scrollWidth > pane.clientWidth + 1, still: document.querySelector('#main').scrollHeight <= document.querySelector('#main').clientHeight + 1};
      });
      assert.ok(look.name >= 12, `${tag}: names read at ${look.name}px`);
      assert.ok(look.off < 4, `${tag}: centred on the owner (${look.off}px off)`);
      assert.ok(look.gap >= 0 && look.gap <= 24, `${tag}: the frame runs to the tab bar (${look.gap}px short)`);
      assert.ok(!look.wider, tag + ': the small team fits the frame across, nothing clipped at the sides');
      // A long name ends in "…" on one line; its card keeps its width (the name is the card's tooltip).
      const long = await page.locator(card('bot:builder')).evaluate(el => {
        const b = el.querySelector('b'), width = el.getBoundingClientRect().width;
        b.textContent = 'Head of Customer Success Operations';
        const out = {cut: b.scrollWidth > b.clientWidth, dots: getComputedStyle(b).textOverflow, lines: Math.round(b.getBoundingClientRect().height / parseFloat(getComputedStyle(b).lineHeight)),
          same: Math.abs(el.getBoundingClientRect().width - width) < 1, title: el.title};
        b.textContent = 'Builder';
        return out;
      });
      assert.deepEqual(long, {cut: true, dots: 'ellipsis', lines: 1, same: true, title: 'Builder'}, tag + ': a long name ends in an ellipsis');
      assert.ok(look.still, tag + ': the page itself does not scroll');
      await fitted(page, tag);
      const first = await zoomOf(page);
      await shot(page, 'phone-org');

      // Fold Engineering: its teammates leave, the tab counts them, and it stays folded on the next visit.
      await page.locator('[data-rdo-fold="eng"]').click();
      await page.locator(card('bot:builder')).waitFor({state: 'hidden'});
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
      // Fit goes back to the first look.
      await page.locator('[data-rdo-zoom="fit"]').click();
      assert.equal(await zoomOf(page), first, tag + ': Fit goes back to the first look');

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
      await fitted(page, tag);
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

    // A narrower computer and a bigger org (six more groups): too wide to fit readable, so it opens at 12px names with the
    // owner in the middle of the frame, and either end pans into view whole. A new width fits it again until the person
    // zooms or pans by hand.
    {
      const tag = 'narrow desktop, a big org';
      const {page, errors} = await openStrategy(browser, {viewport: {width: 1075, height: 820}});
      await chart(page, tag);
      await fitted(page, tag);
      await page.evaluate(() => {
        for (let i = 1; i <= 6; i++) {
          const id = 'x' + i, bots = [0, 1, 2].map(j => ({name: `${id}-b${j}`, display_name: `Group ${i} bot ${j + 1}`, role: 'Specialist', org_parent: j ? `b:${id}-b0` : '', team: id, status: 'active', can_chat: true}));
          S.orgGroups.push({id, name: 'Group ' + i, parent: '', org_parent: '', order: 2 + i});
          S.emps.push(...bots);
          RDO.groups.push({id, name: 'Group ' + i, parent: '', lead: `bot:${id}-b0`, people: [], bots: bots.map(b => b.name), order: 2 + i});
        }
        rdoDraw({refit: true});
      });
      const ends = await page.evaluate(() => {
        const pane = document.querySelector('#rdo-pane'), p = pane.getBoundingClientRect(), l = p.left + pane.clientLeft, r = l + pane.clientWidth;
        const cards = () => [...document.querySelectorAll('#rdo-cv .rdo-card')].map(el => el.getBoundingClientRect());
        const owner = document.querySelector('.rdo-card.top').getBoundingClientRect();
        const name = parseFloat(getComputedStyle(document.querySelector('.rdo-card b')).fontSize) * RDO.zoom;
        const out = {name, off: Math.abs(owner.left + owner.width / 2 - (l + pane.clientWidth / 2)), wider: pane.scrollWidth > pane.clientWidth};
        pane.scrollLeft = 0; out.left = Math.min(...cards().map(c => c.left)) - l;
        pane.scrollLeft = pane.scrollWidth; out.right = r - Math.max(...cards().map(c => c.right));
        return out;
      });
      assert.ok(ends.wider && ends.name >= 12, `${tag}: readable (${ends.name}px names), panned across`);
      assert.ok(ends.off <= 2, `${tag}: the owner in the middle of the frame (${ends.off}px off)`);
      assert.ok(ends.left >= 0 && ends.right >= 0, `${tag}: either end pans into view whole (${ends.left}px, ${ends.right}px spare)`);
      // A wider window fits it again (the owner back in the middle of the wider frame); after a zoom by hand a new width
      // leaves it be.
      const centred = () => page.evaluate(() => {
        const pane = document.querySelector('#rdo-pane'), p = pane.getBoundingClientRect(), o = document.querySelector('.rdo-card.top').getBoundingClientRect();
        return Math.abs(o.left + o.width / 2 - (p.left + pane.clientLeft + pane.clientWidth / 2)) <= 2;
      });
      await page.locator('#rdo-pane').evaluate(el => { el.scrollLeft = 0; });
      assert.equal(await centred(), false, tag + ': panned to the left end');
      await page.setViewportSize({width: 1440, height: 900});
      await page.waitForFunction(() => {
        const pane = document.querySelector('#rdo-pane'), p = pane.getBoundingClientRect(), o = document.querySelector('.rdo-card.top').getBoundingClientRect();
        return Math.abs(o.left + o.width / 2 - (p.left + pane.clientLeft + pane.clientWidth / 2)) <= 2;
      });
      const z = await zoomOf(page);
      await page.locator('[data-rdo-zoom="in"]').click();
      assert.ok(Math.abs(await zoomOf(page) - z * 1.25) < 0.001, tag + ': + zooms in');
      await page.setViewportSize({width: 1200, height: 900});
      await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
      assert.ok(Math.abs(await zoomOf(page) - z * 1.25) < 0.001, tag + ': a zoom by hand holds through a resize');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    console.log('strategy org ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
