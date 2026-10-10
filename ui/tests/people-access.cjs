// Offline browser regression for Settings -> People: one choice between adding people by hand and syncing a
// directory, an inline add row, and one compact row per person whose role (owner only), sign-in and menu
// (what a member may do, mark as left, make owner) save as they change. Domain sign-in maps onto the allow
// list with the revision it was read at; the bot limit shows 25 by default. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

const CONFIG = {environment_id: 'initech', company_name: 'Initech', app_name: 'Initech Hub',
  assistant_name: 'Ace', assistant_bot: 'coo', public_url: 'https://initech.test',
  runner_url: 'https://initech.test', github_owner: '', local: false, release: '',
  onboarding_needed: false, providers_configured: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const context = await browser.newContext({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    const calls = [];
    let owner = 'ana@acme.example', revision = 3, limit = 25, rules = {};
    let allowedPeople = ['ana@acme.example', 'old@partner.example'], allowedDomains = [];
    let directory = {source: '', filter: {groups: [], org_units: [], domains: []}, interval_minutes: 360, mass_leave_limit: 10,
      revision: 2, last: {}, credentials: {google: {configured: false}, entra: {configured: false}}, scim: {url: 'https://initech.test/scim/v2', enabled: true, created: '2026-09-01T00:00:00Z'}};
    let people = [
      {id: 'ana', name: 'Ana Rivera', email: 'ana@acme.example', title: 'CEO', team: 'leadership'},
      {id: 'ben', name: 'Ben Cole', email: 'ben@acme.example', title: 'Ops', team: 'leadership'}];
    const access = () => ({
      owner: {email: owner, revision: 1, person: 'ana'},
      people: people.map(p => {
        const isOwner = p.email === owner, role = isOwner ? 'owner' : p.admin ? 'admin' : 'member';
        return {...p, left: !!p.left, owner: isOwner, role, bot_admin: role === 'admin', create_bots: p.create_bots !== false,
                add_people: p.add_people === undefined ? true : !!p.add_people, add_people_default: p.add_people === undefined,
                sign_in: p.sign_in !== false, can_sign_in: !p.left && !!p.email && p.sign_in !== false};
      }),
      allowed: allowedPeople, allowed_domains: allowedDomains, admins: [], bot_admins: [], member_bot_limit: limit, rules: {assistant_direct: true, botops_direct: true, botops_manages_bots: true, admin_credentials: true, admin_sql: true, member_tokens: true, ...rules},
      company_domains: ['acme.example'], company_domain_source: 'owner', revision, proxy: 'cloudflare',
      home_domain: 'acme.example', domain_sign_in: allowedDomains.includes('acme.example'), directory: directory.source});
    let me = {id: 'ana', name: 'Ana Rivera', role: 'owner', cloud: true, email: 'ana@acme.example', credential_access: false, config: CONFIG};
    await context.route('**/*', async route => {
      const request = route.request(), p = new URL(request.url()).pathname, method = request.method();
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const module = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (module) {
        const file = uiFile(module[1]);
        if (fs.existsSync(file)) return route.fulfill({contentType: module[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')});
      }
      if (p === '/api/me') return json(me);
      if (p === '/api/v2/config') return json(CONFIG);
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], recent_runs: []});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people});
      if (p === '/api/v2/models') return json({models: [], harnesses: [], enabled_providers: [], default: {}});
      if (p === '/api/v2/operations') return json({machines: [], services: [], issues: [], scheduler_enabled: true});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p === '/api/v2/access' && method === 'GET') return json(access());
      if (p === '/api/v2/directory' && method === 'GET') return json(directory);
      if (p === '/api/v2/directory' && method === 'PUT') {
        const body = request.postDataJSON(); calls.push(['directory', body]);
        directory = {...directory, source: body.source, revision: directory.revision + 1}; return json({revision: directory.revision});
      }
      if (p === '/api/v2/access/humans' && method === 'POST') {
        const body = request.postDataJSON(); calls.push(['add', body]);
        people.push({id: body.email.split('@')[0], ...body}); allowedPeople = [...allowedPeople, body.email]; return json({person: 'x'});
      }
      let m;
      if ((m = p.match(/^\/api\/v2\/access\/humans\/([^/]+)$/))) {
        const body = request.postDataJSON(); calls.push(['edit', m[1], body]);
        const row = people.find(x => x.id === m[1]);
        if ('role' in body) row.admin = body.role === 'admin';
        for (const k of ['create_bots', 'add_people', 'left', 'sign_in']) if (k in body) row[k] = body[k];
        return json({person: m[1]});
      }
      if ((m = p.match(/^\/api\/v2\/humans\/([^/]+)$/)) && method === 'POST') {
        const body = request.postDataJSON(); calls.push(['left', m[1], body]);
        people.find(x => x.id === m[1]).left = true; return json({});
      }
      if (p === '/api/v2/access/limits') { const body = request.postDataJSON(); calls.push(['limits', body]); limit = body.member_bot_limit; return json(body); }
      if (p === '/api/v2/access/rules') { const body = request.postDataJSON(); calls.push(['rules', body]); rules = {...rules, ...body}; return json(body); }
      if (p === '/api/v2/access/allow') {
        const body = request.postDataJSON(); calls.push(['allow', body]); revision += 1;
        allowedPeople = body.allowed; allowedDomains = body.allowed_domains;
        return json({revision, allowed: allowedPeople, allowed_domains: allowedDomains});
      }
      if (p === '/api/v2/access/owner') {
        calls.push(['owner', request.postDataJSON()]); owner = people.find(x => x.id === request.postDataJSON().person).email;
        return json({owner});
      }
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json({});
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const next = async () => { for (const end = Date.now() + t(10000); !calls.length && Date.now() < end;) await new Promise(r => setTimeout(r, 50)); return calls.shift(); };
    const row = id => page.locator(`.people-row[data-person=${id}]`);
    // A save re-reads /v2/access and repaints the list. Mark the current paint first, then wait for a fresh
    // one, so the next click never lands on a row that is being replaced.
    const repainted = async action => {
      await page.evaluate(() => { document.querySelector('#set-people').firstElementChild.dataset.stale = '1'; });
      const result = await action();
      await page.waitForFunction(() => { const first = document.querySelector('#set-people')?.firstElementChild; return first && !first.dataset.stale; });
      return result;
    };
    const menuAct = async (id, act) => {
      await row(id).locator('.people-more').click();
      const item = row(id).locator(`[data-person-act=${act}]`);
      await item.waitFor({state: 'visible'});
      await item.click();
    };

    await page.goto('https://tico-ui.test/#/settings');
    await page.getByRole('tab', {name: 'Humans'}).click();
    await row('ben').waitFor();
    // Add manually is the mode with no directory; no title/team column and no Edit button; the bot limit reads 25.
    assert.equal(await page.locator('[data-people-mode=manual]').getAttribute('aria-pressed'), 'true');
    assert.doesNotMatch(await page.locator('#set-people').textContent(), /leadership|Edit/);
    assert.equal(await page.locator('#member-bot-limit').inputValue(), '25');
    assert.equal(await page.locator('#people-proxy-note').count(), 0);
    assert.equal(await row('ana').locator('[data-person-role]').count(), 0);
    assert.equal(await row('ana').locator('[data-person-signin]').count(), 0, 'the owner can always sign in: words, not a switch');
    assert.equal((await row('ana').locator('.people-cell-signin').innerText()).trim(), 'Always');

    // Add a person inline; Cloudflare Access is not changed by Tico, so one line says so.
    await page.locator('#people-add [name=email]').fill('cy@acme.example');
    await page.locator('#people-add [name=name]').fill('Cy Dunn');
    await page.locator('#people-add [type=submit]').click();
    await row('cy').waitFor();
    assert.deepEqual(await next(), ['add', {email: 'cy@acme.example', name: 'Cy Dunn'}]);
    assert.match(await page.locator('#people-proxy-note').textContent(), /Cloudflare Access/);

    // Role and sign-in change in place and save at once.
    await repainted(async () => {
      await row('ben').locator('[data-person-role]').selectOption('admin');
      assert.deepEqual(await next(), ['edit', 'ben', {role: 'admin'}]);
    });
    await page.waitForFunction(() => document.querySelector('.people-row[data-person=ben] [data-person-role]')?.value === 'admin');
    await repainted(async () => {
      await row('cy').locator('[data-person-signin]').uncheck();
      assert.deepEqual(await next(), ['edit', 'cy', {sign_in: false}]);
    });
    await page.waitForFunction(() => document.querySelector('.people-row[data-person=cy] [data-person-signin]')?.checked === false);

    // The menu: what a member may do, and Mark as left behind its confirm; Restore brings them back.
    await repainted(async () => {
      await menuAct('cy', 'create_bots');
      assert.deepEqual(await next(), ['edit', 'cy', {create_bots: false}]);
    });
    await menuAct('cy', 'left');
    const dialog = page.locator('#people-dialog');
    assert.match(await dialog.textContent(), /API tokens/);
    await repainted(async () => {
      await dialog.locator('[type=submit]').click();
      assert.deepEqual(await next(), ['left', 'cy', {left: true}]);
    });
    await page.locator('.people-left summary').click();
    await page.locator('.people-row.is-left[data-person=cy] [data-person-act=restore]').click();
    assert.deepEqual(await next(), ['edit', 'cy', {left: false}]);

    // Domain sign-in is the company domain on the allow list; anything else listed stays, shown as a chip.
    assert.equal(await page.locator('#people-domain').isChecked(), false);
    await page.locator('#people-domain').check();
    assert.deepEqual(await next(), ['allow', {allowed: ['ana@acme.example', 'old@partner.example', 'cy@acme.example'],
      allowed_domains: ['acme.example'], expected_revision: 3}]);
    await page.waitForFunction(() => document.querySelector('#people-domain')?.checked === true);
    await page.locator('#people-domain').uncheck();
    assert.deepEqual(await next(), ['allow', {allowed: ['ana@acme.example', 'old@partner.example', 'cy@acme.example'],
      allowed_domains: [], expected_revision: 4}]);
    await page.locator('[data-allow-remove="old@partner.example"]').click();
    assert.deepEqual(await next(), ['allow', {allowed: ['ana@acme.example', 'cy@acme.example'], allowed_domains: [], expected_revision: 5}]);
    await page.waitForFunction(() => !document.querySelector('[data-allow="old@partner.example"]'));

    await page.locator('#member-bot-limit').fill('30');
    await page.locator('#member-bot-limit').press('Enter');
    assert.deepEqual(await next(), ['limits', {member_bot_limit: 30}]);

    // The owner's rules are all on, and turning one off saves it (backend/team_rules.py).
    assert.equal(await page.locator('[data-rule]').count(), 6);
    assert.equal(await page.locator('[data-rule]:checked').count(), 6);
    await page.locator('[data-rule=admin_sql]').uncheck();
    assert.deepEqual(await next(), ['rules', {admin_sql: false}]);
    await page.waitForFunction(() => document.querySelector('[data-rule=admin_sql]')?.checked === false);

    // Sync with directory: pick a source and save; going back to manual stops the sync after a confirm.
    await page.locator('[data-people-mode=sync]').click();
    await page.locator('#directory-sync select[name=source]').waitFor();
    assert.equal(await page.locator('#people-add').count(), 0);
    await page.locator('#directory-sync select[name=source]').selectOption('scim');
    await page.locator('#directory-sync [type=submit]').click();
    const saved = await next();
    assert.equal(saved[0], 'directory'); assert.equal(saved[1].source, 'scim'); assert.equal(saved[1].mass_leave_limit, 10);
    await page.locator('#directory-sync [data-ds-last]').waitFor();
    await page.locator('[data-people-mode=manual]').click();
    assert.match(await dialog.textContent(), /Stop syncing from SCIM/);
    await dialog.locator('[type=submit]').click();
    const stopped = await next();
    assert.deepEqual([stopped[0], stopped[1].source, stopped[1].expected_revision], ['directory', '', 3]);
    await page.locator('#people-add').waitFor();

    // Ownership moves only after the new owner's email is typed.
    await row('ben').locator('.people-more').click();
    await row('ben').locator('[data-person-act=owner]').click();
    const go = dialog.locator('[type=submit]');
    assert.equal(await go.isDisabled(), true);
    await dialog.locator('[name=typed]').fill('someone@else.example');
    assert.equal(await go.isDisabled(), true);
    await dialog.locator('[name=typed]').fill('BEN@acme.example');
    await dialog.locator('[name=admin]').check();
    assert.equal(calls.length, 0);
    await go.click();
    assert.deepEqual(await next(), ['owner', {person: 'ben', previous_owner_bot_admin: true, expected_revision: 1, confirm: true}]);

    // An admin sees no mode choice and no role picker, and cannot switch another admin's sign-in.
    owner = 'ana@acme.example';
    me = {...me, id: 'ben', name: 'Ben Cole', role: 'human', bot_admin: true, email: 'ben@acme.example'};
    people.find(p => p.id === 'cy').admin = true;
    const admin = await context.newPage();
    admin.on('pageerror', e => errors.push(e.message));
    await admin.goto('https://tico-ui.test/#/settings');
    await admin.getByRole('tab', {name: 'Humans'}).click();
    await admin.locator('.people-row[data-person=cy]').waitFor();
    assert.equal(await admin.locator('[data-people-mode], [data-person-role]').count(), 0);
    assert.equal(await admin.locator('.people-row[data-person=cy] [data-person-signin]').isDisabled(), true);
    assert.equal(await admin.locator('.people-row[data-person=ben] [data-person-signin]').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: People tab switches sync and manual, adds inline, saves role, sign-in, domain sign-in and bot limit as they change.');
  } finally {
    await browser.close();
  }
})();
