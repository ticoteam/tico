// Org rule checks in the app (ui/app/org-rule-holds.js, backend/org_rule_check.py): a rule review task shows each held
// output with its rule and Keep blocked / Send anyway / Edit rule (the list is summaries, each card opens its hold for the
// text, and Show more reads the next page); Keep blocked and Send anyway post once and the card says what was decided; a held reply in a chat is one line naming the rule, never the text; the Rules header carries
// "Checks on · N today · N unchecked · $N left", and Edit rule opens that rule's side panel on the Rules page. No sideways
// scroll at phone width. Every request is intercepted.
// TICO_SHOTS=<dir> saves the screenshots (1440 and 390, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {open} = require('./tasks-page.cjs');
const SHOTS = process.env.TICO_SHOTS || '';
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();

const REVIEW = {id: 't-review', title: 'Review: Support Bot broke a rule', owner_id: 'human:ana', requester_id: 'tico', status: 'open',
  body: "Support Bot's output was held: it broke a rule. Keep it blocked, send it anyway, or edit the rule.\n\nHold: h-1",
  lane: 'company', rank: null, labels: ['rule-review'], tags: [], links: [], parts: {total: 0, done: 0}, version: 1,
  created_at: ago(30), updated_at: ago(5)};
const HOLD = {id: 'h-1', operation: 'message', author: 'bot:support', author_name: 'Support Bot', state: 'held', outcome: 'escalated',
  rules: [{id: 'r-eu', kind: 'internal', text: "Acme data stays in the EU and only goes to vendors on Acme's list.", version: 2, status: 'active',
           if_broken: 'escalate'}],
  text: "Attached Acme's ticket export for the new transcription vendor to test with.", to: ['Ops'], reviewer: 'human:ana',
  reviewer_name: 'Ana', task_id: 't-review', created_at: ago(30), decided_at: null, decided_by: null, can_send: true, nonce: 'n-1',
  why: 'The vendor signed the EU addendum last week.'};

async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
}

const SECOND = {...HOLD, id: 'h-2', outcome: 'blocked', text: 'Sent the Acme roster to a personal address.', why: null,
  nonce: 'n-2', created_at: ago(20)};
const scope = (bots = []) => ({everyone: false, all_bots: false, groups: [], bots, people: []});
const RULE = {id: 'r-eu', kind: 'internal', text: HOLD.rules[0].text, description: '', who: scope([{id: 'support', name: 'Support Bot'}]),
  with: {groups: [], bots: [], people: []}, source: null, if_broken: 'escalate', reviewer: null, status: 'active', version: 2,
  created_at: ago(900), updated_at: ago(60), due_at: [], due_more: 0, can_edit: true};
const summary = h => { const {text, why, nonce, ...rest} = h; return {...rest, appealed: !!why}; };

async function review(browser, {viewport, theme, paged = false}) {
  const {page, errors} = await open(browser, {viewport, theme, hash: '#/tasks', baseTasks: [REVIEW]});
  let hold = {...HOLD};
  const posted = [];
  await page.route(url => url.pathname === '/api/v2/rule-holds', route => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor');
    const body = cursor === 'c-1' ? {holds: [summary(SECOND)], next_cursor: null}
      : {holds: [summary(hold)], next_cursor: paged ? 'c-1' : null};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
  });
  await page.route(url => /^\/api\/v2\/rule-holds\/[^/]+$/.test(url.pathname), route => {
    const id = new URL(route.request().url()).pathname.split('/').pop();
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(id === 'h-2' ? SECOND : hold)});
  });
  await page.route(url => /^\/api\/v2\/rule-holds\/[^/]+\/(keep|send)$/.test(url.pathname), route => {
    const [, id, act] = new URL(route.request().url()).pathname.match(/rule-holds\/([^/]+)\/([^/]+)$/);
    posted.push({id, act, body: route.request().postDataJSON()});
    hold = {...hold, state: act === 'send' ? 'sent' : 'kept', decided_by: 'human:ana', decided_at: ago(0), nonce: undefined, can_send: false};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(act === 'send' ? {hold, result: {}} : hold)});
  });
  await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't-review')));
  const card = page.locator('#task-modal .rh-card[data-hold="h-1"]');
  await card.waitFor();
  await page.waitForTimeout(400);   // the dialog's open animation, so the screenshot is the settled modal
  const text = await card.innerText();
  assert.match(text, /Tried to send[\s\S]*transcription vendor/i);
  assert.match(text, /Acme data stays in the EU/);
  assert.match(text, /The vendor signed the EU addendum/);
  assert.equal(await card.locator('a[href="#/rules?rule=r-eu"]').innerText(), 'Edit rule');
  await page.route(url => url.pathname.startsWith('/api/v2/rules') || url.pathname.startsWith('/api/v2/rule-sources'), route => {
    const p = new URL(route.request().url()).pathname;
    const body = p === '/api/v2/rules/stats' ? {checks_today: 12, unchecked_today: 0, blocked_today: 1, hits_today: 2, usd_left: 48.2, on: true}
      : p === '/api/v2/rules/counts' ? {internal: 1, external: 0, due_at: 0, suggested: 0}
      : p === '/api/v2/rules/r-eu' ? RULE : p === '/api/v2/rules' ? {rules: [RULE], next_cursor: null} : {sources: [], rules: [], next_cursor: null};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
  });
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
        const paged = viewport.width === 1440 && theme === 'light';
        const {page, errors, card, posted} = await review(browser, {viewport, theme, paged});
        if (paged) {
          await page.locator('#task-modal .rh-more').click();
          const next = page.locator('#task-modal .rh-card[data-hold="h-2"]');
          await next.waitFor();
          assert.match(await next.innerText(), /personal address/);
          assert.equal(await page.locator('#task-modal .rh-more').count(), 0, 'the last page has no Show more');
          assert.equal(await page.locator('#task-modal .rh-card').count(), 2);
        }
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
        // Edit rule opens the rule on the Rules page, whose header carries the checks line.
        const edit = card.locator('a.rh-btn');
        if (await edit.count()) await edit.click();
        else await page.evaluate(() => { location.hash = '#/rules?rule=r-eu'; });   // a decided card has no buttons
        await page.evaluate(() => document.querySelector('#task-modal')?.open && document.querySelector('#task-modal').close());
        await page.locator('#ru-side form[data-rule="r-eu"]').waitFor();
        const line = page.locator('#ru-root .ru-ph .ru-checks');
        await line.waitFor();
        assert.equal(await line.innerText(), 'Checks on · 12 today · 0 unchecked · $48 left');
        assert.equal(await page.locator('.ru-checks').count(), 1);
        // The header on its own (on a phone the panel is a sheet over it), once the page change has settled.
        await page.evaluate(() => { location.hash = '#/rules'; });
        await page.locator('#ru-side').waitFor({state: 'hidden'});
        await page.waitForTimeout(600);
        const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        assert.ok(wide <= 1, `no sideways scroll on Rules (${wide}px)`);
        assert.equal(await page.locator('.ru-checks').count(), 1);
        await shot(page, `rules-header-${viewport.width}-${theme}`);
        // The held reply in a chat, drawn by the same code the chat uses.
        await page.evaluate(() => {
          const host = document.createElement('div');
          host.id = 'rh-sample'; host.style.cssText = 'padding:24px;max-width:720px';
          host.innerHTML = `<div class="conv-thread">${v2MessageHTML({id: 'm-1', kind: 'notice', sender_id: 'tico', recipient_id: 'human:ana', created_at: new Date().toISOString(),
              body: "Support Bot's reply was held: it broke the rule 'Acme data stays in the EU.'", refs: {org_rule: {hold: 'h-2', bot: 'support'}}})}</div>`;
          document.querySelector('#main').replaceChildren(host);
        });
        const note = page.locator('#rh-sample .rh-note');
        assert.match(await note.innerText(), /reply was held: it broke the rule/);
        await page.waitForTimeout(300);
        await shot(page, `held-note-${viewport.width}-${theme}`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    console.log('org rule holds: review card, send anyway, keep blocked, held note, Rules header and Edit rule: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
