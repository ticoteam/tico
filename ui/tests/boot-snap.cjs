// The last screen comes back the moment the same person opens the app again (ui/app/boot-snap.js): it is kept once the
// page settles (only read-mostly pages, nothing private), shown as soon as /me answers as that person (before the
// roster) in a shadow root over #main, out of the page's id space, while the new page draws into the real #main under
// it, and goes when that page settles, or at most SNAP_SHOW_MAX_MS after it was shown. Another person, or a sign-out,
// never sees it, and a sign-out takes a copy on screen down at once.
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
    await context.addInitScript(() => { window.EventSource = undefined; window.ticoBootSnap = true; });
    let person = 'ana', held = null;
    await context.route('**/*', async route => {
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

    const copyState = () => page.evaluate(() => {
      const host = document.querySelector('[data-boot-snap]'), main = document.getElementById('main');
      const inner = host?.shadowRoot?.querySelector('main');
      return {host: !!host, inert: host?.inert, hidden: host?.getAttribute('aria-hidden'), text: inner?.textContent || '',
        innerId: inner?.id, mains: document.querySelectorAll('#main').length, mainKids: main.childElementCount,
        mainShown: main.style.visibility, routed: performance.getEntriesByName('tico:route').length};
    });

    await page.goto('https://tico-ui.test/#/tasks', {waitUntil: 'domcontentloaded'});
    await heading().waitFor();
    const first = await keptAs(entry => entry?.route === '#/tasks', 'the settled screen is kept');
    assert.deepEqual(JSON.parse(first.who), ['https://tico-ui.test', 'ana', 'ana', 'ana@acme.example']);
    assert.match(first.html, /Tasks/);
    assert.doesNotMatch(first.html, /<(?:iframe|script|dialog|video|object)\b/i, 'nothing that runs or loads on its own');
    assert.ok(new TextEncoder().encode(first.html).length <= 256 * 1024);

    // Only read-mostly pages are kept, and nothing private on them is.
    assert.deepEqual(await page.evaluate(() => ['#/home', '#/overview', '#/tasks', '#/tasks?view=board', '#/threads', '#/team/eng',
      '#/settings', '#/settings/keys', '#/task/t1', '#/thread/x', '#/bot/ops/dm', '#/mail', '#/welcome', '#/team/eng/x', '']
      .map(snapRouteOk)), [true, true, true, true, true, true, false, false, false, false, false, false, false, false, false]);
    await page.evaluate(() => {
      const bits = document.createElement('div');
      bits.id = 'private-bits';
      bits.innerHTML = '<div data-secret>sk-live-secret</div><input value="typed-secret"><input type="password" value="pw-secret">'
        + '<textarea>draft-secret</textarea><div contenteditable="true">edit-secret</div>'
        + '<div class="settings-card"><code>code-secret</code><pre>pre-secret</pre></div><p>kept-words</p>';
      document.getElementById('main').append(bits);
      bootSnapSave();
    });
    const scrubbed = await keptAs(entry => /kept-words/.test(entry?.html || ''), 'the screen is kept again');
    assert.doesNotMatch(scrubbed.html, /secret/, 'no secret, value, draft or settings code is kept');
    await page.evaluate(() => { document.getElementById('private-bits').remove(); bootSnapSave(); });
    await keptAs(entry => entry && !/kept-words/.test(entry.html), 'kept without the test bits');

    // Again, with the roster held: the kept screen is up before it, in a copy outside #main's id space.
    held = [];
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-cached').length === 1);
    const early = await copyState();
    assert.deepEqual({...early, text: /Tasks/.test(early.text)}, {host: true, inert: true, hidden: 'true', text: true,
      innerId: 'main', mains: 1, mainKids: 0, mainShown: 'hidden', routed: 0}, 'shown before the roster, the real #main empty');
    assert.ok(held.length > 0, 'the roster is still on its way');
    // The roster answers: the page draws into the real #main under the copy, which goes once it settles.
    const answers = held; held = null; answers.forEach(answer => answer());
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-fresh').length === 1);
    await page.waitForFunction(() => !document.querySelector('[data-boot-snap]'));
    const after = await copyState();
    assert.deepEqual({mains: after.mains, shown: after.mainShown, drawn: after.mainKids > 0}, {mains: 1, shown: '', drawn: true});
    await heading().waitFor();

    // A roster that never answers: past boot's wait the copy goes, and the page says it couldn't load, with Try again
    // (never a blank page, never a wait for ever).
    held = [];
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-cached').length === 1);
    const shownAt = Date.now();
    await page.getByRole('heading', {name: "Couldn't load Tico"}).waitFor({timeout: t(20000)});
    assert.ok(Date.now() - shownAt > 7000, 'it stayed up until the wait ended');
    const stalled = await copyState();
    assert.deepEqual({host: stalled.host, routed: stalled.routed, shown: stalled.mainShown}, {host: false, routed: 0, shown: ''},
      'the copy is down while the roster still has not answered');
    const late = held; held = null; late.forEach(answer => answer());
    await page.getByRole('button', {name: 'Try again'}).click();
    await heading().waitFor();

    // Someone else signed in here: nothing of Ana's is shown, and her screen is gone.
    person = 'bo'; held = [];
    await page.reload({waitUntil: 'domcontentloaded'});
    await keptAs(entry => entry === null, "another person's open drops the kept screen");
    assert.equal(await page.evaluate(() => performance.getEntriesByName('tico:paint-cached').length), 0);
    const forBoState = await copyState();
    assert.deepEqual({host: forBoState.host, kids: forBoState.mainKids}, {host: false, kids: 0}, 'nothing drawn before the roster');
    const forBo = held; held = null; forBo.forEach(answer => answer());
    await heading().waitFor();
    const second = await keptAs(entry => entry?.route === '#/tasks', 'the new person gets their own');
    assert.equal(JSON.parse(second.who)[2], 'bo');

    // A second tab of the same org, open and settled before the sign-out, has its own screen to keep.
    const other = await context.newPage();
    other.on('pageerror', e => errors.push(e.message));
    await other.goto('https://tico-ui.test/#/tasks', {waitUntil: 'domcontentloaded'});
    await other.getByRole('heading', {name: 'Tasks', exact: true}).waitFor();
    await other.waitForFunction(() => performance.getEntriesByName('tico:paint-fresh').length === 1);
    assert.equal(await other.evaluate(() => SNAP.who !== ''), true, 'the other tab would keep its screen');

    // Signing out while the copy is up takes it down at once, and leaves no screen behind (the navigation itself is
    // not followed).
    held = [];
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-cached').length === 1);
    const out = await page.evaluate(() => {
      const link = document.querySelector('#sign-out');
      link.hidden = false;
      window.addEventListener('click', e => { if (e.target.closest('#sign-out')) e.preventDefault(); });
      link.click();
      const main = document.getElementById('main');
      return {host: !!document.querySelector('[data-boot-snap]'), kids: main.childElementCount, shown: main.style.visibility};
    });
    assert.deepEqual(out, {host: false, kids: 0, shown: ''}, 'nothing of the screen is left on the page');
    await keptAs(entry => entry === null, 'sign-out drops the kept screen');
    // The roster answering after the sign-out, the page settling and leaving keep nothing either.
    const rest = held; held = null; rest.forEach(answer => answer());
    await heading().waitFor();
    await page.waitForFunction(() => performance.getEntriesByName('tico:paint-fresh').length === 1);
    await page.waitForTimeout(2500);
    await page.evaluate(() => { bootSnapSave(); window.dispatchEvent(new Event('pagehide')); });
    await page.waitForTimeout(300);
    assert.equal(await kept(), null, 'nothing is kept again after a sign-out in this page');
    // Nor by the other tab, which never heard a 401: the sign-out reached it, and its leaving keeps nothing.
    const otherAfter = await other.evaluate(() => { bootSnapSave(); window.dispatchEvent(new Event('pagehide')); return SNAP.who; });
    assert.equal(otherAfter, '', "the other tab drops whose screen it was");
    await other.close({runBeforeUnload: true});
    await page.waitForTimeout(300);
    assert.equal(await kept(), null, 'a sign-out in one tab bans keeping in every tab open before it');
    assert.deepEqual(errors, []);
    console.log('boot snap: the same person sees their last screen at once, outside #main; nothing private, nobody else, nothing after sign-out');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
