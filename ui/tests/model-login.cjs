// Settings > Computers' model sign-in pop-ups on one browser, with every request intercepted (ui/model-login.js; the
// harness capabilities come from runner-rs/harnesses/*.toml). What were model-login-hosted (hosted sign-in shows only the
// link and code), model-sign-out (Sign out and Switch account), model-to-key (Switch to API key) and model-keys
// (Replace, Clear and Add key); each keeps its own header below and names itself in a failure ("[signOut] ...").
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

// ---- model-login-hosted
// Sign in on a hosted Tico (config `hosted`, TICO_HOSTED; ui/model-login.js). The person has no terminal on the
// computer, so Settings > Computers signs Claude Code and Codex in with the provider's link and code only: the dialog
// starts the sign-in by itself, shows the link (and Codex's one-time code, or Claude's paste field), never what else
// the CLI printed (a failure gives its message), never points to a terminal when the computer is slow to start, and
// closes by itself once signed in (saying so in a toast) or when canceled, canceling it on the computer. A
// self-hosted Tico keeps the computer's lines, the terminal hint and the dialog open after a cancel. Every request is
// intercepted; the harness capabilities come from runner-rs/harnesses/*.toml.
async function loginHosted(browser) {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const CLAUDE_URL = 'https://claude.com/cai/oauth/authorize?code=true&client_id=fixture';

    async function open(hosted) {
      const box = {id: 'box', label: 'Server', owner_id: 'sam', bots: [], platform: 'linux', last_seen_at: at(-3000), readiness_at: at(-3000),
        needed_runtimes: ['claude', 'codex'], bot_runtimes: [], signed_out: {}, profiles: [],
        readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}, claude: {installed: true, authenticated: 'missing'}},
          harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude')}}};
      const writes = [], rows = {};
      let seq = 0, refusal = null;
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.1.0', app_name: 'Tico', hosted};
        if (p === '/api/me') return json({id: 'sam', role: 'owner', name: 'Sam', email: 'sam@example.com', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/issues') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'sam', name: 'Sam'}], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p === '/api/v2/operations') return json({machines: [box], services: []});
        if (p === '/api/v2/models') return json({models: [], harnesses});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [{runner_id: 'box', label: 'Server', profiles: []}], assignments: []});
        if (p === '/api/v2/computers/box/logins' && method === 'POST') {
          const body = req.postDataJSON(), id = `in${++seq}`; writes.push({p, body});
          // The computer has not picked it up yet: it starts on the next look, still printing only its own lines.
          rows[id] = {id, runtime: body.runtime, profile: '', kind: 'sign_in', state: 'starting', created_at: new Date().toISOString(),
            lines: ['Opening browser to sign in…', 'bot@server:~$'], accepts_code: false};
          return json(rows[id]);
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)(\/cancel|\/code)?$/);
        if (one && one[2] === '/cancel') {
          writes.push({p});
          // `refuse`: the next `times` cancels fail with this status and message, moving the row to `state` first if
          // given (a switch the computer is already putting in place answers 409 and goes on).
          if (refusal) {
            const {status, detail, state} = refusal;
            if (!--refusal.times) refusal = null;
            if (state) rows[one[1]].state = state;
            return json({error: {detail}}, status);
          }
          rows[one[1]].state = 'canceled';
          return json(rows[one[1]]);
        }
        if (one && one[2] === '/code') { writes.push({p}); rows[one[1]].code_sent = true; return json(rows[one[1]]); }
        if (one) return json(rows[one[1]]);
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      // A tool it is not signed in to is signed in from Add connection in the computer's pop-up.
      await page.locator('#set-machines .machine-name[data-computer-open=box]').click();
      const add = page.locator('#set-machines dialog[open] [data-add-connection]');
      await add.waitFor();
      // Its own login, through the Add connection pop-up: the tool, then the computer's login (on the name step where
      // the tool also takes a subscription).
      const signIn = name => ({click: async () => {
        await add.click();
        const step = page.locator('dialog.computer-add-dialog[open]');
        await step.locator(`[data-add-tool="${name}"]`).click();
        if (!await step.locator('[data-add-own]').count()) await step.locator('[data-add-kind=subscription]').click();
        await step.locator('[data-add-own]').click();
      }});
      return {page, box, writes, errors, rows, signIn, last: () => rows[`in${seq}`], refuse: r => { refusal = {times: 1, ...r}; }};
    }
    const login = page => page.locator('dialog.model-login:not(.model-logout)');

    // A duplicate connection is refused by the shared API, and its existing dialog shows how to change accounts.
    {
      const {page, errors, signIn} = await open('own');
      const detail = 'Claude Code is already connected or signing in on Server. Use Switch account on the existing connection to change it';
      await page.route('**/api/v2/computers/box/logins', route => route.fulfill({status: 409,
        contentType: 'application/json', body: JSON.stringify({error: {code: 'subscription_exists', detail}})}));
      await signIn('claude').click();
      const dialog = login(page);
      await dialog.locator('[data-status]', {hasText: detail}).waitFor();
      assert.equal(await dialog.getByRole('button', {name: 'Try again', exact: true}).count(), 0);
      await dialog.getByRole('button', {name: 'Close', exact: true}).last().click();
      await dialog.waitFor({state: 'detached'});
      assert.deepEqual(errors, []);
      await page.close();
    }

    // ---- Hosted: Claude Code. The dialog starts the sign-in itself; waiting, it shows no CLI lines and no terminal.
    {
      const {page, writes, errors, signIn, last} = await open('own');
      assert.equal(await page.locator('#set-machines').getByText(/terminal|docker exec/i).count(), 0, 'Computers points to no terminal');
      await signIn('claude').click();
      const dialog = login(page);
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in on Server'}).waitFor();
      assert.deepEqual(writes, [{p: '/api/v2/computers/box/logins', body: {runtime: 'claude'}}], 'it started by itself');
      // Slow to start: the hint names Settings > Computers, never a terminal or the computer's lines.
      Object.assign(last(), {created_at: new Date(Date.now() - 60000).toISOString()});
      await dialog.locator('[data-status]', {hasText: 'Check that it shows as online in Settings > Computers'}).waitFor();
      assert.doesNotMatch(await dialog.innerText(), /terminal/i);
      // Waiting with no link yet: no CLI lines.
      Object.assign(last(), {state: 'waiting'});
      await dialog.locator('ol', {hasText: 'Waiting for the sign-in link from the computer'}).waitFor();
      assert.equal(await dialog.locator('[data-lines]').count(), 0, 'what the computer printed stays hidden');
      // The link and the paste field, nothing else.
      Object.assign(last(), {url: CLAUDE_URL, accepts_code: true});
      await dialog.locator('[data-link]').waitFor();
      assert.equal(await dialog.locator('[data-link]').getAttribute('href'), CLAUDE_URL);
      assert.equal(await dialog.locator('[name=code]').count(), 1);
      assert.equal(await dialog.locator('[data-lines]').count(), 0);
      assert.doesNotMatch(await dialog.innerText(), /bot@server|\$ |terminal/i, 'no shell prompt or command line');
      await dialog.locator('[name=code]').fill('pasted-code');
      await dialog.getByRole('button', {name: 'Send code'}).click();
      await dialog.getByText('Code sent').waitFor();
      // Signed in: the dialog closes by itself and says so.
      Object.assign(last(), {state: 'signed_in', updated_at: new Date().toISOString()});
      await dialog.waitFor({state: 'detached'});
      await page.locator('.toast', {hasText: 'Signed in. Claude Code is ready on Server.'}).waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }

    // ---- Hosted: Codex. Link and one-time code; Cancel sign-in closes only once the computer's sign-in is canceled.
    {
      const {page, writes, errors, signIn, last, refuse} = await open('invited');
      await signIn('codex').click();
      const dialog = login(page);
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in'}).waitFor();
      Object.assign(last(), {state: 'waiting', url: 'https://auth.openai.com/codex/device', code: 'ABCD-12345'});
      await dialog.locator('code[data-code]', {hasText: 'ABCD-12345'}).waitFor();
      assert.equal(await dialog.locator('[data-link]').getAttribute('href'), 'https://auth.openai.com/codex/device');
      assert.equal(await dialog.locator('[data-lines]').count(), 0);
      // A cancel the server never confirms (busy on every try) leaves the sign-in running: the pane stays, says so
      // across the next looks, and still offers Cancel sign-in.
      const unconfirmed = 'Could not confirm whether this saved.';
      refuse({status: 503, detail: 'The server is busy', times: 3});
      await dialog.getByRole('button', {name: 'Cancel sign-in'}).click();
      await dialog.locator('[data-status]', {hasText: unconfirmed}).waitFor();
      await page.waitForTimeout(2500);   // one look (POLL_MS) redraws it
      assert.match(await dialog.locator('[data-status]').innerText(), new RegExp(`^Waiting for you to finish signing in… ${unconfirmed}`));
      assert.equal(await dialog.locator('code[data-code]').innerText(), 'ABCD-12345');
      // Confirmed, it closes and says so.
      await dialog.getByRole('button', {name: 'Cancel sign-in'}).click();
      await dialog.waitFor({state: 'detached'});
      assert.deepEqual(writes.at(-1), {p: `/api/v2/computers/box/logins/${last().id}/cancel`});
      await page.locator('.toast', {hasText: 'The sign-in was canceled.'}).waitFor();
      // Refused because the computer is already putting the account in place (409): the pane stays, follows it to
      // signed in, and closes then.
      await signIn('codex').click();
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in'}).waitFor();
      Object.assign(last(), {state: 'waiting', url: 'https://auth.openai.com/codex/device', code: 'WXYZ-67890'});
      await dialog.locator('code[data-code]', {hasText: 'WXYZ-67890'}).waitFor();
      const late = "The new account is already being put in place, so it can't be canceled now";
      refuse({status: 409, detail: late, state: 'finishing'});
      await dialog.getByRole('button', {name: 'Cancel sign-in'}).click();
      await dialog.locator('[data-status][data-state=finishing]', {hasText: late}).waitFor();
      assert.equal(await dialog.locator('[data-actions] button').count(), 0, 'nothing to cancel once it is finishing');
      Object.assign(last(), {state: 'signed_in', updated_at: new Date().toISOString()});
      await dialog.waitFor({state: 'detached'});
      await page.locator('.toast', {hasText: 'Signed in. Codex (ChatGPT) is ready on Server.'}).waitFor();
      // A failure gives its message, still without the computer's lines.
      await signIn('codex').click();
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in'}).waitFor();
      Object.assign(last(), {state: 'failed', message: 'The sign-in failed on the computer.'});
      await dialog.locator('[data-status][data-state=failed]', {hasText: 'The sign-in failed on the computer.'}).waitFor();
      assert.equal(await dialog.locator('[data-lines]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // ---- Self-hosted: unchanged. The computer's lines show while there is no link, the slow start mentions its
    // terminal, a cancel keeps the dialog open to say so, and a sign-in stays open on Done.
    {
      const {page, errors, signIn, last} = await open('');
      await signIn('claude').click();
      const dialog = login(page);
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in'}).waitFor();
      Object.assign(last(), {created_at: new Date(Date.now() - 60000).toISOString()});
      await dialog.locator('[data-status]', {hasText: 'you can also sign in from its terminal'}).waitFor();
      Object.assign(last(), {state: 'waiting'});
      await dialog.locator('[data-lines]', {hasText: 'Opening browser to sign in'}).waitFor();
      await dialog.getByRole('button', {name: 'Cancel sign-in'}).click();
      await dialog.locator('[data-status]', {hasText: 'canceled'}).waitFor();
      await dialog.getByRole('button', {name: 'Try again'}).click();
      // The retry is a new sign-in: change it only once the computer has answered its start.
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in'}).waitFor();
      Object.assign(last(), {state: 'waiting', url: 'https://auth.openai.com/codex/device'});
      await dialog.locator('[data-link]').waitFor();
      Object.assign(last(), {state: 'signed_in', updated_at: new Date().toISOString()});
      await dialog.getByRole('button', {name: 'Done'}).waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: hosted sign-in shows only the link and code, no terminal, closes on sign-in and cancel; self-hosted unchanged');
}

// ---- model-sign-out
// Sign out and Switch account from the app (ui/model-login.js, backend/model_login.py). In a computer's pop-up on
// Settings > Computers each signed-in Codex, Claude Code or Muse Code connection, the computer's one login for that tool,
// offers Switch account and Sign out to the computer's operator or an admin, only while the computer is online. Sign out first names the bots that
// stop, then asks the computer and follows it to Signed out; a computer login someone signed out reads Signed out and
// offers Sign in and Use the shared key. Switch account is the sign-in dialog, titled as a switch, posting switch: true,
// and a cancel says the old account still works. A named subscription a runner still reports from before one login
// per computer (#1259) has no line. A tool without a logout in its manifest, and a plain member, see none of it. Sign out stays off until the bots are
// named, and a failed lookup offers Try again. After a change the page looks again until a heartbeat newer than the
// change shows it (a stale one still reads the old state). A switch past its commit point reads as finishing with no cancel, and a Mac offers no
// Switch account for Claude Code (switch_unavailable_on). Every request is intercepted; the harness capabilities come
// from runner-rs/harnesses/*.toml.
async function signOut(browser) {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const fixture = () => ({id: 'box', label: 'Team box', owner_id: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
      readiness_at: at(-3000),
      needed_runtimes: ['claude', 'codex'], bot_runtimes: ['codex'], signed_out: {},
      readiness: {
        runtimes: {
          codex: {installed: true, authenticated: 'ready', account: {email: 'old@acme.example', plan: 'pro', kind: 'subscription', checked_at: at(-60000)}},
          claude: {installed: true, authenticated: 'ready', account: {email: 'chris@acme.example', plan: 'max', checked_at: at(-60000)}},
          gemini: {installed: true, authenticated: 'ready'},
          musecode: {installed: true, authenticated: 'ready'},
        },
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude'), gemini: chip('Gemini CLI', 'gemini'), musecode: chip('Muse Code', 'musecode')}},
      profiles: [{name: 'acme', display_name: 'Acme', runtimes: {codex: {signed_in: true, account: {email: 'dev@acme.example', plan: 'pro'}}, musecode: {signed_in: true}}}]});

    async function open(as = 'sam') {
      const box = fixture(), writes = [], rows = {};
      let seq = 0, gate = null;
      const heartbeats = [];
      // The computer's next heartbeat, a few seconds after the change finished: until then the page reads the old one.
      // `early` sends one sooner that still reads the old state (it began before the change landed).
      const beat = (apply, early) => {
        if (early) heartbeats.push({at: Date.now() + 1000, apply: () => {}});
        heartbeats.push({at: Date.now() + 4000, apply});
      };
      const finish = (id, state, apply, early) => { Object.assign(rows[id], {state, updated_at: new Date().toISOString()}); beat(apply, early); };
      // Hold the next bots lookup until the test lets it go: `fail` answers it with an error.
      const hold = () => { let let_go; const held = new Promise(resolve => { let_go = resolve; }); gate = {held}; return fail => let_go(fail); };
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.1.0', app_name: 'Tico'};
        // Sam is a member who operates the computer; Cara is a member who does not.
        if (p === '/api/me') return json({id: as, role: 'member', name: as, email: `${as}@example.com`, cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/issues') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'sam', name: 'Sam'}], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p === '/api/v2/operations') {
          while (heartbeats.length && Date.now() >= heartbeats[0].at) { heartbeats.shift().apply(); box.readiness_at = new Date().toISOString(); }
          return json({machines: [box], services: []});
        }
        if (p === '/api/v2/models') return json({models: [], harnesses});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [{runner_id: 'box', label: 'Team box', profiles: box.profiles}], assignments: []});
        // backend/model_login.py affected_bots
        if (p === '/api/v2/computers/box/logouts/bots') {
          if (gate) {
            const {held} = gate; gate = null;
            if (await held) return json({detail: 'The server is busy'}, 503);
          }
          const runtime = url.searchParams.get('runtime'), profile = url.searchParams.get('profile') || '';
          const named = names => names.map(name => ({bot: name.toLowerCase().replace(/ /g, '-'), name}));
          return json({runtime, profile, bots: profile ? [] : runtime === 'codex' ? named(['Writer', 'Scout'])
            : runtime === 'claude' ? named(['Reviewer 1', 'QA', 'Scout', 'Writer']) : []});
        }
        if (p === '/api/v2/computers/box/logouts' && method === 'POST') {
          const body = req.postDataJSON(), id = `out${++seq}`; writes.push({p, body});
          rows[id] = {id, runtime: body.runtime, profile: body.profile || '', kind: 'sign_out', state: 'requested', polls: 0, lines: []};
          return json(rows[id]);
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)(\/cancel)?$/);
        if (one && one[2]) {
          writes.push({p});
          rows[one[1]].state = 'canceled';
          return json(rows[one[1]]);
        }
        if (one) {
          const row = rows[one[1]];
          // The computer signs out on its second check: the server marks it at once, the readiness follows on the
          // next heartbeat.
          if (row.kind === 'sign_out' && ++row.polls === 2) {
            // backend/model_login.py records every sign-out from Tico, the computer's own login's ('') and a subscription's.
            box.signed_out = {...box.signed_out, [row.runtime]: [...(box.signed_out[row.runtime] || []), row.profile]};
            finish(row.id, 'signed_out', () => {
              if (row.profile) box.profiles.find(x => x.name === row.profile).runtimes[row.runtime] = {signed_in: false};
              else box.readiness.runtimes[row.runtime] = {installed: true, authenticated: 'missing'};
            }, true);
          }
          return json(row);
        }
        if (p === '/api/v2/computers/box/shared-key' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          // The computer takes the shared key on its next check.
          box.signed_out = {};
          beat(() => { box.readiness.runtimes[body.runtime] = {installed: true, authenticated: 'ready', account: {kind: 'api_key', checked_at: new Date().toISOString()}}; });
          return json({id: 'key1', kind: 'use_shared_key', state: 'done', created_at: new Date().toISOString(), lines: []});
        }
        if (p === '/api/v2/computers/box/logins' && method === 'POST') {
          const body = req.postDataJSON(), id = `in${++seq}`; writes.push({p, body});
          rows[id] = {id, runtime: body.runtime, profile: body.profile || '', kind: body.switch ? 'switch' : body.to ? 'to_subscription' : 'sign_in', state: 'waiting',
            url: 'https://auth.openai.com/codex/device', code: 'ABCD-1234', created_at: new Date().toISOString(), lines: []};
          return json(rows[id]);
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines .machine-name[data-computer-open=box]').click();
      await page.locator('#set-machines dialog.computer-detail[open] [data-connection]').first().waitFor();
      return {page, box, writes, errors, rows, finish, hold, last: () => `${['in', 'out'].find(k => rows[`${k}${seq}`])}${seq}`};
    }

    // ---- The operator, on Settings > Computers
    const {page, box, writes, errors, rows, finish, hold, last} = await open();
    const table = page.locator('#set-machines dialog.computer-detail[open]');
    const own = name => table.locator(`[data-connection="own:${name}"]`);
    // Switch account and Sign out are icons: their name is the aria-label.
    const buttons = row => row.locator('.computer-conn-signin button').evaluateAll(all => all.map(b => b.getAttribute('aria-label') || b.innerText));
    assert.deepEqual(await buttons(own('codex')), ['Switch account', 'Switch to API key', 'Sign out']);
    assert.deepEqual(await buttons(own('claude')), ['Switch account', 'Switch to API key', 'Sign out']);
    assert.equal(await table.locator('[data-connection^="profile:"]').count(), 0, 'a leftover named subscription has no line');
    assert.deepEqual(await buttons(own('gemini')), [], 'no logout in its manifest: nothing to offer');

    // Muse has no account metadata; its signed-in file still enables both controls at both widths.
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      assert.deepEqual(await buttons(own('musecode')), ['Switch account', 'Sign out']);
      for (const row of [own('musecode')]) {
        assert.ok(await row.getByRole('button', {name: 'Switch account', exact: true}).isVisible());
        assert.ok(await row.getByRole('button', {name: 'Sign out', exact: true}).isVisible());
      }
    }
    await page.setViewportSize({width: 1440, height: 900});
    // The same Muse capability enables key recovery and subscription switching for the computer's own login.
    const museActions = await page.evaluate(() => {
      const actions = options => window.TicoModelLogin.actionsHTML({runnerId: 'box', runtime: 'musecode', ...options});
      const labels = html => {
        const row = document.createElement('div');
        row.innerHTML = html;
        return Array.from(row.querySelectorAll('button'), b => b.getAttribute('aria-label') || b.textContent);
      };
      return [labels(actions({signedOut: true})), labels(actions({signedOut: true, profile: 'acme'})),
        labels(actions({signedIn: true, onKey: true}))];
    });
    assert.deepEqual(museActions, [['Use the shared key'], [], ['Switch to subscription', 'Sign out']]);

    // Sign out: off while the bots that stop are looked up and when the lookup fails, which offers Try again; then
    // the confirmation names them. Cancel sends nothing.
    let letGo = hold();
    await own('codex').getByRole('button', {name: 'Sign out'}).click();
    const confirm = page.locator('dialog.model-logout');
    await confirm.locator('[data-bots]', {hasText: 'Checking which bots'}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), true, 'off while the bots load');
    letGo(true);
    await confirm.locator('[data-bots]', {hasText: "Couldn't check which bots use it"}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), true, 'off when the lookup failed');
    await confirm.locator('[data-sign-out]').dispatchEvent('click');
    assert.deepEqual(writes, [], 'a click on the disabled button sends nothing');
    letGo = hold();
    await confirm.getByRole('button', {name: 'Try again'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Checking which bots'}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), true);
    letGo(false);
    await confirm.locator('[data-bots]', {hasText: 'Writer'}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), false, 'on once the bots are named');
    assert.equal(await confirm.locator('h2').innerText(), 'Sign out of Codex (ChatGPT) on Team box?');
    // The spec's words: "Reviewer 1, QA and 2 more will stop running until this is signed in again".
    assert.equal(await confirm.locator('[data-bots]').innerText(), 'Writer and Scout will stop running until this is signed in again.');
    assert.match(await confirm.innerText(), /shared key won't sign it back in by itself/);
    await confirm.getByRole('button', {name: 'Cancel'}).click();
    await page.locator('dialog.model-logout').waitFor({state: 'detached'});   // removed on the dialog's close event, a task after the click
    assert.deepEqual(writes, []);
    // Four bots: the first two by name and how many more, every name on hover.
    await own('claude').getByRole('button', {name: 'Sign out'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Reviewer 1'}).waitFor();
    assert.equal(await confirm.locator('[data-bots]').innerText(), 'Reviewer 1, QA and 2 more will stop running until this is signed in again.');
    assert.equal(await confirm.locator('[data-bots]').getAttribute('title'), 'Reviewer 1, QA, Scout, Writer');
    await confirm.getByRole('button', {name: 'Cancel'}).click();
    await page.locator('dialog.model-logout').waitFor({state: 'detached'});
    assert.deepEqual(writes, []);

    // Confirmed: it asks the computer and follows it to Signed out; the row then reads Signed out, with Sign in and
    // Use the shared key, once a heartbeat newer than the sign-out says so (the first look still reads it signed in).
    await own('codex').getByRole('button', {name: 'Sign out'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Writer'}).waitFor();
    await confirm.locator('[data-sign-out]').click();
    await confirm.locator('[data-status][data-state=signed_out]').waitFor();
    assert.equal(await confirm.locator('[data-status]').innerText(), 'Signed out. Codex (ChatGPT) on Team box stays signed out until someone signs in.');
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/logouts', body: {runtime: 'codex'}}]);
    await confirm.locator('[data-actions] [data-close]').click();
    await own('codex').getByRole('button', {name: 'Use the shared key'}).waitFor({timeout: 15000});
    // Add key: a key of its own (Dev 97, ui/tests/model-keys.cjs).
    assert.deepEqual(await buttons(own('codex')), ['Sign in', 'Use the shared key', 'Add key']);
    // "Signed out · Sign in", and the chip on the computer's row has the red dot.
    assert.match((await own('codex').locator('.computer-conn-signin').textContent()).replace(/\s+/g, ' ').trim(), /^Signed out · Sign in/);
    assert.equal(await own('codex').locator('.computer-conn-head .machine-chip-dot').count(), 1);
    assert.equal(await page.locator('#set-machines [data-chip="own:codex"].attention .machine-chip-dot').count(), 1);
    assert.match(await own('codex').locator('.computer-conn-signin .pill').getAttribute('title'), /shared key does not sign it back in/);
    await own('codex').getByRole('button', {name: 'Use the shared key'}).click();
    await page.locator('.toast', {hasText: 'shared key signs it in'}).waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/shared-key', body: {runtime: 'codex'}});
    await own('codex').getByRole('button', {name: 'Sign out'}).waitFor({timeout: 15000});

    // On the shared key it runs on an API key, so it offers Switch to subscription, not Switch account (Computers part
    // 2). It is the sign-in dialog as a switch of type: a cancel says the key is still in use; done, the line shows
    // the subscription and offers Switch account again.
    assert.deepEqual(await buttons(own('codex')), ['Switch to subscription', 'Sign out']);
    const toSub = page.locator('dialog.model-login:not(.model-logout)');
    await own('codex').getByRole('button', {name: 'Switch to subscription'}).click();
    await toSub.locator('h2', {hasText: 'Switch Codex (ChatGPT) on Team box to a subscription'}).waitFor();
    await toSub.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/logins', body: {runtime: 'codex', to: 'subscription'}});
    await toSub.locator('[data-cancel]').click();
    await toSub.locator('[data-status]', {hasText: 'canceled'}).waitFor();
    assert.match(await toSub.locator('[data-status]').innerText(), /The API key is still in use\./);
    await page.keyboard.press('Escape');
    await own('codex').getByRole('button', {name: 'Switch to subscription'}).click();
    await toSub.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    finish(last(), 'signed_in', () => { box.readiness.runtimes.codex.account = {email: 'old@acme.example', plan: 'pro', kind: 'subscription', checked_at: new Date().toISOString()}; });
    await toSub.locator('[data-status][data-state=signed_in]').waitFor();
    assert.equal(await toSub.locator('[data-status]').innerText(), 'Switched. Codex (ChatGPT) on Team box now runs on the subscription; its bots keep it, and the API key is off.');
    await toSub.getByRole('button', {name: 'Done'}).click();
    await own('codex').getByRole('button', {name: 'Switch account'}).waitFor({timeout: 15000});

    // Switch account: the sign-in dialog, as a switch; a cancel keeps the old account.
    const login = page.locator('dialog.model-login:not(.model-logout)');
    writes.length = 0;
    await own('claude').getByRole('button', {name: 'Switch account'}).click();
    await login.locator('h2', {hasText: 'Switch account for Claude Code on Team box'}).waitFor();
    await login.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/logins', body: {runtime: 'claude', switch: true}}]);
    await login.locator('[data-cancel]').click();
    await login.locator('[data-status]', {hasText: 'canceled'}).waitFor();
    assert.match(await login.locator('[data-status]').innerText(), /The account signed in before still works\./);
    await page.keyboard.press('Escape');

    // Past the commit point a switch reads as finishing, with nothing to cancel. Done, the row shows the new account
    // once a heartbeat newer than the switch carries it, not on the first look, which still reads the old one ready.
    await own('claude').getByRole('button', {name: 'Switch account'}).click();
    await login.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    rows[last()].state = 'finishing';
    await login.locator('[data-status][data-state=finishing]').waitFor();
    assert.equal(await login.locator('[data-status]').innerText(), 'Putting the new account in place on Team box…');
    assert.equal(await login.locator('[data-actions] button').count(), 0, 'no cancel once it is finishing');
    finish(last(), 'signed_in', () => { box.readiness.runtimes.claude.account = {email: 'new@acme.example', plan: 'max', checked_at: new Date().toISOString()}; });
    await login.locator('[data-status][data-state=signed_in]').waitFor();
    await login.getByRole('button', {name: 'Done'}).click();
    await own('claude').locator('.computer-conn-signin', {hasText: 'new@acme.example'}).waitFor({timeout: 15000});

    // Offline: Switch account and Sign out stay, disabled, saying why; nothing can be started.
    const online = await buttons(own('claude'));
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await own('claude').locator('.computer-conn-signin', {hasText: 'last checked'}).waitFor();
    assert.deepEqual(await buttons(own('claude')), online);
    assert.ok(online.includes('Sign out') && online.includes('Switch account'), online.join());
    assert.deepEqual(await own('claude').locator('.computer-conn-signin button').evaluateAll(all => all.map(b => b.disabled && b.title)),
      online.map(() => 'Computer is offline'));
    box.last_seen_at = at(-3000);
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Sign out'}).waitFor();

    // A Mac: Claude Code keeps its login in the keychain there, so it offers Sign out but no Switch account, nor Switch
    // to API key (a logout there could not be undone).
    box.platform = 'darwin';
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Sign out'}).waitFor();
    assert.deepEqual(await buttons(own('claude')), ['Sign out']);
    assert.ok((await buttons(own('codex'))).includes('Switch account'), 'Codex still switches on a Mac');
    box.platform = 'linux';
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Switch account'}).waitFor();

    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: nothing to sign out or switch.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-sign-out], #set-machines [data-model-switch], #set-machines [data-model-to-subscription]').count(), 0);
    assert.deepEqual(other.errors, []);
    console.log('PASS: sign out with the bots named, switch account, use the shared key, rights, offline, refresh on a newer heartbeat, and Mac');
}

// ---- model-to-key
// Switch to API key from the app (ui/model-login.js, backend/model_login.py `to_key`, runner-rs/src/login.rs). In a
// computer's pop-up on Settings > Computers, the computer's own login on a subscription offers Switch to API key next to
// Switch account and Sign out, for a tool Tico keeps a key for, to the computer's operator while the computer is online.
// The key is taken in a password field, posted once with `switch` and emptied from the page; the dialog says the
// subscription stays in use while the computer checks it, offers Cancel switch meanwhile, and ends saying the
// subscription was signed out, or that it is still in use. Once switched, the same connection (same bots) runs on the
// key. Every request is intercepted; the harness capabilities come from runner-rs/harnesses/*.toml.
async function toKey(browser) {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const SECRET = 'sk-test-not-a-real-key-123456';
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const subscription = () => ({installed: true, authenticated: 'ready', account: {email: 'dev@acme.example', plan: 'pro', checked_at: at(-60000)}});
    const fixture = () => ({id: 'box', label: 'Team box', owner_id: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
      readiness_at: at(-3000), needed_runtimes: ['codex', 'claude'], bot_runtimes: ['codex', 'claude'], signed_out: {}, keys: {}, shared_keys: {},
      bot_connections: [{bot: 'writer', runtime: 'codex', profile: ''}],
      readiness: {runtimes: {codex: subscription(), claude: subscription()},
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude')}},
      profiles: []});
    // How each job ends on the computer: on its second check, or (`hold`) not until it is canceled.
    const ending = {codex: {state: 'done', message: 'Switched to the API key; the subscription is signed out'}, claude: 'hold'};
    // `held`: the next Switch to API key request waits until it is released, as on a slow connection.
    let held = null;

    async function open(as = 'sam') {
      const box = fixture(), writes = [], rows = {};
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.1.0', app_name: 'Tico'};
        // Sam is a member who operates the computer; Cara is a member who does not.
        if (p === '/api/me') return json({id: as, role: 'member', name: as, email: `${as}@example.com`, cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/issues') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'sam', name: 'Sam'}], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p === '/api/v2/operations') return json({machines: [box], services: []});
        if (p === '/api/v2/models') return json({models: [], harnesses});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
        const keys = p.match(/^\/api\/v2\/computers\/(\w+)\/keys$/);
        if (keys && method === 'GET') {
          const runtime = url.searchParams.get('runtime'), name = runtime === 'codex' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY';
          return json({runtime, source: null, name, preview: '', cleared: false, names: [name], computers: [], can_replace_all: false});
        }
        if (p === '/api/v2/computers/box/keys' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          if (held) await held.promise;
          const id = 'key' + Object.keys(rows).length;
          rows[id] = {id, kind: body.switch ? 'to_key' : 'use_key', runtime: body.runtime, state: 'requested', polls: 0, created_at: new Date().toISOString(), lines: []};
          return json({...rows[id], key: {id: 'cred9', env: 'OPENAI_API_KEY', preview: 'sk-…456'}});
        }
        const cancel = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)\/cancel$/);
        if (cancel) {
          writes.push({p, body: req.postDataJSON()});
          return json(Object.assign(rows[cancel[1]], {state: 'canceled', updated_at: new Date().toISOString()}));
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)$/);
        if (one) {
          const row = rows[one[1]], end = ending[row.runtime];
          if (++row.polls === 2 && end !== 'hold') Object.assign(row, end, {updated_at: new Date().toISOString()});
          return json(row);
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines .machine-name[data-computer-open=box]').click();
      await page.locator('#set-machines dialog.computer-detail[open] [data-connection]').first().waitFor();
      return {page, box, writes, errors};
    }

    // ---- The operator, on Settings > Computers
    const {page, box, writes, errors} = await open();
    const detail = page.locator('#set-machines dialog.computer-detail[open]');
    const own = name => detail.locator(`[data-connection="own:${name}"]`);
    // Switch account and Sign out are icons: their name is the aria-label.
    const buttons = row => row.locator('.computer-conn-signin button').evaluateAll(all => all.map(b => b.getAttribute('aria-label') || b.innerText));
    assert.deepEqual(await buttons(own('codex')), ['Switch account', 'Switch to API key', 'Sign out']);
    assert.deepEqual(await buttons(own('claude')), ['Switch account', 'Switch to API key', 'Sign out']);
    const before = await own('codex').innerText();
    assert.match(before, /Bots: writer/i);

    // Switch: the key in a password field, posted once with `switch`, and the subscription in use until it is checked.
    await own('codex').getByRole('button', {name: 'Switch to API key'}).click();
    const dialog = page.locator('dialog.model-key');
    await dialog.locator('input[name=secret]').waitFor();
    assert.equal(await dialog.locator('h2').innerText(), 'Switch Codex (ChatGPT) on Team box to an API key');
    assert.match(await dialog.locator('[data-key-form]').innerText(), /The subscription stays signed in and in use while Team box checks the key; once it works there the subscription is signed out/);
    assert.match(await dialog.locator('[data-key-form]').innerText(), /Bots keep this login, so nothing needs reassigning/);
    assert.equal(await dialog.locator('[data-key-scope]').count(), 0);
    assert.equal(await dialog.locator('input[name=secret]').getAttribute('type'), 'password');
    assert.ok(await dialog.locator('[data-key-go]').isDisabled(), 'nothing to switch to yet');
    await dialog.locator('input[name=secret]').fill(SECRET);
    assert.equal(await dialog.locator('[data-key-go]').innerText(), 'Switch to API key');
    await dialog.locator('[data-key-go]').click();
    await dialog.locator('[data-status][data-state=done]').waitFor();
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/keys', body: {runtime: 'codex', secret: SECRET, scope: 'this', switch: true}}]);
    assert.equal(await dialog.locator('[data-status]').innerText(), 'Switched. Codex (ChatGPT) on Team box now uses sk-…456; the subscription is signed out.');
    assert.equal(await dialog.locator('input[name=secret]').inputValue(), '');
    assert.ok(!(await page.content()).includes(SECRET), 'the key is not kept in the page');
    await dialog.locator('[data-actions] [data-close]').click();
    assert.equal(writes.length, 1, 'closing a finished switch cancels nothing');

    // Once the computer reports it: the same connection, its bots kept, runs on this computer's key.
    box.readiness.runtimes.codex = {installed: true, authenticated: 'ready', key: {name: 'OPENAI_API_KEY', source: 'computer'}, account: {kind: 'api_key', checked_at: at(0)}};
    box.keys = {codex: {env: 'OPENAI_API_KEY', preview: 'sk-…456', since: at(0)}};
    box.readiness_at = new Date().toISOString();
    await page.evaluate(() => loadSettings());
    await own('codex').locator('.machine-key', {hasText: "this computer's key sk-…456"}).waitFor({timeout: 15000});
    assert.deepEqual(await buttons(own('codex')), ['Replace key', 'Clear key', 'Switch to subscription']);
    assert.match(await own('codex').innerText(), /Bots: writer/i, 'its bots kept, nothing reassigned');

    // Cancel switch while the key is checked: the subscription is still in use.
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    await dialog.getByRole('button', {name: 'Cancel switch'}).click();
    await dialog.locator('[data-status][data-state=canceled]').waitFor();
    assert.deepEqual(writes.slice(-1).map(w => w.p), ['/api/v2/computers/box/logins/key1/cancel']);
    assert.equal(await dialog.locator('[data-status]').innerText(), 'Canceled. The subscription is still in use.');
    await dialog.locator('[data-actions] [data-close]').click();

    // Closing the dialog while it is checked cancels it too.
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    await dialog.getByRole('button', {name: 'Cancel switch'}).waitFor();
    await dialog.locator('[data-close]').first().click();
    for (let i = 0; i < 50 && writes.at(-1).p !== '/api/v2/computers/box/logins/key2/cancel'; i++) await page.waitForTimeout(100);
    assert.equal(writes.at(-1).p, '/api/v2/computers/box/logins/key2/cancel');

    // Closed while the request is still on its way: the switch it started is canceled once it is back.
    held = {}; held.promise = new Promise(resolve => { held.release = resolve; });
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    for (let i = 0; i < 50 && writes.at(-1).p !== '/api/v2/computers/box/keys'; i++) await page.waitForTimeout(100);
    await dialog.locator('[data-close]').first().click();
    await dialog.waitFor({state: 'detached'});
    const release = held.release; held = null; release();
    for (let i = 0; i < 50 && writes.at(-1).p !== '/api/v2/computers/box/logins/key3/cancel'; i++) await page.waitForTimeout(100);
    assert.equal(writes.at(-1).p, '/api/v2/computers/box/logins/key3/cancel');

    // A failure says what the computer reported: the subscription still in use when it put everything back, and what
    // could not be put back when it could not, with no promise that the subscription is in use.
    const fails = async message => {
      ending.claude = {state: 'failed', message};
      await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
      await dialog.locator('input[name=secret]').fill(SECRET);
      await dialog.locator('[data-key-go]').click();
      await dialog.locator('[data-status][data-state=failed]').waitFor();
      const said = await dialog.locator('[data-status]').innerText();
      await dialog.locator('[data-actions] [data-close]').click();
      return said;
    };
    assert.equal(await fails('The new key did not work for claude; the subscription is still in use'),
      'The new key did not work for claude; the subscription is still in use.');
    const lost = "The subscription could not be signed out on claude, and the subscription's login could not be put back (mv failed); its files are in /home/sam/.tico-switch-1";
    assert.equal(await fails(lost), lost + '.');
    const keys = "Could not check that claude signed its subscription out; the subscription is back, but this computer's keys could not all be put back (PermissionDenied)";
    assert.equal(await fails(keys), keys + '.');
    for (const said of [lost, keys]) assert.doesNotMatch(said, /still in use/);

    // Offline: Switch to API key stays, disabled, saying why.
    const offered = await detail.locator('[data-model-to-key]').count();
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await own('claude').locator('.computer-conn-signin', {hasText: 'last checked'}).waitFor();
    assert.ok(offered > 0);
    assert.deepEqual(await detail.locator('[data-model-to-key]').evaluateAll(all => all.map(b => b.disabled && b.title)),
      Array(offered).fill('Computer is offline'));
    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: no switch.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-to-key]').count(), 0);
    assert.deepEqual(other.errors, []);
    console.log('PASS: Switch to API key on a subscription line, the key posted once with switch, done / cancel / close (also while it is being asked for) / failed with what the computer put back, same connection after, offline and rights');
}

// ---- model-keys
// Replace key / Clear key / Add key from the app (ui/model-login.js, backend/computer_keys.py, backend/model_login.py).
// In a computer's pop-up on Settings > Computers, a login a model key signs in shows whose key it is by its stored preview only (this
// computer's, or the company's shared one) and offers Replace key and Clear key in place of Sign out; a signed-out key
// runtime offers Add key in the Add connection pop-up (API key). Clear key names the bots that stop before it can go; for the shared key it first asks "this
// computer only" or "all of them", listing the computers, and says an offline one clears its copy when it is next
// online. Replace key takes the key in a password field, posts it once and empties the field, and follows the
// computer's check to the end. Replacing the shared key for all of them is a credential administrator's alone. A member
// who does not run the computer sees none of it. Every request is intercepted; the harness capabilities come from
// runner-rs/harnesses/*.toml.
async function keys(browser) {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const SECRET = 'sk-test-not-a-real-key-123456';
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const fixture = () => ({id: 'box', label: 'Team box', owner_id: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
      readiness_at: at(-3000), needed_runtimes: ['codex', 'claude'], bot_runtimes: ['codex'], signed_out: {},
      keys: {claude: {env: 'ANTHROPIC_API_KEY', preview: 'sk-…abc', since: at(-86400000)}},
      shared_keys: {codex: {env: 'OPENAI_API_KEY', preview: 'sk-…xyz'}},
      readiness: {
        runtimes: {
          codex: {installed: true, authenticated: 'ready', key: {name: 'OPENAI_API_KEY', source: 'shared'}, account: {kind: 'api_key', checked_at: at(-60000)}},
          claude: {installed: true, authenticated: 'ready', key: {name: 'ANTHROPIC_API_KEY', source: 'computer'}, account: {kind: 'api_key', checked_at: at(-60000)}},
          cursor: {installed: true, authenticated: 'missing'},
        },
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude'), 'cursor-agent': chip('Cursor Agent', 'cursor')}},
      profiles: []});
    // GET /v2/computers/{id}/keys (backend/model_login.py key_info)
    let oldBox = false;
    const info = runtime => runtime === 'codex'
      ? {runtime, source: 'shared', name: 'OPENAI_API_KEY', preview: 'sk-…xyz', cleared: false, names: ['OPENAI_API_KEY'], can_replace_all: false,
        computers: [{id: 'box', label: 'Team box', online: true, may: true, updated: true}, {id: 'far', label: 'Far box', online: false, may: true, updated: true},
          ...(oldBox ? [{id: 'old', label: 'Old box', online: false, may: true, updated: false}] : [])]}
      : runtime === 'claude'
        ? {runtime, source: 'computer', name: 'ANTHROPIC_API_KEY', preview: 'sk-…abc', cleared: false, names: ['ANTHROPIC_API_KEY'], computers: [], can_replace_all: false}
        : {runtime, source: null, name: '', preview: '', cleared: false, names: ['CURSOR_API_KEY'], computers: [], can_replace_all: false};

    async function open(as = 'sam') {
      const box = fixture(), writes = [], rows = {};
      // What /v2/operations sends someone who may not change the computer (backend/views.py): no key previews.
      if (as !== 'sam') for (const key of [...Object.values(box.keys), ...Object.values(box.shared_keys)]) delete key.preview;
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.1.0', app_name: 'Tico'};
        // Sam is a member who operates the computer; Cara is a member who does not.
        if (p === '/api/me') return json({id: as, role: 'member', name: as, email: `${as}@example.com`, cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/issues') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'sam', name: 'Sam'}], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p === '/api/v2/operations') return json({machines: [box], services: []});
        if (p === '/api/v2/models') return json({models: [], harnesses});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
        const keys = p.match(/^\/api\/v2\/computers\/(\w+)\/keys$/);
        if (keys && method === 'GET') return json(info(url.searchParams.get('runtime')));
        // backend/model_login.py affected_bots, per computer
        const bots = p.match(/^\/api\/v2\/computers\/(\w+)\/logouts\/bots$/);
        if (bots) return json({bots: url.searchParams.get('runtime') !== 'codex' ? [] : bots[1] === 'box' ? [{bot: 'writer', name: 'Writer'}] : [{bot: 'scout', name: 'Scout'}]});
        if (p === '/api/v2/computers/box/keys/clear' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          rows.clr1 = {id: 'clr1', kind: 'clear_key', runtime: body.runtime, state: 'requested', polls: 0, created_at: new Date().toISOString(), lines: []};
          return json({computers: [{id: 'box', label: 'Team box', login: rows.clr1, waiting: false},
            ...(body.scope === 'all' ? [{id: 'far', label: 'Far box', login: null, waiting: true}] : [])]});
        }
        if (p === '/api/v2/computers/box/keys' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          rows.use1 = {id: 'use1', kind: 'use_key', runtime: body.runtime, state: 'requested', polls: 0, created_at: new Date().toISOString(), lines: []};
          return json({...rows.use1, key: {id: 'cred9', env: 'ANTHROPIC_API_KEY', preview: 'sk-…456'}});
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)$/);
        if (one) {
          const row = rows[one[1]];
          // The computer finishes on its second check.
          if (++row.polls === 2) Object.assign(row, {state: row.kind === 'clear_key' ? 'signed_out' : 'done', updated_at: new Date().toISOString()});
          return json(row);
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines .machine-name[data-computer-open=box]').click();
      await page.locator('#set-machines dialog.computer-detail[open] [data-connection]').first().waitFor();
      return {page, box, writes, errors};
    }

    // ---- The operator, on Settings > Computers
    const {page, box, writes, errors} = await open();
    const detail = page.locator('#set-machines dialog.computer-detail[open]');
    const own = name => detail.locator(`[data-connection="own:${name}"]`);
    const buttons = row => row.locator('.computer-conn-signin button').allInnerTexts();
    // Add connection > the tool > API key: the pop-up at that step.
    const apiKey = async name => {
      await detail.locator('[data-add-connection]').click();
      const add = page.locator('dialog.computer-add-dialog[open]');
      await add.locator(`[data-add-tool="${name}"]`).click();
      await add.locator('[data-add-kind=api_key]').click();
      await add.locator('[data-add-step=api_key]').waitFor();
      return add;
    };
    // Whose key, by its preview only; Clear key stands in for Sign out.
    assert.match(await own('codex').locator('.machine-key').innerText(), /the company's shared key sk-…xyz/);
    assert.match(await own('claude').locator('.machine-key').innerText(), /this computer's key sk-…abc/);
    // Switch to subscription moves a login off its key (ui/tests/model-sign-out.cjs covers the switch itself).
    assert.deepEqual(await buttons(own('codex')), ['Replace key', 'Clear key', 'Switch to subscription']);
    assert.deepEqual(await buttons(own('claude')), ['Replace key', 'Clear key', 'Switch to subscription']);
    assert.equal(await own('cursor').count(), 0, 'no bot uses it: not a connection');
    let add = await apiKey('cursor');
    assert.deepEqual(await add.locator('[data-add-key="cursor"] button').allInnerTexts(), ['Add key'], 'a signed-out key runtime offers Add key');
    await add.locator('[data-add-close]').first().click();
    for (const name of ['codex', 'claude']) {
      add = await apiKey(name);
      assert.equal(await add.locator('[data-add-key]').count(), 0, 'not where a key signs it in');
      assert.match(await add.innerText(), /already signs in with a key on Team box: Replace key is on its line/);
      await add.locator('[data-add-close]').first().click();
    }

    // Clear key on the shared key: the ask lists the computers and what the offline one does, the bots are named for
    // the choice, and the reply says Far box clears its copy when it is next online.
    await own('codex').getByRole('button', {name: 'Clear key'}).click();
    const dialog = page.locator('dialog.model-key');
    await dialog.locator('[data-key-bots]', {hasText: 'Writer'}).waitFor();
    assert.match(await dialog.locator('h2').innerText(), /Clear the Codex \(ChatGPT\) key on Team box\?/);
    assert.deepEqual(await dialog.locator('[data-scope-computer]').allInnerTexts(), ['Team box', 'Far box (offline: clears its copy when it is next online)']);
    assert.equal(await dialog.locator('[data-key-bots]').innerText(), 'This bot stops until a key is added again: Writer.');
    await dialog.locator('input[name=key-scope][value=all]').check();
    await dialog.locator('[data-key-bots]', {hasText: 'Scout'}).waitFor();
    assert.equal(await dialog.locator('[data-key-bots]').innerText(), 'These 2 bots stop until a key is added again: Writer (Team box), Scout (Far box).');
    await dialog.locator('[data-key-go]').click();
    await dialog.locator('[data-status][data-state=signed_out]').waitFor();
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/keys/clear', body: {runtime: 'codex', scope: 'all'}}]);
    assert.equal(await dialog.locator('[data-status]').innerText(),
      'Cleared. Codex (ChatGPT) on Team box has no key until one is added. Far box is offline and clears its copy when next online.');
    await dialog.locator('[data-actions] [data-close]').click();

    // Replace key on this computer's own key: no ask, a password field, the key posted once and gone from the page.
    await own('claude').getByRole('button', {name: 'Replace key'}).click();
    await dialog.locator('input[name=secret]').waitFor();
    assert.equal(await dialog.locator('[data-key-scope]').count(), 0, 'its own key: nothing to ask');
    assert.equal(await dialog.locator('input[name=secret]').getAttribute('type'), 'password');
    assert.ok(await dialog.locator('[data-key-go]').isDisabled(), 'nothing to replace with yet');
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    await dialog.locator('[data-status][data-state=done]').waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/keys', body: {runtime: 'claude', secret: SECRET, scope: 'this'}});
    assert.equal(await dialog.locator('[data-status]').innerText(), 'Replaced. Claude Code on Team box now uses sk-…456.');
    assert.equal(await dialog.locator('input[name=secret]').inputValue(), '');
    assert.ok(!(await page.content()).includes(SECRET), 'the key is not kept in the page');
    await dialog.locator('[data-actions] [data-close]').click();

    // Replace key on the shared key: "All listed computers" is a credential administrator's alone.
    await own('codex').getByRole('button', {name: 'Replace key'}).click();
    await dialog.locator('[data-key-scope]').waitFor();
    assert.ok(await dialog.locator('input[name=key-scope][value=all]').isDisabled());
    assert.match(await dialog.locator('[data-key-scope]').innerText(), /Only a credential administrator can do this/);
    assert.match(await dialog.locator('[data-key-scope]').innerText(), /Far box \(offline: gets the new key when it is next online\)/);
    await dialog.locator('[data-close]').first().click();

    // An older runner, offline or not, cannot clear its copy: All listed computers waits for its update.
    oldBox = true;
    await own('codex').getByRole('button', {name: 'Clear key'}).click();
    await dialog.locator('[data-key-scope]').waitFor();
    assert.ok(await dialog.locator('input[name=key-scope][value=all]').isDisabled());
    assert.match(await dialog.locator('[data-key-scope]').innerText(), /Old box \(needs a runner update to clear it from here\)/);
    assert.match(await dialog.locator('[data-key-scope]').innerText(), /Update Old box first, or delete the shared key in Credentials/);
    await dialog.locator('[data-close]').first().click();
    oldBox = false;

    // Add key where none signs it in.
    await (await apiKey('cursor')).getByRole('button', {name: 'Add key'}).click();
    // It closes in the click itself and leaves the page on its close event, a task later: count it shut now, then gone.
    assert.equal(await page.locator('dialog.computer-add-dialog[open]').count(), 0, 'Add connection gives way to the key pop-up');
    await page.locator('dialog.computer-add-dialog').waitFor({state: 'detached'});
    await dialog.locator('h2', {hasText: 'Add a Cursor Agent key on Team box'}).waitFor();
    await dialog.locator('[data-close]').first().click();

    // A key cleared from here reads "No key · Add key", not Signed out; the shared key and a sign-in are the other ways
    // back. A login signed out from here still reads "Signed out · Sign in".
    const line = async row => (await row.locator('.computer-conn-signin').innerText()).replace(/\s+/g, ' ').trim();
    const codex = {...box.readiness.runtimes.codex};
    Object.assign(box, {signed_out: {codex: ['']}, key_cleared: ['codex']});
    box.readiness.runtimes.codex = {installed: true, authenticated: 'missing'};
    await page.evaluate(() => loadSettings());
    await own('codex').locator('.computer-conn-signin .pill', {hasText: 'No key'}).waitFor();
    assert.equal(await line(own('codex')), 'No key · Add key Sign in Use the shared key');
    assert.match(await own('codex').locator('.computer-conn-signin .pill').getAttribute('title'), /key was cleared from Tico/);
    box.key_cleared = [];
    await page.evaluate(() => loadSettings());
    await own('codex').locator('.computer-conn-signin .pill', {hasText: 'Signed out'}).waitFor();
    assert.equal(await line(own('codex')), 'Signed out · Sign in Use the shared key Add key');
    Object.assign(box, {signed_out: {}});
    box.readiness.runtimes.codex = codex;

    // Offline: Replace key, Clear key and Switch to subscription stay on the line, disabled, saying why.
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await own('claude').locator('[data-model-key-replace][disabled]').waitFor();
    for (const name of ['claude', 'codex']) {
      const off = own(name).locator('.computer-conn-signin button');
      assert.deepEqual(await off.allInnerTexts(), ['Replace key', 'Clear key', 'Switch to subscription']);
      assert.deepEqual(await off.evaluateAll(all => all.map(b => b.disabled && b.title)), Array(3).fill('Computer is offline'));
    }
    const sent = writes.length;
    await own('claude').locator('[data-model-key-clear]').click({force: true});
    assert.equal(await page.locator('dialog.model-key[open]').count(), 0, 'nothing opens offline');
    assert.equal(writes.length, sent, 'nothing queues offline');
    box.last_seen_at = at(-3000);
    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: no key to change.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-key-replace], #set-machines [data-model-key-clear], #set-machines [data-model-key-add]').count(), 0);
    assert.doesNotMatch(await other.page.locator('#set-machines dialog.computer-detail[open]').innerText(), /sk-/, 'no key preview');
    assert.deepEqual(other.errors, []);
    console.log('PASS: key previews, clear with the bots named and the this/all ask (offline clears when next online, an older runner waits for its update), replace through a password field, admin-only replace-all, add key, No key · Add key once cleared, disabled offline, rights');
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [name, step] of Object.entries({loginHosted, signOut, toKey, keys})) {
      try { await step(browser); }
      catch (error) { error.message = `[${name}] ${error.message}`; throw error; }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
