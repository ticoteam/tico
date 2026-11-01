// The credential card (ui/credential-card.js): a bot asks for a secret inside the chat, the person types it
// there, and it goes to one route only (the credential request's save), never anywhere else, never back into
// the page, a toast or the console. Fixtures only, no network.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SECRET = 'ana@acme.example:ATATT-super-secret-7788';
const now = new Date().toISOString();

const card = (over = {}) => ({id: 'cr1', status: 'pending', env: 'JIRA_BASIC_AUTH', bot: 'jira-manager', bot_name: 'Jira Manager',
  label: 'your Jira login', title: 'Jira Manager needs your Jira login', format: 'you@company.com:API token',
  help_url: 'https://id.atlassian.com/manage-profile/security/api-tokens', kind: 'api_key', can_save: true, note: null,
  credential_id: null, ...over});

async function open(browser, viewport, view) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const seen = {errors: [], console: [], requests: [], toasts: [], view: {...view}, saves: 0, cancels: 0, rejectFormat: false};
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('console', m => seen.console.push(m.text()));
  const messages = [
    {id: 'm1', sender_id: 'human:ana', recipient_id: 'bot:botops', kind: 'say', body: 'Make me a Jira bot', created_at: now, refs: {}},
    {id: 'm2', sender_id: 'bot:botops', recipient_id: 'human:ana', kind: 'say', body: 'Jira Manager needs your Jira login', created_at: now, refs: {credential_request: 'cr1'}}];
  const snapshot = {messages, execution: null};
  await page.context().route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    seen.requests.push({method: req.method(), url: req.url(), body: req.postData() || ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: 'event: snapshot\ndata: ' + JSON.stringify(snapshot) + '\n\n'});
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-botops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:botops']}] : []});
    if (p.startsWith('/api/v2/conversations/c-') && p.endsWith('/snapshot')) return json(snapshot);
    if (p === '/api/v2/credential-requests/cr1' && req.method() === 'GET') return json(seen.view);
    if (p === '/api/v2/credential-requests/cr1/save') {
      seen.saves++;
      if (seen.rejectFormat) return json({error: {code: 'format', detail: 'This should look like you@company.com:API token'}}, 422);
      seen.view = {...seen.view, status: 'saved', credential_id: 'c1'};
      return json(seen.view);
    }
    if (p === '/api/v2/credential-requests/cr1/cancel') { seen.cancels++; seen.view = {...seen.view, status: 'cancelled'}; return json(seen.view); }
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/botops/chat');
  return {page, seen};
}

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless: true})
    : await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true});
  try {
    for (const [tag, viewport] of [['desktop', {width: 1200, height: 800}], ['phone', {width: 390, height: 800}]]) {
      const {page, seen} = await open(browser, viewport, card());
      const input = page.locator('[data-credential-host] input[type=password]');
      await input.waitFor();
      assert.equal(await page.locator('[data-credential-host] b').innerText(), 'Jira Manager needs your Jira login', tag + ': the one-line title');
      assert.equal(await input.getAttribute('placeholder'), 'you@company.com:API token', tag + ': the format is the placeholder');
      assert.equal(await input.getAttribute('autocomplete'), 'off');
      const help = page.locator('[data-credential-host] a.cc-help');
      assert.equal(await help.innerText(), 'Get one');
      assert.equal(await help.getAttribute('target'), '_blank');
      assert.match(await help.getAttribute('rel'), /noopener/);
      assert.match(await page.locator('[data-credential-host]').innerText(), /Goes straight to Credentials/);
      if (tag === 'phone') {
        const box = await page.locator('[data-credential-host] .cc-card').boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, 'phone: the card fits the screen');
        for (const b of await page.locator('[data-credential-host] button').all()) assert.ok((await b.boundingBox()).height >= 44, 'phone: 44px tap targets');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'phone: no sideways scroll');
      }

      // The format check runs before anything is sent, and never echoes the value.
      await input.fill('no-colon-token-value');
      await page.locator('[data-credential-host] button[type=submit]').click();
      assert.match(await page.locator('[data-cc-error]').innerText(), /format/i);
      assert.equal(seen.saves, 0, tag + ': a value that does not fit the format is not sent');
      assert.ok(!(await page.locator('[data-cc-error]').innerText()).includes('no-colon'), 'the error never echoes the value');
      await input.fill('a b:c');
      await page.locator('[data-credential-host] button[type=submit]').click();
      assert.equal(seen.saves, 0, 'spaces are refused before sending');

      // The server can refuse the format too: its words show, the input stays.
      seen.rejectFormat = true;
      await input.fill(SECRET);
      await page.locator('[data-credential-host] button[type=submit]').click();
      await page.locator('[data-cc-error]:not([hidden])').waitFor();
      assert.match(await page.locator('[data-cc-error]').innerText(), /This should look like/);
      assert.equal(await input.inputValue(), SECRET, 'the input is kept after a refusal');
      seen.rejectFormat = false;

      await page.locator('[data-credential-host] button[type=submit]').click();
      await page.getByText('Saved. your Jira login is set for Jira Manager.').waitFor();
      assert.equal(await page.locator('[data-credential-host] input').count(), 0, tag + ': the input is gone once saved');

      // The value went to the save route, and only there; it is nowhere on the page or in the console.
      const carrying = seen.requests.filter(r => r.url.includes(SECRET) || r.body.includes(SECRET) || r.body.includes(encodeURIComponent(SECRET)));
      assert.ok(carrying.length >= 1 && carrying.every(r => r.method === 'POST' && r.url.endsWith('/api/v2/credential-requests/cr1/save')),
        tag + ': the value is sent only to the save route');
      assert.deepEqual(JSON.parse(carrying.at(-1).body), {value: SECRET});
      assert.ok(!(await page.content()).includes(SECRET), 'the value is not in the page');
      assert.ok(!seen.console.join('\n').includes(SECRET), 'the value is not in the console');
      assert.equal(await page.evaluate(s => JSON.stringify([localStorage, sessionStorage, location.href]).includes(s), SECRET), false, 'nor in storage or the URL');
      assert.deepEqual(seen.errors, []);
      await page.close();
    }

    // A card the person may not fill shows why and offers no input.
    {
      const {page, seen} = await open(browser, {width: 1200, height: 800}, card({can_save: false, note: 'Only an admin can store this. Ask one to open it.'}));
      await page.getByText('Only an admin can store this. Ask one to open it.').waitFor();
      assert.equal(await page.locator('[data-credential-host] input').count(), 0);
      assert.equal(await page.locator('[data-credential-host] button').count(), 0);
      assert.deepEqual(seen.errors, []);
      await page.close();
    }

    // Not now.
    {
      const {page, seen} = await open(browser, {width: 1200, height: 800}, card());
      await page.locator('[data-cc-cancel]').click();
      await page.getByText('Not now.').waitFor();
      assert.equal(seen.cancels, 1);
      await page.close();
    }
    console.log('credential card: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
