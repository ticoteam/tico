// Sign in on a hosted Tico (config `hosted`, TICO_HOSTED; ui/model-login.js). The person has no terminal on the
// computer, so Settings > Computers signs Claude Code and Codex in with the provider's link and code only: the dialog
// starts the sign-in by itself, shows the link (and Codex's one-time code, or Claude's paste field), never what else
// the CLI printed (a failure gives its message), never points to a terminal when the computer is slow to start, and
// closes by itself once signed in (saying so in a toast) or when cancelled, cancelling it on the computer. A
// self-hosted Tico keeps the computer's lines, the terminal hint and the dialog open after a cancel. Every request is
// intercepted; the harness capabilities come from runner/harnesses/*.toml.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const CLAUDE_URL = 'https://claude.com/cai/oauth/authorize?code=true&client_id=fixture';

    async function open(hosted) {
      const box = {id: 'box', label: 'Server', operator: 'sam', bots: [], platform: 'linux', last_seen_at: at(-3000), readiness_at: at(-3000),
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
          rows[one[1]].state = 'cancelled';
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

    // ---- Hosted: Codex. Link and one-time code; Cancel sign-in closes only once the computer's sign-in is cancelled.
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
      await page.locator('.toast', {hasText: 'The sign-in was cancelled.'}).waitFor();
      // Refused because the computer is already putting the account in place (409): the pane stays, follows it to
      // signed in, and closes then.
      await signIn('codex').click();
      await dialog.locator('[data-status]', {hasText: 'Starting the sign-in'}).waitFor();
      Object.assign(last(), {state: 'waiting', url: 'https://auth.openai.com/codex/device', code: 'WXYZ-67890'});
      await dialog.locator('code[data-code]', {hasText: 'WXYZ-67890'}).waitFor();
      const late = "The new account is already being put in place, so it can't be cancelled now";
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
      await dialog.locator('[data-status]', {hasText: 'cancelled'}).waitFor();
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
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
