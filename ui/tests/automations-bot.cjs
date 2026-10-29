// BotOps on Automations (ui/app/automations-bot.js) against the fake hub in support/automations-fake.cjs. A message goes
// to BotOps' chat naming the open automation (POST /api/v2/chat/botops, refs.automation); with the viewer's rights it
// saves the change as the new version, which the flow then shows. "+ New" opens the same chat with no automation; what
// BotOps makes there is off, as a card with Turn on (PATCH enabled). A bot's proposal (a pending version) is still a
// card: View diff, Turn on (restore), Discard. A draft (off, its first version pending) shows Pending in the list, and
// Turn on turns it on. A phone gives the chat its own screen; a computer shows it on the right.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

async function ask(page, text) {
  await page.locator('.au-bf .p-text').fill(text);
  await page.locator('.au-bf .p-send').click();
}

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: BotOps' own screen; the edit is saved as v8 and the flow shows it.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, hash: '#/automations/au_inbox'});
      await page.waitForSelector('.au-card[data-open="f1"]');
      assert.equal(await page.locator('.au-botp').isVisible(), false);
      // BotOps is a labelled button over the flow, as the Strategy Bot's is, clear of the tab bar.
      assert.equal((await page.locator('.au-botbtn').innerText()).trim().endsWith('BotOps'), true);
      const fab = await page.locator('.au-botbtn').boundingBox(), tabs = await page.locator('#mobile-nav').boundingBox();
      assert.ok(fab.height >= 44 && (!tabs || fab.y + fab.height <= tabs.y), 'a big button above the tab bar');
      await page.locator('.au-botbtn').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'bot');
      assert.equal(await page.locator('.au-pane').isVisible(), false);
      assert.equal(await page.locator('.au-botbtn').isVisible(), false, 'no button on BotOps\' own screen');
      assert.equal(await page.locator('.au-botp').isVisible(), true);
      assert.equal(await page.locator('.au-bh h2').innerText(), 'BotOps');
      const bar = await page.locator('.au-bf').boundingBox();
      assert.ok(bar.height < 64, 'the composer is one line');
      assert.equal(await page.locator('.au-bf .ask').evaluate(el => getComputedStyle(el).borderTopWidth), '0px', 'one border, the pill’s');
      await ask(page, 'Archive receipts too');
      await page.waitForFunction(() => /Saved v8/.test(document.querySelector('.au-msgs').textContent));
      // Each message names its speaker once, inside the bubble, as the app's other chats do.
      assert.equal(await page.locator('.au-msgs .conv-run-head:visible').count(), 0, 'no second name line');
      assert.deepEqual(await page.locator('.au-msgs .bubble .who:visible').count(), await page.locator('.au-msgs .bubble').count());
      const sent = writes.find(w => w.path === '/api/v2/chat/botops');
      assert.deepEqual(sent.body.refs, {automation: 'au_inbox'});
      assert.equal(sent.body.text, 'Archive receipts too');
      assert.equal(await page.locator('.au-prop').count(), 0, 'a person’s edit is no proposal');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-bot');
      await page.locator('.au-botp [data-bot-back]').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'detail');
      assert.equal(await page.locator('.au-ver').innerText(), 'v8');
      assert.match(await page.locator('.au-card[data-open="f1"]').innerText(), /2 rules[\s\S]*subject has “receipt”/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: + New opens BotOps; what it makes is off, and its card turns it on in one tap.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, hash: '#/automations'});
      await page.getByRole('button', {name: '+ New'}).click();
      await page.waitForURL(/#\/automations\/bot$/);
      assert.equal(await page.locator('.au-bh h2').innerText(), 'BotOps');
      assert.equal(await page.locator('.au-bh p, .au-bh .muted').count(), 0, 'no subtitle');
      await shot(page, 'phone-new');
      await ask(page, 'Label demo requests in the sales inbox');
      const card = page.locator('.au-made[data-made="au_new"]');
      await card.waitFor();
      assert.deepEqual(writes.at(-1).body.refs, {});
      assert.match(await card.innerText(), /Sales leads[\s\S]*Off/);
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-made');
      await card.getByRole('button', {name: 'Turn on'}).click();
      await page.waitForSelector('.au-made[data-made="au_new"] .pill.ok');
      assert.deepEqual(writes.at(-1), {method: 'PATCH', path: '/api/v2/automations/au_new', body: {enabled: true}});
      assert.equal(await card.getByRole('button', {name: 'Turn on'}).count(), 0);
      await card.getByRole('link', {name: 'Open'}).click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'detail');
      assert.equal(await page.locator('.au-hd .au-sw').isChecked(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: Blank makes an empty one, off, and its own page's header turns it on.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, hash: '#/automations/bot'});
      await page.locator('.au-blank').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'detail' && /New automation/.test(document.querySelector('.au-name')?.textContent));
      const sw = page.locator('.au-hd [data-on-cur]');
      assert.equal(await sw.isVisible(), true, 'the switch shows on a phone');
      assert.equal(await sw.isChecked(), false);
      await sw.click();
      await page.waitForFunction(() => document.querySelector('.au-hd [data-on-cur]')?.checked);
      assert.equal(writes.filter(w => w.method === 'POST' && w.path === '/api/v2/automations').length, 1);
      assert.deepEqual([writes.at(-1).method, writes.at(-1).body], ['PATCH', {enabled: true}]);
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A computer: BotOps beside the flow; a bot's proposal with its diff, Discard; + New asked to turn it on.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_inbox', proposal: true});
      await page.waitForSelector('.au-card[data-open="f1"]');
      assert.equal(await page.locator('.au-botp').isVisible(), true);
      assert.equal(await page.locator('.au-botbtn').isVisible(), false);
      assert.equal(await page.locator('.au-row[data-row="au_inbox"] .pill:text-is("Pending")').count(), 1);
      const card = page.locator('.au-prop[data-n="8"]');
      await card.waitFor();
      assert.match(await card.innerText(), /Inbox · dana@acme\.example · v8[\s\S]*Archive receipts before triage/);
      await card.getByRole('button', {name: 'View diff'}).click();
      assert.deepEqual(await card.locator('.au-diff .add').allTextContents(), ['+ filter subject has “receipt” → Archive']);
      assert.equal(await card.locator('.au-diff .del').count(), 0);
      await shot(page, 'desktop-bot');
      await card.getByRole('button', {name: 'Discard'}).click();
      await page.waitForSelector('.au-prop[data-n="8"] .pill:text("Discarded")');
      assert.deepEqual(writes.at(-1), {method: 'DELETE', path: '/api/v2/automations/au_inbox/versions/8', body: {}});
      assert.equal(await page.locator('.au-ver').innerText(), 'v7');

      await page.getByRole('button', {name: '+ New'}).click();
      await page.waitForURL(/#\/automations\/bot$/);
      assert.equal(await page.locator('.au-botp').isVisible(), true);
      await ask(page, 'Which inbox should it watch?');
      await page.waitForFunction(() => /Which inbox\?/.test(document.querySelector('.au-msgs').textContent));
      assert.equal(await page.locator('.au-prop').count(), 0, 'a question makes nothing');
      await ask(page, 'Label demo requests in the sales inbox and turn it on');
      const made = page.locator('.au-made[data-made="au_new"]');
      await made.waitFor();
      assert.equal(await made.locator('.pill.ok').innerText(), 'On');
      assert.equal(await made.getByRole('button', {name: 'Turn on'}).count(), 0);
      assert.equal(await page.locator('.au-row[data-row="au_new"] .au-sw').isChecked(), true);
      await shot(page, 'desktop-new');
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A draft (a bot's: off, version 1 pending): Pending and Off in the list; Turn on enables it and clears Pending.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_draft', draft: true});
      await page.waitForSelector('.au-card[data-open="f1"]');
      const row = page.locator('.au-row[data-row="au_draft"]');
      assert.equal(await row.locator('.pill:text-is("Pending")').count(), 1);
      assert.equal(await row.locator('.au-sw').isChecked(), false, 'a draft is off');
      const card = page.locator('.au-prop[data-n="1"]');
      await card.waitFor();
      assert.match(await card.innerText(), /Receipts to archive · v1/);
      await card.getByRole('button', {name: 'Turn on'}).click();
      await page.waitForSelector('.au-prop[data-n="1"] .pill.ok');
      assert.deepEqual(writes.at(-1), {method: 'POST', path: '/api/v2/automations/au_draft/versions/1/restore', body: {}});
      assert.equal(await row.locator('.pill:text-is("Pending")').count(), 0);
      assert.equal(await row.locator('.au-sw').isChecked(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations bot ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
