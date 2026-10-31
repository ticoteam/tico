// Org rule checks in the app (ui/app/org-rule-holds.js, backend/org_rule_check.py): a rule review task shows each held
// output with its rule and Keep blocked / Send anyway / Edit rule; Keep blocked and Send anyway post once and the card
// says what was decided; a held reply in a chat is one line naming the rule, never the text; the Rules header carries
// "Checks on · N today · N unchecked · $N left". No sideways scroll at phone width. Every request is intercepted.
// TICO_SHOTS=<dir> saves the screenshots (1440 and 390, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {open} = require('./tasks-page.cjs');
const SHOTS = process.env.TICO_SHOTS || '';
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();

const REVIEW = {id: 't-review', title: 'Review: Support Bot broke a rule', owner: 'human:ana', requester: 'tico', status: 'open',
  body: "Support Bot's output was held: it broke a rule. Keep it blocked, send it anyway, or edit the rule.\n\nHold: h-1",
  lane: 'company', rank: null, labels: ['rule-review'], tags: [], links: [], parts: {total: 0, done: 0}, version: 1,
  created: ago(30), updated: ago(5)};
const HOLD = {id: 'h-1', operation: 'message', author: 'bot:support', author_name: 'Support Bot', state: 'held', outcome: 'escalated',
  rules: [{id: 'r-eu', text: "Acme data stays in the EU and only goes to vendors on Acme's list.", version: 2, status: 'active', if_broken: 'escalate'}],
  text: "Attached Acme's ticket export for the new transcription vendor to test with.", to: ['Ops'], reviewer: 'human:ana',
  reviewer_name: 'Ana', task_id: 't-review', created: ago(30), decided_at: null, decided_by: null, can_send: true, nonce: 'n-1',
  why: 'The vendor signed the EU addendum last week.'};

async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
}

async function review(browser, {viewport, theme}) {
  const {page, errors} = await open(browser, {viewport, theme, hash: '#/tasks', baseTasks: [REVIEW]});
  let hold = {...HOLD};
  const posted = [];
  await page.route(url => url.pathname === '/api/v2/rule-holds', route => route.fulfill({contentType: 'application/json',
    body: JSON.stringify({holds: [hold], more: false})}));
  await page.route(url => /^\/api\/v2\/rule-holds\/[^/]+\/(keep|send)$/.test(url.pathname), route => {
    const [, id, act] = new URL(route.request().url()).pathname.match(/rule-holds\/([^/]+)\/([^/]+)$/);
    posted.push({id, act, body: route.request().postDataJSON()});
    hold = {...hold, state: act === 'send' ? 'sent' : 'kept', decided_by: 'human:ana', decided_at: ago(0), nonce: undefined, can_send: false};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(act === 'send' ? {hold, result: {}} : hold)});
  });
  await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't-review')));
  const card = page.locator('#task-modal .rh-card[data-hold="h-1"]');
  await card.waitFor();
  const text = await card.innerText();
  assert.match(text, /Tried to send[\s\S]*transcription vendor/);
  assert.match(text, /Acme data stays in the EU/);
  assert.match(text, /The vendor signed the EU addendum/);
  assert.equal(await card.locator('a[href="#/rules?rule=r-eu"]').innerText(), 'Edit rule');
  assert.equal(await card.locator('[data-hold-send]').innerText(), 'Send anyway');
  assert.equal(await card.locator('[data-hold-keep]').innerText(), 'Keep blocked');
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(wide <= 1, `no sideways scroll (${wide}px)`);
  return {page, errors, card, posted};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
        const {page, errors, card, posted} = await review(browser, {viewport, theme});
        await shot(page, `held-review-${viewport.width}-${theme}`);
        if (viewport.width === 1440 && theme === 'dark') {
          await card.locator('[data-hold-send]').click();
          await card.locator('.rh-done').waitFor();
          assert.deepEqual(posted, [{id: 'h-1', act: 'send', body: {nonce: 'n-1'}}], 'Send anyway posts once, with the nonce');
          assert.match(await card.locator('.rh-done').innerText(), /^Sent anyway by /);
          assert.equal(await card.locator('button').count(), 0, 'nothing left to press');
        }
        if (viewport.width === 390 && theme === 'dark') {
          await card.locator('[data-hold-keep]').click();
          await card.locator('.rh-done').waitFor();
          assert.deepEqual(posted.map(x => x.act), ['keep']);
        }
        // The held reply in a chat, and the Rules header line, drawn by the same code the pages use.
        await page.evaluate(() => {
          document.querySelector('#task-modal')?.close();
          const host = document.createElement('div');
          host.id = 'rh-sample'; host.style.cssText = 'padding:24px;max-width:720px';
          host.innerHTML = `<div class="ru" id="ru-root"><header class="ru-ph"><h1>Rules</h1><span class="spacer"></span></header></div>
            <div class="conv-thread">${v2MessageHTML({id: 'm-1', kind: 'notice', from_actor: 'tico', to_actor: 'human:ana', created: new Date().toISOString(),
              body: "Support Bot's reply was held: it broke the rule 'Acme data stays in the EU.'", refs: {org_rule: {hold: 'h-2', bot: 'support'}}})}</div>`;
          document.querySelector('#main').replaceChildren(host);
        });
        await page.route(url => url.pathname === '/api/v2/rules/stats', route => route.fulfill({contentType: 'application/json',
          body: JSON.stringify({checks_today: 12, unchecked_today: 0, blocked_today: 1, hits_today: 2, usd_left: 48.2, on: true})}));
        await page.evaluate(() => ruleChecksMount(document.querySelector('#ru-root .ru-ph')));
        await page.locator('.ru-checks').waitFor();
        assert.equal(await page.locator('.ru-checks').innerText(), 'Checks on · 12 today · 0 unchecked · $48 left');
        const note = page.locator('#rh-sample .rh-note');
        assert.match(await note.innerText(), /reply was held: it broke the rule/);
        await shot(page, `held-note-and-header-${viewport.width}-${theme}`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    console.log('org rule holds: review card, send anyway, keep blocked, held note, header line: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
