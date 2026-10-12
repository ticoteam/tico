// Decide on a phone (390px): a task a bot set waiting on me leads with what I must do, in the bot's words to me,
// never with the bot's own status note. In order: an open question to me; the last thing the bot said to me on the
// task (a mirror of the status note does not count); the task's request, when a person wrote it. The note folds
// under "Latest note", muted, as text. With nothing said, the card says so and the comment box is the way to ask.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = minutes => new Date(NOW - minutes * 60000).toISOString();
const NOTE = 'Engineering answer db14c907 resolves initial inquiry 8719e1a6: public example is independent static Larkspur. '
  + 'Docs task Doing; no sample edit/deploy or duplicate human question. <img src=x onerror="window.pwned=1">';
const wait = (id, title, over = {}) => ({id, kind: 'waiting', title, body: '', owner: 'bot:writer', requester: 'bot:botops',
  status: 'waiting', waiting_on: 'human:ana', note: NOTE, version: 2, created: at(400), updated: at(360), ask: null, open_asks: 0, ...over});
const items = [
  wait('t-ask', 'Coordinate the external agent scenario in the example', {created: at(500),
    ask: {id: 'q-1', from_actor: 'bot:writer', to_actor: 'human:ana', body: 'Should the public example show the agent demo, or stay static?', created: at(300)}}),
  wait('t-said', 'Refresh the pricing page screenshots', {created: at(490)}),
  wait('t-body', 'Renew the docs domain', {created: at(480), requester: 'human:sam',
    body: 'Renew the docs domain\n\nPlease renew docs.acme.example before it lapses on Friday; the card on file expired.'}),
  // a bot wrote the request: its body is the bot's own record, not an ask to me
  wait('t-botbody', 'Sort out the release checklist', {created: at(475),
    body: 'Sort out the release checklist\n\nRefs db14c907, 8719e1a6-3f2a-4c1d-9e0b-5a6b7c8d9e0f; see run 03QEZT3C.'}),
  wait('t-blank', 'Check the newsletter list', {created: at(470)}),
];
const comments = {
  // the status note's mirror in chat, then a message to me: the message leads
  't-said': [{id: 'm-1', kind: 'say', from_actor: 'bot:writer', to_actor: 'human:ana', body: NOTE, refs: {task: 't-said', note: true}, created: at(360)},
    {id: 'm-2', kind: 'say', from_actor: 'bot:writer', to_actor: 'human:ana', body: 'Can you send me the new plan names so I can retake the screenshots?', refs: {task: 't-said', comment: true}, created: at(350)},
    {id: 'm-3', kind: 'say', from_actor: 'bot:writer', to_actor: 'bot:botops', body: 'Blocked on 3f2a9c1d, see run log.', refs: {task: 't-said', comment: true}, created: at(340)}],
  't-blank': [{id: 'm-4', kind: 'say', from_actor: 'bot:writer', to_actor: 'human:ana', body: NOTE, refs: {task: 't-blank', note: true}, created: at(360)}],
};
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks'});
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => {
      const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2));
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({comments: comments[id] || []})});
    });
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { await v2Refresh(); location.hash = DECIDE; });
    const card = page.locator('.decide .dc-card');
    // The card as Chris saw it, and after: what it leads with, and where the note went.
    const read = async id => {
      await page.locator(`.decide .dc-card[data-key="task:${id}"]`).waitFor();
      await page.waitForFunction(id => DEC?.data.get(id)?.comments, id);
      return card.evaluate(el => {
        const body = el.querySelector('.dc-body'), first = [...body.children].find(x => !x.classList.contains('dc-title'));
        const latest = body.querySelector('.dc-latest');
        return {sub: el.querySelector('.dc-who-sub').textContent, lead: first?.textContent.trim() || null, leadClass: first?.className || null,
          note: latest ? {open: latest.open, summary: latest.querySelector('summary').textContent, text: latest.querySelector('p').textContent,
            last: body.lastElementChild === latest, size: parseFloat(getComputedStyle(latest).fontSize)} : null,
          msgSize: parseFloat(getComputedStyle(body.querySelector('.dc-msg') || body).fontSize),
          ask: !!body.querySelector('[data-dc="ask"]')};
      });
    };
    const noted = {open: false, summary: 'Latest note', text: NOTE, last: true};
    // DECIDE_SHOTS=<dir> saves each card for the owner.
    const shot = name => process.env.DECIDE_SHOTS && page.screenshot({path: require('node:path').join(process.env.DECIDE_SHOTS, name + '.png')});
    // Skip once the last action's lock (350 ms) has passed: a tap inside it does nothing, by design.
    const next = async name => {
      await shot(name);
      await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
      await page.locator('.decide .dc-actions [data-dc="skip"]').click();
    };

    // (a) an open question to me leads
    let c = await read('t-ask');
    assert.match(c.sub, /^Waiting on you · /);
    assert.equal(c.lead, 'Should the public example show the agent demo, or stay static?');
    assert.equal(c.leadClass, 'dc-msg md');
    assert.deepEqual({...c.note, size: undefined}, {...noted, size: undefined});
    assert.ok(c.note.size < c.msgSize, `the note is smaller than the ask: ${c.note.size} < ${c.msgSize}`);
    assert.equal(c.ask, false);
    await page.locator('.decide .dc-latest summary').click();
    assert.equal(await page.locator('.decide .dc-latest').evaluate(d => d.open), true, 'the note opens on a tap');
    await next('ask');

    // (b) what the bot said to me leads; the note's mirror in chat and a message to another bot do not
    c = await read('t-said');
    assert.equal(c.lead, 'Can you send me the new plan names so I can retake the screenshots?');
    assert.deepEqual({...c.note, size: undefined}, {...noted, size: undefined});
    await next('said');

    // (c) the request a person wrote leads, without its title said twice
    c = await read('t-body');
    assert.equal(c.lead, 'Please renew docs.acme.example before it lapses on Friday; the card on file expired.');
    assert.deepEqual({...c.note, size: undefined}, {...noted, size: undefined});
    await next('request');

    // a bot's request body is not an ask: the card says nothing was asked, as with no body
    c = await read('t-botbody');
    assert.equal(c.lead, 'Writer set this waiting on you without saying what it needs.');
    assert.equal(c.ask, true);
    assert.deepEqual({...c.note, size: undefined}, {...noted, size: undefined});
    await next('bot-request');

    // nothing said: the card says so plainly and offers the comment box
    c = await read('t-blank');
    assert.equal(c.lead, 'Writer set this waiting on you without saying what it needs.');
    assert.equal(c.ask, true);
    assert.deepEqual({...c.note, size: undefined}, {...noted, size: undefined});
    await page.locator('.decide [data-dc="ask"]').click();
    assert.equal(await page.evaluate(() => document.activeElement?.matches('.decide .dc-note textarea')), true, 'Comment to ask focuses the box');

    // the note is only ever text
    assert.equal(await page.locator('.decide .dc-body img').count(), 0);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    await shot('blank');
    assert.deepEqual(errors, []);
    console.log('Decide: a waiting card leads with the ask, what the bot said to me or the request; the note folds away');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
