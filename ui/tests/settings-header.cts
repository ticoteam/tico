import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// The Settings header is the one page header (pageHead, ui/app/page-head.ts; style guide "Top bars: one line"): the
// title, its tabs, the search as a tool and ⋯ last, on one line on a computer; on a phone the tabs take the row under
// it. Tabs past the fifth are listed under ⋯, so none is lost: each opens its pane from there, and ⋯ closes once one is
// chosen. The theme is a row of General's App card (set once), with the version line under it. Every control is a 44px
// target on a phone and nothing scrolls sideways. Fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

async function scenario(browser: import('playwright').Browser, role: 'owner' | 'member', width: number) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message + ' @ ' + String(e.stack).split('\n').slice(1, 3).join(' ')));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.6.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({active: [], recent_runs: [], health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/settings/catalog') return json({settings: []});
    if (p === '/api/v2/billing') return json({state: 'no_gateway'});
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  const where = `${role} at ${width}`;
  try {
    await page.goto('https://tico-ui.test/#/settings/general');
    const head = page.locator('header.ph.settings-head');
    await head.locator('#settings-tabs').waitFor();
    assert.equal(await page.locator('header.ph').count(), 1, `${where}: one header`);
    assert.equal((await head.locator('.ph-t').innerText()).trim(), 'Settings');
    assert.equal(await head.locator('.ph-tools #settings-search-q').count(), 1, `${where}: the search is a header tool`);
    assert.equal(await page.locator('#settings-more, .meeting-head').count(), 0, `${where}: no second tab row, no old header`);
    // The header's last control is ⋯; the title line is one line (the tabs have their own row on a phone).
    const last = await head.evaluate(h => [...h.children].filter(c => !c.matches('[popover]') && c.getBoundingClientRect().width).at(-1)?.className);
    assert.match(String(last), /ph-more/, `${where}: ⋯ is last`);
    const line = await head.evaluate(h => {
      const boxes = [...h.children].filter(c => !c.matches('[popover], .ph-tabs, .ph-sp') && c.getBoundingClientRect().width).map(c => c.getBoundingClientRect());
      return Math.max(...boxes.map(b => b.top)) < Math.min(...boxes.map(b => b.bottom));   // every part shares one band
    });
    assert.equal(line, true, `${where}: the title line is one line`);
    if (width < 761) {
      const [titleTop, tabsTop] = await head.evaluate(h => [h.querySelector('.ph-t')!.getBoundingClientRect().bottom, h.querySelector('.ph-tabs')!.getBoundingClientRect().top]);
      assert.ok(tabsTop >= titleTop - 1, `${where}: the tabs sit on their own row under the title`);
    }
    // Five tabs on the row, the rest under ⋯; each one opens its pane from there, and ⋯ closes.
    const all = await head.locator('#settings-tabs > [data-settings-tab]').evaluateAll(b => b.map(x => (x as HTMLElement).dataset.settingsTab!));
    const shown = await head.locator('#settings-tabs > [data-settings-tab]:visible').evaluateAll(b => b.map(x => (x as HTMLElement).dataset.settingsTab!));
    assert.deepEqual(shown, all.slice(0, 5), `${where}: five on the row`);
    for (const tab of all.slice(5)) {
      await head.locator('.ph-more').click();
      const label = await head.locator(`#settings-tabs > [data-settings-tab="${tab}"]`).evaluate(x => x.textContent!.trim());
      await head.locator('.ph-menu .ph-mtab', {hasText: label}).click();
      await page.locator(`#settings-tabs > [data-settings-tab="${tab}"][aria-selected=true]`).waitFor();
      assert.equal(await page.locator(`#settings-${tab}`).evaluate(e => (e as HTMLElement).hidden), false, `${where}: ${tab} opens from ⋯`);
      assert.equal(await head.locator('.ph-menu').evaluate(m => m.matches(':popover-open')), false, `${where}: ⋯ closes once ${tab} is chosen`);
      assert.equal(await head.locator(`#settings-tabs > [data-settings-tab="${tab}"]`).isVisible(), true, `${where}: the current tab shows on the row`);
    }
    // The theme is General's: a segment in the App card, with the version under it, and it still switches the theme.
    await head.locator('#settings-tabs > [data-settings-tab="general"]').click();
    const app = page.locator('#settings-app');
    await app.locator('#settings-theme [data-theme-choice=light]').click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    assert.equal(await app.locator('#settings-theme [data-theme-choice=light]').getAttribute('aria-checked'), 'true');
    await app.locator('#settings-theme [data-theme-choice=dark]').click();
    assert.equal((await app.locator('#settings-version').innerText()).trim(), 'Version v0.6.0');
    // A phone: 44px targets in the header and the App card, and nothing scrolls sideways.
    if (width < 761) {
      const small = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('header.ph :is(button,input,a):not([popover] *), #settings-app button')]
        .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().height < 43.5).map(e => e.outerHTML.slice(0, 80)));
      assert.deepEqual(small, [], `${where}: every header and theme control is a 44px target`);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, `${where}: no sideways scroll`);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    await scenario(browser, 'owner', 1280);
    await scenario(browser, 'owner', 390);
    await scenario(browser, 'member', 390);
  } finally { await browser.close(); }
  console.log('settings-header ok');
})().catch(error => { console.error(error); process.exit(1); });
