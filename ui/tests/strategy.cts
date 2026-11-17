// The Strategy section (ui/strategy-page.ts) against a stand-in for GET /api/v2/strategy, the market cards' reads and
// all their writes (fixtures only, the Acme example): the owner reads the draft as cards (the summary on top, no vision;
// Where we play beside the bets; Competitors beside Related news; the seven fixed sections and an extra one), edits the
// summary and each bet's statement in place with the draft's revision, adds a bet with + Bet (active), removes one with
// × and restores it, moves one by its number (arrow keys or a drag), and is told when the draft changed elsewhere (409);
// a click on a segment makes it Us, then Next, then neither, × removes one and one can be added by name; competitors are
// removed and added by name or site; there is no Publish: each save is live at once as the next version; a member
// reads the live version; old #/market links land on the Market tab keeping their query;
// the Goals page's Bet picker saves bet_id and its chip opens the bet; a team group's row has no Bet flag.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots (desktop in both themes, and a phone).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const uiDir = path.join(__dirname, '..');
type Page = import('playwright').Page;
type Browser = import('playwright').Browser;
type Route = import('playwright').Route;
type Est = {id: string; method: string; status: string; spend_usd: number; low_usd: number; high_usd: number;
  evidence: {id: string; source_url: string; source_kind: string; quote: string | null}[]};
type Seg = {id: string; name: string; parent: string | null; depth: number; summary: string; size: Est | null;
  rolled_usd: {spend_usd: number; low_usd: number; high_usd: number} | null; estimates: Est[]; players: unknown[]; self: boolean; next: boolean};
type Goal = {id: string; title: string; owner_id: string; parent_id: string | null; status: string; status_source?: string; rank?: number;
  kpis: unknown[]; why: string; bet_id: string | null};
// The write bodies the tests read back (what the page sent); only the fields a check names are listed.
type Body = {revision: number; id?: string; name: string; target: string; note: string; summary?: string; parent_id?: string;
  sections?: {[key: string]: unknown; extra?: {[key: string]: unknown}}; wrong_if?: {kind?: string; check_by?: string | null} | null;
  statement: string; status?: string; rank?: number; title?: string; bet_id?: string | null; decision?: string; moves?: Record<string, string>};
type Sent = [string, string, Body];
type Bet = {id: string; statement: string; why: string; rules_out: string; wrong_if: {kind?: string; check_by?: string | null; text?: string} | null;
  cites: unknown[]; status: string; rank: number; since_n?: number | null; until_n?: number | null};
type Draft = {id: string; n: number | null; status: string; guide: string; summary: string; vision: string;
  sections: {[key: string]: unknown; extra: {[key: string]: unknown}}; bets?: Bet[]; note: string; revision: number;
  updated_at?: string; updated_by?: string; published_at?: string | null; published_by?: string | null};
type State = {draft: Draft; current: Draft | null; bets: Bet[]};
declare var $: (selector: string) => HTMLElement;

const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page: Page, name: string, themes: string[] = ['dark', 'light']) => {
  if (!shots) return;
  for (const theme of themes) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.screenshot({animations: 'disabled', path: path.join(shots, `strategy-${name}-${theme}.png`)});
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
};
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const bot = (name: string, display_name: string, org_parent: string, over?: Record<string, unknown>) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('assistant', 'Assistant', ''), bot('botops', 'BotOps', ''), bot('librarian', 'Librarian', ''),
  bot('strategy-owner', 'Strategy Owner', 'p:ana', {reports_to: ''}), bot('cmo', 'Head of Marketing', 'b:strategy-owner', {reports_to: ''})];
const kpis = [{id: 'k-margin', name: 'Gross margin per job', unit: '%'}, {id: 'k-jobs', name: 'Jobs per week', unit: ''}];
const bets = () => [
  {id: 'b-price', statement: 'Price at half the market and still make money, because bots do the office work.',
   why: "Our cost per job is 40% below CleanCo's, because booking, scheduling and billing need no office staff.",
   rules_out: 'Premium add-ons, custom quotes, phone booking.',
   wrong_if: {kind: 'kpi', kpi_id: 'k-margin', direction: 'below', threshold: 25, check_by: '2027-01-31'}, cites: [], status: 'active', rank: 0, since_n: 1, until_n: null},
  {id: 'b-offices', statement: 'Sell to small offices through property managers, not to tenants.',
   why: 'One manager brings ten buildings; tenants churn with their leases.', rules_out: 'Ads to tenants, door-to-door sales.',
   wrong_if: {kind: 'text', text: 'Fewer than 5 managers signed', check_by: '2027-02-01'}, cites: [], status: 'active', rank: 1, since_n: 1, until_n: null},
  {id: 'b-weekly', statement: 'Offer weekly only, no one-off cleans.', why: 'Routes stay full and crews learn the homes.', rules_out: '',
   wrong_if: {kind: 'text', text: '', check_by: null}, cites: [], status: 'proposed', rank: 2, since_n: null, until_n: null},
  {id: 'b-old', statement: 'Clean only offices.', why: '', rules_out: 'Homes', wrong_if: null, cites: [], status: 'retired', rank: 3, since_n: null, until_n: 1},
];
const sections = {
  serve: 'Busy households and **small offices** in Austin who now hire a freelancer or CleanCo. Not: move-out deep cleans.',
  sell: 'Weekly cleaning at $79 a visit for a home. Doubled, we are CleanCo; halved, we lose money.',
  know: 'Most of a cleaning company is the office, not the cleaning. Bots run the office.',
  not: 'No one-off deep cleans, no custom quotes, no phone line.',
  extra: {brand: {title: 'Brand', body: 'Plain, on time, half the price.'}},
};
const B = 1e9;
const seg = (id: string, name: string, parent: string | null, usd: number, over: Partial<Seg> = {}): Seg => ({id: 'segment/' + id, name, parent: parent ? 'segment/' + parent : null, depth: parent ? 2 : 1,
  summary: '', size: null, rolled_usd: usd ? {spend_usd: usd, low_usd: usd * 0.7, high_usd: usd * 1.3} : null, estimates: [], players: [],
  self: false, next: false, ...over});
// A size with its one source: an estimate that carries spend and cites a link (the spend API's shape, market_spend.py).
const est = (usd: number, url: string): Est => ({id: 'e-' + url, method: 'top_down', status: 'accepted', spend_usd: usd, low_usd: usd * 0.8, high_usd: usd * 1.2,
  evidence: [{id: 'ev-' + url, source_url: url, source_kind: 'report', quote: null}]});
const homesSize = est(4 * B, 'https://homes.example/cleaning-market');
const market = () => ({definition: {root: 'segment/cleaning'}, segments: [
  seg('homes', 'Home cleaning', null, 4 * B, {self: true, size: homesSize, estimates: [homesSize]}),
  seg('offices', 'Office cleaning', null, 3 * B, {size: est(3 * B, 'https://www.offices.example/market-size'), estimates: [est(3 * B, 'https://www.offices.example/market-size')]}),
  seg('medical', 'Medical offices', 'offices', 1 * B, {size: est(1 * B, 'https://medical.example/size'), estimates: [est(1 * B, 'https://medical.example/size')]}),
  seg('moveout', 'Move-out cleans', null, 0)],
  rivals: [{id: 'company/cleanco', name: 'CleanCo', url: 'https://cleanco.example'}, {id: 'company/sparkle', name: 'Sparkle', url: ''}],
  recent: [{item_id: 'li-1', source: 'reddit', url: 'https://reddit.example/r/austin/1', author: 'kim', published_at: '2026-10-20T08:00:00Z',
    text: 'Anyone know a cleaner in Austin that does weekly?', destinations: ['sales'], at: '2026-10-20T09:00:00Z'}],
  insights: [{id: 'mi-1', kind: 'other', about: 'Listening: hot', claim: 'CleanCo raises prices 20%', source_url: 'https://news.example/cleanco',
    status: 'new', reported_at: '2026-10-18T10:00:00Z'}, {id: 'mi-2', kind: 'other', about: 'Pricing', claim: 'Not a listening post',
    source_url: 'https://x.example', status: 'new', reported_at: '2026-10-19T10:00:00Z'}]});
const v1 = {id: 'v-1', n: 1, status: 'published', guide: 'default',
  summary: 'Acme cleans homes and small offices in Austin at about half the usual price. Bots handle booking, scheduling and billing, so the price holds.',
  vision: 'Every home in Austin cleaned by Acme.', sections, bets: bets().filter(b => b.id !== 'b-weekly'), note: 'First version', revision: 7,
  updated_at: '2026-10-24T09:00:00Z', updated_by: 'human:ana', published_at: '2026-10-24T09:00:00Z', published_by: 'human:ana'};

async function open(browser: Browser, {role = 'owner', route = '#/strategy', viewport = DESKTOP, fresh = false, needs = [], more = [], marketOf = market, touch = false}: {role?: string; route?: string; viewport?: {width: number; height: number}; fresh?: boolean; needs?: unknown[]; more?: Goal[]; marketOf?: typeof market; touch?: boolean} = {}) {
  // touch: a phone's touch screen (no hover), where the small marks always show
  const page = await browser.newPage({viewport, serviceWorkers: 'block', locale: 'en-GB', hasTouch: touch, isMobile: touch});  // dates read "24 Oct"
  const errors: string[] = [], sent: Sent[] = [];
  page.on('pageerror', e => errors.push(e.message));
  // fresh: nothing saved yet, one proposed bet with no Rules out, and What we won't do unwritten.
  const st: State = fresh
    ? {draft: {id: 'v-d', n: null, status: 'draft', guide: 'default', summary: 'Acme cleans homes in Austin at half the price.', vision: '',
        sections: {serve: 'Busy households.', extra: {}}, bets: [], note: '', revision: 1}, current: null,
      bets: [{id: 'b-price', statement: 'Price at half the market.', why: '', rules_out: '', wrong_if: null, cites: [], status: 'proposed', rank: 0}]}
    : {draft: {...v1, id: 'v-d', n: null, status: 'draft', sections: {...sections, extra: {...sections.extra}}, revision: 7, published_at: null, published_by: null},
      current: v1, bets: bets()};
  const goals = [
    {id: 'g-top', title: 'Margin per job ≥ 30%', owner_id: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: [], why: '', bet_id: 'b-price'},
    {id: 'g-cmo', title: 'Sign 5 property managers', owner_id: 'bot:cmo', parent_id: 'g-top', status: 'red', status_source: 'auto', rank: 0, kpis: [], why: '', bet_id: null},
    ...more];
  const groups = [{id: 'ops', name: 'Operations', parent: '', bet_id: null}];
  const mk = marketOf();
  const strategy = () => ({...(role === 'owner' ? {draft: st.draft} : {}), current: st.current, bets: role === 'owner' ? st.bets : st.current?.bets || [],
    label: 'Bets', label_one: 'Bet', may_edit: role === 'owner',
    website: 'https://acme.example', goals_by_bet: {'b-price': [{id: 'g-top', title: 'Margin per job ≥ 30%', owner_id: 'company', status: 'green'},
      {id: 'g-jobs', title: '400 jobs/week', owner_id: 'bot:cmo', status: 'yellow'}], 'b-offices': [{id: 'g-cmo', title: 'Sign 5 property managers', owner_id: 'bot:cmo', status: 'red'}]},
    // goals_by_bet is the first 20 a bet has; this counts all of its live ones.
    live_by_bet: {'b-price': 21, 'b-offices': 1},
    health_by_bet: {'b-price': 'green', 'b-offices': 'red', 'b-weekly': 'gray'}});
  // Every save is live at once as the next version, as the server's go_live makes it.
  const live = () => {
    st.current = {...st.draft, n: (st.current?.n || 0) + 1, status: 'published', note: '', bets: st.bets.filter(b => b.status !== 'retired').map(b => ({...b})),
      published_at: new Date().toISOString(), published_by: 'human:ana'};
  };
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', name: role === 'owner' ? 'Ana Silva' : 'Cara Diaz', email: 'x@example.test',
      role: role === 'owner' ? 'owner' : 'viewer', company_role: role, cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}, {id: 'cara', name: 'Cara Diaz', org_parent: 'p:ana'}],
      org_groups: groups.map((g, i) => ({...g, org_parent: '', order: i}))});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/kpis') return json({kpis});
    if (p === '/api/v2/strategy' && method === 'GET') return json(strategy());
    if (p.startsWith('/api/v2/strategy') && method !== 'GET') {
      const body = req.postDataJSON() as Body;
      sent.push([method, p.slice('/api/v2/strategy'.length), body]);
      if (role !== 'owner') return json({error: {code: 'forbidden', detail: 'Only the owner'}}, 403);
      if (p === '/api/v2/strategy/draft') {
        if (body.revision !== st.draft.revision) return json({error: {code: 'conflict', detail: 'The draft changed', revision: st.draft.revision}}, 409);
        // Merges sections, as the server does; an empty one clears it, extra ones included.
        const {revision, ...change} = body, now = st.draft.sections;
        const merged: Draft['sections'] = {...now, ...(change.sections || {}), extra: {...now.extra, ...(change.sections?.extra || {})}};
        for (const k of Object.keys(merged)) if (merged[k] === '') delete merged[k];
        for (const k of Object.keys(merged.extra)) if (merged.extra[k] === '') delete merged.extra[k];
        st.draft = {...st.draft, ...change, sections: merged, revision: revision + 1}; live(); return json(st.draft);
      }
      const one = p.match(/^\/api\/v2\/strategy\/bets\/([^/]+)$/);
      // The server's rule (strategy._wrong_if): a sentence wrong-if has a Check by.
      if (body.wrong_if?.kind === 'text' && !body.wrong_if.check_by) return json({error: {code: 'lint', detail: 'a wrong-if in words needs a check_by date: the day to look again'}}, 422);
      if (one) { const c = st.bets.find(x => x.id === one[1])!; Object.assign(c, body); ++st.draft.revision; live(); return json({bet: c, revision: st.draft.revision}); }
      if (p === '/api/v2/strategy/bets') { const c = {id: 'b-new', since_n: null, until_n: null, rank: st.bets.length, status: 'proposed', why: '', rules_out: '', wrong_if: null, cites: [], ...body}; st.bets.push(c); ++st.draft.revision; live(); return json({bet: c, revision: st.draft.revision}); }
    }
    if (p === '/api/v2/strategy/versions') return json([{n: 1, note: 'First version', published_at: v1.published_at, published_by: 'human:ana'}]);
    if (p === '/api/v2/strategy/versions/1') return json(v1);
    if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: needs});
    const decide = p.match(/^\/api\/v2\/proposals\/([^/]+)\/decide$/);
    if (decide) { const body = req.postDataJSON() as Body; sent.push(['POST', '/proposals/' + decide[1], body]); needs.splice(0); return json({proposal: {id: decide[1], status: 'confirmed'}}); }
    if (p === '/api/v2/bots/strategy-owner/routines') return json({routines: []});
    const goal = p.match(/^\/api\/v2\/goals\/([^/]+)$/);
    if (goal && method === 'POST') { const body = req.postDataJSON() as Body; sent.push(['POST', '/goals/' + goal[1], body]); Object.assign(goals.find(g => g.id === goal[1])!, body); return json({goal: goals.find(g => g.id === goal[1])}); }
    const group = p.match(/^\/api\/v2\/groups\/([^/]+)$/);
    if (group && method === 'PATCH') { const body = req.postDataJSON() as Body; sent.push(['PATCH', '/groups/' + group[1], body]); Object.assign(groups.find(g => g.id === group[1])!, body); return json({group: groups.find(g => g.id === group[1])}); }
    // The market cards: the spend tree, the competitors, and what Listening found.
    if (p.startsWith('/api/v2/market/') && method !== 'GET') {
      const body = req.postDataJSON() as Body;
      sent.push([method, p, body]);
      if (role !== 'owner') return json({error: {code: 'forbidden', detail: 'Only the owner'}}, 403);
      if (p === '/api/v2/market/segments/target') {
        const g = mk.segments.find(x => x.id === body.id)!;
        g.self = body.target === 'ours'; g.next = body.target === 'next';
        return json({id: body.id, ours: body.target === 'ours', next: body.target === 'next'});
      }
      if (p === '/api/v2/market/segments/remove') {
        const g = mk.segments.find(x => x.id === body.id)!;
        mk.segments = mk.segments.filter(x => x !== g).map(x => (x.parent === g.id ? {...x, parent: g.parent, depth: 1} : x));
        return json({id: body.id, removed: true});
      }
      if (p === '/api/v2/market/segments') {
        const made = seg(body.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), body.name, body.parent_id ? body.parent_id.replace(/^segment\//, '') : null, 0, {summary: body.summary || ''});
        if (mk.segments.some(x => x.id === made.id)) return json({error: {code: 'exists', detail: 'exists'}}, 409);
        mk.segments.push(made); return json({segment: made});
      }
      const renamed = p.match(/^\/api\/v2\/market\/entities\/(segment\/[^/]+)$/);
      if (renamed) { const g = mk.segments.find(x => x.id === renamed[1])!; g.name = body.name; return json({entity: {id: g.id, name: g.name}}); }
      if (p === '/api/v2/market/competitors') {
        const host = /\./.test(body.name) ? body.name.replace(/^https?:\/\/(www\.)?/, '').split('/')[0]!.toLowerCase() : '';
        const made = {id: 'company/' + (host || body.name.toLowerCase()), name: host || body.name, url: host ? 'https://' + host : ''};
        mk.rivals = [...mk.rivals, made].sort((a, b) => a.name.localeCompare(b.name)); return json({competitor: made});
      }
      if (p === '/api/v2/market/competitors/remove') { mk.rivals = mk.rivals.filter(x => x.id !== body.id); return json({id: body.id, removed: true}); }
    }
    if (p === '/api/v2/market/spend') return json({definition: mk.definition, year: 2026, dimension: 'offering', basis: 'product',
      total: {spend_usd: 7 * B, low_usd: 5 * B, high_usd: 9 * B}, segments: mk.segments});
    if (p === '/api/v2/market/competitors') return json({competitors: mk.rivals});
    if (p === '/api/v2/listening/stats') return role === 'owner' ? json({daily: [], recent: mk.recent}) : json({error: {code: 'forbidden', detail: 'no'}}, 403);
    if (p === '/api/v2/market/insights') return role === 'owner' ? json({insights: mk.insights}) : json({error: {code: 'forbidden', detail: 'no'}}, 403);
    if (p === '/api/v2/market/graph') return json({entities: [], edges: []});
    if (p === '/api/v2/market/entities') return json({entities: [], total: 0});
    if (p === '/api/company-docs') return json({documents: []});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + route);
  // On Goals, what waits is one line; its cards are answered in the sheet Review opens.
  if (route.startsWith('#/goals') && needs.length) {
    await page.locator('#goal-needs-line [data-needs-review]').click();
    await page.locator('#goal-needs-sheet[open] #goal-needs').waitFor();
  }
  return {page, errors, sent, st, mk};
}
const sectionTitles = (page: Page) => page.locator('[data-section] .strat-sec-head .strat-k').allInnerTexts();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // No Publish: a new strategy has no Publish and no draft status, and each save is live at once as the next version;
    // the Proposed pill makes a bet active.
    {
      const {page, errors, sent} = await open(browser, {fresh: true});
      await page.locator('li[data-bet="b-price"]').waitFor();
      assert.equal(await page.locator('[data-strat-publish], [data-strat-status], [data-strat-why-not]').count(), 0);
      await page.locator('li[data-bet="b-price"] [data-bet-activate]').click();
      await page.locator('li[data-bet="b-price"].strat-active').waitFor();
      assert.deepEqual(sent.find(x => x[1] === '/bets/b-price'), ['POST', '/bets/b-price', {status: 'active', revision: 1}]);
      await page.locator('.strat-meta', {hasText: /^v1 · /}).waitFor();
      await page.locator('[data-section="not"] [data-section-edit]').click();
      await page.locator('[data-section-form] textarea').fill('No deep cleans.');
      await page.locator('[data-section-form] button[type="submit"]').click();
      await page.locator('.strat-meta', {hasText: /^v2 · /}).waitFor();
      assert.doesNotMatch(await page.locator('.strat-meta').innerText(), /publish/i);
      assert.equal(await page.locator('[data-strat-publish]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The bets: a statement edited in place, + Bet adds an active one (an empty one goes), × retires one with an Undo,
    // Restore brings one back.
    {
      const {page, errors, sent, st} = await open(browser);
      const price = page.locator('li[data-bet="b-price"] [data-bet-st]');
      await price.waitFor();
      await price.fill('Price at half the market.');
      await price.press('Enter');
      await page.waitForFunction(() => document.querySelector<HTMLInputElement>('li[data-bet="b-price"] [data-bet-st]')?.defaultValue === 'Price at half the market.');
      assert.deepEqual(sent.filter(x => x[1].startsWith('/bets')), [['POST', '/bets/b-price', {statement: 'Price at half the market.', revision: 7}]]);
      // Emptied, it keeps its words; nothing is sent.
      await price.fill('');
      await price.press('Enter');
      assert.equal(await price.inputValue(), 'Price at half the market.');
      await page.locator('[data-bet-add]').click();
      const fresh = page.locator('[data-bet-new] [data-bet-st]');
      assert.equal(await page.evaluate(() => document.activeElement?.closest('[data-bet-new]') !== null), true, 'the new row has the focus');
      await fresh.press('Escape');
      await page.locator('[data-bet-new]').waitFor({state: 'detached'});
      await page.locator('[data-bet-add]').click();
      await fresh.fill('Win offices through managers.');
      await fresh.press('Enter');
      await page.locator('li[data-bet="b-new"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/bets').map(x => x[2]), [{statement: 'Win offices through managers.', status: 'active', revision: 8}]);
      assert.equal(await page.locator('li[data-bet="b-new"] .strat-bet-n').innerText(), '4');
      // × retires it; Undo puts its status back.
      await page.locator('li[data-bet="b-offices"]').hover();
      await page.locator('li[data-bet="b-offices"] [data-bet-x]').click();
      await page.locator('.strat-retired li[data-bet="b-offices"]').waitFor({state: 'attached'});
      assert.deepEqual(sent.filter(x => x[1] === '/bets/b-offices').at(-1)![2], {status: 'retired', revision: 9});
      await page.locator('.toast', {hasText: 'Removed bet'}).getByRole('button', {name: 'Undo'}).click();
      await page.locator('#strat-bets-ol li[data-bet="b-offices"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/bets/b-offices').at(-1)![2], {status: 'active', revision: 10});
      // Restore: a retired bet comes back active while there is room.
      await page.locator('.strat-retired summary').click();
      await page.locator('.strat-retired li[data-bet="b-old"] [data-bet-restore]').click();
      await page.locator('#strat-bets-ol li[data-bet="b-old"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/bets/b-old').at(-1)![2], {status: 'active', revision: 11});
      assert.equal(st.bets.find(b => b.id === 'b-old')!.status, 'active');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Where we play, check (a): a segment with a source shows its figure, marked as an estimate, and its source link out;
    // a segment with no source shows no figure at all, its name only, and the total is not shown.
    {
      const {page, errors} = await open(browser);
      const where = page.locator('#strat-where');
      await where.locator('.strat-map').waitFor();
      const homes = where.locator('[data-seg="segment/homes"]');
      assert.match(await homes.locator('.strat-est').innerText(), /^~\$4B$/, 'the figure is marked as an estimate on the tile');
      assert.equal(await homes.locator('.strat-src a').getAttribute('href'), 'https://homes.example/cleaning-market');
      assert.equal(await homes.locator('.strat-src a').innerText(), 'homes.example', 'the source is named by its host');
      assert.match((await homes.getAttribute('title'))!, /about \$4B a year, an estimate, from homes\.example/);
      // Offices' figure is its selected size, so its source is that size's own link, not its part's (medical's).
      assert.equal(await where.locator('.strat-tm[data-seg="segment/offices"] .strat-src a').count(), 1);
      assert.equal(await where.locator('.strat-tm[data-seg="segment/offices"] .strat-src a').first().getAttribute('href'), 'https://www.offices.example/market-size');
      assert.equal(await where.locator('.strat-cap').innerText(), 'About $7B a year', 'the total shows when every segment has a source');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // The figure is the selected size's: a sourced estimate that was not selected gives its source to nothing.
      const unselected = () => { const m = market(); const homes = m.segments.find(s => s.id === 'segment/homes')!;
        homes.size = est(4 * B, 'https://homes.example/no-source-selected'); homes.size.evidence = [];
        homes.estimates = [homes.size, {...est(4 * B, 'https://other.example/proposed'), status: 'proposed'}]; return m; };
      const {page, errors} = await open(browser, {marketOf: unselected});
      const where = page.locator('#strat-where');
      // One sized segment left is not a map, so its tile is listed plainly: wait for that tile, not the map.
      await where.locator('[data-seg="segment/homes"]').waitFor();
      const homes = where.locator('[data-seg="segment/homes"]');
      assert.equal(await homes.locator('.strat-est, .strat-src').count(), 0, 'the selected size has no source: no figure, no source line');
      assert.doesNotMatch((await homes.getAttribute('title'))!, /other\.example|about/, 'the unselected estimate names no source');
      assert.equal(await where.locator('.strat-map [data-seg="segment/homes"]').count(), 0, 'and the tile is not drawn on the map');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const ghost = () => { const m = market(); m.segments.push(seg('ghost', 'Unsourced segment', null, 2 * B)); return m; };
      const {page, errors} = await open(browser, {marketOf: ghost});
      const where = page.locator('#strat-where');
      await where.locator('.strat-map').waitFor();
      const bare = where.locator('[data-seg="segment/ghost"]');
      assert.equal(await bare.count(), 1, 'the unsourced segment is still listed');
      assert.equal(await bare.locator('.strat-est, .strat-src').count(), 0, 'no dollar figure and no source line for it');
      assert.doesNotMatch(await where.innerText(), /\$2B/, 'its size is not on the map anywhere');
      assert.equal(await where.locator('.strat-map [data-seg="segment/ghost"]').count(), 0, 'it is not drawn on the map');
      assert.equal(await where.locator('.strat-cap').count(), 0, 'the total is not shown while a segment has no source');
      assert.equal(await where.locator('[data-seg="segment/homes"] .strat-est').innerText(), '~$4B', 'sourced segments still show');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Where we play: a click makes a segment Us, then Next, then neither (several Us at once); × removes one, its parts move up;
    // one is added by name. Competitors: × removes one, one is added by its site. Related news: hot first, links out.
    {
      const {page, errors, sent, mk} = await open(browser);
      const where = page.locator('#strat-where');
      await where.locator('.strat-map').waitFor();
      const tile = (id: string) => where.locator(`[data-seg="segment/${id}"]`);
      assert.equal(await where.locator('.strat-hint').count(), 0, 'no helper line: a tile says what it is (aria-pressed)');
      assert.equal(await tile('homes').getAttribute('aria-pressed'), 'true');
      // Opening a source does not target its tile: the link's click is not the tile's (the link itself stays put here).
      await page.evaluate(() => {
        const a = document.querySelector<HTMLAnchorElement>('#strat-where [data-seg="segment/homes"] .strat-src a')!;
        a.addEventListener('click', e => e.preventDefault(), {once: true});
        a.click();
      });
      await page.waitForTimeout(150);
      assert.equal(await tile('homes').getAttribute('aria-pressed'), 'true', 'a source click leaves the tile as it was');
      assert.equal(sent.filter(x => x[1] === '/api/v2/market/segments/target').length, 0, 'and sends no target change');
      assert.match(await tile('homes').innerText(), /^Us\s*Home cleaning\s*~\$4B/);
      assert.equal(await where.locator('.strat-tm-plain [data-seg="segment/moveout"]').count(), 1, 'an unsized segment is a plain tile');
      assert.equal(await where.locator('.strat-cap').innerText(), 'About $7B a year');
      await tile('offices').click();
      await page.waitForFunction(() => document.querySelector('#strat-where [data-seg="segment/offices"]')?.classList.contains('ours'));
      assert.equal(await tile('homes').getAttribute('aria-pressed'), 'true', 'several Us');
      assert.equal(await where.locator('.strat-tm.ours em').count(), 2, 'each Us marked');
      await tile('offices').click();
      await page.waitForFunction(() => document.querySelector('#strat-where [data-seg="segment/offices"]')?.classList.contains('next'));
      await tile('offices').click();
      await page.waitForFunction(() => document.querySelector('#strat-where [data-seg="segment/offices"]')?.getAttribute('aria-pressed') === 'false');
      await tile('homes').focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelector('#strat-where [data-seg="segment/homes"]')?.classList.contains('next'));
      for (let i = 0; i < 100 && sent.filter(x => x[1] === '/api/v2/market/segments/target').length < 4; i++) await page.waitForTimeout(20);
      assert.deepEqual(sent.filter(x => x[1] === '/api/v2/market/segments/target').map(x => [x[2].id, x[2].target]),
        [['segment/offices', 'ours'], ['segment/offices', 'next'], ['segment/offices', 'none'], ['segment/homes', 'next']]);
      await tile('offices').hover();
      await tile('offices').locator('[data-seg-x]').click();
      await tile('offices').waitFor({state: 'detached'});
      await page.waitForFunction(() => document.querySelector('#strat-where [data-seg="segment/medical"]'));
      assert.deepEqual(sent.find(x => x[1] === '/api/v2/market/segments/remove'), ['POST', '/api/v2/market/segments/remove', {id: 'segment/offices'}]);
      assert.equal(mk.segments.find(x => x.id === 'segment/medical')!.parent, null, 'its part moved up');
      const addSeg = where.locator('[data-add="seg"] input[name="name"]');
      await addSeg.fill('Window cleaning');
      await addSeg.press('Enter');
      await tile('window-cleaning').waitFor();
      assert.deepEqual(sent.find(x => x[1] === '/api/v2/market/segments')![2], {name: 'Window cleaning'});
      // A part with its one line, under the segment picked.
      await addSeg.fill('Gutters');
      await where.locator('[data-add="seg"] input[name="summary"]').fill('Gutter cleaning for homes');
      await where.locator('[data-add="seg"] select[name="parent"]').selectOption('segment/homes');
      await where.locator('[data-add="seg"] [type="submit"]').click();
      await tile('gutters').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/api/v2/market/segments').map(x => x[2]).at(-1), {name: 'Gutters', summary: 'Gutter cleaning for homes', parent_id: 'segment/homes'});
      assert.equal(await where.locator('[data-add="seg"] input[name="summary"]').inputValue(), '', 'the row is emptied for the next one');
      // Rename in place: Enter saves, Escape keeps the old name.
      await tile('moveout').hover();
      await tile('moveout').locator('[data-seg-edit]').click();
      const renaming = tile('moveout').locator('[data-seg-rename] input');
      await renaming.fill('Move-out cleaning');
      await renaming.press('Escape');
      await tile('moveout').locator('b').waitFor();
      assert.equal(sent.filter(x => x[1].startsWith('/api/v2/market/entities/')).length, 0, 'Escape sends nothing');
      await tile('moveout').hover();
      await tile('moveout').locator('[data-seg-edit]').click();
      await renaming.fill('Move-out cleaning');
      await renaming.press('Enter');
      await page.waitForFunction(() => document.querySelector('#strat-where [data-seg="segment/moveout"] b')?.textContent === 'Move-out cleaning');
      assert.deepEqual(sent.find(x => x[1].startsWith('/api/v2/market/entities/')), ['POST', '/api/v2/market/entities/segment/moveout', {name: 'Move-out cleaning'}]);
      assert.equal(await tile('moveout').getAttribute('aria-pressed'), 'false', 'renaming did not target the tile');
      const rivals = page.locator('#strat-rivals');
      assert.deepEqual(await rivals.locator('.strat-chip').allInnerTexts(), ['CleanCo', 'Sparkle']);
      assert.equal(await rivals.locator('.strat-chip a').getAttribute('href'), 'https://cleanco.example');
      assert.equal(await rivals.locator('.strat-chip a').getAttribute('target'), '_blank');
      await rivals.locator('[data-rival="company/sparkle"]').hover();
      await rivals.locator('[data-rival="company/sparkle"] [data-rival-x]').click();
      await rivals.locator('[data-rival="company/sparkle"]').waitFor({state: 'detached'});
      const addRival = rivals.locator('[data-add="rival"] input');
      assert.equal(await addRival.getAttribute('placeholder'), 'Add a competitor or its site');
      await addRival.fill('https://www.maidpro.example/austin');
      await addRival.press('Enter');
      await rivals.locator('[data-rival="company/maidpro.example"]').waitFor();
      assert.deepEqual(await rivals.locator('.strat-chip').allInnerTexts(), ['CleanCo', 'maidpro.example']);
      assert.deepEqual(sent.filter(x => x[1].startsWith('/api/v2/market/competitors')).map(x => [x[1], x[2]]),
        [['/api/v2/market/competitors/remove', {id: 'company/sparkle'}], ['/api/v2/market/competitors', {name: 'https://www.maidpro.example/austin'}]]);
      const news = page.locator('#strat-news .strat-posts li');
      assert.equal(await news.count(), 2, 'listening posts only');
      assert.match(await news.first().innerText(), /^Hot\s*CleanCo raises prices 20%\s*news\.example · 18 Oct/);
      assert.equal(await news.first().locator('a').getAttribute('href'), 'https://news.example/cleanco');
      assert.match(await news.nth(1).innerText(), /Anyone know a cleaner in Austin[\s\S]*Reddit · 20 Oct/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A member reads the live version: no fields.
    {
      const {page, errors} = await open(browser, {role: 'member'});
      await page.locator('.strat-summary .strat-text').waitFor();
      assert.equal(await page.locator('.strat-summary .strat-text').innerText(), v1.summary);
      assert.equal(await page.locator('textarea, [data-bet-edit], [data-section-edit], [data-section-add]').count(), 0);
      assert.equal(await page.locator('li[data-bet="b-weekly"]').count(), 0, 'a bet not in the live version is not shown');
      assert.equal(await page.locator('.strat-retired').count(), 0);
      // The market cards read only: no ×, no add rows, no tile to click; no news for one who is not routed any.
      await page.locator('#strat-where .strat-map').waitFor();
      await page.locator('#strat-news .strat-none-s').waitFor();
      assert.equal(await page.locator('.strat-x, .strat-addrow, .strat-tm.pick, .strat-hint').count(), 0);
      assert.deepEqual(await page.locator('#strat-rivals .strat-chip').allInnerTexts(), ['CleanCo', 'Sparkle']);
      assert.deepEqual(await sectionTitles(page), ['Who we serve', 'What we sell, and for how much', "What we know that others don't", "What we won't do", 'Brand']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A phone: tabs on top, one column, no sideways scroll.
    {
      const {page, errors} = await open(browser, {viewport: PHONE});
      await page.locator('li[data-bet="b-price"]').waitFor();
      await page.locator('#strat-where .strat-map').waitFor();
      await page.locator('#strat-news .strat-posts').waitFor();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && $('#main').scrollWidth <= $('#main').clientWidth), 'no horizontal scroll');
      // The title stands where every page's does: #main keeps its 18px phone padding, the header adds none.
      const at = await page.evaluate(() => {
        const main = document.querySelector<HTMLElement>('#main')!, h1 = document.querySelector<HTMLElement>('.rd-strat-head > h1')!;
        return {pad: getComputedStyle(main).paddingTop, gap: Math.round(h1.getBoundingClientRect().top - main.getBoundingClientRect().top)};
      });
      assert.deepEqual(at, {pad: '18px', gap: 18}, 'the title lines up with the other pages');
      // The small marks in a card are 44px targets on a phone (.tap-mark), though they stay small marks.
      for (const sel of ['li[data-bet="b-price"] [data-bet-x]', 'li[data-bet="b-price"] [data-bet-drag]', '#strat-rivals [data-rival-x]']) {
        const hit = await page.locator(sel).first().evaluate((el: Element) => {
          const r = getComputedStyle(el, '::before');
          return {pos: getComputedStyle(el).position, before: r.content !== 'none' && r.position === 'absolute', h: el.getBoundingClientRect().height + 2 * Math.max(0, -parseFloat(r.top))};
        });
        assert.ok(hit.pos !== 'static' && hit.before && hit.h >= 43.5, `${sel}: a 44px target (${JSON.stringify(hit)})`);
      }
      await shot(page, 'phone', ['dark']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A touch phone: every map tile's × shows and removes the segment without retargeting it; a competitor's × target
    // stays clear of its name (a tap on the name's last pixels keeps it) and its removal has an Undo; a bet's × target
    // stays clear of Make active; a bet's pill, Make active and × sit on their own row under the statement.
    {
      const {page, errors, sent} = await open(browser, {viewport: PHONE, touch: true});
      page.context().on('page', p => void p.close());   // a competitor's link opens out; the test stays on this page
      const where = page.locator('#strat-where');
      await where.locator('.strat-map').waitFor();
      assert.ok(await page.evaluate(() => matchMedia('(hover:none)').matches), 'a touch screen');
      const xs = await where.locator('.strat-map .strat-x').evaluateAll(els => els.map(e => (e as HTMLElement).offsetWidth > 0));
      assert.ok(xs.length > 0 && xs.every(Boolean), 'every tile shows its ×');
      // Each × has the tile's corner to itself: nothing else answers inside its 44px target.
      const clash = await where.locator('.strat-map .strat-tm').evaluateAll(tiles => tiles.flatMap(t => {
        const x = t.querySelector('.strat-x'), r = t.getBoundingClientRect();
        if (!x) return [];
        const hits: string[] = [];
        for (let dx = 2; dx < Math.min(44, r.width); dx += 6) for (let dy = 2; dy < Math.min(44, r.height); dy += 6) {
          const el = document.elementFromPoint(r.right - dx, r.top + dy);
          if (el && !x.contains(el) && el.closest('a, button')) hits.push(el.outerHTML.slice(0, 40));
        }
        return hits;
      }));
      assert.deepEqual(clash, [], 'no link or button under a tile\'s × target');
      await where.locator('[data-seg="segment/offices"] [data-seg-x]').tap();
      await where.locator('.strat-map [data-seg="segment/offices"]').waitFor({state: 'detached'});
      for (let i = 0; i < 50 && !sent.some(x => x[1] === '/api/v2/market/segments/remove'); i++) await page.waitForTimeout(20);
      assert.deepEqual(sent.find(x => x[1] === '/api/v2/market/segments/remove'), ['POST', '/api/v2/market/segments/remove', {id: 'segment/offices'}]);
      assert.equal(sent.filter(x => x[1] === '/api/v2/market/segments/target').length, 0, 'removing sent no target change');

      // A competitor: the last pixels of its name are the name, not the ×.
      const rivals = page.locator('#strat-rivals');
      await rivals.locator('.strat-chips').scrollIntoViewIfNeeded();
      const name = rivals.locator('.strat-chip').first().locator('a, span').first();
      const nb = (await name.boundingBox())!;
      assert.equal(await page.evaluate(([x, y]) => !!document.elementFromPoint(x!, y!)?.closest('[data-rival-x]'), [nb.x + nb.width - 2, nb.y + nb.height / 2]), false, 'the name\'s end is not the ×');
      await page.touchscreen.tap(nb.x + nb.width - 3, nb.y + nb.height / 2);
      await page.waitForTimeout(200);
      assert.deepEqual(await rivals.locator('.strat-chip').allInnerTexts(), ['CleanCo', 'Sparkle'], 'a tap on the name keeps the competitor');
      assert.equal(sent.filter(x => x[1] === '/api/v2/market/competitors/remove').length, 0);
      // The next chip's first pixels are that chip, not this one's ×.
      const second = (await rivals.locator('.strat-chip').nth(1).boundingBox())!;
      const atNext = await page.evaluate(([x, y]) => { const e = document.elementFromPoint(x!, y!); return e?.closest('[data-rival]')?.getAttribute('data-rival') || e?.outerHTML.slice(0, 80); }, [second.x + 2, second.y + second.height / 2]);
      assert.equal(atNext, 'company/sparkle', `the next chip starts clear of the ×: ${JSON.stringify(second)}`);
      // Removing one offers Undo, which adds it back.
      await rivals.locator('[data-rival="company/cleanco"] [data-rival-x]').tap();
      await rivals.locator('[data-rival="company/cleanco"]').waitFor({state: 'detached'});
      await page.locator('.toast', {hasText: 'Removed CleanCo'}).getByRole('button', {name: 'Undo'}).click();
      await rivals.locator('.strat-chip', {hasText: 'cleanco'}).or(rivals.locator('.strat-chip', {hasText: 'CleanCo'})).first().waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/api/v2/market/competitors').map(x => x[2]), [{name: 'CleanCo'}], 'Undo adds it back by its name');

      // A proposed bet: its pill, Make active and × on a row of their own under the statement, the statement the
      // card's width; Make active's right edge is Make active, not the ×; it is named for its bet.
      const bet = page.locator('li[data-bet="b-weekly"]');
      await bet.locator('[data-bet-activate]').scrollIntoViewIfNeeded();
      const layout = await bet.evaluate((li: Element) => {
        const st = li.querySelector('.strat-bet-st')!.getBoundingClientRect(), side = li.querySelector('.strat-bet-side')!.getBoundingClientRect();
        const go = li.querySelector<HTMLElement>('[data-bet-activate]')!, g = go.getBoundingClientRect();
        return {under: side.top >= st.bottom - 1, wide: Math.round(st.width), sideH: Math.round(side.height),
          edge: document.elementFromPoint(g.right - 1, g.top + g.height / 2) === go, mid: document.elementFromPoint(g.right - 5, g.top + 4) === go};
      });
      assert.ok(layout.under && layout.wide >= 290 && layout.sideH >= 44, `the side row under the statement: ${JSON.stringify(layout)}`);
      assert.ok(layout.edge && layout.mid, `Make active's right edge is Make active: ${JSON.stringify(layout)}`);
      assert.match(await bet.locator('[data-bet-activate]').getAttribute('aria-label') || '', /^Make bet \d+ active$/, 'named for its bet');
      // An active bet has nothing to decide: no row of its own. Its × is the small mark at the top right, its 44px target
      // inside the bet (clear of the statement, the number and the bets around it); its health dot sits under its number.
      const active = page.locator('li[data-bet="b-offices"]');
      await active.scrollIntoViewIfNeeded();
      const act = await active.evaluate((li: Element) => {
        const box = li.getBoundingClientRect(), x = li.querySelector('[data-bet-x]')!.getBoundingClientRect(), st = li.querySelector('.strat-bet-st')!.getBoundingClientRect();
        const n = li.querySelector('.strat-bet-n')!.getBoundingClientRect(), dot = li.querySelector('.strat-health')!.getBoundingClientRect();
        const cx = x.left + x.width / 2, cy = x.top + x.height / 2;
        // inside the bet top to bottom; on the right it may use the card's padding (nothing there), never past the card
        const card = li.closest('section')!.getBoundingClientRect();
        const inside = cy - 22 >= box.top - 0.5 && cy + 22 <= box.bottom + 0.5 && cx + 22 <= card.right - 1 && cx - 22 >= st.right - 0.5;
        const stEdge = document.elementFromPoint(st.right - 2, st.top + 6)?.closest('[data-bet-x]') == null;
        return {sideRow: getComputedStyle(li.querySelector('.strat-bet-side')!).display, xTop: Math.round(x.top - st.top), inside, stEdge,
          dotUnderNumber: dot.top >= n.bottom - 1 && Math.abs((dot.left + dot.width / 2) - (n.left + n.width / 2)) <= 2};
      });
      assert.deepEqual(act, {sideRow: 'contents', xTop: act.xTop, inside: true, stEdge: true, dotUnderNumber: true}, `an active bet: no side row, × at the top right: ${JSON.stringify(act)}`);
      assert.ok(Math.abs(act.xTop) <= 4, 'the × beside the statement\'s first line');
      assert.notEqual(await bet.locator('.strat-bet-side').evaluate(el => getComputedStyle(el).display), 'contents', 'a proposed bet keeps its row');
      for (const theme of ['dark', 'light']) {
        await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
        await bet.scrollIntoViewIfNeeded();
        await shot(page, 'phone-proposed-bet-' + theme, [theme]);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The header (style guide, "Top bars: one line"): ⋯ last at the far right; Lenses and Versions are in
    // it, not on the bar. Card titles are card titles, not the uppercase label. Every action row is Cancel, then the
    // action, last; Versions closes with ✕, never a "Close" word.
    {
      const {page, errors} = await open(browser);
      await page.locator('li[data-bet="b-price"]').waitFor();
      const order = await page.locator('.rd-strat-head > :is(.ph-tools, .ph-act, .ph-more)').evaluateAll(els => els.filter(e => (e as HTMLElement).offsetWidth).map(e => e.className.split(' ')[0]));
      assert.equal(order.at(-1), 'ph-more', `⋯ is the last control: ${order}`);
      assert.equal(await page.locator('.rd-strat-head > .ph-tools :is(a, button)').count(), 0, 'nothing less than daily on the bar');
      assert.equal(await page.locator('.strat-sub').count(), 0, 'no Lenses link on the bar');
      await page.locator('.rd-strat-head > .ph-more').click();
      const menu = page.locator('.rd-strat-head > .ph-menu');
      assert.equal(await menu.locator('[data-strat-lenses]').getAttribute('href'), '#/strategy/lenses', 'Lenses in ⋯');
      await menu.locator('[data-strat-versions]').click();
      const drawer = page.locator('#strat-versions[open]');
      await drawer.waitFor();
      assert.equal(await drawer.locator('.tmodal-x').getAttribute('aria-label'), 'Close', 'the drawer has its ✕');
      assert.equal(await drawer.getByRole('button', {name: /^Close$/}).filter({hasText: 'Close'}).count(), 0, 'no Close word');
      await page.keyboard.press('Escape');
      await drawer.waitFor({state: 'detached'}).catch(() => drawer.waitFor({state: 'hidden'}));
      const title = await page.locator('#strat-where .strat-k').evaluate((el: Element) => ({size: getComputedStyle(el).fontSize, caps: getComputedStyle(el).textTransform}));
      assert.deepEqual(title, {size: '15px', caps: 'none'}, 'a card title is 15px, in sentence case');
      // Adding a bet is the card's quiet mini button, like a section's Edit.
      assert.match(await page.locator('[data-bet-add]').getAttribute('class') || '', /\bghost\b.*\bstrat-mini\b/);
      // A section's form and the bet form: Cancel, then the action, last.
      await page.locator('[data-section="not"] [data-section-edit]').click();
      assert.deepEqual(await page.locator('[data-section-form] .goal-actions button').allInnerTexts(), ['Cancel', 'Save']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Old Market links land on the Strategy section's Market tab, keeping the query.
    {
      const {page, errors} = await open(browser, {route: '#/market?note=x'});
      await page.waitForFunction(() => location.hash === '#/strategy/market?note=x');
      await page.locator('.strat-tabs a.cur', {hasText: 'Market'}).waitFor();
      assert.equal(await page.locator('#side [data-nav="strategy"]').getAttribute('aria-current'), 'page');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Goals: no Strategy line at the top (Strategy is the tab beside it); the goal editor's Bet picker saves bet_id; the
    // bet's chip opens it on Strategy.
    {
      const {page, errors, sent} = await open(browser, {route: '#/goals'});
      await page.locator('#goal-tree .gt-row').first().waitFor();
      assert.equal(await page.locator('#goal-strategy').count(), 0);
      await page.locator('#goal-tree .gt-row[data-owner="bot:cmo"]:not(.gt-cont) .gt-name').click();
      const panel = page.locator('#goal-panel[open]');
      await panel.locator('[data-gp-edit="g-cmo"]').click();
      const picker = panel.locator('select[name="bet"]');
      assert.equal(await panel.locator('.goal-bet-f > span').innerText(), 'Bet');
      assert.deepEqual(await picker.locator('option').allInnerTexts(), ['None', bets()[0]!.statement, bets()[1]!.statement], 'active bets only');
      await picker.selectOption('b-offices');
      await shot(page, 'goals-picker');
      await panel.locator('button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-panel [data-goal-form]'));
      assert.deepEqual(sent.find(x => x[1] === '/goals/g-cmo'), ['POST', '/goals/g-cmo', {bet_id: 'b-offices'}]);
      const chip = panel.locator('[data-goal-bet="b-offices"] .goal-bet-chip');
      assert.equal(await chip.innerText(), 'Bet 2');
      assert.equal(await chip.getAttribute('href'), '#/strategy?bet=b-offices');
      assert.equal(await panel.locator('[data-why="g-cmo"]').count(), 0, 'the bet is the why');
      await chip.click();
      await page.locator('li[data-bet="b-offices"].strat-flash').waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The team chart: a group's row has add, rename and delete, and no Bet flag, for the owner too.
    {
      const {page, errors} = await open(browser);
      const row = page.locator('#tree li.org-group > .noderow', {hasText: 'Operations'});
      await row.waitFor();
      await row.hover();
      await row.locator('[data-group-rename="ops"]').waitFor();
      assert.equal(await page.locator('[data-group-bet], .org-bet-pop').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A lens's long section is cut to four lines with one Show all; a change the strategy already holds is "Got it",
    // which sets it aside; "No, leave it" rejects; with no strategy yet, Now reads Empty.
    {
      const long = Array.from({length: 8}, (_, i) => `Line ${i + 1} of who we serve.`).join('\n');
      const card = (id: string, payload: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({kind: 'proposal', ref: id, owner_id: 'human:ana', proposal: {id, kind: 'strategy_draft', goal_id: null, kpi_id: null,
        status: 'pending', proposed_by: 'bot:strategy-owner', proposed_at: new Date().toISOString(), reason: '', payload, ...extra}});
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [
        card('p-lens', {source: 'lens', lens: 'blue-ocean', sections: {serve: long}}, {lens_name: 'Blue Ocean'}),
        card('p-same', {source: 'briefing', vision: v1.vision}),
        card('p-rename', {source: 'briefing', sections: {extra: {brand: {title: 'Positioning', body: sections.extra.brand.body}}}})]});
      const lens = page.locator('#goal-needs [data-proposal="p-lens"]'), same = page.locator('#goal-needs [data-proposal="p-same"]');
      // A section renamed with its text unchanged is a change, not "already says this".
      const rename = page.locator('#goal-needs [data-proposal="p-rename"]');
      assert.equal((await rename.locator('.strat-prop-list').innerText()).replace(/\s+/g, ' ').trim(), 'Positioning · Title Now Brand Proposed Positioning');
      assert.deepEqual(await rename.locator('.kpi-prop-do button').allInnerTexts(), ['No, leave it', 'Add to strategy']);
      await lens.waitFor();
      assert.match(await lens.locator('.kpi-prop-head').innerText(), /^Strategy Owner suggests a change to your strategy from the Blue Ocean lens/);
      const values = lens.locator('.prop-np-v');
      const tall = () => values.evaluateAll(els => els.map(el => el.scrollHeight > el.clientHeight + 1));
      // The draft's own text is short, so only Proposed is cut; both are clamped to four lines until Show all.
      assert.deepEqual(await tall(), [false, true], 'the long Proposed is cut to four lines');
      assert.deepEqual(await values.evaluateAll(els => els.map(el => getComputedStyle(el).webkitLineClamp)), ['4', '4']);
      await lens.locator('[data-np-all]').click();
      assert.deepEqual(await tall(), [false, false], 'one Show all opens both');
      assert.match(await same.locator('.kpi-prop-head').innerText(), /from this week's market briefing/);
      assert.equal(await same.locator('.kpi-prop-detail').innerText(), 'Your strategy already says this.');
      assert.deepEqual(await same.locator('.kpi-prop-do button').allInnerTexts(), ['Got it']);
      assert.deepEqual(await lens.locator('.kpi-prop-do button').allInnerTexts(), ['No, leave it', 'Add to strategy']);
      await same.locator('[data-prop-yes]').click();
      await same.waitFor({state: 'detached'});
      assert.deepEqual(sent.find(x => x[1] === '/proposals/p-same'), ['POST', '/proposals/p-same', {decision: 'reject'}]);
      assert.deepEqual(errors, []);
      await page.close();
      const no = await open(browser, {route: '#/goals', needs: [card('p-lens', {source: 'lens', lens: 'blue-ocean', sections: {serve: long}}, {lens_name: 'Blue Ocean'})]});
      const left = no.page.locator('#goal-needs [data-proposal="p-lens"]');
      await left.getByRole('button', {name: 'No, leave it'}).click();
      await left.waitFor({state: 'detached'});
      assert.deepEqual(no.sent.find(x => x[1] === '/proposals/p-lens'), ['POST', '/proposals/p-lens', {decision: 'reject'}]);
      assert.deepEqual(no.errors, []);
      await no.page.close();
      const fresh = await open(browser, {route: '#/goals', fresh: true, needs: [card('p-new', {vision: 'Every home in Austin.'})]});
      const first = fresh.page.locator('#goal-needs [data-proposal="p-new"]');
      await first.waitFor();
      assert.equal(await first.locator('.kpi-prop-line').count(), 0);
      assert.equal((await first.locator('.strat-prop-list').innerText()).replace(/\s+/g, ' ').trim(), 'Vision Now Empty Proposed Every home in Austin.');
      assert.deepEqual(fresh.errors, []);
      await fresh.page.close();
    }
    // A goal serving a bet this person can't see (retired) keeps it when saved for something else.
    {
      const old = {id: 'g-old', title: 'Win the office park', owner_id: 'bot:cmo', parent_id: null, status: 'green', status_source: 'auto', rank: 1, kpis: [], why: '', bet_id: 'b-gone'};
      const {page, errors, sent} = await open(browser, {route: '#/goals', more: [old]});
      await page.locator('#goal-tree .gt-row').first().waitFor();
      await page.locator('#goal-tree .gt-row[data-owner="bot:cmo"]:not(.gt-cont) .gt-name').click();
      const panel = page.locator('#goal-panel[open]');
      await panel.locator('[data-gp-edit="g-old"]').click();
      const picker = panel.locator('select[name="bet"]');
      assert.equal(await picker.inputValue(), 'b-gone');
      assert.equal(await picker.locator('option:checked').innerText(), 'Retired');
      await panel.locator('input[name="title"]').fill('Win both office parks');
      await panel.locator('button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-panel [data-goal-form]'));
      assert.deepEqual(sent.find(x => x[1] === '/goals/g-old'), ['POST', '/goals/g-old', {title: 'Win both office parks'}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Moving a bet by its number: arrow keys, or a drag. Each changed rank is saved with the revision the last save
    // answered.
    {
      const {page, errors, sent} = await open(browser);
      await page.locator('li[data-bet="b-offices"] [data-bet-drag]').focus();
      await page.keyboard.press('ArrowUp');
      await page.waitForFunction(() => document.querySelector<HTMLElement>('#strat-bets-ol li[data-bet]')?.dataset.bet === 'b-offices');
      assert.deepEqual(sent.filter(x => x[1].startsWith('/bets/')), [['POST', '/bets/b-offices', {rank: 0, revision: 7}],
        ['POST', '/bets/b-price', {rank: 1, revision: 8}]]);
      const order = () => page.locator('#strat-bets-ol li[data-bet]').evaluateAll(lis => lis.map(li => li.dataset.bet));
      // Dragged from first to last.
      const from = (await page.locator('li[data-bet="b-offices"] [data-bet-drag]').boundingBox())!;
      const to = (await page.locator('li[data-bet="b-weekly"]').boundingBox())!;
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + from.width / 2, to.y + to.height - 2, {steps: 6});
      await page.mouse.up();
      await page.waitForFunction(() => [...(document.querySelectorAll<HTMLElement>('#strat-bets-ol li[data-bet]') as unknown as HTMLElement[])].at(-1)?.dataset.bet === 'b-offices' && !document.querySelector('.strat-bet.dragging'));
      await page.waitForFunction(() => document.querySelectorAll('#strat-bets-ol li[data-bet]').length === 3);
      assert.deepEqual(await order(), ['b-price', 'b-weekly', 'b-offices']);
      for (let i = 0; i < 100 && sent.filter(x => x[1].startsWith('/bets/')).length < 5; i++) await page.waitForTimeout(20);
      assert.deepEqual(sent.filter(x => x[1].startsWith('/bets/')).slice(2).map(x => [x[1], x[2].rank]),
        [['/bets/b-price', 0], ['/bets/b-weekly', 1], ['/bets/b-offices', 2]]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // After a save retired or reworded bets: each of their goals is kept, retired or moved, sent together by one button
    // that counts what changes (no Reject); a reworded bet shows its wording before and now (Dev 533).
    {
      const realign = (closed: string[] = [], old: boolean = false) => ({id: 'p-realign', kind: 'strategy_realign', goal_id: null, kpi_id: null, status: 'pending', proposed_by: 'keeper',
        proposed_at: new Date().toISOString(), reason: 'strategy v2 retired or rewrote the bets these goals serve', closed_goals: closed,
        payload: {n: 2, goals: [
          {goal_id: 'g-top', title: 'Margin per job ≥ 30%', owner_id: 'company', bet_id: 'b-price', change: 'retired', statement: bets()[0]!.statement},
          {goal_id: 'g-cmo', title: 'Sign 5 property managers', owner_id: 'bot:cmo', bet_id: 'b-offices', change: 'rewritten', statement: 'Sell to offices through managers and agents.',
           ...(old ? {} : {was: bets()[1]!.statement})}],
          bets: [{id: 'b-offices', number: 1, statement: 'Sell to offices through managers and agents.'}, {id: 'b-weekly', number: 2, statement: bets()[2]!.statement}]}});
      const proposal = realign();
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner_id: 'human:ana', proposal}]});
      const card = page.locator('#goal-needs [data-proposal="p-realign"]');
      await card.waitFor();
      assert.match(await card.locator('.kpi-prop-head').innerText(), /^Strategy v2 changed bets that 2 goals serve/);
      assert.equal(await card.locator('.kpi-prop-detail').innerText(), 'Choose what happens to each goal. Nothing changes until you press the button.');
      const top = card.locator('[data-realign-goal="g-top"]'), cmo = card.locator('[data-realign-goal="g-cmo"]'), yes = card.locator('[data-prop-yes]');
      assert.equal(await top.inputValue(), 'keep', 'Keep by default');
      assert.deepEqual(await top.locator('option').allInnerTexts(), ['Keep as it is', 'Retire goal', 'Move to Bet 1: Sell to offices through managers and agents.', 'Move to Bet 2: ' + bets()[2]!.statement]);
      const rows = card.locator('.strat-realign-goal');
      assert.match(await rows.first().innerText(), /Margin per job ≥ 30%[\s\S]*Its bet was retired: Price at half/);
      assert.ok((await rows.nth(1).innerText()).replace(/\s+/g, ' ').includes(`Before v2 ${bets()[1]!.statement} Now Sell to offices through managers and agents.`));
      assert.deepEqual(await card.locator('.kpi-prop-do button').allInnerTexts(), ['Keep all 2 goals as they are'], 'one button, no Reject');
      await top.selectOption('retire');
      assert.equal(await yes.innerText(), 'Retire 1 goal');
      await cmo.selectOption('b-weekly');
      assert.equal(await yes.innerText(), 'Retire 1 goal, move 1');
      await top.selectOption('keep');
      assert.equal(await yes.innerText(), 'Move 1 goal');
      await top.selectOption('retire');
      await cmo.selectOption('b-offices');
      await shot(page, 'proposal-realign');
      await page.setViewportSize(PHONE);
      await card.scrollIntoViewIfNeeded();
      await shot(page, 'proposal-realign-phone', ['dark']);
      await page.setViewportSize(DESKTOP);
      await yes.click();
      await card.waitFor({state: 'detached'});
      assert.deepEqual(sent.find(x => x[1] === '/proposals/p-realign'), ['POST', '/proposals/p-realign', {decision: 'confirm', moves: {'g-top': 'retire', 'g-cmo': 'b-offices'}}]);
      assert.deepEqual(errors, []);
      await page.close();
      // A goal closed since reads Already closed and is not counted; every goal closed is "Got it", which changes nothing.
      for (const [closed, button, line] of [[['g-top'], 'Keep this goal as it is', /^Choose what happens/], [['g-top', 'g-cmo'], 'Got it', /^These goals were closed since; nothing to decide\.$/]] as [string[], string, RegExp][]) {
        const again = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner_id: 'human:ana', proposal: realign(closed)}]});
        const c = again.page.locator('#goal-needs [data-proposal="p-realign"]');
        await c.waitFor();
        assert.match(await c.locator('.strat-realign-goal').first().innerText(), /Already closed/);
        assert.equal(await c.locator('[data-realign-goal="g-top"]').count(), 0);
        assert.match(await c.locator('.kpi-prop-detail').innerText(), line);
        assert.deepEqual(await c.locator('.kpi-prop-do button').allInnerTexts(), [button]);
        await c.locator('[data-prop-yes]').click();
        await c.waitFor({state: 'detached'});
        assert.deepEqual(again.sent.find(x => x[1] === '/proposals/p-realign')![2], {decision: 'confirm', moves: closed.length === 2 ? {} : {'g-cmo': 'keep'}});
        assert.deepEqual(again.errors, []);
        await again.page.close();
      }
      // A card filed before the earlier wording was carried shows the reworded bet's Now only, with no empty Before row.
      {
        const old = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner_id: 'human:ana', proposal: realign([], true)}]});
        const row = old.page.locator('#goal-needs [data-proposal="p-realign"] .strat-realign-goal').nth(1);
        await row.waitFor();
        assert.deepEqual(await row.locator('.prop-np-k').allInnerTexts(), ['Now']);
        assert.deepEqual(old.errors, []);
        await old.page.close();
      }
    }
    console.log('strategy: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
