// The last screen comes back the moment the same person opens the app again (ui/app/boot-snap.js): it is kept once the
// page settles, shown as soon as /me answers as that person (before the roster), stays still and inert while the new
// page draws under it, and goes when that page settles. Another person, or a sign-out, never sees it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = require('./support/load.cjs');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const context = await browser.newContext({serviceWorkers: 'block'});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // Under automation the kept screen is off unless the page asks for it, as this script does.
    await page.addInitScript(() => { window.EventSource = undefined; window.ticoBootSnap = true; });
    let person = 'ana', held = null;
    await page.route('**/*', async route => {
      const p = new URL(route.request().url()).pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p.startsWith('/tico/ui/')) {
        const file = uiFile(p.slice('/tico/ui/'.length));
        if (fs.existsSync(file)) return route.fulfill({path: file});
      }
      if (p === '/api/me') return json({id: person, email: person + '@acme.example', role: 'owner', cloud: true, owner_id: 'ana',
        config: {company_name: 'Acme'}});
      if (p === '/api/employees') {
        const answer = () => json([{name: 'ops', display_name: 'Ops', status: 'active', host: 'keeper', can_chat: true}]);
        if (held) { held.push(answer); return; }
        return answer();
      }
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'bo', name: 'Bo'}], groups: []});
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle', open_tasks: 0}]});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/task-types') return json({types: []});
      return json(unrouted(p));
    });
    // The kept entry, read without creating the database when there is none.
    const kept = () => page.evaluate(async () => {
      if (!(await indexedDB.databases()).some(d => d.name === 'tico-boot')) return null;
      return new Promise(done => {
        const req = indexedDB.open('tico-boot');
        req.onsuccess = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains('screens')) { db.close(); done(null); return; }
          const get = db.transaction('screens').objectStore('screens').get(location.origin);
          get.onsuccess = () => { db.close(); done(get.result || null); };
          get.onerror = () => { db.close(); done(null); };
        };
        req.onerror = () => done(null);
      });
    });
    const keptAs = async (test, what) => {
      for (const end = Date.now() + t(10000); Date.now() < end; await page.waitForTimeout(50)) {
        const entry = await kept();
        if (test(entry)) return entry;
      }
      assert.fail(what);
    };
    const heading = () => page.getByRole('heading', {name: 'Tasks', exact: true});

    await page.goto('https://tico-ui.test/#/tasks', {waitUntil: 'domcontentloaded'});
    await heading().waitFor();
    const first = await keptAs(entry => entry?.route === '#/tasks', 'the settled screen is kept');
    assert.deepEqual(JSON.parse(first.who), ['https://tico-ui.test', 'ana', 'ana', 'ana@acme.example']);
    assert.match(first.html, /Tasks/);
    assert.doesNotMatch(first.html, /<(?:iframe|script|dialog|video|object)\b/i, 'nothing that runs or loads on its own');
    assert.ok(new TextEncoder().encode(first.html).length <= 256 * 1024);

    // Again, with the roster held: the kept screen is up before it, still and out of reach.
    held = [];
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-cached').length === 1);
    const early = await page.evaluate(() => {
      const main = document.getElementById('main');
      return {inert: main.inert, busy: main.getAttribute('aria-busy'), text: main.textContent,
        routed: performance.getEntriesByName('tico:route').length, held: true};
    });
    assert.equal(early.inert, true);
    assert.equal(early.busy, 'true');
    assert.match(early.text, /Tasks/);
    assert.equal(early.routed, 0, 'shown before the roster answered');
    assert.ok(held.length > 0, 'the roster is still on its way');
    // The roster answers: the page draws under a still copy, which goes once it settles.
    const answers = held; held = null; answers.forEach(answer => answer());
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-fresh').length === 1);
    await page.waitForFunction(() => !document.querySelector('[data-boot-snap]'));
    const after = await page.evaluate(() => ({mains: document.querySelectorAll('main#main').length,
      hidden: document.getElementById('main').style.visibility, inert: document.getElementById('main').inert}));
    assert.deepEqual(after, {mains: 1, hidden: '', inert: false});
    await heading().waitFor();

    // Someone else signed in here: nothing of Ana's is shown, and her screen is gone.
    person = 'bo'; held = [];
    await page.reload({waitUntil: 'domcontentloaded'});
    await keptAs(entry => entry === null, "another person's open drops the kept screen");
    assert.equal(await page.evaluate(() => performance.getEntriesByName('tico:paint-cached').length), 0);
    assert.equal(await page.evaluate(() => document.getElementById('main').childElementCount), 0, 'nothing drawn before the roster');
    const forBo = held; held = null; forBo.forEach(answer => answer());
    await heading().waitFor();
    const second = await keptAs(entry => entry?.route === '#/tasks', 'the new person gets their own');
    assert.equal(JSON.parse(second.who)[2], 'bo');

    // Signing out leaves no screen behind (the navigation itself is not followed).
    await page.evaluate(() => {
      document.querySelector('#sign-out').hidden = false;
      window.addEventListener('click', e => { if (e.target.closest('#sign-out')) e.preventDefault(); });
      document.querySelector('#sign-out').click();
      window.dispatchEvent(new Event('pagehide'));
    });
    await keptAs(entry => entry === null, 'sign-out drops the kept screen');
    assert.deepEqual(errors, []);
    console.log('boot snap: the same person sees their last screen at once; nobody else, and nothing after sign-out');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
