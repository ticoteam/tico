// Invites and a failed import (bot-lifecycle-spec.md acceptance tests 2 and 3). Settings -> Humans: "Send invite" on a
// roster row and Invite on each person the setup's company file names post once, and a refusal from Tico's sign-in mail
// stays on screen in words; a sent one reads "Invited". "Create password" shows only when the issuer makes accounts,
// shows the password once in a dialog and clears it on close. The welcome says a chosen chart that failed to import was not
// replaced by a sample team. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const CONFIG = {environment_id: 'initech', company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace',
  assistant_bot: 'coo', public_url: 'https://initech.test', runner_url: 'https://initech.test', github_owner: '', local: false,
  release: '', onboarding_needed: false, providers_configured: true};
const REFUSED = {error: {code: 'invite_failed', detail: "Tico's sign-in mail couldn't deliver the invite to dana@acme.example. Check the address, then try again."}};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const context = await browser.newContext({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    const calls = [];
    let config = CONFIG, refuse = true, invites = {}, accountPassword = false, made = 0;
    const people = [
      {id: 'ana', name: 'Ana Rivera', email: 'ana@acme.example'},
      {id: 'ben', name: 'Ben Cole', email: 'ben@acme.example'}];
    let setupPeople = [{id: 'dana', name: 'Dana Ruiz', title: 'Controller', seats: ['s-1'], invited_at: null}];
    let record = {names: {company_name: 'Initech'}, completed: '2026-10-20T10:00:00Z', needed: false, bots: [],
      company_import_failed: null, company_import: {seats: 1, deferred: [], people: setupPeople}};
    const access = () => ({
      owner_id: {email: 'ana@acme.example', revision: 1, person: 'ana'},
      people: people.map(p => ({...p, left: false, owner_id: p.id === 'ana', role: p.id === 'ana' ? 'owner' : 'member',
        bot_admin: false, create_bots: true, add_people: true, add_people_default: true, sign_in: true, can_sign_in: true})),
      allowed: people.map(p => p.email), allowed_domains: [], admins: [], bot_admins: [], member_bot_limit: 25, rules: {},
      company_domains: ['acme.example'], company_domain_source: 'owner', revision: 3, proxy: 'oidc', home_domain: 'acme.example',
      domain_sign_in: false, directory: '', invites, invite_email: true, account_password: accountPassword});
    await context.route('**/*', async route => {
      const request = route.request(), p = new URL(request.url()).pathname, method = request.method();
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const module = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (module && fs.existsSync(uiFile(module[1]))) return route.fulfill({contentType: module[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(module[1]), 'utf8')});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana Rivera', role: 'owner', cloud: true, email: 'ana@acme.example', config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], recent_runs: []});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people});
      if (p === '/api/v2/models') return json({models: [], harnesses: [], enabled_providers: [], default: {}});
      if (p === '/api/v2/operations') return json({machines: [], services: [], issues: [], scheduler_enabled: true});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p === '/api/v2/access' && method === 'GET') return json(access());
      if (p === '/api/v2/directory') return json({source: '', filter: {groups: [], org_units: [], domains: []}, revision: 1, last: {}, credentials: {}});
      if (p === '/api/v2/setup' && method === 'GET') return json({...record, home: 'human:ana'});
      if (p === '/api/v2/setup/getting-started/state') return json({tour: false, checklist: false, cards: [], skipped: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/access/humans' && method === 'POST') {
        const body = request.postDataJSON(), id = body.email.split('@')[0];
        calls.push(['add', body]); people.push({id, name: body.name, email: body.email});
        return json({person: id, email: body.email, name: body.name});
      }
      let m;
      if ((m = p.match(/^\/api\/v2\/access\/humans\/([^/]+)\/invite$/)) && method === 'POST') {
        calls.push(['invite', m[1], request.postDataJSON()]);
        if (refuse) return json(REFUSED, 422);
        invites = {...invites, [m[1]]: '2026-10-24T09:00:00Z'};
        return json({person: m[1], invited_at: invites[m[1]]});
      }
      if ((m = p.match(/^\/api\/v2\/access\/humans\/([^/]+)\/password$/)) && method === 'POST') {
        calls.push(['password', m[1], request.postDataJSON()]);
        if (!made++) return json({error: {code: 'password_exists', detail: 'Ben Cole already has a Tico account; use Invite.'}}, 422);
        return json({person: m[1], email: 'ben@acme.example', password: 'abcde-fghjk-mnpqr-stuvw'});
      }
      if ((m = p.match(/^\/api\/v2\/setup\/company\/people\/([^/]+)\/invite$/)) && method === 'POST') {
        calls.push(['setup-invite', m[1], request.postDataJSON()]);
        // Added to the roster either way, so a retry needs no address.
        setupPeople = setupPeople.map(x => x.id === m[1] ? {...x, person: 'dana'} : x);
        if (refuse) { record = {...record, company_import: {...record.company_import, people: setupPeople}}; return json(REFUSED, 422); }
        setupPeople = setupPeople.map(x => x.id === m[1] ? {...x, invited_at: '2026-10-24T09:00:00Z'} : x);
        record = {...record, company_import: {...record.company_import, people: setupPeople}};
        return json({...record, home: 'human:ana'});
      }
      if (p === '/api/v2/setup/company/import-failure/dismiss' && method === 'POST') {
        calls.push(['dismiss-import-failure']);
        record = {...record, company_import_failed: null};
        return json({...record, home: 'human:ana'});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const row = id => page.locator(`.people-row[data-person=${id}]`);
    const menuAct = async (id, act) => {
      await row(id).locator('.people-more').click();
      const item = row(id).locator(`[data-person-act=${act}]`);
      await item.waitFor({state: 'visible'});
      await item.click();
    };

    await page.goto('https://tico-ui.test/#/settings/people');
    await row('ben').waitFor();
    // The owner is never offered an invite to themselves.
    await row('ana').locator('.people-more').click();
    assert.equal(await row('ana').locator('[data-person-act=invite]').count(), 0);
    await page.keyboard.press('Escape');
    // No Create password unless the issuer makes accounts.
    await row('ben').locator('.people-more').click();
    assert.equal(await row('ben').locator('[data-person-act=password]').count(), 0);
    await page.keyboard.press('Escape');

    // A refused roster invite is a visible error on that person's row and nothing reads "Invited".
    await menuAct('ben', 'invite');
    await page.locator('#set-people [data-field-err]', {hasText: "couldn't deliver the invite"}).waitFor();
    assert.deepEqual(calls.shift(), ['invite', 'ben', {}]);
    assert.equal(await row('ben').locator('[data-invited]').count(), 0);

    // The person from setup: typed address, refused, the error stays on the row and Invite tries again with no address.
    const dana = page.locator('[data-setup-person=dana]');
    await dana.waitFor();
    assert.match(await page.locator('#setup-people').innerText(), /Tico's sign-in email.*not your company's mailbox/s);
    await dana.locator('[name=email]').fill('dana@acme.example');
    await dana.locator('button[type=submit]').click();
    await dana.locator('.err', {hasText: "couldn't deliver the invite to dana@acme.example"}).waitFor();
    assert.deepEqual(calls.shift(), ['setup-invite', 'dana', {email: 'dana@acme.example'}]);
    assert.equal(await dana.locator('[name=email]').isHidden(), true);
    refuse = false;
    await dana.locator('button[type=submit]').click();
    await page.locator('[data-setup-person=dana] [data-setup-invited]').waitFor();
    assert.deepEqual(calls.shift(), ['setup-invite', 'dana', {email: 'dana@acme.example'}]);

    // A sent roster invite reads "Invited" and the menu offers to send it again.
    await menuAct('ben', 'invite');
    await row('ben').locator('[data-invited]').waitFor();
    assert.deepEqual(calls.shift(), ['invite', 'ben', {}]);
    // The time moves on without a redraw and carries the full date on hover.
    const when = row('ben').locator('[data-invited] [data-ago]');
    assert.ok((await when.getAttribute('title')).length > 5);
    await when.evaluate(n => { n.dataset.ago = new Date(Date.now() - 3 * 3600e3).toISOString(); agoTick(); });
    assert.equal(await when.innerText(), '3h ago');
    await when.evaluate(n => { n.dataset.ago = new Date(Date.now() - 5e3).toISOString(); agoTick(); });
    assert.equal(await when.innerText(), 'just now', 'under a minute is not counted in seconds');
    // Add and send invite: one click adds the person and sends their invite.
    await page.locator('#people-add [name=email]').fill('eli@acme.example');
    await page.locator('#people-add [name=name]').fill('Eli Park');
    await page.locator('#people-add [data-people-add-invite]').click();
    await row('eli').locator('[data-invited]').waitFor();
    assert.deepEqual(calls.splice(0), [['add', {email: 'eli@acme.example', name: 'Eli Park'}], ['invite', 'eli', {}]]);
    // A refused invite still leaves them added, and their row says why.
    refuse = true;
    await page.locator('#people-add [name=email]').fill('fay@acme.example');
    await page.locator('#people-add [data-people-add-invite]').click();
    await page.locator('#set-people [data-field-err]', {hasText: "couldn't deliver the invite"}).waitFor();
    await row('fay').waitFor();
    assert.deepEqual(calls.splice(0).map(c => c.slice(0, 2)), [['add', {email: 'fay@acme.example', name: ''}], ['invite', 'fay']]);
    refuse = false;
    await row('ben').locator('.people-more').click();
    assert.equal((await row('ben').locator('[data-person-act=invite]').innerText()).trim(), 'Send invite again');
    await page.keyboard.press('Escape');

    // Create password: a refusal is a red line on the row, in words; a made account's password shows once, then is gone.
    accountPassword = true;
    await page.reload();   // the address already names People, so going to it again would not redraw
    await row('ben').waitFor();
    await menuAct('ben', 'password');
    await page.locator('#set-people [data-field-err]', {hasText: 'already has a Tico account; use Invite.'}).waitFor();
    await menuAct('ben', 'password');
    const dialog = page.locator('#people-dialog');
    await dialog.locator('[data-made-password]').waitFor();
    assert.equal(await dialog.locator('[data-made-password]').inputValue(), 'abcde-fghjk-mnpqr-stuvw');
    assert.match(await dialog.innerText(), /Shown once/);
    assert.deepEqual(calls.splice(0, 2), [['password', 'ben', {}], ['password', 'ben', {}]]);
    await dialog.locator('button[type=submit]').click();
    await dialog.waitFor({state: 'hidden'});
    await dialog.locator('[data-made-password]').waitFor({state: 'detached'});      // not left in the page

    // A company file that could not be imported at install: the welcome says so and that no sample team replaced it.
    config = {...CONFIG, onboarding_needed: true};
    record = {...record, completed: null, needed: true, company_import: null,
      company_import_failed: {at: '2026-10-24T08:00:00Z', reason: 'The company file is not valid YAML (line 3).'}};
    const welcome = await context.newPage();
    welcome.on('pageerror', e => errors.push(e.message));
    await welcome.goto('https://tico-ui.test/');
    const failed = welcome.locator('#onb-import-failed');
    await failed.waitFor();
    assert.equal(await failed.getAttribute('role'), 'alert');
    assert.match(await failed.innerText(), /Your chosen org chart wasn't imported.*not valid YAML \(line 3\).*No sample team was set up in its place/s);
    // Dismiss is the way out when the owner won't import it again: the alert goes.
    await failed.locator('#onb-import-failed-dismiss').click();
    await failed.waitFor({state: 'detached'});
    assert.deepEqual(calls.pop(), ['dismiss-import-failure']);
    assert.deepEqual(errors, []);
    console.log('setup-invites: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
