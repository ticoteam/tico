import type {Route} from 'playwright';
// Settings > Computers' model sign-in pop-ups, with every request intercepted (ui/model-login.ts; the harness capabilities
// come from runner-rs/harnesses/*.toml). What were model-login-hosted (hosted sign-in shows only the link and code) and
// model-to-key (Switch to API key); each keeps its own header below and names itself in a failure ("[toKey] ...").
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
declare function loadSettings(): Promise<void>;

// ---- model-login-hosted
// Sign in on a hosted Tico (config `hosted`, TICO_HOSTED; ui/model-login.ts). The person has no terminal on the
// computer, so Settings > Computers signs Claude Code and Codex in with the provider's link and code only: the dialog
// starts the sign-in by itself, shows the link (and Codex's one-time code, or Claude's paste field), never what else
// the CLI printed (a failure gives its message), never points to a terminal when the computer is slow to start, and
// closes by itself once signed in (saying so in a toast) or when canceled, canceling it on the computer. A
// self-hosted Tico keeps the computer's lines, the terminal hint and the dialog open after a cancel. Every request is
// intercepted; the harness capabilities come from runner-rs/harnesses/*.toml.
async function loginHosted(browser: import('playwright').Browser) {
    const now = Date.now(), at = (ms: number) => new Date(now + ms).toISOString();
    const chip = (name: string, runtime: string) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const CLAUDE_URL = 'https://claude.com/cai/oauth/authorize?code=true&client_id=fixture';

    async function open(hosted: string) {
      const box = {id: 'box', label: 'Server', owner_id: 'sam', bots: [], platform: 'linux', last_seen_at: at(-3000), readiness_at: at(-3000),
        needed_runtimes: ['claude', 'codex'], bot_runtimes: [], signed_out: {}, profiles: [],
        readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}, claude: {installed: true, authenticated: 'missing'}},
          harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude')}}};
      const writes: {p: string; body?: unknown}[] = [], rows: Record<string, {state?: string; code_sent?: boolean; [k: string]: unknown}> = {};
      let seq = 0, refusal = null as {times: number; status: number; detail: string; state?: string} | null;
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors: string[] = [];
      page.on('pageerror', (e: Error) => errors.push(e.message));
      await page.route('**/*', async (route: Route) => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!)))
          return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
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
            if (state) rows[one[1]!]!.state = state;
            return json({error: {detail}}, status);
          }
          rows[one[1]!]!.state = 'canceled';
          return json(rows[one[1]!]!);
        }
        if (one && one[2] === '/code') { writes.push({p}); rows[one[1]!]!.code_sent = true; return json(rows[one[1]!]!); }
        if (one) return json(rows[one[1]!]!);
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
      const signIn = (name: string) => ({click: async () => {
        await add.click();
        const step = page.locator('dialog.computer-add-dialog[open]');
        await step.locator(`[data-add-tool="${name}"]`).click();
        if (!await step.locator('[data-add-own]').count()) await step.locator('[data-add-kind=subscription]').click();
        await step.locator('[data-add-own]').click();
      }});
      return {page, box, writes, errors, rows, signIn, last: () => rows[`in${seq}`]!, refuse: (r: {status: number; detail: string; state?: string; times?: number}) => { refusal = {times: 1, ...r}; }};
    }
    const login = (page: import('playwright').Page) => page.locator('dialog.model-login:not(.model-logout)');

    // A duplicate connection is refused by the shared API, and its existing dialog shows how to change accounts.
    {
      const {page, errors, signIn} = await open('own');
      const detail = 'Claude Code is already connected or signing in on Server. Use Switch account on the existing connection to change it';
      await page.route('**/api/v2/computers/box/logins', (route: Route) => route.fulfill({status: 409,
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

// ---- model-to-key
// Switch to API key from the app (ui/model-login.ts, backend/model_login.py `to_key`, runner-rs/src/login.rs). In a
// computer's pop-up on Settings > Computers, the computer's own login on a subscription offers Switch to API key next to
// Switch account and Sign out, for a tool Tico keeps a key for, to the computer's operator while the computer is online.
// The key is taken in a password field, posted once with `switch` and emptied from the page; the dialog says the
// subscription stays in use while the computer checks it, offers Cancel switch meanwhile, and ends saying the
// subscription was signed out, or that it is still in use. Once switched, the same connection (same bots) runs on the
// key. Every request is intercepted; the harness capabilities come from runner-rs/harnesses/*.toml.
async function toKey(browser: import('playwright').Browser) {
    const now = Date.now(), at = (ms: number) => new Date(now + ms).toISOString();
    const SECRET = 'sk-test-not-a-real-key-123456';
    const chip = (name: string, runtime: string) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const subscription = () => ({installed: true, authenticated: 'ready', account: {email: 'dev@acme.example', plan: 'pro', checked_at: at(-60000)}});
    const fixture = () => ({id: 'box', label: 'Team box', owner_id: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
      readiness_at: at(-3000), needed_runtimes: ['codex', 'claude'], bot_runtimes: ['codex', 'claude'], signed_out: {}, keys: {}, shared_keys: {},
      bot_connections: [{bot: 'writer', runtime: 'codex', profile: ''}],
      readiness: {runtimes: {codex: subscription(), claude: subscription()},
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude')}},
      profiles: []});
    // How each job ends on the computer: on its second check, or (`hold`) not until it is canceled.
    const ending: Record<string, string | {state: string; message: string}> = {codex: {state: 'done', message: 'Switched to the API key; the subscription is signed out'}, claude: 'hold'};
    // `held`: the next Switch to API key request waits until it is released, as on a slow connection.
    let held: {promise?: Promise<void>; release?: () => void} | null = null;

    async function open(as = 'sam') {
      const box = fixture(), writes: {p: string; body?: unknown}[] = [], rows: Record<string, {polls: number; runtime: string; [k: string]: unknown}> = {};
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors: string[] = [];
      page.on('pageerror', (e: Error) => errors.push(e.message));
      await page.route('**/*', async (route: Route) => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!)))
          return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
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
          return json(Object.assign(rows[cancel[1]!]!, {state: 'canceled', updated_at: new Date().toISOString()}));
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)$/);
        if (one) {
          const row = rows[one[1]!]!, end = ending[row.runtime];
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
    const own = (name: string) => detail.locator(`[data-connection="own:${name}"]`);
    // Switch account and Sign out are icons: their name is the aria-label.
    const buttons = (row: import('playwright').Locator) => row.locator('.computer-conn-signin button').evaluateAll((all: HTMLElement[]) => all.map(b => b.getAttribute('aria-label') || b.innerText));
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
    box.readiness.runtimes.codex = {installed: true, authenticated: 'ready', key: {name: 'OPENAI_API_KEY', source: 'computer'}, account: {kind: 'api_key', checked_at: at(0)}} as unknown as typeof box.readiness.runtimes.codex;
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
    for (let i = 0; i < 50 && writes.at(-1)!.p !== '/api/v2/computers/box/logins/key2/cancel'; i++) await page.waitForTimeout(100);
    assert.equal(writes.at(-1)!.p, '/api/v2/computers/box/logins/key2/cancel');

    // Closed while the request is still on its way: the switch it started is canceled once it is back.
    held = {}; held.promise = new Promise(resolve => { held!.release = resolve; });
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    for (let i = 0; i < 50 && writes.at(-1)!.p !== '/api/v2/computers/box/keys'; i++) await page.waitForTimeout(100);
    await dialog.locator('[data-close]').first().click();
    await dialog.waitFor({state: 'detached'});
    const release = held.release!; held = null; release();
    for (let i = 0; i < 50 && writes.at(-1)!.p !== '/api/v2/computers/box/logins/key3/cancel'; i++) await page.waitForTimeout(100);
    assert.equal(writes.at(-1)!.p, '/api/v2/computers/box/logins/key3/cancel');

    // A failure says what the computer reported: the subscription still in use when it put everything back, and what
    // could not be put back when it could not, with no promise that the subscription is in use.
    const fails = async (message: string) => {
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
    assert.deepEqual(await detail.locator('[data-model-to-key]').evaluateAll((all: HTMLElement[]) => all.map(b => (b as HTMLButtonElement).disabled && b.title)),
      Array(offered).fill('Computer is offline'));
    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: no switch.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-to-key]').count(), 0);
    assert.deepEqual(other.errors, []);
    console.log('PASS: Switch to API key on a subscription line, the key posted once with switch, done / cancel / close (also while it is being asked for) / failed with what the computer put back, same connection after, offline and rights');
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    for (const [name, step] of Object.entries({loginHosted, toKey})) {
      try { await step(browser); }
      catch (error) { (error as Error).message = `[${name}] ${(error as Error).message}`; throw error; }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
