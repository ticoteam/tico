// The Strategy section (ui/strategy-page.js) against a stand-in for GET /api/v2/strategy and its writes (fixtures only,
// the Acme example): the owner reads the draft with its bets (Why, Rules out, Wrong if in plain words, goals, health),
// the four fixed sections and an extra one, edits the summary in place with the draft's revision, and is told when the
// draft changed elsewhere (409); Publish stays off with its reason until one bet is active with Rules out and Wrong if
// and What we won't do is written, and a bet chosen Active without them stays Proposed; a member reads only the
// published version; old #/market links land on the Market tab keeping their query; the Goals page's Bet picker saves
// bet_id and its chip opens the bet; the team chart's group Bet picker saves the group's bet_id.
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
  bot('goal-manager', 'CEO Bot', 'p:ana', {reports_to: ''}), bot('cmo', 'Head of Marketing', 'b:goal-manager', {reports_to: ''})];
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
const v1 = {id: 'v-1', n: 1, status: 'published', guide: 'default',
  summary: 'Acme cleans homes and small offices in Austin at about half the usual price. Bots handle booking, scheduling and billing, so the price holds.',
  vision: 'Every home in Austin cleaned by Acme.', sections, bets: bets().filter(b => b.id !== 'b-weekly'), note: 'First version', revision: 7,
  updated: '2026-10-24T09:00:00Z', updated_by: 'human:ana', published: '2026-10-24T09:00:00Z', published_by: 'human:ana'};

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
    {id: 'g-top', title: 'Margin per job ≥ 30%', owner: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: [], why: '', bet_id: 'b-price'},
    {id: 'g-cmo', title: 'Sign 5 property managers', owner: 'bot:cmo', parent_id: 'g-top', status: 'red', status_source: 'auto', rank: 0, kpis: [], why: '', bet_id: null},
    ...more];
  const groups = [{id: 'ops', name: 'Operations', parent: '', bet_id: null}];
  const strategy = () => ({...(role === 'owner' ? {draft: st.draft} : {}), current: st.current, bets: role === 'owner' ? st.bets : st.current?.bets || [],
    label: 'Bets', label_one: 'Bet', may_edit: role === 'owner',
    website: 'https://acme.example', goals_by_bet: {'b-price': [{id: 'g-top', title: 'Margin per job ≥ 30%', owner: 'company', status: 'green'},
      {id: 'g-jobs', title: '400 jobs/week', owner: 'bot:cmo', status: 'yellow'}], 'b-offices': [{id: 'g-cmo', title: 'Sign 5 property managers', owner: 'bot:cmo', status: 'red'}]},
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
    if (p === '/api/v2/market/graph') return json({entities: [], edges: []});
    if (p === '/api/v2/market/entities') return json({entities: [], total: 0});
    if (p === '/api/company-docs') return json({documents: []});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + route);
  return {page, errors, sent, st};
}
const sectionTitles = page => page.locator('[data-section] .strat-sec-head .strat-k').allInnerTexts();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // The owner reads the draft: summary, the Bets in plain words, the four sections in their order and the extra one.
    {
      const {page, errors, sent, st} = await open(browser);
      const summary = page.locator('[data-strat-field="summary"]');
      await summary.waitFor();
      assert.equal(await page.locator('#main h1').innerText(), 'Strategy');
      assert.equal(await page.locator('.strat-tabs a.cur').innerText(), 'Strategy');
      assert.equal(await page.locator('#side [data-nav="strategy"]').getAttribute('aria-current'), 'page');
      assert.match(await page.locator('.strat-meta').innerText(), /^v1 · published \d+ Oct( \d{4})? by Ana · Draft differs from v1$/);
      assert.equal(await page.locator('[data-strat-framework], .strat-fw').count(), 0, 'no framework picker');
      assert.equal(await page.locator('#strat-bets-h').innerText(), 'BETS');
      const first = page.locator('li[data-bet="b-price"]');
      assert.match(await first.innerText(), /Price at half the market[\s\S]*Why[\s\S]*40% below CleanCo[\s\S]*Rules out[\s\S]*Premium add-ons/);
      assert.match(await first.locator('[data-bet-wrong]').innerText(), /^Wrong if\s*Gross margin per job below 25% · check by 31 Jan( 2027)?$/);
      assert.deepEqual(await first.locator('.strat-goals .gdot').evaluateAll(dots => dots.map(d => [...d.classList].find(c => c !== 'gdot'))), ['green', 'yellow']);
      assert.equal(await first.locator('.strat-goals a').first().getAttribute('href'), '#/goals/g-top');
      assert.equal(await first.locator('[data-bet-health]').getAttribute('data-bet-health'), 'green');
      assert.equal(await page.locator('li[data-bet="b-offices"] [data-bet-health]').getAttribute('data-bet-health'), 'red');
      assert.match(await page.locator('li[data-bet="b-offices"] [data-bet-wrong]').innerText(), /Fewer than 5 managers signed · check by 1 Feb/);
      assert.equal(await page.locator('li[data-bet="b-weekly"] .strat-pill').innerText(), 'Proposed');
      assert.match(await page.locator('li[data-bet="b-weekly"] [data-bet-wrong]').innerText(), /Not yet measurable/);
      assert.equal(await page.locator('.strat-retired li[data-bet="b-old"]').count(), 1, 'retired bets sit apart');
      assert.deepEqual(await sectionTitles(page), ['WHO WE SERVE', 'WHAT WE SELL, AND FOR HOW MUCH', "WHAT WE KNOW THAT OTHERS DON'T", "WHAT WE WON'T DO", 'BRAND']);
      assert.equal(await page.locator('[data-section="serve"] .strat-md strong').innerText(), 'small offices', 'sections are markdown');
      assert.equal(await page.locator('[data-bet-add]').getAttribute('aria-label'), 'Add Bet');
      assert.equal(await page.locator('[data-strat-publish]').isEnabled(), true, 'the Acme draft may be published');
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
      assert.match(await drawer.innerText(), /v1 · First version[\s\S]*Every home in Austin[\s\S]*WHAT WE WON'T DO[\s\S]*BRAND/);
      assert.equal(await drawer.locator('select, textarea, [data-sv-compare]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Nothing publishable yet: Publish is off with its reason, and a bet chosen Active without Rules out stays Proposed.
    {
      const {page, errors, sent} = await open(browser, {fresh: true});
      await page.locator('li[data-bet="b-price"]').waitFor();
      assert.equal(await page.locator('[data-strat-status]').innerText(), 'Not published');
      const publish = page.locator('[data-strat-publish]');
      assert.equal(await publish.isDisabled(), true);
      assert.equal(await page.locator('[data-strat-why-not]').innerText(), 'Needs an active bet with Rules out and Wrong if');
      assert.match(await page.locator('li[data-bet="b-price"] [data-bet-wrong]').innerText(), /Not yet measurable/);
      await page.locator('li[data-bet="b-price"] [data-bet-edit]').click();
      const form = page.locator('[data-bet-form="b-price"]');
      await form.locator('select[name="status"]').selectOption('active');
      assert.equal(await form.locator('select[name="status"]').inputValue(), 'proposed', 'stays Proposed');
      assert.equal(await form.locator('[data-bet-why-not]').innerText(), 'An active bet needs Rules out and Wrong if');
      // With Rules out and a KPI Wrong if, Active holds.
      await form.locator('textarea[name="rules_out"]').fill('Premium add-ons, custom quotes.');
      await form.locator('select[name="status"]').selectOption('active');
      assert.equal(await form.locator('select[name="status"]').inputValue(), 'proposed');
      assert.equal(await form.locator('[data-bet-why-not]').innerText(), 'An active bet needs Wrong if');
      await form.locator('select[name="wrong_kind"]').selectOption('kpi');
      assert.equal(await form.locator('[data-wrong-kind="kpi"]').isVisible(), true);
      assert.equal(await form.locator('[data-wrong-kind="text"]').isVisible(), false);
      assert.deepEqual(await form.locator('select[name="kpi_id"] option').allInnerTexts(), ['KPI…', 'Gross margin per job', 'Jobs per week']);
      await form.locator('select[name="kpi_id"]').selectOption('k-margin');
      await form.locator('input[name="threshold"]').fill('25');
      await form.locator('input[name="check_by"]').fill('2027-01-31');
      await form.locator('select[name="status"]').selectOption('active');
      assert.equal(await form.locator('select[name="status"]').inputValue(), 'active');
      assert.equal(await form.locator('[data-bet-why-not]').isVisible(), false);
      await shot(page, 'bet-form');
      await page.setViewportSize(PHONE);
      await form.scrollIntoViewIfNeeded();
      await shot(page, 'bet-form-phone', ['dark']);
      await page.setViewportSize(DESKTOP);
      await form.locator('button[type="submit"]').click();
      await form.waitFor({state: 'detached'});
      assert.deepEqual(sent.find(x => x[1] === '/bets/b-price'), ['POST', '/bets/b-price', {rules_out: 'Premium add-ons, custom quotes.',
        wrong_if: {kind: 'kpi', kpi_id: 'k-margin', direction: 'below', threshold: 25, check_by: '2027-01-31'}, status: 'active', revision: 1}]);
      await page.locator('[data-strat-why-not]', {hasText: "Needs What we won't do"}).waitFor();
      assert.equal(await publish.isDisabled(), true);
      await page.locator('[data-section="not"] [data-section-edit]').click();
      await page.locator('[data-section-form] textarea').fill('No deep cleans.');
      await page.locator('[data-section-form] button[type="submit"]').click();
      await page.locator('[data-strat-publish]:not([disabled])').waitFor();
      assert.equal(await page.locator('[data-strat-why-not]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A sentence wrong-if saved with no Check by gets one 90 days out: the server refuses it without a date.
    {
      const {page, errors, sent} = await open(browser);
      await page.locator('li[data-bet="b-weekly"] [data-bet-edit]').click();
      const form = page.locator('[data-bet-form="b-weekly"]');
      await form.locator('input[name="wrong_text"]').fill('Weekly churn above 10%');
      await form.locator('button[type="submit"]').click();
      await form.waitFor({state: 'detached'});
      const {wrong_if} = sent.find(x => x[1] === '/bets/b-weekly')[2];
      assert.equal(wrong_if.text, 'Weekly churn above 10%');
      assert.equal(wrong_if.check_by, new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10));
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
      assert.deepEqual(await sectionTitles(page), ['WHO WE SERVE', 'WHAT WE SELL, AND FOR HOW MUCH', "WHAT WE KNOW THAT OTHERS DON'T", "WHAT WE WON'T DO", 'BRAND']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A phone: tabs on top, one column, no sideways scroll.
    {
      const {page, errors} = await open(browser, {viewport: PHONE});
      await page.locator('li[data-bet="b-price"]').waitFor();
      assert.equal(await page.locator('.strat-toc').isVisible(), false);
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
    // Goals: one Strategy line at the top; the goal editor's Bet picker saves bet_id; the bet's chip opens it on Strategy.
    {
      const {page, errors, sent} = await open(browser, {route: '#/goals'});
      await page.locator('#goal-tree .gt-row').first().waitFor();
      assert.match(await page.locator('#goal-strategy').innerText(), /^Strategy v1\s*·?\s*Acme cleans homes/);
      assert.equal(await page.locator('#goal-strategy').getAttribute('href'), '#/strategy');
      await page.locator('#goal-tree .gt-row[data-owner="bot:cmo"]').first().click();
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
    // The team chart: the owner gives a group a bet from the flag on its row.
    {
      const {page, errors, sent} = await open(browser);
      const row = page.locator('#tree li.org-group > .noderow', {hasText: 'Operations'});
      await row.waitFor();
      await row.hover();
      await row.locator('[data-group-bet="ops"]').click();
      const select = page.locator('#tree [data-group-bet-select="ops"]');
      await select.waitFor();
      assert.equal(await page.locator('#tree .org-bet-f > span').innerText(), 'Bet');
      assert.deepEqual(await select.locator('option').allInnerTexts(), ['None', bets()[0].statement, bets()[1].statement]);
      await shot(page, 'group-picker');
      if (shots) {
        await page.setViewportSize(PHONE);
        await page.evaluate(() => setDrawer(true));
        await select.scrollIntoViewIfNeeded();
        await shot(page, 'group-picker-phone', ['dark']);
        await page.evaluate(() => setDrawer(false));
        await page.setViewportSize(DESKTOP);
      }
      await select.selectOption('b-price');
      await select.waitFor({state: 'detached'});
      assert.deepEqual(sent.find(x => x[1] === '/groups/ops'), ['PATCH', '/groups/ops', {bet_id: 'b-price'}]);
      await row.hover();
      assert.match(await row.locator('[data-group-bet="ops"]').getAttribute('class'), /\bon\b/, 'the flag stays lit');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A strategy change from the CEO Bot in Needs you: who suggests it and why, each change as Now / Proposed against the
    // draft, then "Add to draft" / "No, leave it" with the line that nothing is published (Dev 533).
    {
      const proposal = {id: 'p-draft', kind: 'strategy_draft', goal_id: null, kpi_id: null, status: 'pending', proposed_by: 'bot:goal-manager',
        proposed_at: new Date().toISOString(), reason: 'Margin per job fell to 22% for two months.',
        payload: {source: 'bet_tripped', summary: 'Acme cleans homes in Austin at a fair price.', vision: v1.vision,
          sections: {sell: 'Weekly at $99.', extra: {brand: {title: 'Brand', body: 'Fair, on time.'}}},
          bets: [{id: 'b-price', status: 'retired'}, {statement: 'Price at the market and win on reliability.', rules_out: 'Discounts.',
            wrong_if: {kind: 'text', text: 'Churn above 5% a month', check_by: '2027-03-01'}}, {id: 'b-offices', why: 'Managers sign yearly.'}]}};
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-draft', owner: 'human:ana', proposal}]});
      const card = page.locator('#goal-needs [data-proposal="p-draft"]');
      await card.waitFor();
      const flat = t => t.replace(/\s+/g, ' ').trim();
      assert.equal(flat(await card.locator('.kpi-prop-head').innerText()),
        'CEO Bot suggests a change to your strategy because Bet 1 may be wrong: Margin per job fell to 22% for two months. Open Strategy');
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
      const card = (id, payload, extra = {}) => ({kind: 'proposal', ref: id, owner: 'human:ana', proposal: {id, kind: 'strategy_draft', goal_id: null, kpi_id: null,
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
      assert.match(await lens.locator('.kpi-prop-head').innerText(), /^CEO Bot suggests a change to your strategy from the Blue Ocean lens/);
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
      const old = {id: 'g-old', title: 'Win the office park', owner: 'bot:cmo', parent_id: null, status: 'green', status_source: 'auto', rank: 1, kpis: [], why: '', bet_id: 'b-gone'};
      const {page, errors, sent} = await open(browser, {route: '#/goals', more: [old]});
      await page.locator('#goal-tree .gt-row').first().waitFor();
      await page.locator('#goal-tree .gt-row', {hasText: 'Win the office park'}).click();
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
    // Moving a bet saves each changed rank with the revision the last save answered.
    {
      const {page, errors, sent} = await open(browser);
      await page.locator('li[data-bet="b-offices"] [data-bet-up]').click();
      await page.waitForFunction(() => document.querySelector('ol.strat-bets li[data-bet]')?.dataset.bet === 'b-offices');
      assert.deepEqual(sent.filter(x => x[1].startsWith('/bets/')), [['POST', '/bets/b-offices', {rank: 0, revision: 7}],
        ['POST', '/bets/b-price', {rank: 1, revision: 8}]]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // After a publish retired or reworded bets: each of their goals is kept, retired or moved, sent together by one button
    // that counts what changes (no Reject); a reworded bet shows its wording before and now (Dev 533).
    {
      const realign = (closed = [], old = false) => ({id: 'p-realign', kind: 'strategy_realign', goal_id: null, kpi_id: null, status: 'pending', proposed_by: 'keeper',
        proposed_at: new Date().toISOString(), reason: 'strategy v2 retired or rewrote the bets these goals serve', closed_goals: closed,
        payload: {n: 2, goals: [
          {goal_id: 'g-top', title: 'Margin per job ≥ 30%', owner: 'company', bet_id: 'b-price', change: 'retired', statement: bets()[0].statement},
          {goal_id: 'g-cmo', title: 'Sign 5 property managers', owner: 'bot:cmo', bet_id: 'b-offices', change: 'rewritten', statement: 'Sell to offices through managers and agents.',
           ...(old ? {} : {was: bets()[1].statement})}],
          bets: [{id: 'b-offices', number: 1, statement: 'Sell to offices through managers and agents.'}, {id: 'b-weekly', number: 2, statement: bets()[2].statement}]}});
      const proposal = realign();
      const {page, errors, sent} = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner: 'human:ana', proposal}]});
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
        const again = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner: 'human:ana', proposal: realign(closed)}]});
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
        const old = await open(browser, {route: '#/goals', needs: [{kind: 'proposal', ref: 'p-realign', owner: 'human:ana', proposal: realign([], true)}]});
        const row = old.page.locator('#goal-needs [data-proposal="p-realign"] .strat-realign-goal').nth(1);
        await row.waitFor();
        assert.deepEqual(await row.locator('.prop-np-k').allInnerTexts(), ['Now']);
        assert.deepEqual(old.errors, []);
        await old.page.close();
      }
    }
    // A member has no Bet flag on a group.
    {
      const {page, errors} = await open(browser, {role: 'member'});
      await page.locator('.strat-summary').waitFor();
      assert.equal(await page.locator('[data-group-bet]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('strategy: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
