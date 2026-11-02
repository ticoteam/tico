// Shared by the redesign's real-hub journeys (ui/tests/redesign-e2e-*.cjs): a browser, a fresh hub per pass (phone
// 390x844 first, then a desktop at 1440), a page signed in as an Acme teammate, the API as that page sees it, and
// screenshots. TICO_UI_SHOTS=<dir> saves a screenshot at each step worth looking at.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {startHub, pageAs} = require('./real-hub.cjs');
const {t} = require('./load.cjs');

const launch = () => chromium.launch({headless: true,
  channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

// A page as `who`, once the app has drawn its shell; page errors are kept in page.errors. `time` starts the page's clock
// there (it runs on from it), such as the hub's frozen one, so "in 3d" and "Overdue" read as they would on the hub.
async function open(browser, hub, who, {phone = false, hash = '', time = null, timezone = ''} = {}) {
  const page = await pageAs(browser, hub, who, {phone, timezone});
  if (time) await page.clock.install({time});
  // The simulated runner reports version "test", which the hub calls out of date: that card is the harness's, not
  // the journey's, so it stays snoozed and the screenshots show the page.
  await page.context().addInitScript(() => {
    try { localStorage.setItem('tico.needsYou', JSON.stringify({snoozedUntil: Date.now() + 864e5})); } catch {}
  });
  await page.reload();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  await page.waitForFunction(() => {
    const me = document.querySelector('#account .account-email')?.textContent || '';
    return me !== '' && me !== 'Signing in…' && document.querySelector('#main')?.children.length > 0;
  }, null, {timeout: t(20000)});
  if (hash) await go(page, hash);
  return page;
}
const go = (page, hash) => page.evaluate(h => { location.hash = h; }, hash);

// The API as the page's teammate calls it (its Bearer rides on every request); a write carries an Idempotency-Key.
async function api(page, method, p, body) {
  const out = await page.evaluate(async ([method, p, body]) => {
    const r = await fetch('/api/v2/' + p, {method, cache: 'no-store', headers: {'Content-Type': 'application/json',
      ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})}, body: body === undefined ? undefined : JSON.stringify(body)});
    return {status: r.status, body: await r.json().catch(() => null)};
  }, [method, p, body]);
  return out;
}
async function ok(page, method, p, body) {
  const r = await api(page, method, p, body);
  assert.ok(r.status >= 200 && r.status < 300, `${method} ${p}: ${r.status} ${JSON.stringify(r.body).slice(0, 300)}`);
  return r.body;
}
const automationNamed = async (page, name) => (await ok(page, 'GET', 'automations?limit=500')).automations.find(a => a.name === name);

// No sideways scroll on the page itself (a path strip scrolls inside its own box).
const sideways = page => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.evaluate(() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png')});
}

// Runs `pass({hub, browser, phone, tag})` on a phone, then on a desktop, each on a fresh hub with these seeds.
async function journey(name, seeds, pass) {
  const browser = await launch();
  try {
    for (const phone of [true, false]) {
      const hub = await startHub({seeds});
      try {
        assert.deepEqual(hub.skipped, [], 'every seed ran');
        await pass({hub, browser, phone, tag: phone ? 'phone' : 'desktop'});
      } finally { await hub.stop(); }
    }
  } finally { await browser.close(); }
  console.log(name + ': ok');
}
const run = (name, seeds, pass) => journey(name, seeds, pass).catch(e => { console.error(e); process.exit(1); });

module.exports = {run, open, go, api, ok, automationNamed, sideways, shot, t};
