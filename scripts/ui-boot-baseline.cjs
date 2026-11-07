// The boot baseline for scripts/ui_test_map.py: loads the app once and does nothing else. The measuring workflow
// (.github/workflows/ui-test-map.yml) runs it under scripts/ui-coverage.cjs, which writes what that load ran as boot.json.
// A browser script's calls to a function are compared with this load's, so a function every page runs at boot is not
// taken for the mark of a script that exercises its file.
'use strict';

const fs = require('node:fs');
const {chromium} = require('playwright');
const {html, uiFile} = require('../ui/tests/support/page.cjs');
const {unrouted} = require('../ui/tests/support/routes.cjs');

// The owner the browser scripts sign in as (chat.cjs answers /api/me the same way).
const ME = {id: 'ana', name: 'Ana', role: 'owner', cloud: true};

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    await page.route('http://tico-ui.test/**', route => {
      const {pathname} = new URL(route.request().url());
      if (pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      const asset = /^\/tico\/ui\/(.+)$/.exec(pathname);
      if (asset) {
        const file = uiFile(asset[1]);
        if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
        const type = asset[1].endsWith('.css') ? 'text/css' : 'application/javascript';
        return route.fulfill({contentType: type, body: fs.readFileSync(file, 'utf8')});
      }
      if (pathname.startsWith('/api/')) {
        const body = pathname === '/api/me' ? ME : unrouted(pathname);
        return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      }
      return route.abort();
    });
    await page.goto('http://tico-ui.test/');
    await page.waitForLoadState('load');
    await page.waitForTimeout(2000);
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
