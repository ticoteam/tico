import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// The account menu offers a download only for the company's own app (#306, Chris Nov 13). A generic build, which
// asks for a server address a team member does not know, gets no menu row and no top nudge; it stays on the GitHub
// release page and /download/<os>. A company build gets a short row, "Download Mac app" (Windows, Linux), and the nudge.
// #405: the row and its Beta pill are whole on screen at 1280 px; the open menu draws past the sidebar's clip. Fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

const DMG = 'https://github.com/ticoteam/tico/releases/download/v0.3.46/Tico.dmg';

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const PLATFORM = {mac: 'MacIntel', windows: 'Win32', linux: 'Linux x86_64'} as const;
    const open = async (appKind: 'generic' | 'company', os: keyof typeof PLATFORM = 'mac') => {
      const context = await browser.newContext({serviceWorkers: 'block', viewport: {width: 1280, height: 820}});
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', (e: Error) => errors.push(e.message));
      let downloads = 0;
      await page.addInitScript((platform: string) => {
        window.EventSource = undefined as unknown as typeof EventSource;
        Object.defineProperty(navigator, 'platform', {get: () => platform});
      }, PLATFORM[os]);
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
        if (p === `/api/download/${os}`) {
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
      assert.ok(downloads > 0, `the page asked /api/download/${os}`);
      await page.waitForLoadState('networkidle');
      // Closed, the sidebar clips as it always has (#405 AC-5).
      assert.equal(await page.locator('#side').evaluate(e => getComputedStyle(e).overflow), 'hidden', 'the closed sidebar still clips');
      await page.locator('#account').click();
      await page.locator('#account-menu').waitFor({state: 'visible'});
      // Measure only once the open animation has ended, never after a fixed pause.
      await page.locator('#account-menu').evaluate(e => Promise.all(e.getAnimations({subtree: true}).map(a => a.finished)));
      return {context, page, errors};
    };
    // What a person sees: the topmost element at a point is the given element or inside it. A box check alone would
    // pass while #side's overflow hides the end of the menu.
    const seenAt = (page: import('playwright').Page, selector: string, at: 'centre' | 'right') =>
      page.locator(selector).evaluate((e, where) => {
        const r = e.getBoundingClientRect();
        const x = where === 'centre' ? r.left + r.width / 2 : r.right - 2, y = r.top + r.height / 2;
        const top = document.elementFromPoint(x, y);
        return !!top && e.contains(top);
      }, at);

    // #294 AC-1 and AC-3 (#405 AC-6): a generic build shows no menu row, no generic wording and no nudge.
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

    // #405 AC-1, AC-3, AC-4: on Mac the company's own app shows "Download Mac app" and its Beta pill, whole, and the
    // nudge's button uses the same words while its sentence names the team.
    {
      const {context, page, errors} = await open('company');
      const row = page.locator('#download-app');
      await row.waitFor({state: 'visible'});
      await page.locator('#account-menu').evaluate(e => Promise.all(e.getAnimations({subtree: true}).map(a => a.finished)));
      assert.equal((await row.innerText()).replace(/\s+/g, ' ').trim(), 'download Download Mac app Beta');
      assert.doesNotMatch(await row.innerText(), /Tico for/);
      assert.equal(await row.locator('.nav-icon').textContent(), 'download');
      assert.equal(await row.getAttribute('href'), DMG);
      assert.doesNotMatch((await row.getAttribute('title'))!, /manual server setup/i);
      assert.match((await row.getAttribute('title'))!, /for Mac \(120 MB\)\. First open: .*Open Anyway/);
      assert.equal(await row.locator('.pill.beta').textContent(), 'Beta', 'a Beta pill follows on Mac');
      assert.ok(await seenAt(page, '#download-app .pill.beta', 'centre'), 'the Beta pill is seen, not hidden by the sidebar');
      assert.ok(await seenAt(page, '#download-app .pill.beta', 'right'), "the Beta pill's right end is seen");
      assert.ok(await seenAt(page, '#account-menu', 'right'), "the menu's right edge is seen");
      await page.locator('#app-nudge').waitFor({state: 'visible'});
      assert.equal(await page.locator('#app-nudge .app-nudge-get').getAttribute('href'), DMG);
      assert.equal(await page.locator('#app-nudge .app-nudge-get').textContent(), 'Download Mac app');
      assert.match((await page.locator('#app-nudge .app-nudge-text').textContent())!, /Tico for Acme/, 'the nudge sentence still names the team');
      // Closing the menu brings the sidebar's clip back.
      await page.keyboard.press('Escape');
      if (await page.locator('#account-menu').isVisible()) await page.locator('#account').click();
      await page.locator('#account-menu').waitFor({state: 'hidden'});
      assert.equal(await page.locator('#side').evaluate(e => getComputedStyle(e).overflow), 'hidden', 'the sidebar clips again once the menu closes');
      assert.deepEqual(errors, []);
      await context.close();
    }

    // #405 AC-2: Windows and Linux get their own words and no Beta pill, whole on screen.
    for (const [os, words] of [['windows', 'Windows'], ['linux', 'Linux']] as const) {
      const {context, page, errors} = await open('company', os);
      const row = page.locator('#download-app');
      await row.waitFor({state: 'visible'});
      await page.locator('#account-menu').evaluate(e => Promise.all(e.getAnimations({subtree: true}).map(a => a.finished)));
      assert.equal((await row.innerText()).replace(/\s+/g, ' ').trim(), `download Download ${words} app`);
      assert.equal(await row.locator('.pill').count(), 0, `no pill on ${words}`);
      assert.ok(await seenAt(page, '#download-app', 'right'), `the ${words} row's right end is seen`);
      assert.equal(await page.locator('#app-nudge .app-nudge-get').textContent(), `Download ${words} app`);
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('download link: company app only, short per-OS words, whole on screen; never the generic build');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
