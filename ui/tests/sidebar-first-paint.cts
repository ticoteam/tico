// The sidebar is whole from the first paint (ui/index.html, ui/theme.ts): no row appears, goes or moves once the app's
// code runs or its reads answer. Each visit records the rail three times: painted (the page's markup and styles, its
// scripts held back), ran (the scripts run, every /api read held back) and settled (every read answered); at 1440 px and
// at 390 px with the phone drawer (More) open, with the bottom bar. Finance shows as the last answer this browser had
// for the same person: none kept is hidden, a kept "shown" stands until /v2/finance/access says no (403, or a list with
// nothing in view), a failed read changes nothing, and a sign-out or another person drops what was kept. Finish setup and
// the folds (Company, Library, Rules, Team) are kept the same way. A sign-out in one tab stops every other tab keeping anything,
// and an Ask tap made before /me never opens Ask over the setup wizard.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const uiDir = path.join(__dirname, '..');
const roster: {employees: unknown[]; humans: unknown[]} = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
const t = (ms: number) => ms * (Number(process.env.TICO_TEST_TIMEOUT_SCALE) || 1);
type Row = {key: string; on: boolean; top: number | null};
type Rail = {rows: Row[]; chevrons: string[]; foot: string[]; bar: string[]};
// What the test reads back from localStorage's kept rail (a sign-out or another person empties it; see 'kept').
type Kept = {who: string; finance?: boolean; welcome?: boolean};
// A boot read's gate: the page's request waits in the list until the test releases it.
type Gate = (value?: unknown) => void;
declare const S: {emps: {name: string; revision: number}[]; route: string; me?: unknown};

declare var FIN_PROBE: unknown;
declare var THREADS: {on: unknown};
declare var renderTree: () => void;

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const ORIGIN = 'http://tico-ui.test';
const KEPT = 'tico.nav.kept';
// GET /v2/finance/access as Finance answers it: the viewer's own row says what they see.
const access = (seen: boolean) => ({teammates: [{teammate: 'human:ana', default: seen ? 'everything' : 'none', grants: [], rights: []},
  {teammate: 'human:bea', default: 'none', grants: [], rights: []}], teams: [], private_lines: [], defaults: {}});

// The rail as drawn now: each link, fold chevron and section heading with whether it shows and where (its top within the
// scrolled rail, so the phone drawer's slide does not count), the folds' chevrons, Ask and Search at its foot, and the
// phone's bottom bar.
const rail = (page: import('playwright').Page) => page.evaluate((): Rail => {
  const side = document.querySelector('#side .side-scroll') as HTMLElement, base = side.getBoundingClientRect().top - side.scrollTop;
  const key = (el: HTMLElement) => el.dataset.nav || el.dataset.sectionToggle || el.id || el.getAttribute('aria-label') || el.className;
  const row = (el: HTMLElement) => {
    const on = el.checkVisibility(), r = el.getBoundingClientRect();
    return {key: key(el), on, top: on ? Math.round(r.top - base) : null};
  };
  return {
    rows: [...(side.querySelectorAll('a.nav-link, .rd-lib-toggle, .section-toggle') as NodeListOf<HTMLElement> & Iterable<HTMLElement>)].filter(el => !el.closest('#pin-list')).map(row),
    chevrons: [...(side.querySelectorAll('.rd-lib-toggle, .section-toggle') as NodeListOf<HTMLElement> & Iterable<HTMLElement>)].map(el => key(el) + ':' + getComputedStyle(el.querySelector('.section-chev')!).transform),
    foot: ['#ask-row', '#search-open .search-label', '#support-open'].map(s => s + ':' + document.querySelector(s)!.checkVisibility()),
    bar: [...(document.querySelectorAll('#mobile-nav .mobile-nav-item') as NodeListOf<HTMLElement> & Iterable<HTMLElement>)].filter(el => el.checkVisibility()).map(key),
  };
});
const shows = (snap: Rail, nav: string) => snap.rows.find(r => r.key === nav)?.on;
const kept = (page: import('playwright').Page): Promise<Kept> => page.evaluate((k: string): Kept => { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch { return 'unreadable' as unknown as Kept; } }, KEPT);

// One visit in `context` (its localStorage is this browser's): who /me answers as, what Finance's access read says
// ('yes', 'no', or a status to fail with), and whether the server asks for setup. `holdFinance` leaves Finance's read
// out (visit.releaseFinance lets it answer); `early` runs once the scripts are in and before any read answers.
async function visit(context: import('playwright').BrowserContext, {phone = false, who = 'ana', finance = 'no', onboarding = false, proxy = false, hash = '#/updates',
  holdFinance = false, early = null}: {phone?: boolean; who?: string; finance?: string | number; onboarding?: boolean; proxy?: boolean; hash?: string;
  holdFinance?: boolean; early?: ((page: import('playwright').Page) => Promise<void>) | null} = {}) {
  const page = await context.newPage();
  const errors: string[] = [], gates: {scripts: Gate[]; reads: Gate[]; finance: Gate[]; holdScripts: boolean; holdReads: boolean; holdFinance: boolean} = {scripts: [], reads: [], finance: [], holdScripts: true, holdReads: true, holdFinance};
  page.on('pageerror', e => errors.push(e.message));
  const config = {company_name: 'Acme', onboarding_needed: onboarding};
  const me = {id: who, name: who === 'ana' ? 'Ana Silva' : 'Bea Lopez', email: who + '@acme.example', role: who === 'ana' ? 'owner' : 'member',
    company_role: who === 'ana' ? 'owner' : 'member', cloud: true, proxy_session: proxy, config};
  await page.route('**/*', async r => {
    const req = r.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== ORIGIN) return r.abort();
    if (p === '/') return r.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/v2/logout') return r.fulfill({contentType: 'text/html', body: '<!doctype html><title>Signed out</title>'});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) {
      // The page's scripts wait (theme.ts in the head runs, as it does before the first paint), so the rail is
      // recorded as its markup and styles draw it.
      if (gates.holdScripts && /\.(?:js|ts)$/.test(p) && !p.endsWith('/theme.ts')) await new Promise(go => gates.scripts.push(go));
      return r.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    }
    if (p === '/api/v2/events') return r.abort();
    if (p.startsWith('/api/') && gates.holdReads) await new Promise(go => gates.reads.push(go));
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, version: 'v0'});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [], catch_up: 0, catch_up_ids: [], next_cursor: null, dms: {}});
    if (p === '/api/v2/threads') return json({attention: [], threads: [], dms: [], bot_dms: {}, next_cursor: null});
    if (p === '/api/v2/finance/access') {
      if (gates.holdFinance) await new Promise(go => gates.finance.push(go));
      if (typeof finance === 'number') return json({error: {code: finance === 403 ? 'denied' : 'failed', message: 'No'}}, finance);
      return json(access(finance === 'yes'));
    }
    return json(unrouted(p));
  });
  await page.goto(ORIGIN + '/' + hash, {waitUntil: 'commit'});
  // Painted: the whole body is in and styled, no script of the app's has run.
  await page.waitForFunction(() => document.getElementById('org-fan') && [...(document.querySelectorAll('link[rel="stylesheet"]') as NodeListOf<HTMLLinkElement> & Iterable<HTMLLinkElement>)].every(l => l.sheet)
    && typeof S === 'undefined', null, {timeout: t(15000)});
  await page.evaluate(() => { void document.body.offsetHeight; return document.fonts.ready.then(() => 0); });
  const painted = await rail(page);
  // Ran: the scripts in, every read still out.
  gates.holdScripts = false; gates.scripts.splice(0).forEach(go => go());
  await page.waitForFunction(() => document.readyState === 'complete' && typeof S !== 'undefined' && !S.me, null, {timeout: t(15000)});
  if (phone) {
    await page.locator('#mobile-more').click();
    await page.waitForFunction(() => document.body.classList.contains('drawer'));
  }
  const ran = await rail(page);
  if (early) await early(page);
  // Settled: every read answered, Finance's included.
  gates.holdReads = false; gates.reads.splice(0).forEach(go => go());
  await page.waitForFunction(() => S.me && typeof FIN_PROBE !== 'undefined' && FIN_PROBE && THREADS.on !== null
    && document.querySelector('#tree li:not(.empty)'), null, {timeout: t(15000)});
  if (!holdFinance) await page.evaluate(() => FIN_PROBE);
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  const settled = await rail(page);
  const releaseFinance = async () => {
    gates.holdFinance = false; gates.finance.splice(0).forEach(go => go());
    await page.evaluate(() => FIN_PROBE);
  };
  return {page, errors, painted, ran, settled, releaseFinance};
}
// Nothing moved: painted, ran and settled are the same rail.
function same({painted, ran, settled}: {painted: Rail; ran: Rail; settled: Rail}, tag: string) {
  assert.deepEqual(ran, painted, tag + ': the scripts move nothing');
  assert.deepEqual(settled, painted, tag + ': the reads move nothing');
}
const newContext = (browser: import('playwright').Browser, phone: boolean) => browser.newContext({viewport: phone ? PHONE : DESKTOP, serviceWorkers: 'block', reducedMotion: 'reduce',
  ...(phone ? {isMobile: true, hasTouch: true} : {})});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const phone of [false, true]) {
      const tag = phone ? 'phone' : 'desktop';
      const context = await newContext(browser, phone);
      // No Finance for Ana and nothing kept: the rail is whole and still from the first paint.
      {
        const v = await visit(context, {phone, finance: 'no'});
        same(v, tag + ', no Finance');
        const keys = v.painted.rows.filter(r => r.on).map(r => r.key);
        assert.deepEqual(keys.slice(0, 15), ['tasks', 'calendar', 'threads', 'updates', 'overview', 'Company pages', 'strategy', 'growth',
          'automations', 'listening', 'rules', 'library', 'Library pages', 'docs', 'files'], tag + ': the order, from the first paint');
        assert.equal(await v.page.locator('#side a[data-nav="overview"]').evaluate(a => a.textContent!.trim()), 'Company', tag + ': Overview is Company');
        assert.ok(!keys.includes('connections'), tag + ': Connections is part of Tools');
        assert.ok(keys.includes('threads') && keys.includes('organisation'), tag + ': Threads and the Team section too');
        if (phone) assert.deepEqual(v.painted.bar, ['org', 'threads', 'mobile-ask', 'tasks', 'updates', 'more'], tag + ': the bottom bar');
        else assert.ok(v.painted.foot.includes('#ask-row:true'), tag + ': Tico Assistant at the foot from the first paint');
        assert.equal(await v.page.locator('#nav-finance').getAttribute('hidden'), '', tag + ': Finance hidden once the scripts run, too');
        assert.equal((await kept(v.page)).finance, false, tag + ': the answer is kept');
        assert.deepEqual(v.errors, []);
        await v.page.close();
      }
      // Ana gets Finance: with "no" kept, it shows once its read lands (her first visit with it), and from the first
      // paint on the next.
      {
        const first = await visit(context, {phone, finance: 'yes'});
        assert.equal(shows(first.painted, 'finance'), false, tag + ': the kept "no" holds until the read');
        assert.equal(shows(first.settled, 'finance'), true, tag + ': Finance once access says so');
        assert.equal((await kept(first.page)).finance, true);
        await first.page.close();
        const again = await visit(context, {phone, finance: 'yes'});
        same(again, tag + ', Finance kept');
        assert.equal(shows(again.painted, 'finance'), true, tag + ': Finance from the first paint');
        const order = again.painted.rows.filter(r => r.on).map(r => r.key);
        assert.deepEqual(order.slice(4, 9), ['overview', 'Company pages', 'strategy', 'finance', 'growth'], tag + ': Finance between Strategy and Growth');
        assert.deepEqual(again.errors, []);
        await again.page.close();
      }
      // A failed read is no answer: Finance stays, and so does what was kept.
      {
        const v = await visit(context, {phone, finance: 500});
        same(v, tag + ', access read failed');
        assert.equal(shows(v.settled, 'finance'), true, tag + ': a server error leaves Finance as it was');
        assert.equal((await kept(v.page)).finance, true);
        await v.page.close();
      }
      // Kept as shown, then Finance says no: it goes, and the next visit starts without it.
      for (const no of [403, 'no']) {
        const v = await visit(context, {phone, finance: no});
        assert.equal(shows(v.painted, 'finance'), true, `${tag}, ${no}: shown from what was kept`);
        assert.equal(shows(v.settled, 'finance'), false, `${tag}, ${no}: gone once access says no`);
        assert.equal(await v.page.locator('#nav-finance').getAttribute('hidden'), '', `${tag}, ${no}: hidden, so Search leaves it out`);
        assert.equal((await kept(v.page)).finance, false);
        await v.page.close();
        if (no === 403) {
          const back = await visit(context, {phone, finance: 'yes'});   // shown again, for the next case
          await back.page.close();
        }
      }
      await context.close();
    }

    // Nothing kept at all (a new browser) and Finance for the viewer: hidden until the read, the default that moves
    // least for most people (Finance is seen by few).
    {
      const context = await newContext(browser, false);
      const v = await visit(context, {finance: 'yes'});
      assert.equal(shows(v.painted, 'finance'), false, 'nothing kept: Finance hidden at first');
      assert.equal(shows(v.ran, 'finance'), false, 'and once the scripts run');
      assert.equal(shows(v.settled, 'finance'), true, 'then shown by its read');
      await v.page.close();
      await context.close();
    }

    // Sign-out drops what was kept: Bea, signing in next in this browser, never starts with Ana's Finance.
    {
      const context = await newContext(browser, false);
      const ana = await visit(context, {finance: 'yes', proxy: true});
      await ana.page.close();
      const again = await visit(context, {finance: 'yes', proxy: true});
      assert.equal(shows(again.painted, 'finance'), true, 'Ana: Finance from the first paint');
      await again.page.locator('#account').click();
      await again.page.locator('#sign-out').click();
      await again.page.waitForURL('**/api/v2/logout');
      const out = await kept(again.page);
      assert.ok(!out.who && !('finance' in out), 'a sign-out forgets the rail kept for Ana');
      await again.page.close();
      const bea = await visit(context, {who: 'bea', finance: 'no'});
      same(bea, 'Bea after Ana signed out');
      assert.equal(shows(bea.painted, 'finance'), false, 'Bea: no Finance at any point');
      assert.match((await kept(bea.page)).who, /bea@acme\.example/, 'what is kept now is Bea\'s');
      await bea.page.close();
      // Someone else without a sign-out between (/me answers as Ana again): Bea's answer is dropped before the config
      // draws, and Ana's own read decides.
      const ana2 = await visit(context, {finance: 'yes'});
      assert.equal(shows(ana2.settled, 'finance'), true);
      assert.match((await kept(ana2.page)).who, /ana@acme\.example/);
      await ana2.page.close();
      const bea2 = await visit(context, {who: 'bea', finance: 'no'});
      assert.equal(shows(bea2.ran, 'finance'), true, 'before /me answers, the last person\'s answer stands');
      assert.equal(shows(bea2.settled, 'finance'), false, 'Bea: Ana\'s Finance goes once /me names her');
      assert.equal((await kept(bea2.page)).finance, false);
      await bea2.page.close();
      await context.close();
    }

    // Two tabs, Ana in both; one signs out while the other's Finance read is still out. That answer, and the other tab's
    // next sidebar draw, keep nothing: no first paint after the sign-out shows Ana's rows.
    {
      const context = await newContext(browser, false);
      const seed = await visit(context, {finance: 'yes', proxy: true});
      await seed.page.close();
      const other = await visit(context, {finance: 'yes', proxy: true, holdFinance: true});
      const here = await visit(context, {finance: 'yes', proxy: true});
      await here.page.locator('#account').click();
      await here.page.locator('#sign-out').click();
      await here.page.waitForURL('**/api/v2/logout');
      await other.releaseFinance();
      assert.equal(shows(await rail(other.page), 'finance'), true, 'the other tab still draws its own answer');
      await other.page.evaluate(() => renderTree());
      const out = await kept(other.page);
      assert.ok(!out.who && !('finance' in out) && !('welcome' in out), 'nothing kept for Ana after a sign-out in another tab: ' + JSON.stringify(out));
      await other.page.close();
      await here.page.close();
      const next = await visit(context, {who: 'bea', finance: 'no'});
      assert.equal(shows(next.painted, 'finance'), false, 'the next first paint has no Finance');
      await next.page.close();
      await context.close();
    }

    // A sign-out in another tab while this tab's boot reads are still out: its /me, once in, keeps nothing either.
    {
      const context = await newContext(browser, false);
      const seed = await visit(context, {finance: 'yes', proxy: true});
      await seed.page.close();
      const late = await visit(context, {finance: 'yes', early: async () => {
        const there = await visit(context, {finance: 'yes', proxy: true});
        await there.page.locator('#account').click();
        await there.page.locator('#sign-out').click();
        await there.page.waitForURL('**/api/v2/logout');
        await there.page.close();
      }});
      assert.equal(shows(late.settled, 'finance'), true, 'the late tab still draws its own answer');
      await late.page.evaluate(() => renderTree());
      const out = await kept(late.page);
      assert.ok(!out.who && !('finance' in out) && !('welcome' in out), 'nothing kept by a boot that outlived a sign-out: ' + JSON.stringify(out));
      await late.page.close();
      await context.close();
    }

    // A new install opening on the wizard, nothing kept: Ask tapped before /me answers stays shut over setup.
    {
      const context = await newContext(browser, false);
      const v = await visit(context, {hash: '', onboarding: true, early: page => page.locator('#ask-row').click()});
      await v.page.waitForFunction(() => location.hash === '#/welcome');
      assert.equal(await v.page.evaluate(() => document.documentElement.classList.contains('in-setup')), true, 'the wizard is marked');
      assert.equal(await v.page.evaluate(() => document.querySelector('#ask-panel')!.classList.contains('ask-shown')
        || document.body.classList.contains('ask-open')), false, 'Ask stays shut over the wizard');
      assert.equal(await v.page.locator('#ask-row').isVisible(), false, 'and its row goes');
      await v.page.close();
      // The same tap on an ordinary page opens Ask once /me answers.
      const open = await visit(context, {early: page => page.locator('#ask-row').click()});
      await open.page.waitForFunction(() => document.body.classList.contains('ask-open'));
      await open.page.close();
      await context.close();
    }

    // Finish setup and the folds: kept like Finance, so they too hold still from the first paint.
    {
      const context = await newContext(browser, false);
      await context.addInitScript(() => {
        localStorage.setItem('tico.nav.company', 'closed');
        localStorage.setItem('tico.nav.library', 'closed');
        localStorage.setItem('hub.nav.collapsed', JSON.stringify(['organisation']));
      });
      const first = await visit(context, {onboarding: true});
      assert.equal(shows(first.settled, 'welcome'), true, 'Finish setup for an owner mid-setup');
      assert.equal(shows(first.painted, 'docs'), false, 'Library folded from the first paint');
      assert.equal(shows(first.painted, 'strategy'), false, 'Company folded from the first paint');
      assert.equal(shows(first.painted, 'overview'), true, 'its own row stays');
      assert.equal(shows(first.painted, 'rules'), false, 'Rules folds with Company');
      assert.equal(await first.page.locator('#nav-organisation').isVisible(), false, 'Team folded');
      await first.page.close();
      const again = await visit(context, {onboarding: true});
      same(again, 'Finish setup kept, folds kept');
      assert.equal(shows(again.painted, 'welcome'), true, 'Finish setup from the first paint');
      assert.deepEqual(again.errors, []);
      await again.page.close();
      // Setup done: it goes, and stays gone from the next first paint.
      const done = await visit(context, {onboarding: false});
      assert.equal(shows(done.settled, 'welcome'), false);
      await done.page.close();
      const after = await visit(context, {onboarding: false});
      same(after, 'setup done');
      assert.equal(shows(after.painted, 'welcome'), false);
      await after.page.close();
      // On one of its pages a folded Company is open, from the first paint.
      const inside = await visit(context, {hash: '#/growth'});
      same(inside, 'Company folded, on Growth');
      assert.equal(shows(inside.painted, 'growth'), true, 'Company open on Growth');
      assert.equal(await inside.page.locator('#side a[data-nav="growth"]').getAttribute('aria-current'), 'page', 'Growth is the current row');
      await inside.page.close();
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('sidebar-first-paint: ok');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
