// The Strategy section (ui/strategy-page.js) against a stand-in for GET /api/v2/strategy, the market cards' reads and
// all their writes (fixtures only, the Acme example): the owner reads the draft as cards (the summary on top, no vision;
// Where we play beside the bets; Competitors beside Related news; the four fixed sections and an extra one), edits the
// summary and each bet's statement in place with the draft's revision, adds a bet with + Bet (active), removes one with
// × and restores it, moves one by its number (arrow keys or a drag), and is told when the draft changed elsewhere (409);
// a click on a segment makes it Us, then Next, then neither, × removes one and one can be added by name; competitors are
// removed and added by name or site; Publish stays off with its reason until one bet is active and What we won't do is
// written; a member reads only the published version; old #/market links land on the Market tab keeping their query;
// the Goals page's Bet picker saves bet_id and its chip opens the bet; a team group's row has no Bet flag.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots (desktop in both themes, and a phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');

const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page, name, themes = ['dark', 'light']) => {
  if (!shots) return;
  for (const theme of themes) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.screenshot({animations: 'disabled', path: path.join(shots, `strategy-${name}-${theme}.png`)});
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
};
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('coo', 'Assistant', ''), bot('botops', 'BotOps', ''), bot('librarian', 'Librarian', ''),
  bot('goal-manager', 'Strategy Bot', 'p:ana', {reports_to: ''}), bot('cmo', 'Head of Marketing', 'b:goal-manager', {reports_to: ''})];
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
const seg = (id, name, parent, usd, over = {}) => ({id: 'segment/' + id, name, parent: parent ? 'segment/' + parent : null, depth: parent ? 2 : 1,
  summary: '', size: null, rolled_usd: usd ? {spend_usd: usd, low_usd: usd * 0.7, high_usd: usd * 1.3} : null, estimates: [], players: [],
  self: false, next: false, ...over});
const market = () => ({definition: {root: 'segment/cleaning'}, segments: [seg('homes', 'Home cleaning', null, 4 * B, {self: true}),
  seg('offices', 'Office cleaning', null, 3 * B), seg('medical', 'Medical offices', 'offices', 1 * B), seg('moveout', 'Move-out cleans', null, 0)],
  rivals: [{id: 'company/cleanco', name: 'CleanCo', url: 'https://cleanco.example'}, {id: 'company/sparkle', name: 'Sparkle', url: ''}],
  recent: [{item_id: 'li-1', source: 'reddit', url: 'https://reddit.example/r/austin/1', author: 'kim', published_at: '2026-10-20T08:00:00Z',
    text: 'Anyone know a cleaner in Austin that does weekly?', destinations: ['sales'], at: '2026-10-20T09:00:00Z'}],
  insights: [{id: 'mi-1', kind: 'other', about: 'Listening: hot', claim: 'CleanCo raises prices 20%', source_url: 'https://news.example/cleanco',
    status: 'new', reported_at: '2026-10-18T10:00:00Z'}, {id: 'mi-2', kind: 'other', about: 'Pricing', claim: 'Not a listening post',
    source_url: 'https://x.example', status: 'new', reported_at: '2026-10-19T10:00:00Z'}]});
const v1 = {id: 'v-1', n: 1, status: 'published', guide: 'default',
  summary: 'Acme cleans homes and small offices in Austin at about half the usual price. Bots handle booking, scheduling and billing, so the price holds.',
  vision: 'Every home in Austin cleaned by Acme.', sections, bets: bets().filter(b => b.id !== 'b-weekly'), note: 'First version', revision: 7,
  updated_at: '2026-10-24T09:00:00Z', updated_by: 'human:ana', published: '2026-10-24T09:00:00Z', published_by: 'human:ana'};

async function open(browser, {role = 'owner', route = '#/strategy', viewport = DESKTOP, fresh = false, needs = [], more = []} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', locale: 'en-GB'});  // dates read "24 Oct"
  const errors = [], sent = [];
  page.on('pageerror', e => errors.push(e.message));
  // fresh: nothing published yet, one proposed bet with no Rules out, and What we won't do unwritten.
  const st = fresh
    ? {draft: {id: 'v-d', n: null, status: 'draft', guide: 'default', summary: 'Acme cleans homes in Austin at half the price.', vision: '',
        sections: {serve: 'Busy households.', extra: {}}, bets: [], note: '', revision: 1}, current: null,
      bets: [{id: 'b-price', statement: 'Price at half the market.', why: '', rules_out: '', wrong_if: null, cites: [], status: 'proposed', rank: 0}]}
    : {draft: {...v1, id: 'v-d', n: null, status: 'draft', sections: {...sections, extra: {...sections.extra}}, revision: 7, published: null, published_by: null},
      current: v1, bets: bets()};
  const goals = [
    {id: 'g-top', title: 'Margin per job ≥ 30%', owner_id: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: [], why: '', bet_id: 'b-price'},
    {id: 'g-cmo', title: 'Sign 5 property managers', owner_id: 'bot:cmo', parent_id: 'g-top', status: 'red', status_source: 'auto', rank: 0, kpis: [], why: '', bet_id: null},
    ...more];
  const groups = [{id: 'ops', name: 'Operations', parent: '', bet_id: null}];
  const mk = market();
  const strategy = () => ({...(role === 'owner' ? {draft: st.draft} : {}), current: st.current, bets: role === 'owner' ? st.bets : st.current?.bets || [],
    label: 'Bets', label_one: 'Bet', may_edit: role === 'owner',
    website: 'https://acme.example', goals_by_bet: {'b-price': [{id: 'g-top', title: 'Margin per job ≥ 30%', owner_id: 'company', status: 'green'},
      {id: 'g-jobs', title: '400 jobs/week', owner_id: 'bot:cmo', status: 'yellow'}], 'b-offices': [{id: 'g-cmo', title: 'Sign 5 property managers', owner_id: 'bot:cmo', status: 'red'}]},
    // goals_by_bet is the first 20 a bet has; this counts all of its live ones.
    live_by_bet: {'b-price': 21, 'b-offices': 1},
    health_by_bet: {'b-price': 'green', 'b-offices': 'red', 'b-weekly': 'gray'}});
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
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
      const body = req.postDataJSON();
      sent.push([method, p.slice('/api/v2/strategy'.length), body]);
      if (role !== 'owner') return json({error: {code: 'forbidden', detail: 'Only the owner'}}, 403);
      if (p === '/api/v2/strategy/draft') {
        if (body.revision !== st.draft.revision) return json({error: {code: 'conflict', detail: 'The draft changed', revision: st.draft.revision}}, 409);
        // Merges sections, as the server does; an empty one clears it, extra ones included.
        const {revision, ...change} = body, now = st.draft.sections;
        const merged = {...now, ...(change.sections || {}), extra: {...now.extra, ...(change.sections?.extra || {})}};
        for (const k of Object.keys(merged)) if (merged[k] === '') delete merged[k];
        for (const k of Object.keys(merged.extra)) if (merged.extra[k] === '') delete merged.extra[k];
        st.draft = {...st.draft, ...change, sections: merged, revision: revision + 1}; return json(st.draft);
      }
      if (p === '/api/v2/strategy/publish') {
        const n = (st.current?.n || 0) + 1;
        st.current = {...st.draft, n, status: 'published', note: body.note, bets: st.bets.filter(b => b.status !== 'retired').map(b => ({...b})),
          published: new Date().toISOString(), published_by: 'human:ana'};
        st.draft = {...st.draft, revision: st.draft.revision + 1};
        return json(st.current);
      }
      const one = p.match(/^\/api\/v2\/strategy\/bets\/([^/]+)$/);
      // The server's rule (strategy._wrong_if): a sentence wrong-if has a Check by.
      if (body.wrong_if?.kind === 'text' && !body.wrong_if.check_by) return json({error: {code: 'lint', detail: 'a wrong-if in words needs a check_by date: the day to look again'}}, 422);
      if (one) { const c = st.bets.find(x => x.id === one[1]); Object.assign(c, body); return json({bet: c, revision: ++st.draft.revision}); }
      if (p === '/api/v2/strategy/bets') { const c = {id: 'b-new', since_n: null, until_n: null, rank: st.bets.length, status: 'proposed', why: '', rules_out: '', wrong_if: null, cites: [], ...body}; st.bets.push(c); return json({bet: c, revision: ++st.draft.revision}); }
    }
    if (p === '/api/v2/strategy/versions') return json([{n: 1, note: 'First version', published: v1.published, published_by: 'human:ana'}]);
    if (p === '/api/v2/strategy/versions/1') return json(v1);
    if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: needs});
    const decide = p.match(/^\/api\/v2\/proposals\/([^/]+)\/decide$/);
    if (decide) { const body = req.postDataJSON(); sent.push(['POST', '/proposals/' + decide[1], body]); needs.splice(0); return json({proposal: {id: decide[1], status: 'confirmed'}}); }
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    const goal = p.match(/^\/api\/v2\/goals\/([^/]+)$/);
    if (goal && method === 'POST') { const body = req.postDataJSON(); sent.push(['POST', '/goals/' + goal[1], body]); Object.assign(goals.find(g => g.id === goal[1]), body); return json({goal: goals.find(g => g.id === goal[1])}); }
    const group = p.match(/^\/api\/v2\/groups\/([^/]+)$/);
    if (group && method === 'PATCH') { const body = req.postDataJSON(); sent.push(['PATCH', '/groups/' + group[1], body]); Object.assign(groups.find(g => g.id === group[1]), body); return json({group: groups.find(g => g.id === group[1])}); }
    // The market cards: the spend tree, the competitors, and what Listening found.
    if (p.startsWith('/api/v2/market/') && method !== 'GET') {
      const body = req.postDataJSON();
      sent.push([method, p, body]);
      if (role !== 'owner') return json({error: {code: 'forbidden', detail: 'Only the owner'}}, 403);
      if (p === '/api/v2/market/segments/target') {
        const g = mk.segments.find(x => x.id === body.id);
        g.self = body.target === 'ours'; g.next = body.target === 'next';
        return json({id: body.id, ours: body.target === 'ours', next: body.target === 'next'});
      }
      if (p === '/api/v2/market/segments/remove') {
        const g = mk.segments.find(x => x.id === body.id);
        mk.segments = mk.segments.filter(x => x !== g).map(x => (x.parent === g.id ? {...x, parent: g.parent, depth: 1} : x));
        return json({id: body.id, removed: true});
      }
      if (p === '/api/v2/market/segments') {
        const made = seg(body.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), body.name, null, 0);
        if (mk.segments.some(x => x.id === made.id)) return json({error: {code: 'exists', detail: 'exists'}}, 409);
        mk.segments.push(made); return json({segment: made});
      }
      if (p === '/api/v2/market/competitors') {
        const host = /\./.test(body.name) ? body.name.replace(/^https?:\/\/(www\.)?/, '').split('/')[0].toLowerCase() : '';
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
  return {page, errors, sent, st, mk};
}
const sectionTitles = page => page.locator('[data-section] .strat-sec-head .strat-k').allInnerTexts();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // The owner reads the draft as cards: the summary on top (no vision), the bets as statements with their goals and
    // health, the four sections in their order and the extra one.
    {
      const {page, errors, sent, st} = await open(browser);
      const summary = page.locator('[data-strat-field="summary"]');
      await summary.waitFor();
      assert.equal(await page.locator('#main h1').innerText(), 'Strategy');
      assert.equal(await page.locator('.strat-tabs a.cur').innerText(), 'Strategy');
      assert.equal(await page.locator('#side [data-nav="strategy"]').getAttribute('aria-current'), 'page');
      assert.match(await page.locator('.strat-meta').innerText(), /^v1 · published \d+ Oct( \d{4})? by Ana · Draft differs from v1$/);
      assert.equal(await page.locator('[data-strat-framework], .strat-fw').count(), 0, 'no framework picker');
      assert.equal(await page.locator('[data-strat-field="vision"], .strat-toc').count(), 0, 'one line on top: no vision, no contents');
      assert.doesNotMatch(await page.locator('#strat-body').innerText(), /Every home in Austin|Rules out|Wrong if|Watching/);
      assert.equal(await summary.getAttribute('placeholder'), 'Your strategy in one sentence');
      assert.equal(await page.locator('#strat-bets-h').innerText(), 'BETS');
      assert.deepEqual(await page.locator('.strat-memo .strat-card-head .strat-k').allInnerTexts(), ['WHERE WE PLAY', 'BETS', 'COMPETITORS', 'RELATED NEWS']);
      const first = page.locator('li[data-bet="b-price"]');
      assert.equal(await first.locator('[data-bet-st]').inputValue(), bets()[0].statement);
      assert.deepEqual(await first.locator('.strat-goals .gdot').evaluateAll(dots => dots.map(d => [...d.classList].find(c => c !== 'gdot'))), ['green', 'yellow']);
      assert.equal(await first.locator('.strat-goals a').first().getAttribute('href'), '#/goals/g-top');
      assert.equal(await first.locator('[data-bet-health]').getAttribute('data-bet-health'), 'green');
      assert.equal(await page.locator('li[data-bet="b-offices"] [data-bet-health]').getAttribute('data-bet-health'), 'red');
      assert.equal(await page.locator('li[data-bet="b-weekly"] .strat-pill').innerText(), 'Proposed');
      assert.equal(await page.locator('.strat-retired li[data-bet="b-old"]').count(), 1, 'retired bets sit apart');
      assert.deepEqual(await sectionTitles(page), ['WHO WE SERVE', 'WHAT WE SELL, AND FOR HOW MUCH', "WHAT WE KNOW THAT OTHERS DON'T", "WHAT WE WON'T DO", 'BRAND']);
      assert.equal(await page.locator('[data-section="serve"] .strat-md strong').innerText(), 'small offices', 'sections are markdown');
      assert.equal(await page.locator('[data-bet-add]').getAttribute('aria-label'), 'Add Bet');
      assert.equal(await page.locator('[data-bet-add]').innerText(), '+ Bet');
      assert.equal(await page.locator('[data-strat-publish]').isEnabled(), true, 'the Acme draft may be published');
      await page.locator('#strat-where .strat-map').waitFor();
      await page.locator('#strat-news .strat-posts').waitFor();
      await shot(page, 'desktop');
      // The summary saves on Enter, as a partial draft with its revision.
      await summary.fill('Acme cleans homes at half the price.');
      await summary.press('Enter');
      await page.waitForFunction(() => document.activeElement?.dataset?.stratField !== 'summary');
      await page.waitForFunction(() => document.querySelector('[data-strat-publish]'));
      assert.deepEqual(sent.find(x => x[1] === '/draft'), ['PUT', '/draft', {summary: 'Acme cleans homes at half the price.', revision: 7}]);
      // A section saves alone: the server merges sections.
      await page.locator('[data-section="sell"] [data-section-edit]').click();
      await page.locator('[data-section-form] textarea').fill('Weekly cleaning, $79 a visit.');
      await page.locator('[data-section-form] button[type="submit"]').click();
      await page.locator('[data-section="sell"] .strat-md', {hasText: '$79 a visit.'}).waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/draft').at(-1)[2], {sections: {sell: 'Weekly cleaning, $79 a visit.'}, revision: 8});
      // Add a section: it goes under extra, after the others; renaming keeps its key; removing clears it.
      await page.locator('[data-section-add]').click();
      await page.locator('[data-section-new] input[name="title"]').fill('Sales channels');
      await page.locator('[data-section-new] textarea').fill('Property managers, then word of mouth.');
      await page.locator('[data-section-new] button[type="submit"]').click();
      await page.locator('[data-section="sales-channels"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/draft').at(-1)[2], {sections: {extra: {'sales-channels': {title: 'Sales channels', body: 'Property managers, then word of mouth.'}}}, revision: 9});
      assert.deepEqual((await sectionTitles(page)).slice(3), ["WHAT WE WON'T DO", 'BRAND', 'SALES CHANNELS']);
      await page.locator('[data-section="sales-channels"] [data-section-edit]').click();
      await page.locator('[data-section-form] input[name="title"]').fill('Channels we use');
      await page.locator('[data-section-form] button[type="submit"]').click();
      await page.locator('[data-section="sales-channels"] .strat-k', {hasText: 'Channels we use'}).waitFor();
      await page.locator('[data-section="brand"] [data-section-edit]').click();
      page.once('dialog', d => d.accept());
      await page.locator('[data-section-remove]').click();
      await page.locator('[data-section="brand"]').waitFor({state: 'detached'});
      assert.deepEqual(sent.filter(x => x[1] === '/draft').at(-1)[2], {sections: {extra: {brand: ''}}, revision: 11});
      // Changed elsewhere: the draft's revision moved on, so the save is refused and the page reads it again.
      st.draft = {...st.draft, summary: 'Written in another tab.', revision: 40};
      await summary.fill('Mine.');
      await summary.press('Enter');
      await page.locator('.toast', {hasText: 'Changed elsewhere, reloaded'}).waitFor();
      await page.waitForFunction(() => document.querySelector('[data-strat-field="summary"]')?.value === 'Written in another tab.');
      assert.equal(sent.filter(x => x[1] === '/draft').at(-1)[2].revision, 12);
      // Publish asks for one line, then posts it with the revision.
      await page.locator('[data-strat-publish]').click();
      const form = page.locator('[data-strat-publish-form]');
      await form.locator('input[name="note"]').fill('Weekly only, proposed');
      await form.locator('button[type="submit"]').click();
      await form.waitFor({state: 'detached'});
      assert.deepEqual(sent.find(x => x[1] === '/publish'), ['POST', '/publish', {note: 'Weekly only, proposed', revision: 40}]);
      await page.waitForFunction(() => !document.querySelector('[data-strat-status]'));
      assert.match(await page.locator('.strat-meta').innerText(), /^v2 · published/);
      // Versions: the list, and one opens read-only. No compare.
      await page.locator('[data-strat-versions]').click();
      const drawer = page.locator('#strat-versions[open]');
      await drawer.locator('[data-sv-open="1"]').click();
      await drawer.locator('.strat-sv-sec').first().waitFor();
      assert.match(await drawer.innerText(), /v1 · First version[\s\S]*Acme cleans homes[\s\S]*WHAT WE WON'T DO[\s\S]*BRAND/);
      assert.doesNotMatch(await drawer.innerText(), /Every home in Austin/, 'no vision in a version either');
      assert.equal(await drawer.locator('select, textarea, [data-sv-compare]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Nothing publishable yet: Publish is off with its reason; the Proposed pill makes a bet active, Rules out and
    // Wrong if or not.
    {
      const {page, errors, sent} = await open(browser, {fresh: true});
      await page.locator('li[data-bet="b-price"]').waitFor();
      assert.equal(await page.locator('[data-strat-status]').innerText(), 'Not published');
      const publish = page.locator('[data-strat-publish]');
      assert.equal(await publish.isDisabled(), true);
      // Why it is off is a (?) beside Publish, not a line of helper text.
      assert.equal(await page.locator('[data-strat-why-not]').getAttribute('aria-label'), 'Needs an active bet');
      assert.equal(await page.locator('[data-strat-why-not]').innerText(), '?');
      assert.doesNotMatch(await page.locator('#main').innerText(), /Needs an active bet/, 'no helper line');
      await page.locator('[data-strat-why-not]').focus();
      assert.equal(await page.locator('[data-strat-why-not] [role="tooltip"]').isVisible(), true, 'the words show on focus');
      const pop = await page.locator('[data-strat-why-not] [role="tooltip"]').boundingBox();
      assert.ok(pop.y >= 0 && pop.x >= 0 && pop.x + pop.width <= DESKTOP.width, 'the words open on screen');
      await shot(page, 'publish-off', ['dark']);
      await page.setViewportSize(PHONE);
      await page.locator('[data-strat-why-not]').focus();
      const phonePop = await page.locator('[data-strat-why-not] [role="tooltip"]').boundingBox();
      assert.ok(phonePop.x >= 0 && phonePop.x + phonePop.width <= PHONE.width, 'the words open on a phone screen');
      await shot(page, 'publish-off-phone', ['dark']);
      await page.setViewportSize(DESKTOP);
      await page.locator('li[data-bet="b-price"] [data-bet-activate]').click();
      await page.locator('li[data-bet="b-price"].strat-active').waitFor();
      assert.deepEqual(sent.find(x => x[1] === '/bets/b-price'), ['POST', '/bets/b-price', {status: 'active', revision: 1}]);
      await page.locator(`[data-strat-why-not][aria-label="Needs What we won't do"]`).waitFor();
      assert.equal(await publish.isDisabled(), true);
      await page.locator('[data-section="not"] [data-section-edit]').click();
      await page.locator('[data-section-form] textarea').fill('No deep cleans.');
      await page.locator('[data-section-form] button[type="submit"]').click();
      await page.locator('[data-strat-publish]:not([disabled])').waitFor();
      assert.equal(await page.locator('[data-strat-why-not]').count(), 0);
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
      await page.waitForFunction(() => document.querySelector('li[data-bet="b-price"] [data-bet-st]')?.defaultValue === 'Price at half the market.');
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
      assert.deepEqual(sent.filter(x => x[1] === '/bets/b-offices').at(-1)[2], {status: 'retired', revision: 9});
      await page.locator('.toast', {hasText: 'Removed bet'}).getByRole('button', {name: 'Undo'}).click();
      await page.locator('#strat-bets-ol li[data-bet="b-offices"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/bets/b-offices').at(-1)[2], {status: 'active', revision: 10});
      // Restore: a retired bet comes back active while there is room.
      await page.locator('.strat-retired summary').click();
      await page.locator('.strat-retired li[data-bet="b-old"] [data-bet-restore]').click();
      await page.locator('#strat-bets-ol li[data-bet="b-old"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/bets/b-old').at(-1)[2], {status: 'active', revision: 11});
      assert.equal(st.bets.find(b => b.id === 'b-old').status, 'active');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Where we play: a click makes a segment Us, then Next, then neither (several Us at once); × removes one, its parts move up;
    // one is added by name. Competitors: × removes one, one is added by its site. Related news: hot first, links out.
    {
      const {page, errors, sent, mk} = await open(browser);
      const where = page.locator('#strat-where');
      await where.locator('.strat-map').waitFor();
      const tile = id => where.locator(`[data-seg="segment/${id}"]`);
      assert.equal(await where.locator('.strat-hint').innerText(), 'Click to target');
      assert.equal(await tile('homes').getAttribute('aria-pressed'), 'true');
      assert.match(await tile('homes').innerText(), /^Us\s*Home cleaning\s*\$4B/);
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
      assert.equal(mk.segments.find(x => x.id === 'segment/medical').parent, null, 'its part moved up');
      const addSeg = where.locator('[data-add="seg"] input');
      await addSeg.fill('Window cleaning');
      await addSeg.press('Enter');
      await tile('window-cleaning').waitFor();
      assert.deepEqual(sent.find(x => x[1] === '/api/v2/market/segments')[2], {name: 'Window cleaning'});
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
    // A member reads the published version only: no draft, no status, no fields, no Publish.
    {
      const {page, errors} = await open(browser, {role: 'member'});
      await page.locator('.strat-summary .strat-text').waitFor();
      assert.equal(await page.locator('.strat-summary .strat-text').innerText(), v1.summary);
      assert.equal(await page.locator('textarea, [data-strat-publish], [data-bet-edit], [data-section-edit], [data-section-add], [data-strat-status]').count(), 0);
      assert.equal(await page.locator('li[data-bet="b-weekly"]').count(), 0, 'a proposed bet is not in the published version');
      assert.equal(await page.locator('.strat-retired').count(), 0);
      assert.doesNotMatch(await page.locator('.strat-meta').innerText(), /Draft|Not published/);
      // The market cards read only: no ×, no add rows, no tile to click; no news for one who is not routed any.
      await page.locator('#strat-where .strat-map').waitFor();
      await page.locator('#strat-news .strat-none-s').waitFor();
      assert.equal(await page.locator('.strat-x, .strat-addrow, .strat-tm.pick, .strat-hint').count(), 0);
      assert.deepEqual(await page.locator('#strat-rivals .strat-chip').allInnerTexts(), ['CleanCo', 'Sparkle']);
      assert.deepEqual(await sectionTitles(page), ['WHO WE SERVE', 'WHAT WE SELL, AND FOR HOW MUCH', "WHAT WE KNOW THAT OTHERS DON'T", "WHAT WE WON'T DO", 'BRAND']);
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
      await shot(page, 'phone', ['dark']);
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
      assert.deepEqual(await picker.locator('option').allInnerTexts(), ['None', bets()[0].statement, bets()[1].statement], 'active bets only');
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
    // A strategy change from the Strategy Bot in Needs you: who suggests it and why, each change as Now / Proposed
    // against the
    // draft, then "Add to draft" / "No, leave it" with the line that nothing is published (Dev 533).
    {
      const proposal = {id: 'p-draft', kind: 'strategy_draft', goal_id: null, kpi_id: null, status: 'pending', proposed_by: 'bot:goal-manager',
        proposed_at: new Date().toISOString(), reason: 'Margin per job fell to 22% for two months.',
        payload: {source: 'bet_tripped', summary: 'Acme cleans homes in Austin at a fair price.', vision: v1.vision,
          sections: {sell: 'Weekly at $99.', extra: {brand: {title: 'Brand', body: 'Fair, on time.'}}},
          bets: [{id: 'b-price', status: 'retired'}, {statement: 'Price at the market and win on reliability.', rules_out: 'Discounts.',
            wrong_if: {kind: 'text', text: 'Churn above 5% a month', check_by: '2027-03-01'}}, {id: 'b-offices', why: 'Managers sign yearly.'}]}};
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-draft', owner_id: 'human:ana', proposal}]});
      const card = page.locator('#goal-needs [data-proposal="p-draft"]');
      await card.waitFor();
      const flat = t => t.replace(/\s+/g, ' ').trim();
      assert.equal(flat(await card.locator('.kpi-prop-head').innerText()),
        'Strategy Bot suggests a change to your strategy because Bet 1 may be wrong: Margin per job fell to 22% for two months. Open Strategy');
      assert.equal(await card.locator('.strat-prop-open').getAttribute('href'), '#/strategy');
      // The vision it proposes is the draft's already: no row for it.
      assert.deepEqual(await card.locator('.strat-prop-list > li').allInnerTexts().then(rows => rows.map(flat)), [
        `Summary Now ${v1.summary} Proposed Acme cleans homes in Austin at a fair price.`,
        `What we sell, and for how much Now ${sections.sell} Proposed Weekly at $99.`,
        `Brand Now ${sections.extra.brand.body} Proposed Fair, on time.`,
        `Retire Bet 1 ${bets()[0].statement} 21 goals serve it. When you publish, you'll be asked what happens to them.`,
        'New bet Statement Price at the market and win on reliability. Rules out Discounts. Wrong if Churn above 5% a month · check by 1 Mar 2027 It starts as proposed; you make it active on the Strategy page.',
        `Change Bet 2 · Why Now ${bets()[1].why} Proposed Managers sign yearly.`]);
      assert.equal(await card.locator('.kpi-prop-line').innerText(), 'Yes adds this to your strategy draft. Bots keep reading v1 until you publish it on the Strategy page.');
      assert.deepEqual(await card.locator('.kpi-prop-do button').allInnerTexts(), ['Add to draft', 'No, leave it']);
      assert.equal(await card.getByText(/^(Confirm|Reject)$/).count(), 0);
      await shot(page, 'proposal-draft');
      await page.setViewportSize(PHONE);
      await card.scrollIntoViewIfNeeded();
      await shot(page, 'proposal-draft-phone', ['dark']);
      await page.setViewportSize(DESKTOP);
      await card.locator('[data-decide="confirm"]').click();
      await card.waitFor({state: 'detached'});
      assert.deepEqual(sent.find(x => x[1] === '/proposals/p-draft'), ['POST', '/proposals/p-draft', {decision: 'confirm'}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A lens's long section is cut to four lines with one Show all; a change the draft already holds is "Got it", which
    // sets it aside; "No, leave it" rejects; with nothing published the line says so.
    {
      const long = Array.from({length: 8}, (_, i) => `Line ${i + 1} of who we serve.`).join('\n');
      const card = (id, payload, extra = {}) => ({kind: 'proposal', ref: id, owner_id: 'human:ana', proposal: {id, kind: 'strategy_draft', goal_id: null, kpi_id: null,
        status: 'pending', proposed_by: 'bot:goal-manager', proposed_at: new Date().toISOString(), reason: '', payload, ...extra}});
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [
        card('p-lens', {source: 'lens', lens: 'blue-ocean', sections: {serve: long}}, {lens_name: 'Blue Ocean'}),
        card('p-same', {source: 'briefing', vision: v1.vision}),
        card('p-rename', {source: 'briefing', sections: {extra: {brand: {title: 'Positioning', body: sections.extra.brand.body}}}})]});
      const lens = page.locator('#goal-needs [data-proposal="p-lens"]'), same = page.locator('#goal-needs [data-proposal="p-same"]');
      // A section renamed with its text unchanged is a change, not "already says this".
      const rename = page.locator('#goal-needs [data-proposal="p-rename"]');
      assert.equal((await rename.locator('.strat-prop-list').innerText()).replace(/\s+/g, ' ').trim(), 'Positioning · Title Now Brand Proposed Positioning');
      assert.deepEqual(await rename.locator('.kpi-prop-do button').allInnerTexts(), ['Add to draft', 'No, leave it']);
      await lens.waitFor();
      assert.match(await lens.locator('.kpi-prop-head').innerText(), /^Strategy Bot suggests a change to your strategy from the Blue Ocean lens/);
      const values = lens.locator('.prop-np-v');
      const tall = () => values.evaluateAll(els => els.map(el => el.scrollHeight > el.clientHeight + 1));
      // The draft's own text is short, so only Proposed is cut; both are clamped to four lines until Show all.
      assert.deepEqual(await tall(), [false, true], 'the long Proposed is cut to four lines');
      assert.deepEqual(await values.evaluateAll(els => els.map(el => getComputedStyle(el).webkitLineClamp)), ['4', '4']);
      await lens.locator('[data-np-all]').click();
      assert.deepEqual(await tall(), [false, false], 'one Show all opens both');
      assert.match(await same.locator('.kpi-prop-head').innerText(), /from this week's market briefing/);
      assert.equal(await same.locator('.kpi-prop-detail').innerText(), 'Your draft already says this.');
      assert.deepEqual(await same.locator('.kpi-prop-do button').allInnerTexts(), ['Got it']);
      assert.deepEqual(await lens.locator('.kpi-prop-do button').allInnerTexts(), ['Add to draft', 'No, leave it']);
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
      assert.equal(await first.locator('.kpi-prop-line').innerText(), 'Nothing is published until you publish it on the Strategy page.');
      assert.equal((await first.locator('.strat-prop-list').innerText()).replace(/\s+/g, ' ').trim(), 'Vision Now Empty Proposed Every home in Austin.');
      assert.deepEqual(fresh.errors, []);
      await fresh.page.close();
    }
    // A goal serving a bet this person can't see (retired, or not published) keeps it when saved for something else.
    {
      const old = {id: 'g-old', title: 'Win the office park', owner_id: 'bot:cmo', parent_id: null, status: 'green', status_source: 'auto', rank: 1, kpis: [], why: '', bet_id: 'b-gone'};
      const {page, errors, sent} = await open(browser, {route: '#/goals', more: [old]});
      await page.locator('#goal-tree .gt-row').first().waitFor();
      await page.locator('#goal-tree .gt-row[data-owner="bot:cmo"]:not(.gt-cont) .gt-name').click();
      const panel = page.locator('#goal-panel[open]');
      await panel.locator('[data-gp-edit="g-old"]').click();
      const picker = panel.locator('select[name="bet"]');
      assert.equal(await picker.inputValue(), 'b-gone');
      assert.equal(await picker.locator('option:checked').innerText(), 'Retired or unpublished');
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
      await page.waitForFunction(() => document.querySelector('#strat-bets-ol li[data-bet]')?.dataset.bet === 'b-offices');
      assert.deepEqual(sent.filter(x => x[1].startsWith('/bets/')), [['POST', '/bets/b-offices', {rank: 0, revision: 7}],
        ['POST', '/bets/b-price', {rank: 1, revision: 8}]]);
      const order = () => page.locator('#strat-bets-ol li[data-bet]').evaluateAll(lis => lis.map(li => li.dataset.bet));
      // Dragged from first to last.
      const from = await page.locator('li[data-bet="b-offices"] [data-bet-drag]').boundingBox();
      const to = await page.locator('li[data-bet="b-weekly"]').boundingBox();
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + from.width / 2, to.y + to.height - 2, {steps: 6});
      await page.mouse.up();
      await page.waitForFunction(() => [...document.querySelectorAll('#strat-bets-ol li[data-bet]')].at(-1)?.dataset.bet === 'b-offices' && !document.querySelector('.strat-bet.dragging'));
      await page.waitForFunction(() => document.querySelectorAll('#strat-bets-ol li[data-bet]').length === 3);
      assert.deepEqual(await order(), ['b-price', 'b-weekly', 'b-offices']);
      for (let i = 0; i < 100 && sent.filter(x => x[1].startsWith('/bets/')).length < 5; i++) await page.waitForTimeout(20);
      assert.deepEqual(sent.filter(x => x[1].startsWith('/bets/')).slice(2).map(x => [x[1], x[2].rank]),
        [['/bets/b-price', 0], ['/bets/b-weekly', 1], ['/bets/b-offices', 2]]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // After a publish retired or reworded bets: each of their goals is kept, retired or moved, sent together by one button
    // that counts what changes (no Reject); a reworded bet shows its wording before and now (Dev 533).
    {
      const realign = (closed = [], old = false) => ({id: 'p-realign', kind: 'strategy_realign', goal_id: null, kpi_id: null, status: 'pending', proposed_by: 'keeper',
        proposed_at: new Date().toISOString(), reason: 'strategy v2 retired or rewrote the bets these goals serve', closed_goals: closed,
        payload: {n: 2, goals: [
          {goal_id: 'g-top', title: 'Margin per job ≥ 30%', owner_id: 'company', bet_id: 'b-price', change: 'retired', statement: bets()[0].statement},
          {goal_id: 'g-cmo', title: 'Sign 5 property managers', owner_id: 'bot:cmo', bet_id: 'b-offices', change: 'rewritten', statement: 'Sell to offices through managers and agents.',
           ...(old ? {} : {was: bets()[1].statement})}],
          bets: [{id: 'b-offices', number: 1, statement: 'Sell to offices through managers and agents.'}, {id: 'b-weekly', number: 2, statement: bets()[2].statement}]}});
      const proposal = realign();
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner_id: 'human:ana', proposal}]});
      const card = page.locator('#goal-needs [data-proposal="p-realign"]');
      await card.waitFor();
      assert.match(await card.locator('.kpi-prop-head').innerText(), /^Strategy v2 changed bets that 2 goals serve/);
      assert.equal(await card.locator('.kpi-prop-detail').innerText(), 'Choose what happens to each goal. Nothing changes until you press the button.');
      const top = card.locator('[data-realign-goal="g-top"]'), cmo = card.locator('[data-realign-goal="g-cmo"]'), yes = card.locator('[data-prop-yes]');
      assert.equal(await top.inputValue(), 'keep', 'Keep by default');
      assert.deepEqual(await top.locator('option').allInnerTexts(), ['Keep as it is', 'Retire goal', 'Move to Bet 1: Sell to offices through managers and agents.', 'Move to Bet 2: ' + bets()[2].statement]);
      const rows = card.locator('.strat-realign-goal');
      assert.match(await rows.first().innerText(), /Margin per job ≥ 30%[\s\S]*Its bet was retired: Price at half/);
      assert.ok((await rows.nth(1).innerText()).replace(/\s+/g, ' ').includes(`Before v2 ${bets()[1].statement} Now Sell to offices through managers and agents.`));
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
      for (const [closed, button, line] of [[['g-top'], 'Keep this goal as it is', /^Choose what happens/], [['g-top', 'g-cmo'], 'Got it', /^These goals were closed since; nothing to decide\.$/]]) {
        const again = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner_id: 'human:ana', proposal: realign(closed)}]});
        const c = again.page.locator('#goal-needs [data-proposal="p-realign"]');
        await c.waitFor();
        assert.match(await c.locator('.strat-realign-goal').first().innerText(), /Already closed/);
        assert.equal(await c.locator('[data-realign-goal="g-top"]').count(), 0);
        assert.match(await c.locator('.kpi-prop-detail').innerText(), line);
        assert.deepEqual(await c.locator('.kpi-prop-do button').allInnerTexts(), [button]);
        await c.locator('[data-prop-yes]').click();
        await c.waitFor({state: 'detached'});
        assert.deepEqual(again.sent.find(x => x[1] === '/proposals/p-realign')[2], {decision: 'confirm', moves: closed.length === 2 ? {} : {'g-cmo': 'keep'}});
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
