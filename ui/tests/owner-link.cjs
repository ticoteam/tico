// The owner sign-in link of a server Tico hosts (TICO_AUTH_PROXY=owner-link), in a browser against a real server
// (ui/tests/support/owner_link_server.py), with the app's real ui/app/session.js:
//  - the emailed link /auth/owner-link#<secret>: opening it spends nothing, the secret leaves the address bar and is
//    never in a request's address, the button signs in and lands in the app;
//  - a session that lapses: the banner's Sign in again goes to the link page with ?next=, which shows the page that
//    says to open the link (not "This sign-in link is not valid"), and keeps where the app was;
//  - the first 401 of a page that has loaded nothing yet: straight to the link page, ?next= and all, the same.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const path = require('node:path');
const readline = require('node:readline');
const {spawn, execFileSync} = require('node:child_process');
const {html, uiFile} = require('./support/page.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const ROOT = path.join(__dirname, '../..');
const PYTHON = process.env.TICO_PYTHON || 'python3';
const NOT_VALID = 'This sign-in link is not valid';
const LANDING = 'Open the sign-in link Tico sent you';

function server() {
  const child = spawn(PYTHON, ['ui/tests/support/owner_link_server.py'], {cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe']});
  let err = ''; child.stderr.on('data', d => { err += d; });
  return new Promise((resolve, reject) => {
    child.on('exit', code => reject(new Error('owner_link_server.py exited ' + code + ': ' + err.slice(-1500))));
    const timer = setTimeout(() => reject(new Error('server did not start: ' + err.slice(-1500))), t(60000));
    readline.createInterface({input: child.stdout}).on('line', line => {
      try { const v = JSON.parse(line); clearTimeout(timer); resolve({child, ...v}); } catch {}
    });
  });
}

// The browser is on the hosted https address; every request to it goes to the real server on this computer, except
// that with `page` the app's own page and files come from disk, as the other browser tests serve them.
// Playwright does not route the target of a navigation's redirect, so a redirect is handed on as a page that goes
// there (with the response's cookies), and kept in `seen.redirects` for the asserts.
async function hosted(context, tico, seen, {page: own} = {}) {
  seen.redirects = [];
  await context.route(tico.public + '/**', async route => {
    const req = route.request(), url = new URL(req.url());
    seen.push(url.pathname + url.search);
    if (own && url.pathname === '/') return route.fulfill({contentType: 'text/html', body: own});
    if (own && url.pathname.startsWith('/tico/ui/')) return route.fulfill({path: uiFile(url.pathname.slice(9))});
    const res = await route.fetch({url: tico.url + url.pathname + url.search, maxRedirects: 0});
    const headers = res.headers();
    if (headers.location) headers.location = headers.location.replace(tico.url, tico.public);
    if (res.status() >= 300 && res.status() < 400 && req.isNavigationRequest()) {
      const to = headers.location;
      seen.redirects.push({from: req.method() + ' ' + url.pathname, status: res.status(), to});
      delete headers.location; delete headers['content-length'];
      return route.fulfill({status: 200, headers: {...headers, 'content-type': 'text/html'},
                            body: `<meta http-equiv="refresh" content="0;url=${to}">`});
    }
    return route.fulfill({response: res, headers});
  });
}

(async () => {
  const tico = await server();
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // The emailed link, then a session that lapses.
    {
      const context = await browser.newContext({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
      const seen = [];
      await hosted(context, tico, seen);
      const page = await context.newPage();
      await page.goto(tico.public + '/auth/owner-link#' + tico.token);
      await page.waitForSelector('#owner-link-button:not([disabled])');
      assert.equal(new URL(page.url()).hash, '', 'the secret leaves the address bar');
      await page.click('#owner-link-button');
      await page.waitForURL(tico.public + '/');
      assert.deepEqual(seen.redirects, [{from: 'POST /auth/owner-link', status: 303, to: '/'}]);
      const me = await page.evaluate(async () => (await fetch('/api/v2/me')).json());
      assert.equal(me.email, 'ana@acme.example');
      assert.ok(seen.every(u => !u.includes(tico.token)), 'the secret is in no request address');
      console.log('the emailed link: the page spends nothing, the button signs in, the secret is never sent in an address');

      await page.waitForFunction(() => typeof SESSION === 'object' && SESSION.seen, null, {timeout: t(20000)});
      // Our team's command signs the owner out everywhere, as `python3 -m setup owner-link --sign-out` does.
      execFileSync(PYTHON, ['-m', 'backend.owner_link', 'replace', tico.db, 'f'.repeat(64), '--sign-out'],
                   {cwd: ROOT, env: {...process.env, TICO_AUTH_PROXY: 'owner-link', TICO_REGISTRY_DIR: tico.registry}});
      await page.evaluate(() => { location.hash = '#/tasks'; });
      await page.waitForSelector('#signed-out:not([hidden])', {timeout: t(20000)});
      const landing = page.waitForResponse(r => new URL(r.url()).pathname === '/auth/owner-link');
      await page.click('#signed-out button');
      const res = await landing;
      assert.equal(res.status(), 200, 'Sign in again lands on the link page, not ' + res.status());
      assert.ok(new URL(page.url()).searchParams.get('next').startsWith('/'), 'and says where to come back to');
      const text = await page.textContent('body');
      assert.ok(text.includes(LANDING) && !text.includes(NOT_VALID), text);
      console.log('a lapsed session: Sign in again shows the page that says to open the link, with ?next= kept');
      await context.unrouteAll({behavior: 'ignoreErrors'});   // the signed-in app is still fetching
      await context.close();
    }
    // The first 401 of a page that has loaded nothing: session.js goes straight to the sign-in page.
    {
      const context = await browser.newContext({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
      const seen = [];
      await hosted(context, tico, seen, {page: html});
      const page = await context.newPage();
      const landing = page.waitForResponse(r => new URL(r.url()).pathname === '/auth/owner-link', {timeout: t(20000)});
      await page.goto(tico.public + '/?a=1#/tasks');
      const res = await landing;
      assert.equal(res.status(), 200, 'the first 401 lands on the link page, not ' + res.status());
      assert.equal(new URL(res.url()).searchParams.get('next'), '/?a=1#/tasks');
      await page.waitForLoadState();
      const text = await page.textContent('body');
      assert.ok(text.includes(LANDING) && !text.includes(NOT_VALID), text);
      assert.equal(await page.getAttribute('input[name="next"]', 'value'), '/?a=1#/tasks');
      // A link opened on that page (its secret after #) signs in and comes back to where the app was.
      const token = crypto.randomBytes(32).toString('base64url');
      execFileSync(PYTHON, ['-m', 'backend.owner_link', 'replace', tico.db, crypto.createHash('sha256').update(token).digest('hex')],
                   {cwd: ROOT, env: {...process.env, TICO_AUTH_PROXY: 'owner-link', TICO_REGISTRY_DIR: tico.registry}});
      const linkPage = page.url();
      await page.goto('about:blank');   // a new load, as opening the link is, not a same-page hash change
      await page.goto(linkPage + '#' + token);
      await page.waitForSelector('#owner-link-button:not([disabled])');
      seen.redirects = [];
      await page.click('#owner-link-button');
      await page.waitForURL(tico.public + '/?a=1#/tasks');
      assert.deepEqual(seen.redirects, [{from: 'POST /auth/owner-link', status: 303, to: '/?a=1#/tasks'}]);
      console.log('the first 401: straight to the link page, which keeps where the app was and comes back there');
      await context.unrouteAll({behavior: 'ignoreErrors'});   // the signed-in app is still fetching
      await context.close();
    }
  } finally {
    await browser.close();
    tico.child.kill();
  }
})().catch(error => { console.error(error); process.exit(1); });
