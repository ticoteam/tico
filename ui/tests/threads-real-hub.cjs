// Threads on a real hub (ui/tests/support/real-hub.cjs): a company thread and a members-only thread, made through the API as
// Dana. Dana, on a desktop, reads the company thread and replies in its composer; a sub-thread under one of its messages
// shows inline under its header and opens on its own; and her bot page switches between the bot's Home and DM. Omar, a teammate on the
// desktop, sees the company thread in the sidebar but not the members-only one, and its text is not on his page; opened by
// direct URL it is "Not found"; and on a bot he may see but not read, the bot has no Home tab. Priya's Home is hidden from
// Omar until Dana makes him Priya's manager (reports_to), and then he reads it. Priya, on a phone, taps the company thread
// in the sidebar, sees its composer, and the page does not scroll sideways. Dana, on a phone, finds a late sub-thread reply
// by its root's row, reads DMs by who they are with, and switches a bot's Home and DM.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const PLAN = 'Launch is Tuesday';
const SECRET = 'Pricing war room only';
const KICKOFF = 'Kickoff notes';
const RISKS = 'Sub question about the risks';
const HOME_NOTE = 'Standup moves to ten';
const BOT = 'builder-claude';

// One API call as a teammate, by token: the same shape the counsel journey uses (ui/tests/org-rules-counsel.cjs).
async function api(hub, teammate, method, p, body) {
  const r = await fetch(hub.url + '/api' + p, {method, headers: {Authorization: 'Bearer ' + hub.tokens[teammate],
    'content-type': 'application/json', ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const json = await r.json().catch(() => ({}));
  assert.ok(r.status < 300, `${method} ${p} as ${teammate}: ${r.status} ${JSON.stringify(json)}`);
  return json;
}

// Builder-claude's access: everyone may see it (so it is in the chart and the bot list), and only Dana may read or write
// its Home. Omar sees the bot without reading its Home; Dana is the manager up the chart (builder-claude reports to
// engineering-lead, who reports to Dana).
async function restrictBot(hub) {
  const current = await api(hub, 'dana', 'GET', `/v2/bots/${BOT}/access`);
  const everyone = {everyone: true, people: [], teams: [], bots: []};
  const dana = {everyone: false, people: ['dana'], teams: [], bots: []};
  await api(hub, 'dana', 'PUT', `/v2/bots/${BOT}/access`,
    {see: everyone, read: dana, write: dana, revision: current.revision});
}

// Priya's Home: Dana, the owner, reads it; a teammate who only sees Priya reads it only if he manages her (people.manages).
// Priya's read audience names only herself, so the org chart decides who reads her Home.
async function restrictPerson(hub) {
  const current = await api(hub, 'dana', 'GET', '/v2/humans/priya/access');
  const everyone = {everyone: true, people: [], teams: [], bots: []};
  const priya = {everyone: false, people: ['priya'], teams: [], bots: []};
  await api(hub, 'dana', 'PUT', '/v2/humans/priya/access',
    {see: everyone, read: priya, write: priya, revision: current.revision});
}

(async () => {
  // 'toured': the first-visit tour is seen and the usage notice dismissed, so no dialog covers the page (as automations-due does).
  const hub = await startHub({seeds: ['org', 'toured']});
  let browser;
  try {
    const plan = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Launch plan', audience: 'company', members: [], body: PLAN});
    const secret = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Pricing war room', audience: 'members', members: [], body: SECRET});
    const kickoff = await api(hub, 'dana', 'POST', `/v2/threads/${plan.id}/messages`, {text: KICKOFF, kind: 'say'});
    const risks = await api(hub, 'dana', 'POST', `/v2/threads/${plan.id}/messages`,
      {text: RISKS, kind: 'say', parent_message_id: kickoff.id});
    await restrictBot(hub);
    await restrictPerson(hub);
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

    // Dana, desktop: the company thread shows its message and takes a reply.
    const dana = await pageAs(browser, hub, 'dana');
    await dana.goto(hub.url + '/#/thread/' + plan.id);
    await dana.getByText(PLAN).waitFor({timeout: t(15000)});
    await dana.locator('.thv-composer .p-text').fill('Monday works.');
    await dana.locator('.thv-composer .p-send').click();
    await dana.getByText('Monday works.').waitFor({timeout: t(15000)});

    // Dana: a sub-thread's header shows under its message with the reply count, and opens the sub-thread on its own page.
    const head = dana.locator(`.th-msg:has-text("${KICKOFF}") + .th-subhead`);
    await head.waitFor({timeout: t(15000)});
    assert.match(await head.innerText(), /1 reply/, 'the sub-thread header counts its one reply');
    // The sub-thread's messages sit inline under it, indented; the header, or tapping one, opens it.
    await dana.locator('.th-msg.th-sub', {hasText: RISKS}).waitFor({timeout: t(15000)});
    await head.click();
    await dana.waitForURL(new RegExp('#/thread/' + risks.thread_id), {timeout: t(15000)});
    await dana.locator('.thv-composer .p-text').waitFor({timeout: t(15000)});
    await dana.getByText(RISKS).waitFor({timeout: t(15000)});

    // Dana: Keep messages saves through the real PATCH: the server holds it, a fresh load shows it, and it clears.
    const keepDialog = async () => {
      await dana.locator('[data-thv-menu]').click();
      await dana.locator('.thv-pop').getByText('Keep messages').click();
      return dana.locator('#thread-keep select');
    };
    const saveKeep = async () => {
      await dana.locator('#thread-keep [type=submit]').click();
      await dana.waitForFunction(() => !document.querySelector('#thread-keep')?.open, null, {timeout: t(15000)});
    };
    await dana.goto(hub.url + '/#/thread/' + plan.id);
    await dana.getByText(PLAN).waitFor({timeout: t(15000)});
    const keep = await keepDialog();
    assert.equal(await keep.inputValue(), '', 'a thread keeps its messages by default');
    await keep.selectOption('90');
    await saveKeep();
    assert.equal((await api(hub, 'dana', 'GET', `/v2/threads/${plan.id}`)).retention_days, 90, 'the server holds the length');
    await dana.reload();
    await dana.getByText(PLAN).waitFor({timeout: t(15000)});
    const kept = await keepDialog();
    assert.equal(await kept.inputValue(), '90', 'a fresh load shows the saved length');
    await kept.selectOption('');
    await saveKeep();
    assert.equal((await api(hub, 'dana', 'GET', `/v2/threads/${plan.id}`)).retention_days, null, 'and it clears');
    await dana.context().close();

    // Dana: the bot page's Home and DM are two tabs, and each one selects on its own.
    const bot = await pageAs(browser, hub, 'dana');
    await bot.goto(hub.url + '/#/bot/' + BOT + '/home');
    await bot.locator('#bot-work').waitFor({timeout: t(15000)});
    const homeTab = bot.locator('.bot-talk-switch [data-talk="home"]');
    const dmTab = bot.locator('.bot-talk-switch [data-talk="dm"]');
    await homeTab.waitFor({timeout: t(15000)});
    await dmTab.click();
    await bot.waitForFunction(() => document.querySelector('.bot-talk-switch [data-talk="dm"]')?.getAttribute('aria-selected') === 'true',
      null, {timeout: t(15000)});
    assert.equal(await homeTab.getAttribute('aria-selected'), 'false', 'the DM tab replaces the Home tab as selected');
    await homeTab.click();
    await bot.waitForFunction(() => document.querySelector('.bot-talk-switch [data-talk="home"]')?.getAttribute('aria-selected') === 'true',
      null, {timeout: t(15000)});
    await bot.context().close();

    // Omar, desktop: the company thread is in his sidebar; the members-only one is not, and its text is not on the page.
    // Opened by direct URL, the members-only thread is "Not found", and the bot he may only see has no Home tab.
    const omar = await pageAs(browser, hub, 'omar');
    await omar.goto(hub.url + '/#/tasks');
    await omar.locator('#th-list .th-row-title', {hasText: 'Launch plan'}).first().waitFor({timeout: t(15000)});
    assert.equal(await omar.locator('#th-list .th-row-title', {hasText: 'Pricing war room'}).count(), 0,
      'a members-only thread Omar is not in is not in his sidebar');
    await omar.goto(hub.url + '/#/thread/' + secret.id);
    await omar.getByText('Not found').waitFor({timeout: t(15000)});
    assert.ok(!(await omar.content()).includes(SECRET), 'the members-only text is not on Omar\'s page');
    await omar.goto(hub.url + '/#/bot/' + BOT + '/dm');
    await omar.locator('#btabs').waitFor({state: 'attached', timeout: t(15000)});
    await omar.waitForLoadState('networkidle');
    assert.equal(await omar.locator('.bot-talk-switch [data-talk="home"]').count(), 0,
      'a peer with See only on the bot gets no Home tab');
    await omar.context().close();

    // Priya's Home. Omar sees Priya but is not her manager (Priya reports to Dana), so her Home is hidden from him. Dana then
    // makes him her manager by the team chart alone (reports_to), and the same Omar opens her Home.
    const priyaHome = await api(hub, 'priya', 'GET', '/v2/threads/home/' + encodeURIComponent('human:priya'));
    await api(hub, 'priya', 'POST', `/v2/threads/${priyaHome.id}/messages`, {text: HOME_NOTE, kind: 'say'});
    const peer = await pageAs(browser, hub, 'omar');
    await peer.goto(hub.url + '/#/person/priya');
    await peer.locator('h1', {hasText: 'Priya'}).waitFor({timeout: t(15000)});
    await peer.waitForFunction(() => document.getElementById('person-home')?.hidden === true, null, {timeout: t(15000)});
    assert.equal(await peer.getByText(HOME_NOTE).count(), 0, 'a See-only peer does not read the Home of someone he does not manage');
    await peer.context().close();

    await api(hub, 'dana', 'POST', '/v2/humans/priya', {reports_to: 'omar'});
    const manager = await pageAs(browser, hub, 'omar');
    await manager.goto(hub.url + '/#/person/priya');
    await manager.locator('#person-home-thread').getByText(HOME_NOTE).waitFor({timeout: t(15000)});
    assert.equal(await manager.locator('#person-home').isVisible(), true, 'the manager sees the report\'s Home card');
    await manager.context().close();

    // Priya, phone: the company thread is on the Threads tab (the sidebar is in the closed drawer); tapping it opens the
    // thread with its composer, and the page does not scroll sideways.
    const priya = await pageAs(browser, hub, 'priya', {phone: true});
    await priya.goto(hub.url + '/#/tasks');
    await priya.locator('#mobile-threads').tap();
    await priya.locator('.threads-page .th-row-title', {hasText: 'Launch plan'}).first().waitFor({timeout: t(15000)});
    assert.equal(await priya.locator('.threads-page .th-row-title', {hasText: 'Pricing war room'}).count(), 0);
    await priya.locator('.threads-page a.th-row[data-thread="' + plan.id + '"]').first().tap();
    await priya.waitForURL(new RegExp('#/thread/' + plan.id), {timeout: t(15000)});
    await priya.getByText(PLAN).waitFor({timeout: t(15000)});
    await priya.locator('.thv-composer .p-text').waitFor({timeout: t(15000)});
    const sideways = await priya.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    assert.equal(sideways, false, 'the phone page does not scroll sideways');
    await priya.context().close();

    // Dana, phone. Omar answers in the old sub-thread: the list still shows only roots, the root's row carries the reply,
    // and tapping it lands on that reply. Opening the sub-thread adds no row for it.
    const late = await api(hub, 'omar', 'POST', `/v2/threads/${plan.id}/messages`, {text: 'A late risk from Omar', kind: 'say', parent_message_id: kickoff.id});
    const pair = await api(hub, 'dana', 'POST', '/v2/threads/dm', {members: ['human:priya']});
    const trio = await api(hub, 'dana', 'POST', '/v2/threads/dm', {members: ['human:priya', 'human:omar']});
    await api(hub, 'priya', 'POST', `/v2/threads/${(pair.thread || pair).id}/messages`, {text: 'Got a minute?', kind: 'say'});
    const phone = await pageAs(browser, hub, 'dana', {phone: true});
    await phone.goto(hub.url + '/#/threads');
    const planRow = phone.locator(`.threads-page a.th-row[data-thread="${plan.id}"]`).first();
    await planRow.waitFor({timeout: t(15000)});
    const rowTitles = await phone.locator('.threads-page .th-row-title').allInnerTexts();
    assert.ok(!rowTitles.some(title => [PLAN, KICKOFF, RISKS].includes(title)), 'a sub-thread is not a row: ' + rowTitles);
    assert.equal(await phone.locator(`.threads-page a.th-row[data-thread="${risks.thread_id}"]`).count(), 0);
    assert.match(await planRow.getAttribute('class'), /unread/, 'the root carries the new reply');
    const marked = phone.waitForResponse(r => r.url().includes(`/threads/${risks.thread_id}/read`), {timeout: t(15000)});
    await planRow.tap();
    await phone.waitForURL(new RegExp(`#/thread/${risks.thread_id}\\?m=${late.id}`), {timeout: t(15000)});
    await phone.locator('.th-msg.th-focus', {hasText: 'A late risk from Omar'}).waitFor({timeout: t(15000)});
    await marked;
    assert.equal(await phone.locator(`#th-list a[data-thread="${risks.thread_id}"]`).count(), 0, 'opening a sub-thread adds no row');
    await phone.goto(hub.url + '/#/threads?view=dms');
    await phone.locator(`.threads-page a.th-row[data-thread="${(pair.thread || pair).id}"]`).waitFor({timeout: t(15000)});

    // The same phone: a DM reads as who it is with, in the list and on the Unread threads card.
    const titleOf = id => phone.locator(`.threads-page a.th-row[data-thread="${id}"] .th-row-title`).innerText();
    assert.match(await titleOf((pair.thread || pair).id), /Priya/);
    const group = await titleOf((trio.thread || trio).id);
    assert.ok(group.includes('Priya') && group.includes('Omar'), 'a group DM names everyone: ' + group);
    await phone.goto(hub.url + '/#/home');
    const card = phone.locator('.dash-list a', {hasText: 'Priya'}).first();
    await card.waitFor({timeout: t(15000)});
    assert.equal(await phone.locator('.dash-list a', {hasText: 'Untitled'}).count(), 0, 'no DM on the card is Untitled');

    // The same phone: a bot's Home and DM are named in the top line, and each one switches the page.
    await phone.goto(hub.url + '/#/bot/' + BOT + '/home');
    const selected = tab => phone.waitForFunction(name => document.querySelector(`#btabs [data-bt="${name}"]`)?.getAttribute('aria-selected') === 'true',
      tab, {timeout: t(15000)});
    await selected('home');
    assert.equal(await phone.locator('#btabs [data-bt="dm"]').innerText(), 'DM');
    await phone.locator('#btabs [data-bt="dm"]').tap();
    await selected('dm');
    await phone.locator('#btabs [data-bt="home"]').tap();
    await selected('home');
    await phone.context().close();
    console.log('threads-real-hub: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
