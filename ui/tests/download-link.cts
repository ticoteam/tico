import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// The account menu offers a download only for the company's own app (#306, Chris Nov 13). A generic build, which
// asks for a server address a team member does not know, gets no menu row and no top nudge; it stays on the GitHub
// release page and /download/<os>. A company build keeps "Download Tico for <team>" and the nudge. Fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

const DMG = 'https://github.com/ticoteam/tico/releases/download/v0.3.46/Tico.dmg';

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const open = async (appKind: 'generic' | 'company') => {
      const context = await browser.newContext({serviceWorkers: 'block', viewport: {width: 1240, height: 820}});
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', (e: Error) => errors.push(e.message));
      let downloads = 0;
      await page.addInitScript(() => {
        window.EventSource = undefined as unknown as typeof EventSource;
        Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
      });
      await page.route('**/*', async route => {
        const url = new URL(route.request().url()), p = url.pathname;
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (p.startsWith('/tico/ui/')) {
          const name = p.slice('/tico/ui/'.length), file = uiFile(name);
          if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
            : name.endsWith('.css') ? 'text/css' : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
        }
        if (p === '/api/me') return json({id: 'ana', email: 'ana@acme.example', role: 'member', cloud: true, config: {company_name: 'Acme', version: '0.3.46', beta: [{id: 'mac_app', label: 'Mac app'}]}});
        if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: []});
        if (p === '/api/download/mac') {
          downloads++;
          return json({available: true, version: '0.3.46', size_mb: 120, app_kind: appKind, notarized: false, url: DMG});
        }
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/home', {waitUntil: 'domcontentloaded'});
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent?.includes('Signing in'));
      // Wait for the download lookup to come back, then for the page to settle, before reading the menu.
      const deadline = Date.now() + 10_000;
      while (!downloads && Date.now() < deadline) await page.waitForTimeout(50);
      assert.ok(downloads > 0, 'the page asked /api/download/mac');
      await page.waitForLoadState('networkidle');
      await page.locator('#account').click();
      await page.locator('#account-menu').waitFor({state: 'visible'});
      return {context, page, errors};
    };

    // AC-1 and AC-3: a generic build shows no menu row, no generic wording and no nudge.
    {
      const {context, page, errors} = await open('generic');
      assert.equal(await page.locator('#download-app').isHidden(), true, 'no download row for a generic build');
      assert.equal(await page.locator('#download-app').getAttribute('href'), null, 'the hidden row links nowhere');
      const menu = (await page.locator('#account-menu').innerText());
      assert.doesNotMatch(menu, /Generic|manual setup/i, 'no menu text offers the generic app');
      assert.equal(await page.locator('#app-nudge').isHidden(), true, 'no top nudge for a generic build');
      assert.deepEqual(errors, []);
      await context.close();
    }

    // AC-2 and AC-3: the company's own app keeps its row, Beta pill on Mac, link and nudge.
    {
      const {context, page, errors} = await open('company');
      const row = page.locator('#download-app');
      await row.waitFor({state: 'visible'});
      assert.match((await row.innerText()), /Download Tico for Acme/);
      assert.equal(await row.locator('.nav-icon').textContent(), 'download');
      assert.equal(await row.getAttribute('href'), DMG);
      assert.doesNotMatch((await row.getAttribute('title'))!, /manual server setup/i);
      assert.match((await row.getAttribute('title'))!, /for Mac \(120 MB\)\. First open: .*Open Anyway/);
      assert.equal(await row.locator('.pill.beta').textContent(), 'Beta', 'a Beta pill follows on Mac');
      await page.locator('#app-nudge').waitFor({state: 'visible'});
      assert.equal(await page.locator('#app-nudge .app-nudge-get').getAttribute('href'), DMG);
      assert.equal(await page.locator('#app-nudge .app-nudge-get').textContent(), 'Download Tico for Acme');
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('download link: company app only, never the generic manual-setup build');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
