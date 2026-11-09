import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// A failed asset load must not look like a payment lock. The recovery card uses only the page's own markup/styles.
const {chromium, webkit}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile, bundled} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

async function check(engine: import('playwright').BrowserType) {
  const browser = await engine.launch({headless: true});
  try {
    for (const blocked of ['all', 'css', 'part']) {
      const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
      let failing = true;
      await page.route('**/*', async route => {
        const p = new URL(route.request().url()).pathname;
        const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (p.startsWith('/tico/ui/')) {
          if (failing && (blocked === 'all' || blocked === 'css' && p.endsWith('.css')
              || blocked === 'part' && (bundled ? /app\.bundle\.2\.js$/.test(p) : p.endsWith('/app/core.ts')))) return route.abort();
          const name = p.slice('/tico/ui/'.length), file = uiFile(name);
          if (fs.existsSync(file)) return route.fulfill({path: file, contentType: name.endsWith('.css') ? 'text/css'
            : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
        }
        if (p === '/api/me') return json({id: 'ana', email: 'ana@acme.example', role: 'owner', cloud: true, config: {company_name: 'Acme'}});
        if (p === '/api/humans') return json({people: [], groups: []});
        if (p === '/api/employees') return json([]);
        if (p === '/api/status') return json({cloud: true, cloud_access: null, active: [], queued: []});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/tasks');
      const card = page.locator('#asset-recovery');
      assert.equal(await card.isVisible(), true, `${engine.name()} ${blocked}: a failed load has a recovery screen`);
      assert.equal(await page.locator('#cloud-banner').isVisible(), false, 'no unconfirmed out-of-credit claim');
      assert.equal(await page.locator('#cloud-lock').isVisible(), false, 'no unconfirmed plan lock');
      const box = await card.boundingBox();
      assert.ok(box && box.width <= 390 && box.height <= 844, 'the recovery fits a phone');
      assert.ok((await card.evaluate(el => getComputedStyle(el).fontFamily)).includes('system-ui'), 'styled without downloaded CSS or fonts');
      assert.equal(await card.getByRole('link', {name: 'Contact support'}).getAttribute('href'), 'https://app.tico.dev/support');
      if (process.env.TICO_SHOTS) await page.screenshot({path: path.join(process.env.TICO_SHOTS, `recovery-${engine.name()}-${blocked}.png`)});
      // The ordinary link works even when no script loaded. A recovered load removes the recovery screen.
      failing = false;
      await card.getByRole('link', {name: 'Reload Tico'}).click();
      await card.waitFor({state: 'hidden'});
      assert.equal(await page.locator('#cloud-lock').isVisible(), false);
      await page.close();
    }
    // A third-party script that fails (Cloudflare's beacon, blocked by script-src 'self') is not Tico failing.
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    const beacon = '<script defer src="https://static.cloudflareinsights.com/beacon.min.js/v1"></script>';
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname !== 'tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html.replace('</body>', beacon + '</body>')});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({path: file, contentType: name.endsWith('.css') ? 'text/css'
          : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/me') return json({id: 'ana', email: 'ana@acme.example', role: 'owner', cloud: true, config: {company_name: 'Acme'}});
      if (p === '/api/humans') return json({people: [], groups: []});
      if (p === '/api/employees') return json([]);
      if (p === '/api/status') return json({cloud: true, cloud_access: null, active: [], queued: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/tasks');
    await page.waitForLoadState('load');
    await page.locator('#asset-recovery').waitFor({state: 'hidden'});
    await page.close();
    console.log(`${engine.name()} asset recovery: ok`);
  } finally { await browser.close(); }
}
(async () => { await check(chromium); await check(webkit); })().catch(error => { console.error(error); process.exit(1); });
