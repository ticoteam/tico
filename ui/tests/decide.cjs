// Decide on a phone (390px): what a person asked for leads. A task card leads with the plain ask, large, and the title second, smaller; the bot's
// status note, the rest of the body and anything else it said fold under Details, as text. The ask is an open
// question to me, else the one the bot wrote in its waiting note (`person_ask`). A yes/no ask gets Yes and No, which
// answer the bot with a comment. A card with no plain ask (`ask_missing`) comes last and says it waits for the bot
// to explain; it never says the bot set it waiting without saying what it needs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = minutes => new Date(NOW - minutes * 60000).toISOString();
const NOTE = 'Engineering answer db14c907 resolves initial inquiry 8719e1a6: public example is independent static Larkspur. '
  + 'Docs task Doing; no sample edit/deploy or duplicate human question. <img src=x onerror="window.pwned=1">';
const PLAIN = "Approve the video tool trial on Thursday? If yes, I'll send the invite.";
const wait = (id, title, over = {}) => ({id, kind: 'waiting', title, body: '', owner: 'bot:writer', requester: 'bot:botops',
  status: 'waiting', waiting_on: 'human:ana', note: NOTE, version: 2, created: at(400), updated: at(360), ask: null, open_asks: 0,
  person_ask: '', ask_missing: false, ...over});
const items = [
  wait('t-ask', 'Coordinate the external agent scenario in the example', {created: at(500),
    ask: {id: 'q-1', from_actor: 'bot:writer', to_actor: 'human:ana', body: 'Should the public example show the agent demo, or stay static?', created: at(300)}}),
  wait('t-plain', 'Run the video tool trial', {created: at(490), note: PLAIN, person_ask: PLAIN,
    body: 'Run the video tool trial\n\nThe test plan is in the shared folder.'}),
  // set waiting before the rule, with no plain ask: listed first here, shown last
  wait('t-blank', 'Check the newsletter list', {created: at(480), ask_missing: true}),
  // a person asked: their name stays plain text, no link
  {id: 't-person', kind: 'task', title: 'Read the Q4 board notes', body: 'Read the Q4 board notes\n\nBefore Thursday, please.', owner: 'human:ana',
    requester: 'human:sam', status: 'open', version: 1, created: at(460), updated: at(460), ask: null, open_asks: 0},
];
const comments = {
  // the status note's mirror in chat, then a message to me: it folds under Details, not the headline
  't-blank': [{id: 'm-1', kind: 'say', from_actor: 'bot:writer', to_actor: 'human:ana', body: NOTE, refs: {task: 't-blank', note: true}, created: at(360)},
    {id: 'm-2', kind: 'say', from_actor: 'bot:writer', to_actor: 'human:ana', body: 'Blocked on 3f2a9c1d, see run log.', refs: {task: 't-blank', comment: true}, created: at(350)}],
};
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks'});
    const sent = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => {
      const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2));
      if (route.request().method() === 'POST') { sent.push({id, ...route.request().postDataJSON()}); return route.fulfill({contentType: 'application/json', body: '{}'}); }
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({comments: comments[id] || []})});
    });
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { await v2Refresh(); location.hash = DECIDE; });
    const card = page.locator('.decide .dc-card');
    const read = async id => {
      await page.locator(`.decide .dc-card[data-key="task:${id}"]`).waitFor();
      await page.waitForFunction(id => DEC?.data.get(id)?.comments, id);
      return card.evaluate(el => {
        const body = el.querySelector('.dc-body'), [first, second] = body.children, details = body.querySelector('.dc-details');
        return {sub: el.querySelector('.dc-who-sub').textContent, lead: first.textContent.trim(), leadClass: first.className,
          title: second?.className === 'dc-title' ? second.textContent : null,
          sizes: [first, second].map(x => parseFloat(getComputedStyle(x).fontSize)),
          details: details ? {open: details.open, text: details.textContent, last: body.lastElementChild === details} : null,
          buttons: [...document.querySelectorAll('.decide .dc-actions button')].map(b => b.dataset.dc),
          ask: !!body.querySelector('[data-dc="ask"]')};
      });
    };
    // DECIDE_SHOTS=<dir> saves each card for the owner.
    const shot = name => process.env.DECIDE_SHOTS && page.screenshot({path: require('node:path').join(process.env.DECIDE_SHOTS, name + '.png')});
    // Act once the last action's lock (350 ms) has passed: a tap inside it does nothing, by design.
    const tap = async what => {
      await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
      await page.locator(`.decide .dc-actions [data-dc="${what}"]`).click();
    };

    // What a person asked for leads, ahead of a bot's older wait on me; a bot's pressing item (due, or old) joins it.
    // The card says whose request it is when that is not the name it shows.
    assert.deepEqual(await page.evaluate(() => ({
      order: decideCards([
        {id: 'bot-made', kind: 'waiting', owner: 'bot:writer', requester: 'bot:writer', waiting_on: 'human:ana', created: '2026-01-01T00:00:00Z'},
        {id: 'for-sam', kind: 'task', owner: 'human:ana', requester: 'bot:writer', from_person: 'human:sam', created: '2026-01-03T00:00:00Z'},
        {id: 'overdue', kind: 'waiting', owner: 'bot:writer', requester: 'bot:writer', waiting_on: 'human:ana', pressing: true, created: '2026-01-02T00:00:00Z'},
      ]).map(c => c.key),
      cue: [decideFromPerson({from_person: 'human:sam'}, 'bot:writer'), decideFromPerson({from_person: 'human:ana'}, 'bot:writer'),
        decideFromPerson({from_person: 'human:sam'}, 'human:sam'), decideFromPerson({}, 'bot:writer')],
    })), {order: ['task:overdue', 'task:for-sam', 'task:bot-made'], cue: ['From Sam', 'You asked', '', '']});

    // An open question to me leads, the title under it, smaller; the note folds under Details, closed.
    let c = await read('t-ask');
    assert.match(c.sub, /^Waiting on you · /);
    assert.equal(c.lead, 'Should the public example show the agent demo, or stay static?');
    assert.equal(c.leadClass, 'dc-msg md');
    assert.equal(c.title, 'Coordinate the external agent scenario in the example');
    assert.ok(c.sizes[1] < c.sizes[0], `the title is smaller than the ask: ${c.sizes}`);
    assert.deepEqual({...c.details, text: undefined}, {open: false, last: true, text: undefined});
    assert.ok(c.details.text.includes('Latest note') && c.details.text.includes('db14c907'));
    assert.deepEqual(c.buttons, ['skip', 'done'], 'a question with "or" is not a yes or a no');
    await shot('ask');
    await tap('skip');

    // The bot's plain yes/no ask leads, with Yes and No; the rest of the body is a detail. Yes answers the bot.
    c = await read('t-plain');
    assert.equal(c.lead, PLAIN);
    assert.equal(c.title, 'Run the video tool trial');
    assert.ok(c.details.text.includes('The test plan is in the shared folder.') && !c.details.text.includes('Latest note'));
    assert.deepEqual(c.buttons, ['no', 'yes']);
    await page.locator('.decide .dc-details summary').click();
    await shot('yes-no');
    await tap('yes');
    await page.locator('.decide .dc-card[data-key="task:t-person"]').waitFor();
    // The pending action clears as its send starts; the outbox empties once the server has the write.
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(sent, [{id: 't-plain', text: 'Yes.'}]);

    // A person's card: the request leads, and the name is plain, with no link.
    const who = () => page.locator('.decide .dc-who').evaluate(el => ({name: el.querySelector('.dc-who-name').textContent,
      links: [...el.querySelectorAll('a')].map(a => ({cls: a.className, href: a.getAttribute('href'), tab: a.tabIndex, text: a.textContent.trim()})),
      height: el.getBoundingClientRect().height}));
    c = await read('t-person');
    assert.equal(c.lead, 'Before Thursday, please.');
    const person = await who();
    assert.equal(person.name, 'Sam');
    assert.deepEqual(person.links, []);
    await tap('skip');

    // No plain ask: last, marked as waiting for the bot to explain, never "without saying what it needs".
    c = await read('t-blank');
    assert.match(c.sub, /^Waiting for Writer to explain · /);
    assert.equal(c.lead, 'Waiting for Writer to explain');
    assert.equal(c.ask, true);
    assert.ok(c.details.text.includes('Latest message') && c.details.text.includes('Blocked on 3f2a9c1d'));
    assert.equal(await page.getByText('without saying what it needs').count(), 0);
    await page.locator('.decide [data-dc="ask"]').click();
    assert.equal(await page.evaluate(() => document.activeElement?.matches('.decide .dc-note textarea')), true, 'Comment to ask focuses the box');
    await page.locator('.decide .dc-details summary').click();
    await shot('waiting-to-explain');
    // the note is only ever text
    assert.equal(await page.locator('.decide .dc-body img').count(), 0);
    assert.equal(await page.evaluate(() => window.pwned), undefined);

    // A bot's face and name open the bot, as the side rail does; the face is not a second tab stop.
    const bot = await who();
    assert.deepEqual(bot.links.map(({cls, href, tab}) => ({cls, href, tab})), [
      {cls: 'dc-who-face', href: '#/bot/writer', tab: -1}, {cls: 'dc-who-link', href: '#/bot/writer', tab: 0}]);
    assert.equal(bot.links[1].text, 'Writer');
    // Sub-pixel layout can differ by a rounding hair (60.5000038 against 60.5); a real change is whole pixels.
    assert.ok(Math.abs(person.height - bot.height) < 0.5, `the header keeps its height: ${person.height} vs ${bot.height}`);
    assert.deepEqual(errors, []);

    // Tapping the name goes to the chat and does nothing to the card
    const writes = [];
    page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()); });
    await page.locator('.decide .dc-who-link').click();
    await page.waitForFunction(() => location.hash === '#/bot/writer');
    assert.deepEqual(writes, [], 'no card action was sent');
    console.log('Decide: a task card leads with the plain ask, title second, details folded; a yes/no ask answers the bot; '
      + 'a card with no ask waits last for its bot to explain');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
