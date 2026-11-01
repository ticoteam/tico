// Replace key / Clear key / Add key from the app (ui/model-login.js, backend/computer_keys.py, backend/model_login.py).
// In a computer's pop-up on Settings > Computers, a login a model key signs in shows whose key it is by its stored preview only (this
// computer's, or the company's shared one) and offers Replace key and Clear key in place of Sign out; a signed-out key
// runtime offers Add key in the Add connection pop-up (API key). Clear key names the bots that stop before it can go; for the shared key it first asks "this
// computer only" or "all of them", listing the computers, and says an offline one clears its copy when it is next
// online. Replace key takes the key in a password field, posts it once and empties the field, and follows the
// computer's check to the end. Replacing the shared key for all of them is a credential administrator's alone. A member
// who does not run the computer sees none of it. Every request is intercepted; the harness capabilities come from
// runner/harnesses/*.toml.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const SECRET = 'sk-test-not-a-real-key-123456';
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const fixture = () => ({id: 'box', label: 'Team box', operator: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
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
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
