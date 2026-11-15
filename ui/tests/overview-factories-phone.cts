import type {Route} from 'playwright';
// Company › Factories in the shared page header, at a phone width (390 × 844) and a desktop one: the title line keeps
// Company, the Day/Week/Month segment, the quiet + and ⋯ on screen; the Company tabs get their own full-width row (five,
// Share in ⋯); #main does not scroll sideways. At a desktop width the header stays one row. Fixtures only.
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
      const box = (el: Element) => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height}; };
      const main = document.querySelector('#main')!, head = document.querySelector('.of-page > header.ph')!;
      const shown = [...head.querySelectorAll<HTMLElement>(':scope > .ph-tabs > .ph-tab')].filter(t => t.checkVisibility());
      const nav = head.querySelector(':scope > .ph-tabs')!;
      return {vw: innerWidth, scroll: main.scrollWidth, client: main.clientWidth, add: box(document.querySelector('#of-new-b')!), period: box(document.querySelector('#of-period')!),
        h1: box(head.querySelector('h1')!), more: box(head.querySelector(':scope > .ph-more')!), nav: box(nav), navScroll: nav.scrollWidth, navClient: nav.clientWidth,
        tabTops: [...new Set(shown.map(t => Math.round(t.getBoundingClientRect().top)))], tabs: shown.map(t => t.textContent),
        held: [...head.querySelectorAll('.ph-menu .ph-mtab')].map(t => t.textContent),
        last: head.querySelector(':scope > .ph-menu')!.previousElementSibling!.className,
        small: [...head.querySelectorAll<HTMLElement>('button, a')].filter(el => el.checkVisibility() && el.getBoundingClientRect().height < 44).map(el => el.textContent || el.getAttribute('aria-label'))};
    });

    // Phone: the shared header (ui/app/page-head.ts). The title line holds Company, Day/Week/Month, the quiet + and ⋯;
    // the Company tabs take one full-width row under it, five of them, Share waiting in ⋯. Nothing past the right edge,
    // and every control a 44px target.
    {
      const {page, errors} = await open(390, 844);
      const g = await geometry(page);
      assert.ok(g.scroll <= g.client, `#main scrolls sideways at 390 px: scrollWidth ${g.scroll} > clientWidth ${g.client}`);
      assert.ok(g.add.left >= 0 && g.add.right <= g.vw, `+ off screen: ${g.add.left}–${g.add.right} of ${g.vw}`);
      assert.ok(g.period.left >= 0 && g.period.right <= g.vw, `Day/Week/Month off screen: ${g.period.left}–${g.period.right} of ${g.vw}`);
      const mid = (b: {top: number; bottom: number}) => (b.top + b.bottom) / 2;
      for (const b of [g.period, g.add, g.more]) assert.ok(Math.abs(mid(b) - mid(g.h1)) < 4, 'the title line holds the period, + and ⋯');
      assert.equal(g.tabTops.length, 1, `the Company tabs stack instead of one row: tops ${g.tabTops.join(', ')}`);
      assert.ok(g.nav.top >= g.add.bottom - 1, 'the tabs sit on their own row below the header controls');
      assert.ok(g.nav.right <= g.vw && g.navScroll <= g.navClient, 'the tab row fits the screen');
      assert.deepEqual(g.tabs, ['Mine', 'Dashboard', 'Campus', 'Graph', 'Factories'], 'five tabs on the row');
      assert.deepEqual(g.held, ['Share'], 'Share, the sixth, is in ⋯');
      assert.equal(g.last, 'ph-more', '⋯ is the last control');
      assert.deepEqual(g.small, [], 'every header control is 44px on a phone');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Desktop: one header row, in the guide's order: Company, tabs, the period, the quiet +, then ⋯.
    {
      const {page, errors} = await open(1280, 860);
      const g = await geometry(page);
      assert.ok(g.scroll <= g.client, 'no sideways scroll on desktop');
      assert.equal(g.tabTops.length, 1, 'tabs one row on desktop');
      const mid = (b: {top: number; bottom: number}) => (b.top + b.bottom) / 2;
      for (const b of [g.nav, g.period, g.add, g.more]) assert.ok(Math.abs(mid(b) - mid(g.h1)) < 4, 'desktop header is a single row');
      assert.ok(g.h1.right <= g.nav.left && g.nav.right <= g.period.left && g.period.right <= g.add.left && g.add.right <= g.more.left, 'desktop order: h1, tabs, period, +, ⋯');
      // Making a factory is rare: a quiet + named for screen readers, not a word button; it opens the form.
      const add = page.locator('#of-new-b');
      assert.equal(await add.getAttribute('aria-label'), 'New factory');
      assert.equal((await add.innerText()).trim(), 'add', 'an icon, no words');
      await add.click();
      assert.equal(await add.getAttribute('aria-expanded'), 'true');
      await page.locator('#of-new [name=name]').waitFor();
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('name')), 'name');
      await page.locator('[data-of-new-cancel]').click();
      assert.equal(await page.locator('#of-new').isHidden(), true);
      // The period is a segment: Day picks the day.
      await page.locator('[data-of-period="1d"]').click();
      assert.equal(await page.locator('[data-of-period="1d"]').getAttribute('aria-pressed'), 'true');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('overview-factories-phone: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
