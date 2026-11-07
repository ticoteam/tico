// Segments in Strategy and Market (ui/app/segment-pages.ts, ui/strategy-page.js, ui/market-page.js) against the fake hub
// in support/strategy-fake.cjs (the Acme example). The Strategy page's segment list: the owner adds, edits, moves and
// removes a segment, asks the Assistant to suggest them, and is refused with the server's text (an add, an edit, a move,
// a remove); a failed read says so; a member reads the list. A segment's page: its strategy field by field (all eight,
// the segment's own text, the company's in grey), the owner's edits with the revision each save is read at, a save refused
// for a changed draft, one write for a double click, its bets, Budget and P&L only for a viewer who sees Finance, and its
// market with the five questions, the Librarian and the sizes. The Market tab opens on the compare table, with a chip per
// segment and What changed across all; with no market yet it shows the setup, and with no segments the whole market. The
// Publish button offers a segment's own change. A computer and a phone; no sideways scroll. TICO_UI_SHOTS names a folder
// for review screenshots (light and dark, desktop and phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, DESKTOP, PHONE, SEGMENTS, sideways, shot} = require('./support/strategy-fake.cjs');

const texts = (page, sel) => page.locator(sel).allInnerTexts().then(a => a.map(t => t.trim()));
// A data attribute of each element that matches, in order (the attribute is the check, not the wording).
const attrs = (page, sel, name) => page.locator(sel).evaluateAll((els, n) => els.map(el => el.dataset[n]), name);
const hrefs = (page, sel) => page.locator(sel).evaluateAll(as => as.map(a => a.getAttribute('href')));
// The ids of a market's question sections (rdqSection), in order: the five questions are named by their ids.
const qIds = (page, root) => page.locator(root + ' .mkt-q').evaluateAll(els => els.map(e => e.id));
const SEG_ASK = 'Run the "Set up segments" job for this company.';
const CHECKS = 'segment/checks', DEPLOY = 'segment/deploy';
const segPath = id => '#/strategy/segment/' + encodeURIComponent(id);
const FIVE = ['mkt-q-changed', 'mkt-q-money', 'mkt-q-players', 'mkt-q-reach', 'mkt-q-saying'];
// Light, then dark, on the page as it is.
const snap = async (page, name) => {
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await shot(page, `${name}-${theme}`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
};
const lastWrite = (writes, path) => writes.filter(w => w.path === path).at(-1);
const writesTo = (writes, path) => writes.filter(w => w.path === path).length;
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
      assert.equal(await first.locator('.seg-dot.c-blue').count(), 1, tag + ': the dot is the shared blue');
      assert.equal(await first.locator('.seg-face').count(), 2, tag + ': the faces, two of three shown');
      assert.equal(await first.locator('.seg-face .av').count(), 2, tag + ': each face draws the app avatar');
      assert.equal(await first.locator('.seg-face .av[data-av="cmo"]').count(), 1, tag + ': the bot face is the bot avatar');
      assert.equal(await first.locator('.seg-faces-more').innerText(), '+1', tag + ': the rest as a count');
      assert.equal(await first.locator('.seg-main').getAttribute('href'), segPath(CHECKS), tag + ': a row opens the segment');
      assert.equal(await page.locator('#strat-segs [data-seg-add]').innerText(), '+ Segment');
      assert.equal(await page.locator('#strat-segs [data-seg-suggest]').innerText(), 'Ask Assistant to suggest');
      const addBox = await page.locator('#strat-segs [data-seg-add]').boundingBox(), sugBox = await page.locator('#strat-segs [data-seg-suggest]').boundingBox();
      const headBox = await page.locator('#strat-segs .strat-card-head').boundingBox();
      assert.ok(addBox.x + addBox.width <= sugBox.x + 1, tag + ': + Segment sits left of Ask Assistant to suggest');
      assert.ok(Math.abs(sugBox.x + sugBox.width - (headBox.x + headBox.width)) <= 2, tag + ': the pair sits at the right edge of the card head');
      assert.equal(await page.locator('#strat-segs .strat-card-head .strat-add').count(), 2, tag + ': the card head\'s two buttons share one style');
      // Move up and down sit in the ⋯ menu (a shared popover), not in the row.
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-menu]').click();
      assert.equal(await page.locator('.seg-menu [data-seg-up]').isDisabled(), true, tag + ': the first row cannot move up');
      await page.keyboard.press('Escape');
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
      await snap(page, 'strategy-segments-add-desktop');
      await form.getByRole('button', {name: 'Add'}).click();
      await page.locator('#strat-segs .seg-main b', {hasText: 'Mid-size firms'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments').body, {name: 'Mid-size firms', who: 'Firms of 50 to 500'}, tag + ': the add body');
      assert.equal(await page.locator('#strat-segs [data-seg-new]').count(), 0, tag + ': the form closes');
      // Edit the first row: the name and who, both sent.
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-edit]').click();
      const edit = page.locator('#strat-segs .seg-editing');
      await edit.locator('input[name="name"]').waitFor();
      await snap(page, 'strategy-segments-edit-desktop');
      await edit.locator('input[name="name"]').fill('Release gates');
      await edit.getByRole('button', {name: 'Save'}).click();
      await page.locator('#strat-segs .seg-main b', {hasText: 'Release gates'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/' + CHECKS).body, {name: 'Release gates', who: 'Platform teams that ship several times a day'}, tag + ': the edit body');
      // Move the first row down: the whole order goes.
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-menu]').click();
      await page.locator('.seg-menu [data-seg-down]').click();
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
      const tag = 'refused add';
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
    // A refused edit keeps the row open with its text.
    {
      const tag = 'refused edit';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy',
        refusals: {['POST /api/v2/segments/' + CHECKS]: {status: 422, detail: 'That name is taken'}}});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-edit]').click();
      const edit = page.locator('#strat-segs .seg-editing');
      await edit.locator('input[name="name"]').fill('Deploy pipelines');
      await edit.getByRole('button', {name: 'Save'}).click();
      await page.locator('.toast', {hasText: 'That name is taken'}).waitFor();
      assert.equal(await page.locator('#strat-segs .seg-editing').count(), 1, tag + ': the row stays open');
      assert.equal(await page.locator('#strat-segs .seg-editing input[name="name"]').inputValue(), 'Deploy pipelines', tag + ': the text stays');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A refused move leaves the order as it was, with the server's words.
    {
      const tag = 'refused order';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy',
        refusals: {'POST /api/v2/segments/order': {status: 409, detail: 'The order changed elsewhere'}}});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      await page.locator('#strat-segs .seg-row').first().locator('[data-seg-menu]').click();
      await page.locator('.seg-menu [data-seg-down]').click();
      await page.locator('.toast', {hasText: 'The order changed elsewhere'}).waitFor();
      assert.deepEqual(await texts(page, '#strat-segs .seg-main b'), ['Release checks', 'Deploy pipelines'], tag + ': the order stays');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A refused remove keeps the row, with the server's words.
    {
      const tag = 'refused remove';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy',
        refusals: {'POST /api/v2/segments/remove': {status: 409, detail: 'Bets still use it'}}});
      await page.locator('#strat-segs .seg-row').first().waitFor();
      page.once('dialog', d => d.accept());
      const deploy = page.locator('#strat-segs .seg-row', {hasText: 'Deploy pipelines'});
      await deploy.hover();
      await deploy.locator('[data-seg-del]').click();
      await page.locator('.toast', {hasText: 'Bets still use it'}).waitFor();
      assert.deepEqual(await texts(page, '#strat-segs .seg-main b'), ['Release checks', 'Deploy pipelines'], tag + ': the row stays');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A failed read of the list says so (the server's words), and keeps the add button: it is not "None yet.".
    {
      const tag = 'list read fails';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy',
        refusals: {'GET /api/v2/segments': {status: 500, detail: 'The segments are down'}}});
      await page.locator('#strat-segs .err', {hasText: 'The segments are down'}).waitFor();
      assert.equal(await page.locator('#strat-segs', {hasText: 'None yet.'}).count(), 0, tag + ': not none');
      assert.equal(await page.locator('#strat-segs [data-seg-add]').count(), 1, tag + ': the add button stays');
      assert.equal(await page.locator('#strat-segs .seg-row').count(), 0, tag + ': no rows');
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
    // The segment page: its header and links (Budget and P&L for a Finance viewer), its strategy field by field, the
    // owner's edits and their revisions, and its market.
    {
      const tag = 'segment';
      const {page, errors, writes, db} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS), finance: true});
      await page.locator('[data-seg-field="serve"]').waitFor();
      assert.equal(await page.locator('.seg-head h2').innerText(), 'Release checks', tag + ': the name');
      assert.equal(await page.locator('.seg-who-l').innerText(), 'Platform teams that ship several times a day', tag + ': who');
      assert.equal(await page.locator('.seg-title .seg-dot.c-blue').count(), 1, tag + ': the dot is the shared blue');
      assert.deepEqual(await attrs(page, '.seg-links a', 'segLink'), ['goals', 'growth', 'budget', 'pnl'], tag + ': Budget and P&L for a Finance viewer');
      for (const href of await hrefs(page, '.seg-links a')) assert.match(href, /\?segment=segment%2Fchecks$/, tag + ': each link filters to the segment: ' + href);
      assert.equal(await page.locator('[data-seg-link="budget"]').getAttribute('href'), '#/finance/budgets?segment=segment%2Fchecks', tag + ': Budget is Finance Budgets for the segment');
      assert.equal(await page.locator('[data-seg-link="pnl"]').getAttribute('href'), '#/finance/pnl?segment=segment%2Fchecks', tag + ': P&L is Finance P&L for the segment');
      // The market's company share is the server's our_share (4.2 in the fixture), not a figure worked out from revenue.
      // The tile label is uppercase by CSS (.mkt-lab), and innerText reads it so, hence the case-blind match.
      await page.waitForFunction(() => /share[\s\S]*4\.2%/i.test(document.querySelector('#seg-market')?.innerText || ''), null, {timeout: 15000})
        .catch(async () => { throw new Error(tag + ': no company share tile; the market reads ' + JSON.stringify((await page.locator('#seg-market').innerText()).slice(0, 300))); });
      assert.equal(await page.locator('[data-seg-field]').count(), 8, tag + ': all eight strategy fields');
      await snap(page, 'segment-page-desktop-top');
      const serve = page.locator('[data-seg-field="serve"]'), sell = page.locator('[data-seg-field="sell"]');
      assert.equal(await serve.locator('[data-seg-from]').getAttribute('data-seg-from'), 'segment', tag + ': the changed field');
      assert.equal(await serve.locator('.seg-from').evaluate(el => el.classList.contains('own')), true, tag + ': its label is in the own style');
      assert.equal(await serve.locator('[data-seg-act="edit"]').evaluate(el => el.classList.contains('linkish')), true, tag + ': the actions are link buttons');
      assert.equal(await serve.locator('[data-seg-act="use"]').count(), 1, tag + ': a changed field can go back to the company');
      assert.equal(await sell.locator('[data-seg-from]').getAttribute('data-seg-from'), 'company', tag + ': the company field');
      assert.equal(await sell.locator('.seg-company').count(), 1, tag + ': the company text in grey');
      assert.equal(await sell.locator('[data-seg-act="change"]').count(), 1, tag + ': a company field can be changed for this segment');
      assert.equal(await sell.locator('[data-seg-act="use"]').count(), 0, tag + ': nothing to give back');
      // Edit the segment's own text: the form starts with it, and a save sends the section and the revision it was read at.
      await serve.locator('[data-seg-act="edit"]').click();
      const area = page.locator('[data-seg-field-form="serve"] textarea');
      assert.equal(await area.inputValue(), 'Platform teams of 50+ engineers who ship several times a day.', tag + ': the own text to edit');
      await area.fill('Platform teams of 100+ engineers.');
      await page.locator('[data-seg-field-form="serve"] button[type="submit"]').click();
      await page.locator('[data-seg-field="serve"] .strat-md', {hasText: '100+'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/strategy').body, {id: CHECKS, revision: 7, sections: {serve: 'Platform teams of 100+ engineers.'}}, tag + ': the PUT');
      // Use company's sends "" for the section: the company's text is back, and the next save reads the new revision.
      await serve.locator('[data-seg-act="use"]').click();
      await page.locator('[data-seg-field="serve"] [data-seg-from="company"]').waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/segments/strategy').body, {id: CHECKS, revision: 8, sections: {serve: ''}}, tag + ': the company text is ""');
      assert.equal(await page.locator('[data-seg-field="serve"] .seg-company').count(), 1, tag + ': grey again');
      // A save of the company's own words is not a change: no write.
      const before = writes.length;
      await sell.locator('[data-seg-act="change"]').click();
      await page.locator('[data-seg-field-form="sell"] button[type="submit"]').click();
      await page.locator('[data-seg-field-form="sell"]').waitFor({state: 'detached'});
      assert.equal(writes.length, before, tag + ': the same words are not written');
      // Someone else changed the draft: the save is refused, the page reads it again and says so.
      db.revision += 5;
      await sell.locator('[data-seg-act="change"]').click();
      await page.locator('[data-seg-field-form="sell"] textarea').fill('A seat at $25 a month.');
      await page.locator('[data-seg-field-form="sell"] button[type="submit"]').click();
      await page.locator('.toast', {hasText: 'Changed elsewhere, reloaded'}).waitFor();
      // The save went out at the revision the page held: the "Use company's" write moved the draft from 8 to 9.
      assert.equal(lastWrite(writes, '/api/v2/segments/strategy').body.revision, 9, tag + ': saved at the revision it had');
      assert.equal(await page.locator('[data-seg-field-form="sell"]').count(), 0, tag + ': the form closes on the reload');
      // The bets: each opens on the Strategy page.
      assert.equal(await page.locator('.seg-bet-list a').getAttribute('href'), '#/strategy?bet=b-bundle', tag + ': a bet opens on Strategy');
      // Its market: the five questions of this segment, Edit sizes for the owner, and the Librarian. No money card: the
      // money is the tile beside the yearly spend.
      await page.locator('#seg-market .mkt-q').first().waitFor();
      assert.deepEqual(await qIds(page, '#seg-market'), FIVE, tag + ': its five questions');
      assert.equal(await page.locator('.seg-money').count(), 0, tag + ': the money card is gone');
      // The section is titled Market; the segment's name is the page's own heading, so the market does not repeat it.
      assert.equal(await page.locator('#seg-market .mkt-top h2').innerText(), 'Market', tag + ': the market is titled Market');
      assert.equal(await page.locator('#seg-market .mkt-name').count(), 0, tag + ': the segment\'s name is not repeated in the market');
      assert.equal(await page.locator('#seg-sizes').isHidden(), true, tag + ': the sizes start folded');
      await page.locator('#seg-market [data-seg-sizes]').click();
      await page.locator('#seg-sizes').waitFor();
      assert.equal(await page.locator('#seg-market [data-seg-sizes]').getAttribute('aria-expanded'), 'true');
      await page.locator('#seg-market [data-seg-research]').click();
      await page.locator('.toast', {hasText: 'The Librarian is on it'}).waitFor();
      assert.deepEqual(lastWrite(writes, '/api/v2/market/segments/' + CHECKS + '/research').body, {note: ''}, tag + ': research');
      await page.locator('#seg-market .mkt-q', {hasText: 'What changed?'}).waitFor();
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await snap(page, 'segment-page-desktop');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A double click on Save is one write: the second click lands while the first is in flight.
    {
      const tag = 'segment busy';
      const {page, errors, writes} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS)});
      await page.locator('[data-seg-field="serve"]').waitFor();
      await page.locator('[data-seg-field="serve"] [data-seg-act="edit"]').click();
      await page.locator('[data-seg-field-form="serve"] textarea').fill('Platform teams of 200+ engineers.');
      await page.locator('[data-seg-field-form="serve"] button[type="submit"]').dblclick();
      await page.locator('[data-seg-field="serve"] .strat-md', {hasText: '200+'}).waitFor();
      assert.equal(writesTo(writes, '/api/v2/segments/strategy'), 1, tag + ': one write for a double click');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A member reads the segment: its published text, no edits, no sizes, and no Budget or P&L link.
    {
      const tag = 'segment member';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS), viewer: 'cara'});
      await page.locator('.seg-head h2').waitFor();
      assert.equal(await page.locator('[data-seg-field-edit], [data-seg-field-use], [data-seg-sizes]').count(), 0, tag + ': read only');
      assert.equal(await page.locator('[data-seg-field="serve"] [data-seg-from]').getAttribute('data-seg-from'), 'segment', tag + ': the published text is the segment\'s');
      assert.deepEqual(await attrs(page, '.seg-links a', 'segLink'), ['goals', 'growth'], tag + ': no Budget or P&L link without Finance');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A report's Budget link is for their dedicated segment, with no P&L (their P&L probe is a 403); a lead's P&L link is
    // for the segment they lead, and a segment they do not lead has none.
    {
      const tag = 'segment: report';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS), finance: 'report'});
      await page.locator('.seg-head h2').waitFor();
      assert.deepEqual(await attrs(page, '.seg-links a', 'segLink'), ['goals', 'growth', 'budget'], tag + ': Budget, no P&L');
      assert.equal(await page.locator('[data-seg-link="budget"]').getAttribute('href'), '#/finance/budgets?segment=segment%2Fchecks', tag + ': Budgets for the segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const tag = 'segment: lead';
      const led = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS), finance: 'lead'});
      await led.page.locator('.seg-head h2').waitFor();
      assert.deepEqual(await attrs(led.page, '.seg-links a', 'segLink'), ['goals', 'growth', 'pnl'], tag + ': P&L of the segment they lead, no Budget');
      assert.deepEqual(led.errors, [], tag + ': no page errors');
      await led.page.close();
      const other = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(DEPLOY), finance: 'lead'});
      await other.page.locator('.seg-head h2').waitFor();
      assert.deepEqual(await attrs(other.page, '.seg-links a', 'segLink'), ['goals', 'growth'], tag + ': no P&L for a segment they do not lead');
      await other.page.close();
    }
    // A phone: the segment page and the list, no sideways scroll.
    {
      const tag = 'phone segment';
      const {page, errors} = await openStrategy(browser, {viewport: PHONE, hash: segPath(CHECKS), finance: true});
      await page.locator('[data-seg-field="serve"]').waitFor();
      await page.locator('#seg-market .mkt-q').first().waitFor();
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await snap(page, 'segment-page-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const tag = 'phone list';
      const {page, errors} = await openStrategy(browser, {viewport: PHONE, hash: '#/strategy'});
      await page.locator('#strat-segs .seg-row').first().waitFor();
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
      assert.deepEqual(await attrs(page, '.seg-cmp th', 'col'), ['segment', 'yearly', 'growth', 'share', 'team'], tag + ': the columns (no Money)');
      assert.equal(await page.locator('.seg-cmp th[data-col="team"]').evaluate(el => el.textContent.trim()), 'Team', tag + ': the last column is the team');
      assert.equal(await page.locator('#seg-cmp-h').count(), 0, tag + ': no heading over the table');
      assert.deepEqual(await texts(page, '.seg-cmp tbody .seg-name'), ['Release checks', 'Deploy pipelines'], tag + ': the rows');
      const row = page.locator('.seg-cmp tbody tr').first();
      assert.equal(await row.locator('.seg-dot.c-blue').count(), 1, tag + ': the dot is the shared blue');
      assert.match(await row.locator('td').nth(1).innerText(), /\$700/, tag + ': yearly spend');
      assert.match(await row.locator('td').nth(2).innerText(), /22%/, tag + ': growth');
      assert.match(await row.locator('td').nth(3).innerText(), /4\.2%/, tag + ': our share');
      assert.equal(await row.locator('.seg-face').count(), 2, tag + ': faces joined from the list');
      assert.deepEqual(await texts(page, '.seg-chip'), ['All segments', 'Release checks', 'Deploy pipelines'], tag + ': the chips');
      assert.deepEqual(await texts(page, '.seg-chip.cur'), ['All segments'], tag + ': All is current');
      assert.equal(await page.locator('.mkt-top a', {hasText: 'Whole market'}).getAttribute('href'), '#/strategy/market?view=whole', tag + ': the whole market link');
      assert.equal(await page.locator('.mkt-items.seg-changed').count(), 1, tag + ': what changed across all is the grid');
      // Each item is tagged: the whole market's as all, the rest with the segment they belong to.
      const tags = await attrs(page, '.mkt-items [data-seg-tag]', 'segTag');
      assert.ok(tags.length > 1 && tags[0] === 'all' && tags.slice(1).every(t => t === CHECKS), tag + ': the items are tagged: ' + tags.join(' | '));
      await snap(page, 'market-compare-desktop');
      // A chip opens that segment's market; All segments goes back to the table.
      await page.locator('.seg-chip', {hasText: 'Deploy pipelines'}).click();
      await page.waitForFunction(() => location.hash === '#/strategy/market?segment=segment%2Fdeploy');
      await page.locator('#seg-mkt-body .mkt-q').first().waitFor();
      assert.deepEqual(await texts(page, '.seg-chip.cur'), ['Deploy pipelines'], tag + ': the chip is current');
      assert.equal(await page.locator('.seg-cmp').count(), 0, tag + ': the table is the all-segments view');
      await page.locator('.seg-chip', {hasText: 'All segments'}).click();
      await page.locator('.seg-cmp').waitFor();
      // The whole market: its five questions at ?view=whole, back to the table from its header.
      await page.locator('.mkt-top a', {hasText: 'Whole market'}).click();
      // The table is replaced at once; the compare view has its own What changed, so wait for the table to go.
      await page.waitForFunction(() => location.hash === '#/strategy/market?view=whole' && !document.querySelector('.seg-cmp'));
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
      await page.locator('#seg-mkt-body .mkt-q').first().waitFor();
      assert.deepEqual(await texts(page, '.seg-chip.cur'), ['Release checks'], tag + ': its chip');
      assert.deepEqual(await qIds(page, '#seg-mkt-body'), FIVE, tag + ': its questions');
      await snap(page, 'market-segment-desktop');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A phone: the chips scroll in their own row, the growth, share and team columns leave, nothing scrolls the page.
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
    // A company with no market yet: the Market tab shows the setup (its blank state), for the compare view and for a
    // segment's market alike.
    {
      const tag = 'market blank';
      for (const hash of ['#/strategy/market', '#/strategy/market?segment=' + encodeURIComponent(CHECKS)]) {
        const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash, market: false});
        await page.locator('#market-shell.market-shell-blank').waitFor();
        assert.equal(await page.locator('.seg-cmp').count(), 0, tag + ': no compare table, ' + hash);
        assert.equal(await page.locator('.seg-chips').count(), 0, tag + ': no chips, ' + hash);
        assert.deepEqual(errors, [], tag + ': no page errors, ' + hash);
        await page.close();
      }
    }
    // A company with no segments: the Market tab is the whole market, with no compare table and no chips.
    {
      const tag = 'market no segments';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market', segments: []});
      await page.locator('#mkt-q-changed').waitFor();
      assert.equal(await page.locator('.seg-cmp').count(), 0, tag + ': no table');
      assert.equal(await page.locator('.seg-chips').count(), 0, tag + ': no chips');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Publish offers a segment's own change: the draft differs by it, though the company's words are the same.
    {
      const tag = 'publish segment change';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy'});
      await page.locator('[data-strat-status]').waitFor();
      assert.equal(await page.locator('[data-strat-status]').innerText(), 'Draft differs from v7', tag + ': the draft differs');
      // The fake has no active bet, so Publish is refused for that reason (strategyPublishRefusal), not for nothing new.
      assert.notEqual(await page.locator('[data-strat-publish]').getAttribute('title'), 'Nothing new to publish', tag + ': not blocked as nothing new');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const tag = 'publish nothing new';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy',
        segments: SEGMENTS.map(s => ({...s, own: {}}))});
      await page.locator('[data-strat-publish]').waitFor();
      assert.equal(await page.locator('[data-strat-status]').count(), 0, tag + ': the draft matches');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A header link lands on its Finance page with the segment in the address, and the page reads it back (segQuery).
    for (const [key, route] of [['budget', 'budgets'], ['pnl', 'pnl']]) {
      const tag = 'landing ' + key;
      const {page} = await openStrategy(browser, {viewport: DESKTOP, hash: segPath(CHECKS), finance: true});
      await page.locator(`[data-seg-link="${key}"]`).click();
      await page.waitForFunction(r => location.hash.startsWith('#/finance/' + r + '?segment='), route);
      assert.equal(await page.evaluate(() => segQuery()), CHECKS, tag + ': the page reads the segment from its address');
      await page.close();
    }
    console.log('segments ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
