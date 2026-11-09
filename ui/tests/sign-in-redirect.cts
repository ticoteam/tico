// A 401 that names the built-in login page sends the browser there and back to the same place.
import type * as PageSupport from './support/page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
(async () => {
  const channel = process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined;
  const browser = await chromium.launch({headless: true, ...(channel ? {channel} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    let login = null as string | null;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (p === '/auth/login') { login = url.search; return route.fulfill({contentType: 'text/html', body: '<title>login</title>'}); }
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      // The page's own scripts and styles come from disk; every other script stubs out.
      const own = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (own && fs.existsSync(uiFile(own[1] ?? '')))
        return route.fulfill({contentType: own[1]?.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(own[1] ?? ''), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p.startsWith('/api/')) return route.fulfill({status: 401, contentType: 'application/json', body: JSON.stringify({error: {code: 'identity', detail: 'Sign in', sign_in: '/auth/login'}})});
      return route.fulfill({contentType: 'application/json', body: '{}'});
    });
    await page.goto('https://tico-ui.test/?a=1#/tasks');
    await page.waitForURL('**/auth/login**');
    assert.equal(new URLSearchParams(login ?? undefined).get('next'), '/?a=1#/tasks');
    console.log('401 with sign_in goes to the login page and asks to return to the same place');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
