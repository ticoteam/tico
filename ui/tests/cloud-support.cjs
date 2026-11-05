// Tico support on cloud machines (ui/app/cloud-machines.js, GET/PUT /api/v2/cloud-machines/support), fixtures only:
//  - Settings > People: on Tico Cloud the owner's team rules carry the switch, as HQ reads it; a change PUTs it, and a
//    failed or uncertain change re-reads HQ with the reason under the switch;
//  - an admin sees the switch read-only; off Tico Cloud (`available` false) there is no switch;
//  - Settings > Computers: each cloud machine's row names its helper's state, matched by HQ's key (`_shared` or the
//    bot), a helper that is not there shows nothing, and each Settings entry reads current state;
//  - a failed read shows nothing and is retried on the next entry, in the same browser page.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const CONFIG = {environment_id: 'acme', company_name: 'Acme', app_name: 'Acme Hub', assistant_name: 'Ace', assistant_bot: 'coo',
  public_url: 'https://acme.test', runner_url: 'https://acme.test', github_owner: '', local: false, release: '',
  onboarding_needed: false, providers_configured: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const context = await browser.newContext({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    const people = [{id: 'ana', name: 'Ana Rivera', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben Cole', email: 'ben@acme.example'}];
    const machines = [{id: 'cm-shared', key: '_shared', helper: 'on', seen: '2026-11-05T10:00:00Z'},
      {id: 'cm-helper', key: 'helper', helper: 'offline', seen: null}, {id: 'cm-scout', key: 'scout', helper: 'none', seen: null}];
    let me = {id: 'ana', name: 'Ana Rivera', role: 'owner', cloud: true, email: 'ana@acme.example', config: CONFIG};
    let support = {available: true, on: true, machines}, failPut = false, failGet = false;
    let rules = {};
    const reads = [], writes = [], writeKeys = [], changes = [];
    const receipts = new Map();
    await context.route('**/*', async route => {
      const request = route.request(), p = new URL(request.url()).pathname, method = request.method();
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const module = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (module && fs.existsSync(uiFile(module[1])))
        return route.fulfill({contentType: module[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(module[1]), 'utf8')});
      if (p === '/api/v2/cloud-machines/support' && method === 'GET') {
        reads.push(p);
        return failGet ? json({error: {code: 'hq_unreachable', detail: "Didn't reach Tico HQ. Try again."}}, 502) : json(support);
      }
      if (p === '/api/v2/cloud-machines/support' && method === 'PUT') {
        const body = request.postDataJSON(); writes.push(body);
        const key = request.headers()['idempotency-key']; writeKeys.push(key);
        assert.ok(key, 'every toggle has an idempotency key');
        if (receipts.has(key)) {
          if (failPut === 'lost') return route.abort('failed');
          return receipts.get(key) ? json(receipts.get(key))
            : json({error: {code: 'hq_unconfirmed', detail: 'Read the switch again.'}}, 409);
        }
        receipts.set(key, null);
        if (failPut === true) return json({error: {code: 'hq_unreachable', detail: "Didn't reach Tico HQ. Try again."}}, 502);
        changes.push(body.on);
        support = {...support, on: body.on, machines: machines.map(m => ({...m, helper: m.helper === 'none' ? 'none' : body.on ? 'on' : 'off'}))};
        // HQ saved it, but core never received its answer.
        if (failPut === 'applied') return json({error: {code: 'hq_unreachable', detail: "Didn't reach Tico HQ. Try again."}}, 502);
        receipts.set(key, structuredClone(support));
        if (failPut === 'lost') return route.abort('failed');
        return json(support);
      }
      if (p === '/api/me') return json(me);
      if (p === '/api/v2/config') return json(CONFIG);
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], recent_runs: []});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people});
      if (p === '/api/v2/models') return json({models: [], harnesses: [], enabled_providers: [], default: {}});
      if (p === '/api/v2/operations') return json({services: [], scheduler_enabled: true, machines: [
        {id: 'r1', label: 'Box', owner_id: 'ana', bots: []},
        {id: 'r9', label: 'Helper (cloud)', owner_id: 'ana', bots: [], cloud_machine: {bot: 'helper', shared: false, size: 'default', state: 'stopped'}},
        {id: 'r8', label: 'Shared cloud machine', owner_id: 'ana', bots: [], cloud_machine: {bot: null, shared: true, size: 'default', state: 'running'}},
        {id: 'r7', label: 'Scout (cloud)', owner_id: 'ana', bots: [], cloud_machine: {bot: 'scout', shared: false, size: 'small', state: 'stopped'}}]});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p === '/api/v2/access/rules' && method === 'PUT') {
        rules = {...rules, ...request.postDataJSON()};
        return json(rules);
      }
      if (p === '/api/v2/access' && method === 'GET') return json({
        owner: {email: 'ana@acme.example', revision: 1, person: 'ana'},
        people: people.map(x => ({...x, left: false, owner: x.id === 'ana', role: x.id === 'ana' ? 'owner' : 'admin', can_sign_in: true, sign_in: true})),
        allowed: [], allowed_domains: [], admins: [], bot_admins: [], member_bot_limit: 25, rules, company_domains: [],
        revision: 1, proxy: '', home_domain: '', domain_sign_in: false, directory: ''});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const errors = [];
    const open = async tab => {
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(`https://tico-ui.test/#/settings/${tab}`);
      await page.locator(`[data-settings-tab=${tab}][aria-selected=true]`).waitFor();
      return page;
    };
    const enter = async (page, tab) => {
      await page.locator('[data-settings-tab=general]').click();
      await page.locator(`[data-settings-tab=${tab}]`).click();
      await page.evaluate(() => CLOUD_SUPPORT.read);
    };

    // The owner: the switch sits with the team rules, on as HQ reads it; turning it off saves at HQ.
    let page = await open('people');
    const toggle = page.locator('#people-cloud-support');
    await toggle.waitFor();
    assert.equal(await toggle.isChecked(), true);
    assert.equal(await toggle.isDisabled(), false);
    assert.equal(await page.locator('.people-rules #people-cloud-support').count(), 1, 'with the other team rules');
    await toggle.click();
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.disabled === false);
    assert.deepEqual(writes.shift(), {on: false});
    assert.equal(await toggle.isChecked(), false);
    const afterOpen = reads.length;
    // HQ not reached: nothing changes, and the reason sits under the switch.
    failPut = true;
    await toggle.click();
    await page.locator('#set-people [data-field-err]').waitFor();
    // The write layer tries a 502 again before it gives up; every try asks for the same change.
    assert.ok(writes.length >= 1 && writes.every(body => body.on === true));
    writes.length = 0;
    assert.equal(await toggle.isChecked(), false, 'a failed change keeps the old state');
    assert.equal(await toggle.isDisabled(), false);
    assert.ok((await page.locator('#set-people [data-field-err]').innerText()).trim());
    assert.equal(reads.length, afterOpen + 1, 'a failed write re-reads HQ');
    failPut = false;

    // A lost write answer must show HQ's new value, rather than undoing the checkbox locally.
    failPut = 'applied';
    await toggle.click();
    await page.locator('#set-people [data-field-err]').waitFor();
    assert.equal(await toggle.isChecked(), true, 'HQ accepted the enable despite the lost answer');
    assert.equal(await toggle.isDisabled(), false);
    writes.length = 0;
    failPut = false;

    // A saved disable with all its answers lost recovers by GET. Later enable/disable choices must reach HQ.
    const firstKey = writeKeys.length, firstChange = changes.length;
    failPut = 'lost';
    await toggle.click();
    await page.locator('#set-people [data-field-err]').waitFor();
    assert.equal(await toggle.isChecked(), false, 'the recovery GET sees the saved disable');
    const retries = writeKeys.slice(firstKey);
    assert.equal(retries.length, 3);
    assert.equal(new Set(retries).size, 1, 'automatic retries keep the same key');
    assert.equal(await page.evaluate(() => pendingWrites.has('PUT /v2/cloud-machines/support\n{"on":false}')), false,
      'recovery forgets the unconfirmed write');
    failPut = false;
    await toggle.click();
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.disabled === false);
    await toggle.click();
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.disabled === false);
    assert.deepEqual(changes.slice(firstChange), [false, true, false], 'every explicit choice reaches HQ');
    assert.equal(new Set(writeKeys.slice(firstKey)).size, 3, 'each explicit toggle gets a fresh key');
    assert.equal(support.on, false);
    assert.equal(await toggle.isChecked(), false);
    // Leave the owner enabled for the entry-refresh checks below.
    await toggle.click();
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.disabled === false);
    writes.length = 0;
    const afterFailures = reads.length;
    // Another rule's save redraws the page from what HQ last answered, without reading it again.
    const redrawn = page.waitForResponse(response => response.url().endsWith('/api/v2/access') && response.request().method() === 'GET');
    await page.locator('[data-rule=admin_sql]').uncheck();
    await redrawn;
    await page.waitForFunction(() => document.querySelector('[data-rule=admin_sql]')?.checked === false);
    assert.equal(await page.locator('#people-cloud-support').isChecked(), true);
    assert.equal(reads.length, afterFailures, 'a rule redraw shares the current entry');

    // Both a fresh pane entry and a return to Settings read HQ again in this same document.
    support = {...support, on: false};
    await enter(page, 'people');
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.checked === false);
    assert.ok(reads.length > afterFailures);
    support = {...support, on: true};
    await page.evaluate(() => { route('#/updates'); route('#/settings'); });
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.checked === true);

    // If neither the write nor the read can be confirmed, the switch stays unknown until re-entry succeeds.
    failPut = true; failGet = true;
    await toggle.click();
    await page.locator('#set-people [data-field-err]').waitFor();
    assert.equal(await toggle.isDisabled(), true);
    assert.equal(await toggle.evaluate(box => box.indeterminate), true);
    failPut = false; failGet = false;
    await enter(page, 'people');
    await page.waitForFunction(() => document.querySelector('#people-cloud-support')?.disabled === false);
    assert.equal(await toggle.isChecked(), true);
    support = {...support, on: false, machines: machines.map(m => ({...m, helper: m.helper === 'none' ? 'none' : 'off'}))};
    await page.close();

    // Settings > Computers: each cloud machine's helper, by key; a helper that is not there and a computer say nothing.
    reads.length = 0;
    page = await open('devices');
    const row = id => page.locator(`#set-machines tr[data-computer-row="${id}"] [data-machine-support]`);
    await row('r8').waitFor();
    assert.equal(await row('r8').innerText(), 'Tico support: off');
    assert.equal(await row('r9').innerText(), 'Tico support: off');
    assert.equal(await row('r7').count(), 0, 'no helper there: nothing');
    assert.equal(await row('r1').count(), 0, 'not a cloud machine: nothing');
    const deviceReads = reads.length;
    support = {...support, machines: machines.map(m => m.key === 'helper' ? {...m, helper: 'offline'} : m)};
    await page.evaluate(() => loadSettings());
    await page.waitForFunction(() => document.querySelector('#set-machines tr[data-computer-row="r9"] [data-machine-support]'));
    assert.equal(await row('r9').innerText(), 'Tico support: off', 'the state is not read again on a redraw');
    assert.equal(reads.length, deviceReads, 'one read per Settings entry');
    await enter(page, 'devices');
    await page.waitForFunction(() => document.querySelector('#set-machines tr[data-computer-row="r9"] [data-machine-support]')?.textContent === 'Tico support: offline');
    assert.equal(await row('r9').innerText(), 'Tico support: offline');
    assert.equal(await row('r8').innerText(), 'Tico support: on');

    // Failed reads clear stale state, then retry on the next entry without reloading the document.
    failGet = true;
    await enter(page, 'devices');
    await page.locator('#set-machines tr[data-computer-row="r8"]').waitFor();
    await page.evaluate(() => CLOUD_SUPPORT.read);   // the failed read has settled
    assert.equal(await page.locator('[data-machine-support]').count(), 0);
    assert.equal(await page.locator('#set-machines > .err').count(), 0);
    await enter(page, 'people');
    await page.locator('[data-rule=admin_sql]').waitFor();
    assert.equal(await page.locator('#people-cloud-support').count(), 0);
    failGet = false;
    await enter(page, 'people');
    await page.locator('#people-cloud-support').waitFor();
    await enter(page, 'devices');
    await row('r9').waitFor();
    assert.equal(await row('r9').innerText(), 'Tico support: offline');
    await page.close();

    // An admin sees it read-only.
    me = {...me, id: 'ben', name: 'Ben Cole', role: 'human', bot_admin: true, email: 'ben@acme.example'};
    page = await open('people');
    await page.locator('#people-cloud-support').waitFor();
    assert.equal(await page.locator('#people-cloud-support').isDisabled(), true);
    await page.close();

    // Off Tico Cloud: no switch, and no status on any row.
    me = {...me, id: 'ana', name: 'Ana Rivera', role: 'owner', bot_admin: false, email: 'ana@acme.example'};
    support = {available: false, on: null, machines: []};
    page = await open('people');
    await page.locator('[data-rule=admin_sql]').waitFor();
    assert.equal(await page.locator('#people-cloud-support').count(), 0);
    await page.close();
    assert.deepEqual(errors, []);
    console.log('PASS: Tico support owner policy, HQ state after uncertain writes, entry refresh, read recovery and helper states.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
