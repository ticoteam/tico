import type {Page, Route} from 'playwright';
// The Learnings page (ui/app/learnings.ts). No nights yet: one line on what the page is for, its little picture and,
// by Learn's switch in Maintenance, one way on: Turn on Learn for whoever may change it, Open Maintenance for anyone
// else (and when Maintenance can't be read), no button while Learn is on. A night: each section's name column is as
// wide as its longest name (80-280px), so no name is cut beside free space and the second column starts at one x;
// a longer name ends in "…" with the full name on hover. A narrow pane (an 800px window, a phone) stacks the line
// under the name with the chevron on the name's line. Fixtures only.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Maint = {status: number; can_change: boolean; learn: boolean};
type Box = {x: number; y: number; right: number; bottom: number};
const LONG = 'Northwind Traders International Wholesale Holdings Group';
const subject = (id: string, name: string, title: string, entries = 1) => ({id, name, saw: '', delivery_status: '',
  entries: Array.from({length: entries}, (_, i) => ({title: i ? `${title} (${i})` : title, sources: [], change: {where: name}}))});
const NIGHT = {id: 'run1', night: '2026-11-12', status: 'done', sections: {
  bots: [subject('support', 'Technical Support Engineer', 'Refunds over $50 need a lead', 2), subject('ops', 'Ops', 'Invoices close on Friday')],
  docs: [subject('ana', 'Ana', 'Onboarding checklist gained a step')],
  market: [subject('nw', LONG, 'Raised prices 4%')],
}};

async function open(browser: import('playwright').Browser, {width, maint, nights}: {width: number; maint: Maint; nights: boolean}):
  Promise<{page: Page; errors: string[]}> {
  const phone = width < 500;
  const page = await browser.newPage({viewport: {width, height: 860}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) {
      const name = p.slice('/tico/ui/'.length), file = uiFile(name);
      if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
        : name.endsWith('.css') ? 'text/css' : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
    }
    if (p === '/api/employees') return json([{name: 'support', display_name: 'Technical Support Engineer', status: 'active', state: 'active', schedules: []},
      {name: 'ops', display_name: 'Ops', status: 'active', state: 'active', schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: maint.can_change ? 'owner' : 'member', cloud: true, config: {company_name: 'Acme'}});
    if (p === '/api/v2/learnings') return json({nights: nights ? [{id: 'run1', night: '2026-11-12', status: 'done', updates: 5}] : []});
    if (p === '/api/v2/learnings/run1') return json(NIGHT);
    if (p === '/api/v2/maintenance') return maint.status === 200
      ? json({settings: {}, steps: [{key: 'learn', name: 'Learn', enabled: maint.learn}], can_change: maint.can_change})
      : json({error: 'boom'}, maint.status);
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/learnings');
  return {page, errors};
}

// Where each row's avatar, name, line and chevron sit, per section.
const rows = (page: Page) => page.evaluate(() => [...document.querySelectorAll('.lrn-grp')].map(g => [...g.querySelectorAll('.lrn-subj summary')].map(s => {
  const box = (sel: string): Box => { const r = s.querySelector(sel)!.getBoundingClientRect(); return {x: r.x, y: r.y, right: r.right, bottom: r.bottom}; };
  const nm = s.querySelector<HTMLElement>('.nm')!;
  return {name: nm.textContent || '', title: nm.title, cut: nm.scrollWidth > nm.clientWidth, nm: box('.nm'), first: box('.first'), more: box('.more .nav-icon'),
    ellipsis: getComputedStyle(nm).textOverflow};
})));

(async () => {
  const browser = await chromium.launch();
  try {
    // No nights yet: the line, the picture, and the one way on.
    const cases: [string, Maint, string, string][] = [
      ['owner, Learn off', {status: 200, can_change: true, learn: false}, 'What your bots learned each night, and from where.', 'primary:Turn on Learn'],
      ['member, Learn off', {status: 200, can_change: false, learn: false}, 'What your bots learned each night, and from where.', 'ghost:Open Maintenance'],
      ['Maintenance unread', {status: 500, can_change: true, learn: false}, 'What your bots learned each night, and from where.', 'ghost:Open Maintenance'],
      ['Learn on', {status: 200, can_change: true, learn: true}, 'Learn is on. The first night shows here tomorrow.', ''],
    ];
    for (const [tag, maint, line, action] of cases) {
      const {page, errors} = await open(browser, {width: 1440, maint, nights: false});
      const empty = page.locator('#lrn-night .empty-state');
      await empty.waitFor();
      assert.equal((await empty.locator('p').innerText()).trim(), line, tag + ': what the page is for');
      assert.equal(await empty.locator('svg.ea-art').count(), 1, tag + ': its little picture');
      const art = (await empty.locator('svg.ea-art').boundingBox())!, box = (await empty.boundingBox())!;
      assert.deepEqual([Math.round(art.width), Math.round(art.height)], [128, 80], tag + ': 128 by 80');
      assert.ok(Math.abs(art.x + art.width / 2 - (box.x + box.width / 2)) < 2, tag + ': centred');
      const links = await empty.locator('a').evaluateAll(els => els.map(a => `${a.className}:${a.textContent}|${a.getAttribute('href')}`));
      assert.deepEqual(links, action ? [`${action}|#/automations/maintenance`] : [], tag + ': one way on, or none while Learn is on');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A night at 1440px: names in full, one x for the second column per section, a short section narrower than 150px,
    // and a name past the cap cut with "…" and the full name on hover.
    {
      const tag = 'night 1440';
      const {page, errors} = await open(browser, {width: 1440, maint: {status: 200, can_change: true, learn: true}, nights: true});
      await page.locator('.lrn-subj').first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(50);
      const [bots, docs, market] = await rows(page);
      assert.equal(bots![0]!.name, 'Technical Support Engineer');
      assert.equal(bots!.some(r => r.cut), false, tag + ': no bot name is cut');
      assert.equal(new Set(bots!.map(r => Math.round(r.first.x))).size, 1, tag + ': the second column starts at one x in a section');
      assert.ok(docs![0]!.first.x - docs![0]!.nm.x < 150, tag + ': a section of short names keeps a narrow column');
      assert.equal(market![0]!.cut, true, tag + ': a name past 280px is cut');
      assert.equal(market![0]!.ellipsis, 'ellipsis', tag + ': with "…"');
      assert.equal(market![0]!.title, LONG, tag + ': and the full name on hover');
      assert.ok(bots!.every(r => r.title === r.name), tag + ': every name carries its full name');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A narrow pane (an 800px window, a phone): the name has the row, the line wraps under it, the chevron on the name's line.
    for (const width of [800, 400]) {
      const tag = `night ${width}`;
      const {page, errors} = await open(browser, {width, maint: {status: 200, can_change: true, learn: true}, nights: true});
      await page.locator('.lrn-subj').first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      const [bots] = await rows(page);
      const r = bots![0]!;
      assert.equal(r.cut, false, tag + ': the name is not cut');
      assert.ok(r.first.y >= r.nm.bottom - 1, tag + ': the line wraps under the name');
      assert.ok(r.more.y < r.nm.bottom && r.more.bottom > r.nm.y, tag + ': the chevron sits on the name\'s line');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, tag + ': no sideways scroll');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Opened stacked (800px), then widened: the columns are measured from each name's own width, not its wrapped one, so
    // the wide layout matches a night opened wide.
    {
      const tag = 'night 800 to 1440';
      const {page, errors} = await open(browser, {width: 800, maint: {status: 200, can_change: true, learn: true}, nights: true});
      await page.locator('.lrn-subj').first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(50);
      await page.setViewportSize({width: 1440, height: 860});
      const [bots, docs, market] = await rows(page);
      assert.equal(bots!.some(r => r.cut), false, tag + ': no bot name is cut');
      assert.equal(new Set(bots!.map(r => Math.round(r.first.x))).size, 1, tag + ': the second column starts at one x');
      assert.ok(docs![0]!.first.x - docs![0]!.nm.x < 150, tag + ': a short section stays narrow');
      assert.ok(market![0]!.first.x - market![0]!.nm.x >= 280, tag + ': a long name gets the full 280px');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    console.log('learnings-page: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
