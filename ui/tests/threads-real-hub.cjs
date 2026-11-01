// Threads on a real hub (ui/tests/support/real-hub.cjs): a company thread and a members-only thread, made through the API as
// Dana. Dana, on a desktop, reads the company thread and replies in its composer. Omar, a teammate on the desktop, sees the
// company thread in the sidebar but not the members-only one, and its text is not on his page. Priya, on a phone, sees the
// company thread and its composer, and the page does not scroll sideways.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const PLAN = 'Launch is Tuesday';
const SECRET = 'Pricing war room only';

// One API call as a teammate, by token: the same shape the counsel journey uses (ui/tests/org-rules-counsel.cjs).
async function api(hub, teammate, method, p, body) {
  const r = await fetch(hub.url + '/api' + p, {method, headers: {Authorization: 'Bearer ' + hub.tokens[teammate],
    'content-type': 'application/json', ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const json = await r.json().catch(() => ({}));
  assert.ok(r.status < 300, `${method} ${p} as ${teammate}: ${r.status} ${JSON.stringify(json)}`);
  return json;
}

(async () => {
  const hub = await startHub({seeds: ['org']});
  let browser;
  try {
    const plan = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Launch plan', audience: 'company', members: [], body: PLAN});
    const secret = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Pricing war room', audience: 'members', members: [], body: SECRET});
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

    // Dana, desktop: the company thread shows its message and takes a reply.
    const dana = await pageAs(browser, hub, 'dana');
    await dana.goto(hub.url + '/#/thread/' + plan.id);
    await dana.getByText(PLAN).waitFor({timeout: t(15000)});
    await dana.locator('.thv-composer .p-text').fill('Monday works.');
    await dana.locator('.thv-composer .p-send').click();
    await dana.getByText('Monday works.').waitFor({timeout: t(15000)});
    await dana.context().close();

    // Omar, desktop: the company thread is in his sidebar; the members-only one is not, and its text is not on the page.
    const omar = await pageAs(browser, hub, 'omar');
    await omar.goto(hub.url + '/#/tasks');
    await omar.locator('#th-list .th-row-title', {hasText: 'Launch plan'}).first().waitFor({timeout: t(15000)});
    assert.equal(await omar.locator('#th-list .th-row-title', {hasText: 'Pricing war room'}).count(), 0,
      'a members-only thread Omar is not in is not in his sidebar');
    await omar.goto(hub.url + '/#/thread/' + secret.id);
    await omar.waitForLoadState('networkidle');
    assert.ok(!(await omar.content()).includes(SECRET), 'the members-only text is not on Omar\'s page');
    await omar.context().close();

    // Priya, phone: the company thread and its composer fit the width, with no sideways scroll.
    const priya = await pageAs(browser, hub, 'priya', {phone: true});
    await priya.goto(hub.url + '/#/tasks');
    await priya.locator('#th-list .th-row-title', {hasText: 'Launch plan'}).first().waitFor({timeout: t(15000)});
    assert.equal(await priya.locator('#th-list .th-row-title', {hasText: 'Pricing war room'}).count(), 0);
    await priya.goto(hub.url + '/#/thread/' + plan.id);
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
