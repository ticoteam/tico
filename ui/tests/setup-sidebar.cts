// Offline regression for the sidebar around the first-run wizard. Fixtures only.
//  - on the setup modal, before setup is done, the app's sidebar stays whole behind it (Finish setup, the pages and
//    the team), What's new and the usage notice wait, and the phone's bottom bar is hidden; anywhere else, and once
//    setup is done, everything is back;
//  - once the team exists, a team of "Head of …" bots, each with its Setup pill and no harness mark, shows each whole name
//    in one color (words shared with another bot's name are not muted), and each row's tooltip has the whole name.
// TICO_SCREENSHOT_DIR=<dir> saves both screens at desktop and phone width, dark and light.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Page = import('playwright').Page;

const shots = process.env.TICO_SCREENSHOT_DIR;
async function shot(page: Page, name: string) {
  if (!shots) return;
  await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
  await page.screenshot({path: path.join(shots, name + '.png')});
}

const CONFIG = {environment_id: 'initech', company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace',
  assistant_bot: 'assistant', public_url: 'https://initech.test', runner_url: 'https://initech.test', local: false, release: '',
  version: '0.3.62', runner_compat: {version: '0.3.62', min_runner: '0.1.0'}, providers_configured: true,
  changelog: {unread_count: 103}, usage_count_notice: true};
const people = [{id: 'ana', name: 'Ana Rivera', email: 'ana@initech.test', org_parent: 'g:leadership', team: 'leadership'}];
const groups = ['Leadership', 'Engineering', 'Finance', 'Marketing', 'Product'].map((name, order) =>
  ({id: name.toLowerCase(), name, parent: '', org_parent: '', order}));
// What Create leaves from the catalog: a head for each group with one report under it, every one still to set up.
const bot = (name: string, display_name: string, team: string, reports_to: string, extra: Record<string, unknown> = {}) => ({name, display_name, team, reports_to,
  org_parent: reports_to.startsWith('human:') ? 'p:' + reports_to.slice(6) : 'b:' + reports_to, status: 'planned',
  state: 'planned', onboarding_state: 'needs_setup', harness: 'claude', model: 'claude-opus-5-5', host: 'keeper',
  can_chat: true, can_manage: true, my_access: {see: true, read: true, write: true}, users: [], owner_id: 'ana', revision: 1, ...extra});
const TEAM = [
  bot('head-of-engineering', 'Head of Engineering', 'engineering', 'human:ana'),
  bot('qa-engineer', 'QA Engineer', 'engineering', 'head-of-engineering'),
  bot('head-of-finance', 'Head of Finance', 'finance', 'human:ana'),
  bot('finance-owner', 'Finance Owner', 'finance', 'head-of-finance'),
  bot('head-of-marketing', 'Head of Marketing', 'marketing', 'human:ana'),
  bot('content', 'Content Owner', 'marketing', 'head-of-marketing'),
  bot('head-of-product', 'Head of Product', 'product', 'human:ana'),
  bot('insights', 'Customer Insights Analyst', 'product', 'head-of-product'),
];

async function open(browser: import('playwright').Browser, {theme, phone, needed, team, route}: {theme: string; phone?: boolean; needed: boolean; team?: boolean; route: string}) {
  const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800},
    isMobile: !!phone, hasTouch: !!phone, serviceWorkers: 'block'});
  await context.addInitScript(value => { try { localStorage.setItem('tico.theme', value); } catch {} }, theme);
  const me = {id: 'ana', name: 'Ana Rivera', role: 'owner', cloud: true, email: 'ana@initech.test'};
  const config = {...CONFIG, onboarding_needed: needed, ...(needed ? {} : {changelog: {unread_count: 0}})}, notices: string[] = [];
  await context.route('**/*', async r => {
    const request = r.request(), url = new URL(request.url()), p = url.pathname;
    const json = (body: unknown) => r.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return r.abort();
    if (p === '/') return r.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return r.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p.startsWith('/vendor/')) {
      const file = path.join(__dirname, '..', p);
      if (fs.existsSync(file)) return r.fulfill({contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript', body: fs.readFileSync(file)});
    }
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/v2/system/usage-count/notice') { notices.push(JSON.parse(request.postData()!).state); return json({ok: true}); }
    if (p === '/api/humans') return json(team ? {people, org_groups: groups} : {people: [{...people[0], org_parent: '', team: ''}], org_groups: []});
    if (p === '/api/employees') return json(team ? TEAM : []);
    if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], queued: [], recent_runs: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/setup') return json({names: {company_name: 'Initech', app_name: 'Initech Hub'}, answers: {}, selected: {},
      completed: needed ? null : '2026-10-19T10:00:00Z', machine: {runners: [], enrolled: false}, needed, home: 'human:ana',
      bots: needed ? [] : TEAM.map(b => ({slug: b.name, display_name: b.display_name, status: b.status, template: b.name,
        onboarding_state: b.onboarding_state, setup_task_id: null, repository_present: false}))});
    if (p === '/api/v2/setup/groups') return json({version: 'fixture', departments: [], cards: [], hq: {available: true}});
    if (p === '/api/v2/templates') return json({cards: []});
    if (p === '/api/v2/needs-you') return json({items: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/operations') return json({machines: [], services: [], issues: [], agents: []});
    if (p === '/api/v2/setup/getting-started/state') return json({tour: false, checklist: false, cards: [], skipped: []});
    if (p.endsWith('/watch')) return r.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  const page = await context.newPage(), errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://tico-ui.test/' + route);
  await page.locator('#tree li').first().waitFor();
  return {page, errors, notices, close: () => context.close()};
}

const shown = (page: Page, selector: string) => page.locator(selector).evaluateAll(els => els.filter(el => el.checkVisibility()).map(el => (el as HTMLElement).dataset.nav || el.id));

// The anonymous usage count's notice: not over the setup modal, and not counted as seen there.
const noNotice = async ({page, notices}: {page: Page; notices: string[]}) => {
  assert.equal(await page.locator('#usage-notice').isVisible(), false, 'the usage notice waits until setup is left');
  assert.deepEqual(notices, []);
};

async function wizard(browser: import('playwright').Browser, theme: string) {
  // A new install opens with no page in the address: the boot sends it to the wizard.
  const {page, errors, notices, close} = await open(browser, {theme, needed: true, route: ''});
  await page.locator('#onb-website').waitFor();
  assert.equal(await page.evaluate(() => location.hash), '#/welcome');
  await noNotice({page, notices});
  // The sidebar is the app's own behind the modal, not a stripped one.
  const nav = await shown(page, '#side .side-scroll > .nav-link, #side .side-scroll > .nav-row > .nav-link, #side .side-scroll > .nav-row');
  assert.ok(nav.includes('welcome') && nav.includes('tasks'), 'Finish setup and the app\'s pages');
  assert.equal(await page.locator('#changelog-notice').isVisible(), false, 'no What\'s new on a fresh install');
  assert.equal(await page.locator('#tree').isVisible(), true, 'the team stays');
  await shot(page, `wizard-desktop-${theme}`);
  // Anywhere else the app is all there, and What's new and the usage notice come back.
  await page.evaluate(() => { location.hash = '#/tasks'; });
  await page.locator('#onb').waitFor({state: 'detached'});
  await page.locator('#changelog-notice').waitFor({state: 'visible'});
  assert.ok((await shown(page, '#side .side-scroll > .nav-link, #side .side-scroll > .nav-row > .nav-link')).includes('overview'));
  await page.locator('#usage-notice').waitFor({state: 'visible'});
  assert.deepEqual(notices, ['shown'], 'and counts as seen only then');
  await shot(page, `left-setup-desktop-${theme}`);
  assert.deepEqual(errors, []);
  await close();

  const phone = await open(browser, {theme, phone: true, needed: true, route: '#/welcome'});
  await phone.page.locator('#onb-website').waitFor();
  assert.equal(await phone.page.locator('#mobile-nav').isVisible(), false, 'no phone navigation during setup');
  assert.deepEqual(await shown(phone.page, '#mobile-nav > *'), []);
  await noNotice(phone);
  await shot(phone.page, `wizard-phone-${theme}`);
  // Leaving setup restores the bar and makes the normal drawer reachable again.
  await phone.page.evaluate(() => { location.hash = '#/tasks'; });
  await phone.page.locator('#mobile-nav').waitFor({state: 'visible'});
  assert.deepEqual(await shown(phone.page, '#mobile-nav > *'), ['org', 'search', 'mobile-ask', 'tasks', 'updates', 'more']);
  await phone.page.locator('#mobile-more').click();
  await phone.page.locator('#tree').waitFor();
  assert.ok((await shown(phone.page, '#side .side-scroll > .nav-link')).includes('tasks'));
  assert.equal(await phone.page.locator('#changelog-notice').isVisible(), true);
  await phone.page.locator('#usage-notice').waitFor({state: 'visible'});
  assert.deepEqual(phone.notices, ['shown'], 'the phone also waits until setup is left');
  await shot(phone.page, `left-setup-phone-drawer-${theme}`);
  assert.deepEqual(phone.errors, []);
  await phone.close();
}

// Each bot row: what it shows, whether its name is drawn in more than one piece, and its tooltip.
type Row = {name: string; pieces: number; title: string; setup: boolean; mark: boolean; visible: boolean; display: string; overflow: string};
const rows = (page: Page) => page.locator('#tree a.node[data-org^="b:"]').evaluateAll(els => (els as HTMLElement[]).map((a): Row => {
  // The marks ride the name's last word inside .nm (.nm-end); the name is its text without them.
  const nm = a.querySelector('.nm')!, MARKS = '.tree-setup, .tree-goal, .org-branch', bare = nm.cloneNode(true) as HTMLElement;
  bare.querySelectorAll(MARKS).forEach(m => m.remove());
  return {name: bare.textContent!, pieces: bare.querySelectorAll('span:not(.proj):not(.nm-end)').length, title: a.title,
          setup: !!a.querySelector('.tree-setup'), mark: !!a.querySelector('.rt'), visible: a.checkVisibility(),
          display: getComputedStyle(nm).whiteSpace, overflow: getComputedStyle(nm).webkitLineClamp};
}));
const expand = async (page: Page) => {
  // Each click draws the tree again, so open the first closed one until none is left.
  for (let left: number; (left = await page.locator('#tree .chev.col').count());) {
    await page.locator('#tree .chev.col').first().click();
    await page.waitForFunction((n: number) => document.querySelectorAll('#tree .chev.col').length < n, left);
  }
};
// One color per name: words a name shares with another bot's are not muted or split off.
const readable = (team: Row[], where: string) => {
  for (const row of team) {
    assert.equal(row.visible, true, `${row.name} ${where}`);
    assert.equal(row.title, row.name, 'the tooltip has the whole name');
    assert.equal(row.pieces, 0, `${row.name} is one piece of text ${where}`);
    assert.equal(row.mark, false, `${row.name} has no harness mark ${where}`);
    // A long name wraps; only past three lines does it end in "…".
    assert.deepEqual([row.display, row.overflow], ['normal', '3'], `${row.name} wraps, up to three lines ${where}`);
  }
};

async function created(browser: import('playwright').Browser, theme: string) {
  const {page, errors, close} = await open(browser, {theme, needed: false, team: true, route: '#/tasks'});
  await page.locator('#tree a.node[data-org="b:head-of-product"]').waitFor();
  // Setup is done: the app's pages are back.
  assert.ok((await shown(page, '#side .side-scroll > .nav-link')).includes('tasks'));
  await expand(page);
  const team = await rows(page);
  // The Finance Owner is a built-in helper, off the Team tree (it opens from Finance).
  assert.equal(team.length, TEAM.length - 1);
  assert.ok(!team.some(row => row.name === 'Finance Owner'));
  readable(team, 'at desktop width');
  const heads = team.filter(row => row.name.startsWith('Head of '));
  assert.deepEqual(heads.map(row => row.name), ['Head of Engineering', 'Head of Finance', 'Head of Marketing', 'Head of Product']);
  assert.ok(heads.every(row => row.setup && !row.mark), 'the Setup pill stays; no harness mark');
  // A name longer than its row wraps between words inside its row, 6px (the row's gap) before its count, and is not cut.
  // The row is redrawn with a count in place of the Setup mark.
  const cut = await page.locator('#tree a.node[data-org="b:head-of-product"]').evaluate(a => {
    const nm = a.querySelector<HTMLElement>('.nm')!;
    nm.textContent = 'Head of Product and Everything Else';
    a.querySelector('.tree-setup')?.remove();
    const count = Object.assign(document.createElement('span'), {className: 'cnt', textContent: '3'});
    a.append(count);
    return {cut: nm.scrollWidth > nm.clientWidth || nm.scrollHeight > nm.clientHeight, row: a.scrollWidth <= a.clientWidth,
            gap: Math.round(count.getBoundingClientRect().left - nm.getBoundingClientRect().right),
            wraps: nm.getBoundingClientRect().height > parseFloat(getComputedStyle(nm).lineHeight) * 1.5};
  });
  assert.deepEqual(cut, {cut: false, row: true, gap: 6, wraps: true}, 'a long name wraps inside its row, 6px before its count');
  assert.equal(await page.locator('#tree .nm-split, #tree .nm-lead').count(), 0, 'no name is split at shared words');
  await shot(page, `created-desktop-${theme}`);
  assert.deepEqual(errors, []);
  await close();

  const phone = await open(browser, {theme, phone: true, needed: false, team: true, route: '#/tasks'});
  await phone.page.locator('#mobile-nav').waitFor({state: 'visible'});
  assert.deepEqual(await shown(phone.page, '#mobile-nav > *'), ['org', 'search', 'mobile-ask', 'tasks', 'updates', 'more']);
  await phone.page.locator('#mobile-more').click();
  await phone.page.locator('#tree a.node[data-org="b:head-of-product"]').waitFor({state: 'visible'});
  await expand(phone.page);
  readable(await rows(phone.page), 'on a phone');
  await shot(phone.page, `created-phone-${theme}`);
  assert.deepEqual(phone.errors, []);
  await phone.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    for (const theme of ['dark', 'light']) {
      await wizard(browser, theme);
      await created(browser, theme);
    }
    console.log('PASS: behind the setup modal the sidebar stays whole and the usage notice waits; once the team exists each bot row shows its whole name in one color.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
