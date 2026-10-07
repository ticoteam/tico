// Connect an agent (ui/connect-agent.js): one tile per agent with its logo; picking Grok shows only
// Grok's steps with the MCP URL; Create token shows the token once and fills the snippet; polling the
// token list flips to Connected when the agent's first call lands; the token never reaches storage, a
// URL or the console; an existing connection can be revoked. Desktop and phone, light and dark.
// Fixtures only - no server. TICO_SCREENSHOT_DIR=<dir> saves the review screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const shots = process.env.TICO_SCREENSHOT_DIR;
const MCP_URL = 'https://runner.acme.example/api/v2/mcp';
const SECRET = 'tico_pt_' + 'Fx7'.repeat(13) + 'Q';

async function open(browser, viewport, colorScheme, bypass) {
  const context = await browser.newContext({viewport, colorScheme, serviceWorkers: 'block', hasTouch: viewport.width < 760,
    isMobile: viewport.width < 760});
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin: 'https://tico-ui.test'});
  const page = await context.newPage();
  const now = new Date();
  await page.clock.install({time: new Date(now.getTime() - 3600000)});
  await page.clock.pauseAt(now);
  const state = {tokens: [{id: 'old-grok', label: 'grok-bot', created: '2026-09-01T10:00:00Z', last_used: '2026-09-29T09:00:00Z',
    expires_at: '2026-12-01T10:00:00Z', revoked_at: null},
  {id: 'ci', label: 'CI script', created: '2026-09-02T10:00:00Z', last_used: null, expires_at: '2026-12-01T10:00:00Z', revoked_at: null}],
  created: [], revoked: [], urls: [], logs: []};
  page.on('pageerror', error => state.logs.push('pageerror ' + error.message));
  page.on('console', message => state.logs.push(message.text()));
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    state.urls.push(request.url());
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && p.endsWith('.woff2')) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(path.join(__dirname, '..', p))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/agent-skill') return json({mcp_url: MCP_URL, text: 'SKILL', access_bypass: bypass});
    if (p === '/api/v2/me/tokens' && request.method() === 'GET') return json({tokens: state.tokens});
    if (p === '/api/v2/me/tokens' && request.method() === 'POST') {
      const body = request.postDataJSON();
      state.created.push(body);
      const row = {id: 'new-' + state.created.length, label: body.label, created: new Date().toISOString(), last_used: null,
        expires_at: '2026-12-29T10:00:00Z', revoked_at: null};
      state.tokens.unshift(row);
      return json({id: row.id, token: SECRET, label: row.label, expires_at: row.expires_at});
    }
    const revoke = p.match(/^\/api\/v2\/me\/tokens\/([^/]+)\/revoke$/);
    if (revoke) {
      state.revoked.push(revoke[1]);
      state.tokens.find(row => row.id === revoke[1]).revoked_at = new Date().toISOString();
      return json({id: revoke[1], revoked: true});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json({});
  });
  return {page, context, state};
}

const shot = (page, name) => shots ? page.screenshot({path: path.join(shots, `connect-agent-${name}.png`)}) : null;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [device, viewport] of [['desktop', {width: 1280, height: 900}], ['phone', {width: 390, height: 844}]]) {
      for (const scheme of ['light', 'dark']) {
        const tag = `${device}-${scheme}`;
        // On the phone runs the URL is Cloudflare Access's hostname, which must let /api/v2/mcp through.
        const bypass = device === 'phone';
        const {page, context, state} = await open(browser, viewport, scheme, bypass);
        await page.goto('https://tico-ui.test/#/tasks');
        await page.waitForFunction(() => typeof connectAgent === 'function' && document.querySelector('#connect-agent'));
        if (device === 'desktop') { await page.locator('#connect-agent').waitFor(); await page.locator('#connect-agent').click(); }
        else await page.evaluate(() => connectAgent());
        const dialog = page.locator('dialog.connect-agent[open]');
        await dialog.waitFor();
        assert.match(await dialog.locator('.ca-intro').innerText(), /Connect Grok, Dots, Muse or any external agent to Tico\./);

        // The tiles, each a logo (or letters) and a name; the existing connections with their agent's mark.
        const tiles = dialog.locator('[data-agent]');
        await tiles.first().waitFor();
        assert.deepEqual(await tiles.evaluateAll(els => els.map(el => el.dataset.agent)),
          ['grok', 'dots', 'muse', 'claude', 'cursor', 'codex', 'other'], tag);
        for (const id of ['grok', 'cursor', 'codex', 'other'])
          assert.equal(await dialog.locator(`[data-agent=${id}] .ca-logo svg`).count(), 1, `${tag}: ${id} logo`);
        // Meta and Anthropic allow their marks only with approval, and Dots has none: letters.
        for (const [id, letters] of [['dots', 'Do'], ['muse', 'Mu'], ['claude', 'Cl']])
          assert.equal(await dialog.locator(`[data-agent=${id}] .ca-initials`).innerText(), letters);
        await dialog.locator('[data-conn]').first().waitFor();
        assert.deepEqual(await dialog.locator('[data-conn]').evaluateAll(els => els.map(el => el.dataset.conn)), ['old-grok', 'ci']);
        assert.equal(await dialog.locator('[data-conn=old-grok] .ca-logo svg').count(), 1, 'an old Grok Bot token shows the Grok mark');
        assert.match(await dialog.locator('[data-conn=old-grok]').innerText(), /Used/);
        assert.match(await dialog.locator('[data-conn=ci]').innerText(), /Never used/);
        const fits = () => page.evaluate(() => { const d = document.querySelector('dialog.connect-agent');
          return d.getBoundingClientRect().right <= innerWidth + 0.5 && d.querySelector('.ca-body').scrollWidth <= d.querySelector('.ca-body').clientWidth + 1; });
        assert.ok(await fits(), tag + ': the picker fits');
        await shot(page, `picker-${tag}`);

        // Revoke an existing connection.
        page.once('dialog', prompt => prompt.accept());
        await dialog.locator('[data-conn=ci] [data-revoke]').click();
        await page.waitForFunction(() => !document.querySelector('[data-conn=ci]'));
        assert.deepEqual(state.revoked, ['ci']);

        // Grok: only its steps, the URL before any token.
        await dialog.locator('[data-agent=grok]').click();
        await dialog.locator('[data-url]').waitFor();
        assert.equal(await dialog.locator('[data-agent]').count(), 0, 'the tiles give way to the steps');
        assert.equal(await dialog.locator('.ca-agent h3').innerText(), 'Grok');
        assert.equal(await dialog.locator('[data-url]').innerText(), MCP_URL);
        assert.equal(await dialog.locator('[data-access-note]').count(), bypass ? 1 : 0);
        const steps = await dialog.locator('.ca-steps li').allInnerTexts();
        assert.ok(steps.length >= 2 && steps.length <= 4, tag + ': 2-4 steps');
        assert.match(steps.join(' '), /Grok/);
        assert.equal(await dialog.locator('[data-token]').count(), 0);
        await dialog.locator('[data-copy-url]').click();
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), MCP_URL);

        // Create token: named for the agent and the day, shown once, in the snippet with the URL.
        await dialog.locator('[data-create]').click();
        await dialog.locator('[data-token]').waitFor();
        assert.match(state.created[0].label, /^Grok · \d{4}-\d{2}-\d{2}$/);
        assert.equal(await dialog.locator('[data-token]').innerText(), SECRET);
        assert.match(await dialog.locator('[data-token-section] .ca-note').innerText(), /Shown once/);
        // Grok's two blocks, a Grok Bot's and Grok Build's, each with the URL and the token.
        const blocks = await dialog.locator('[data-snippet]').allInnerTexts();
        assert.equal(blocks.length, 2);
        for (const block of blocks) assert.ok(block.includes(MCP_URL) && block.includes('Bearer ' + SECRET), tag + ': ' + block);
        await dialog.locator('[data-copy-block="1"]').click();
        assert.match(await page.evaluate(() => navigator.clipboard.readText()), /^grok mcp add --transport http tico /);
        await dialog.locator('[data-copy-token]').click();
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), SECRET);
        assert.equal(await dialog.locator('[data-status]').getAttribute('data-state'), 'waiting');
        assert.ok(await fits(), tag + ': the steps fit');
        await shot(page, `grok-token-${tag}`);

        // The agent's first call lands: the next poll says Connected.
        state.tokens[0].last_used = new Date().toISOString();
        const beforePoll = state.urls.filter(u => u.endsWith('/api/v2/me/tokens')).length;
        await page.clock.fastForward(2999);
        assert.equal(state.urls.filter(u => u.endsWith('/api/v2/me/tokens')).length, beforePoll, 'no poll before three seconds');
        await page.clock.fastForward(1);
        await page.waitForFunction(() => document.querySelector('[data-status]')?.dataset.state === 'connected', null, {timeout: 8000});
        assert.equal(await dialog.locator('[data-status-text]').innerText(), 'Connected');
        await shot(page, `connected-${tag}`);

        // The secret never left the dialog: no storage, no URL, no console line; closing forgets it.
        const stored = await page.evaluate(() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage}) + location.href);
        assert.ok(!stored.includes(SECRET), tag + ': not stored');
        assert.ok(!state.urls.some(u => u.includes(SECRET)), tag + ': never in a URL');
        assert.ok(!state.logs.some(line => line.includes(SECRET)), tag + ': never logged');
        assert.ok(!state.logs.some(line => line.startsWith('pageerror')), state.logs.join('\n'));
        const polls = state.urls.filter(u => u.endsWith('/api/v2/me/tokens')).length;
        await dialog.locator('[data-close]').click();
        await page.waitForFunction(() => !document.querySelector('dialog.connect-agent'));
        assert.equal(await page.evaluate(secret => document.body.innerHTML.includes(secret), SECRET), false);
        if (tag === 'desktop-light') {
          await page.clock.fastForward(300000);
          // one read on close, for the team chart's Connect button; then nothing
          assert.equal(state.urls.filter(u => u.endsWith('/api/v2/me/tokens')).length, polls + 1, 'polling stops on close');
        }
        await context.close();
      }
    }
    console.log('connect-agent: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
