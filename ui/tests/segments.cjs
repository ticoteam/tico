// Segments in Strategy and Market (ui/app/segment-pages.ts, ui/strategy-page.js, ui/market-page.js) against the fake hub
// in support/strategy-fake.cjs (the Acme example). The Strategy page's segment list: the owner adds, edits, moves and
// removes a segment, asks the Assistant to suggest them, and is refused with the server's text; a member reads the
// list. A segment's page: its strategy field by field (the segment's own text, the company's in grey), the owner's
// edits with the revision each save is read at, a save refused for a changed draft, its bets, and its market with the
// Librarian and the sizes. The Market tab opens on the compare table, with a chip per segment and What changed across
// all; the whole market stays one click away. A computer and a phone; no sideways scroll. TICO_UI_SHOTS names a folder
// for review screenshots (light and dark, desktop and phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, DESKTOP, PHONE, sideways, shot} = require('./support/strategy-fake.cjs');

const texts = (page, sel) => page.locator(sel).allInnerTexts().then(a => a.map(t => t.trim()));
const SEG_ASK = 'Run the "Set up segments" job for this company.';
const CHECKS = 'segment/checks', DEPLOY = 'segment/deploy';
const segPath = id => '#/strategy/segment/' + encodeURIComponent(id);
// Light, then dark, on the page as it is.
const snap = async (page, name) => {
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await shot(page, `${name}-${theme}`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
};
const lastWrite = (writes, path) => writes.filter(w => w.path === path).at(-1);
// The elements that reach past the screen's right edge: a sideways scroll is only reported, so this names what caused it.
const pastEdge = page => page.evaluate(() => [...document.querySelectorAll('#main *')]
  .filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 6)
  .map(el => el.tagName.toLowerCase() + '.' + String(el.className).trim().split(/\s+/).join('.') + ' ' + Math.round(el.getBoundingClientRect().right)));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // The owner's list: the rows in order, each with its dot, who, faces, and the +N for the teammates not shown.
    {
      const tag = 'list';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy'});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      assert.deepEqual(await texts(page, '#strat-segs .seg-main b'), ['Release checks', 'Deploy pipelines'], tag + ': the list in its order');
      assert.deepEqual(await texts(page, '#strat-segs .seg-who'), ['Platform teams that ship several times a day', 'CI plans that bundle the checks'], tag + ': who');
      const first = page.locator('#strat-segs .seg-row').first();
      assert.equal(await first.locator('.seg-dot').getAttribute('data-seg-color'), 'blue', tag + ': the colour name');
      assert.equal(await first.locator('.seg-face').count(), 2, tag + ': the faces, two of three shown');
      assert.equal(await first.locator('.seg-more').innerText(), '+1', tag + ': the rest as a count');
      assert.equal(await first.locator('.seg-main').getAttribute('href'), segPath(CHECKS), tag + ': a row opens the segment');
      assert.equal(await page.locator('#strat-segs [data-seg-add]').innerText(), '+ Segment');
      assert.equal(await page.locator('#strat-segs [data-seg-suggest]').innerText(), 'Ask Assistant to suggest');
      assert.equal(await page.locator('#strat-segs [data-seg-up]').first().isDisabled(), true, tag + ': the first row cannot move up');
      assert.equal(await page.locator('#strat-where').count(), 0, tag + ': Where we play is gone');
      assert.equal(await page.locator('.strat-card-head', {hasText: 'Where we play'}).count(), 0, tag + ': no Where we play heading');
      assert.ok(await page.evaluate(() => document.querySelector('#strat-top').getBoundingClientRect().bottom <= document.querySelector('#strat-segs').getBoundingClientRect().top + 1), tag + ': after the summary');
      assert.ok(await page.evaluate(() => document.querySelector('#strat-segs').getBoundingClientRect().bottom <= document.querySelector('#strat-sec-bets').getBoundingClientRect().top + 1), tag + ': above the bets');
      await snap(page, 'strategy-segments-desktop');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // The owner adds one inline, edits it, moves it, removes one, and asks the Assistant to suggest them.
    {
      const tag = 'write';
      const {page, errors, writes} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy'});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      await page.locator('#strat-segs [data-seg-add]').click();
      const form = page.locator('#strat-segs [data-seg-new]');
      await form.locator('input[name="name"]').fill('Mid-size firms');
      await form.locator('input[name="who"]').fill('Firms of 50 to 500');
      await form.getByRole('button', {name: 'Add'}).click();
      await page.locator('#strat-segs .seg-main b', {hasText: 'Mid-size firms'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments').body, {name: 'Mid-size firms', who: 'Firms of 50 to 500'}, tag + ': the add body');
      assert.equal(await page.locator('#strat-segs [data-seg-new]').count(), 0, tag + ': the form closes');
      // Edit the first row: the name and who, both sent.
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-edit]').click();
      const edit = page.locator('#strat-segs .seg-editing');
      await edit.locator('input[name="name"]').fill('Release gates');
      await edit.getByRole('button', {name: 'Save'}).click();
      await page.locator('#strat-segs .seg-main b', {hasText: 'Release gates'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/' + CHECKS).body, {name: 'Release gates', who: 'Platform teams that ship several times a day'}, tag + ': the edit body');
      // Move the first row down: the whole order goes.
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-menu]').click();
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-down]').click();
      // The list is read again after the order is saved: wait for its first row, not for a name that was already there.
      await page.waitForFunction(() => document.querySelector('#strat-segs .seg-main b')?.textContent.trim() === 'Deploy pipelines');
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/order').body, {ids: [DEPLOY, CHECKS, 'segment/mid-size-firms']}, tag + ': the order body');
      assert.deepEqual(await texts(page, '#strat-segs .seg-main b'), ['Deploy pipelines', 'Release gates', 'Mid-size firms'], tag + ': the new order');
      // Remove one, after the confirm.
      page.once('dialog', d => d.accept());
      const deploy = page.locator('#strat-segs .seg-row', {hasText: 'Deploy pipelines'});
      await deploy.hover();
      await deploy.locator('[data-seg-del]').click();
      await page.locator('#strat-segs .seg-main b', {hasText: 'Deploy pipelines'}).waitFor({state: 'detached'});
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/remove').body, {id: DEPLOY}, tag + ': the remove body');
      // The Assistant gets the prompt to run the job, in its composer.
      await page.locator('#strat-segs [data-seg-suggest]').click();
      await page.waitForFunction(text => document.querySelector('#ask-panel .ask-form .p-text')?.value === text, SEG_ASK, {timeout: 15000});
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A refused add keeps the form open with its text, and the server's words on a toast.
    {
      const tag = 'refused';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy',
        refusals: {'POST /api/v2/segments': {status: 422, detail: 'Give the segment a name'}}});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      await page.locator('#strat-segs [data-seg-add]').click();
      await page.locator('#strat-segs [data-seg-new] input[name="name"]').fill('Gaps');
      await page.locator('#strat-segs [data-seg-new] button[type="submit"]').click();
      await page.locator('.toast', {hasText: 'Give the segment a name'}).waitFor();
      assert.equal(await page.locator('#strat-segs [data-seg-new] input[name="name"]').inputValue(), 'Gaps', tag + ': the text stays');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A member reads the list: no add, no edit, no menu, no suggest; each name opens its segment. No segments: a line.
    {
      const tag = 'member';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy', viewer: 'cara'});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      assert.equal(await page.locator('#strat-segs [data-seg-add], #strat-segs [data-seg-suggest], #strat-segs [data-seg-edit], #strat-segs [data-seg-menu], #strat-segs [data-seg-del]').count(), 0, tag + ': read only');
      assert.deepEqual(await texts(page, '#strat-segs .seg-main b'), ['Release checks', 'Deploy pipelines'], tag + ': the same list');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy', segments: []});
      await page.locator('#strat-segs', {hasText: 'None yet.'}).waitFor();
      assert.equal(await page.locator('#strat-segs [data-seg-add]').count(), 1, 'none yet: the owner can add the first');
      assert.deepEqual(errors, [], 'none yet: no page errors');
      await page.close();
    }
    // The segment page: its header and links, its strategy field by field, the owner's edits and their revisions.
    {
      const tag = 'segment';
      const {page, errors, writes, db} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS)});
      await page.locator('.seg-head h2').waitFor();
      assert.equal(await page.locator('.seg-head h2').innerText(), 'Release checks', tag + ': the name');
      assert.equal(await page.locator('.seg-who-l').innerText(), 'Platform teams that ship several times a day', tag + ': who');
      assert.equal(await page.locator('.seg-title .seg-dot').getAttribute('data-seg-color'), 'blue', tag + ': the dot');
      assert.deepEqual(await texts(page, '.seg-links a'), ['Goals', 'Growth', 'Budget', 'P&L'], tag + ': the links');
      for (const href of await page.locator('.seg-links a').evaluateAll(as => as.map(a => a.getAttribute('href')))) {
        assert.match(href, /^#\/.*\?segment=segment%2Fchecks$/, tag + ': each link filters to the segment: ' + href);
      }
      assert.equal(await page.locator('[data-seg-field="serve"] .seg-from').innerText(), 'This segment', tag + ': the changed field');
      assert.equal(await page.locator('[data-seg-field="serve"] .seg-from').evaluate(el => el.classList.contains('own')), true);
      assert.equal(await page.locator('[data-seg-field="serve"] [data-seg-field-edit="serve"]').innerText(), 'Edit');
      assert.equal(await page.locator('[data-seg-field="serve"] [data-seg-field-use="serve"]').innerText(), "Use company's");
      assert.equal(await page.locator('[data-seg-field="sell"] .seg-from').innerText(), 'From company', tag + ': the company field');
      assert.equal(await page.locator('[data-seg-field="sell"] .seg-company').count(), 1, tag + ': the company text in grey');
      assert.equal(await page.locator('[data-seg-field="sell"] [data-seg-field-edit="sell"]').innerText(), 'Change for this segment');
      assert.equal(await page.locator('[data-seg-field="sell"] [data-seg-field-use="sell"]').count(), 0);
      // Edit the segment's own text: the form starts with it, and a save sends the section and the revision it was read at.
      await page.locator('[data-seg-field="serve"] [data-seg-field-edit="serve"]').click();
      const area = page.locator('[data-seg-field-form="serve"] textarea');
      assert.equal(await area.inputValue(), 'Platform teams of 50+ engineers who ship several times a day.', tag + ': the own text to edit');
      await area.fill('Platform teams of 100+ engineers.');
      await page.locator('[data-seg-field-form="serve"] button[type="submit"]').click();
      await page.locator('[data-seg-field="serve"] .strat-md', {hasText: '100+'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/strategy').body, {id: CHECKS, revision: 7, sections: {serve: 'Platform teams of 100+ engineers.'}}, tag + ': the PUT');
      // Use company's sends "" for the section: the company's text is back, and the next save reads the new revision.
      await page.locator('[data-seg-field="serve"] [data-seg-field-use="serve"]').click();
      await page.locator('[data-seg-field="serve"] .seg-from', {hasText: 'From company'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/strategy').body, {id: CHECKS, revision: 8, sections: {serve: ''}}, tag + ': the company text is ""');
      assert.equal(await page.locator('[data-seg-field="serve"] .seg-company').count(), 1, tag + ': grey again');
      // A save of the company's own words is not a change: no write.
      const before = writes.length;
      await page.locator('[data-seg-field="sell"] [data-seg-field-edit="sell"]').click();
      await page.locator('[data-seg-field-form="sell"] button[type="submit"]').click();
      await page.locator('[data-seg-field-form="sell"]').waitFor({state: 'detached'});
      assert.equal(writes.length, before, tag + ': the same words are not written');
      // Someone else changed the draft: the save is refused, the page reads it again and says so.
      db.revision += 5;
      await page.locator('[data-seg-field="sell"] [data-seg-field-edit="sell"]').click();
      await page.locator('[data-seg-field-form="sell"] textarea').fill('A seat at $25 a month.');
      await page.locator('[data-seg-field-form="sell"] button[type="submit"]').click();
      await page.locator('.toast', {hasText: 'Changed elsewhere, reloaded'}).waitFor();
      // The save went out at the revision the page held: the "Use company's" write moved the draft from 8 to 9.
      assert.equal(lastWrite(writes, '/api/v2/segments/strategy').body.revision, 9, tag + ': saved at the revision it had');
      assert.equal(await page.locator('[data-seg-field-form="sell"]').count(), 0, tag + ': the form closes on the reload');
      // The bets: each opens on the Strategy page.
      assert.equal(await page.locator('.seg-bet-list a').getAttribute('href'), '#/strategy?bet=b-bundle', tag + ': a bet opens on Strategy');
      // Its market: the five questions of this segment, Edit sizes for the owner, and the Librarian.
      await page.locator('#seg-market .mkt-q h3').first().waitFor();
      assert.deepEqual(await texts(page, '#seg-market .mkt-q h3'), ['What changed?', 'Where is the money going?', 'Who are we up against?'], tag + ': its five questions');
      assert.equal(await page.locator('#seg-market .mkt-name').innerText(), 'Release checks', tag + ': the market is the segment\'s');
      assert.equal(await page.locator('#seg-sizes').isHidden(), true, tag + ': the sizes start folded');
      await page.locator('#seg-market [data-seg-sizes]').click();
      await page.locator('#seg-sizes').waitFor();
      assert.equal(await page.locator('#seg-market [data-seg-sizes]').getAttribute('aria-expanded'), 'true');
      await page.locator('#seg-market [data-seg-research]').click();
      await page.locator('.toast', {hasText: 'The Librarian is on it'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/market/segments/' + CHECKS + '/research').body, {note: ''}, tag + ': research');
      await page.locator('#seg-market .mkt-q h3', {hasText: 'What changed?'}).waitFor();
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await snap(page, 'segment-page-desktop');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A member reads the segment: its published text, no edits, no sizes.
    {
      const tag = 'segment member';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS), viewer: 'cara'});
      await page.locator('.seg-head h2').waitFor();
      assert.equal(await page.locator('[data-seg-field-edit], [data-seg-field-use], [data-seg-sizes]').count(), 0, tag + ': read only');
      assert.equal(await page.locator('[data-seg-field="serve"] .seg-from').innerText(), 'This segment', tag + ': the published text is the segment\'s');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A phone: the segment page and the list, no sideways scroll.
    {
      const tag = 'phone segment';
      const {page, errors} = await openStrategy(browser, {viewport: PHONE, hash: segPath(CHECKS)});
      await page.locator('[data-seg-field="serve"]').waitFor();
      await page.locator('#seg-market .mkt-q h3').first().waitFor();
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await snap(page, 'segment-page-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const tag = 'phone list';
      const {page, errors} = await openStrategy(browser, {viewport: PHONE, hash: '#/strategy'});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      assert.deepEqual(await pastEdge(page), [], tag + ': nothing past the right edge');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await snap(page, 'strategy-segments-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // The Market tab opens on All segments: the compare table, the chips, and What changed across all.
    {
      const tag = 'compare';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market'});
      await page.locator('.seg-cmp tbody tr').first().waitFor();
      // The labels as written: the table's headers are uppercased on screen, so read the text, not the rendered words.
      assert.deepEqual((await page.locator('.seg-cmp th').allTextContents()).map(t => t.trim()), ['Segment', 'Money', 'Yearly spend', 'Growth', 'Our share', 'Faces'], tag + ': the columns');
      assert.deepEqual(await texts(page, '.seg-cmp tbody .seg-name'), ['Release checks', 'Deploy pipelines'], tag + ': the rows');
      const row = page.locator('.seg-cmp tbody tr').first();
      assert.equal(await row.locator('.seg-dot').getAttribute('data-seg-color'), 'blue', tag + ': the dot');
      assert.match(await row.locator('td').nth(1).innerText(), /\$700/, tag + ': money');
      assert.match(await row.locator('td').nth(2).innerText(), /\$700/, tag + ': yearly spend');
      assert.match(await row.locator('td').nth(3).innerText(), /22%/, tag + ': growth');
      assert.match(await row.locator('td').nth(4).innerText(), /4\.2%/, tag + ': our share');
      assert.equal(await row.locator('.seg-face').count(), 2, tag + ': faces joined from the list');
      assert.deepEqual(await texts(page, '.seg-chip'), ['All segments', 'Release checks', 'Deploy pipelines'], tag + ': the chips');
      assert.deepEqual(await texts(page, '.seg-chip.cur'), ['All segments'], tag + ': All is current');
      assert.equal(await page.locator('.mkt-top a', {hasText: 'Whole market'}).getAttribute('href'), '#/strategy/market?view=whole', tag + ': the whole market link');
      assert.equal(await page.locator('.mkt-items .seg-tag').first().innerText(), 'All', tag + ': a whole-market item is tagged All');
      // The fake tags every change after the first with Release checks; each tag is the segment's own dot and name.
      const tags = await texts(page, '.mkt-items .seg-tag');
      assert.ok(tags.length > 1 && tags.slice(1).every(t => t === 'Release checks'), tag + ': the segment items are tagged with it: ' + tags.join(' | '));
      await snap(page, 'market-compare-desktop');
      // A chip opens that segment's market; All segments goes back to the table.
      await page.locator('.seg-chip', {hasText: 'Deploy pipelines'}).click();
      await page.waitForFunction(() => location.hash === '#/strategy/market?segment=segment%2Fdeploy');
      await page.locator('#seg-mkt-body .mkt-q h3').first().waitFor();
      assert.deepEqual(await texts(page, '.seg-chip.cur'), ['Deploy pipelines'], tag + ': the chip is current');
      assert.equal(await page.locator('.seg-cmp').count(), 0, tag + ': the table is the all-segments view');
      await page.locator('.seg-chip', {hasText: 'All segments'}).click();
      await page.locator('.seg-cmp').waitFor();
      // The whole market: its five questions at ?view=whole, back to the table from its header.
      await page.locator('.mkt-top a', {hasText: 'Whole market'}).click();
      await page.locator('#mkt-q-changed').waitFor();
      assert.equal(await page.locator('.seg-cmp').count(), 0, tag + ': the whole market is not the table');
      await page.locator('.mkt-top a', {hasText: 'All segments'}).click();
      await page.locator('.seg-cmp').waitFor();
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A segment's market on the Market tab: the same five questions, scoped to it.
    {
      const tag = 'market segment';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market?segment=' + encodeURIComponent(CHECKS)});
      await page.locator('#seg-mkt-body .mkt-q h3').first().waitFor();
      assert.deepEqual(await texts(page, '.seg-chip.cur'), ['Release checks'], tag + ': its chip');
      assert.deepEqual(await texts(page, '#seg-mkt-body .mkt-q h3'), ['What changed?', 'Where is the money going?', 'Who are we up against?'], tag + ': its questions');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A phone: the chips scroll in their own row, the growth, share and faces columns leave, nothing scrolls the page.
    {
      const tag = 'phone compare';
      const {page, errors} = await openStrategy(browser, {viewport: PHONE, hash: '#/strategy/market'});
      await page.locator('.seg-cmp tbody tr').first().waitFor();
      assert.equal(await page.locator('.seg-cmp .seg-opt').first().isVisible(), false, tag + ': the optional columns leave');
      assert.equal(await page.locator('.seg-chips').evaluate(el => getComputedStyle(el).overflowX), 'auto', tag + ': the chips scroll');
      assert.equal(await page.locator('.seg-chips').evaluate(el => getComputedStyle(el).flexWrap), 'nowrap', tag + ': the chips do not wrap');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await snap(page, 'market-compare-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    console.log('segments ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
