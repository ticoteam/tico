// Threads on a real hub (ui/tests/support/real-hub.cjs): a company thread and a members-only thread, made through the API as
// Dana. Dana, on a desktop, reads the company thread and replies in its composer; a sub-thread under one of its messages
// shows inline and opens on its own; and her bot page switches between the bot's Home and DM. Omar, a teammate on the
// desktop, sees the company thread in the sidebar but not the members-only one, and its text is not on his page; opened by
// direct URL it is "Not found"; and on a bot he may see but not read, the bot has no Home tab. Priya, on a phone, taps the
// company thread in the sidebar, sees its composer, and the page does not scroll sideways.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const PLAN = 'Launch is Tuesday';
const SECRET = 'Pricing war room only';
const KICKOFF = 'Kickoff notes';
const RISKS = 'Sub question about the risks';
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

(async () => {
  const hub = await startHub({seeds: ['org']});
  let browser;
  try {
    const plan = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Launch plan', audience: 'company', members: [], body: PLAN});
    const secret = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Pricing war room', audience: 'members', members: [], body: SECRET});
    const kickoff = await api(hub, 'dana', 'POST', `/v2/threads/${plan.id}/messages`, {text: KICKOFF, kind: 'say'});
    const risks = await api(hub, 'dana', 'POST', `/v2/threads/${plan.id}/messages`,
      {text: RISKS, kind: 'say', parent_message_id: kickoff.id});
    await restrictBot(hub);
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

    // Dana, desktop: the company thread shows its message and takes a reply.
    const dana = await pageAs(browser, hub, 'dana');
    await dana.goto(hub.url + '/#/thread/' + plan.id);
    await dana.getByText(PLAN).waitFor({timeout: t(15000)});
    await dana.locator('.thv-composer .p-text').fill('Monday works.');
    await dana.locator('.thv-composer .p-send').click();
    await dana.getByText('Monday works.').waitFor({timeout: t(15000)});

    // Dana: the sub-thread shows inline under its message, and its header opens the sub-thread on its own page.
    // A sub-thread's header is titled by its parent's first line (threads contract), so it names the kickoff message.
    const head = dana.locator('.th-subhead', {hasText: KICKOFF});
    await head.waitFor({timeout: t(15000)});
    assert.match(await head.innerText(), /1 reply/, 'the sub-thread header counts its one reply');
    await dana.locator('.th-msg', {hasText: RISKS}).first().waitFor({timeout: t(15000)});
    await head.click();
    await dana.waitForURL(new RegExp('#/thread/' + risks.thread_id), {timeout: t(15000)});
    await dana.locator('.thv-composer .p-text').waitFor({timeout: t(15000)});
    assert.ok((await dana.content()).includes(RISKS), 'the opened sub-thread shows its own message');
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
    await omar.locator('#btabs').waitFor({timeout: t(15000)});
    await omar.waitForLoadState('networkidle');
    assert.equal(await omar.locator('.bot-talk-switch [data-talk="home"]').count(), 0,
      'a peer with See only on the bot gets no Home tab');
    await omar.context().close();

    // Priya, phone: the company thread is in the sidebar; tapping it opens the thread with its composer, and the page
    // does not scroll sideways.
    const priya = await pageAs(browser, hub, 'priya', {phone: true});
    await priya.goto(hub.url + '/#/tasks');
    await priya.locator('#th-list .th-row-title', {hasText: 'Launch plan'}).first().waitFor({timeout: t(15000)});
    assert.equal(await priya.locator('#th-list .th-row-title', {hasText: 'Pricing war room'}).count(), 0);
    await priya.locator('#th-list a.th-row[data-thread="' + plan.id + '"]').tap();
    await priya.waitForURL(new RegExp('#/thread/' + plan.id), {timeout: t(15000)});
    await priya.getByText(PLAN).waitFor({timeout: t(15000)});
    await priya.locator('.thv-composer .p-text').waitFor({timeout: t(15000)});
    const sideways = await priya.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    assert.equal(sideways, false, 'the phone page does not scroll sideways');
    await priya.context().close();
    console.log('threads-real-hub: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
