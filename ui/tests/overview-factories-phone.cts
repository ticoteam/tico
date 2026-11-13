import type {Route} from 'playwright';
// Company › Factories at a phone width (390 × 844): the header row keeps the h1, the Day/Week/Month control and
// + Factory on screen, the Company tabs get their own full-width row of tabs (not a squeezed column, all six an owner
// sees on screen), and #main does not scroll sideways. At a desktop width the header stays one row. Fixtures only.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const FACTORIES = {factories: [{id: 'f-growth', name: 'Growth', goal: null, manager_id: null, done: 3, overdue: 0, live: 1}], pipelines: []};

(async () => {
  const browser = await chromium.launch({channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string, headless: true});
  try {
    const open = async (width: number, height: number) => {
      const page = await browser.newPage({viewport: {width, height}, serviceWorkers: 'block'});
      const errors: string[] = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async (route: Route) => {
        const url = new URL(route.request().url()), p = url.pathname;
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (p.startsWith('/tico/ui/')) {
          const name = p.slice('/tico/ui/'.length), file = uiFile(name);
          if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
            : name.endsWith('.css') ? 'text/css' : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
        }
        if (p === '/api/v2/factories') return json(FACTORIES);
        if (p === '/api/v2/pipelines') return json({pipelines: []});
        if (p === '/api/employees') return json([]);
        if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
        if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, config: {company_name: 'Acme Studio'}});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/');
      await page.waitForFunction(() => location.hash === '#/home');
      await page.evaluate(() => location.hash = '#/overview/factories');
      await page.locator('#of-new-b').waitFor();
      await page.waitForFunction(() => !document.querySelector(':root:active-view-transition'));
      return {page, errors};
    };
    const geometry = (page: import('playwright').Page) => page.evaluate(() => {
      const box = (sel: string) => { const r = document.querySelector(sel)!.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height}; };
      const main = document.querySelector('#main')!;
      const tabs = [...document.querySelectorAll('.of-page .overview-tabs .dash-tabs a, .of-page .overview-tabs .dash-tabs button')]
        .map(t => Math.round(t.getBoundingClientRect().top));
      return {vw: innerWidth, scroll: main.scrollWidth, client: main.clientWidth, add: box('#of-new-b'), period: box('#of-period'),
        h1: box('.of-page .overview-tabs h1'), navScroll: document.querySelector('.of-page .dash-tabs')!.scrollWidth, navClient: document.querySelector('.of-page .dash-tabs')!.clientWidth,
        lastTab: document.querySelector('.of-page .dash-tabs')!.lastElementChild!.getBoundingClientRect().right, nav: box('.of-page .overview-tabs .dash-tabs'), tabTops: [...new Set(tabs)], count: tabs.length};
    });

    // Phone: nothing past the right edge, + Factory and the period control fully on screen, the tabs one row.
    {
      const {page, errors} = await open(390, 844);
      const g = await geometry(page);
      assert.ok(g.scroll <= g.client, `#main scrolls sideways at 390 px: scrollWidth ${g.scroll} > clientWidth ${g.client}`);
      assert.ok(g.add.left >= 0 && g.add.right <= g.vw, `+ Factory off screen: ${g.add.left}–${g.add.right} of ${g.vw}`);
      assert.ok(g.period.left >= 0 && g.period.right <= g.vw, `Day/Week/Month off screen: ${g.period.left}–${g.period.right} of ${g.vw}`);
      assert.ok(g.count > 1, 'the Company tabs are drawn');
      assert.equal(g.tabTops.length, 1, `the Company tabs stack instead of one row: tops ${g.tabTops.join(', ')}`);
      assert.ok(g.nav.top >= g.add.bottom - 1, 'the tabs sit on their own row below the header controls');
      assert.ok(g.nav.right <= g.vw, 'the tab row fits the screen');
      // An owner sees six tabs, Share last: every one on screen, none hidden past the row's own scroll.
      assert.equal(g.count, 6, 'an owner sees six Company tabs');
      assert.ok(g.navScroll <= g.navClient, `tabs past the row's edge: scrollWidth ${g.navScroll} > clientWidth ${g.navClient}`);
      assert.ok(g.lastTab <= g.vw, `the last tab ends at ${g.lastTab}, past ${g.vw}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Desktop: one header row, as before.
    {
      const {page, errors} = await open(1280, 860);
      const g = await geometry(page);
      assert.ok(g.scroll <= g.client, 'no sideways scroll on desktop');
      assert.equal(g.tabTops.length, 1, 'tabs one row on desktop');
      const mid = (b: {top: number; bottom: number}) => (b.top + b.bottom) / 2;
      for (const b of [g.nav, g.period, g.add]) assert.ok(Math.abs(mid(b) - mid(g.h1)) < 4, 'desktop header is a single row');
      assert.ok(g.h1.right <= g.nav.left && g.nav.right <= g.period.left && g.period.right <= g.add.left, 'desktop order: h1, tabs, period, + Factory');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('overview-factories-phone: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
