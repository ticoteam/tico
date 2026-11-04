// The Market tab's views (ui/market-spend.js, ui/market-page.js) against a stand-in for GET /api/v2/market/spend and
// /market/value-chain and their writes (fixtures only, the Acme example): treemap areas follow the sizes; the segment panel shows each estimate with its
// params, and the owner's param edit posts and reads again while a member sees no edit, research or cite; the basis
// switch asks for the other basis; the empty state files the research once; the value chain draws its five layers; the
// growth chart draws a dot per segment; a phone never scrolls sideways; note links and old #/market note links open the graph.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots (Spend on desktop in light, on a phone in dark).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');

const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page, name, theme) => {
  if (!shots) return;
  await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
  await page.screenshot({animations: 'disabled', path: path.join(shots, `market-spend-${name}-${theme}.png`)});
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
};
const PHONE = {width: 400, height: 860}, DESKTOP = {width: 1280, height: 900};
const B = 1e9;

const definition = {root: 'segment/cleaning', description: 'What small offices and homes spend on cleaning services',
  includes: ['Recurring cleaning by a third party'], excludes: ['In-house staff'], counting_rules: ['One vendor per segment', 'Supplies are counted where they are sold'],
  year: 2025, geography: 'Global', currency: 'USD', params: {smb_share: {value: 0.35, source: 'judgement call', judgement: true}}};
const size = (id, segment, spend, low, high, over = {}) => ({id, segment_id: segment, dimension: 'offering', basis: 'product', year: 2025,
  spend_usd: spend * B, low_usd: low * B, high_usd: high * B, growth_pct: null, method: 'bottom_up', arithmetic: '', formula: null, params: {}, evidence_ids: [],
  confidence: 'medium', overlaps: '', spread: '', status: 'accepted', accepted: true, ...over});
const seg = (id, name, parent, sized, over = {}) => ({id: 'segment/' + id, name, parent: parent ? 'segment/' + parent : null, depth: parent ? 2 : 1,
  summary: '', size: sized || null, rolled_usd: sized ? {spend_usd: sized.spend_usd, low_usd: sized.low_usd, high_usd: sized.high_usd} : null,
  estimates: sized ? [sized] : [], disagree: false, bases: sized ? ['product'] : [], players: [], self: false, ...over});

// Eight segments: two with sub-segments, one low confidence that disagrees, one proposed, one with no size, and our own.
const officeBu = size('sz-office-bu', 'segment/office', 4.2, 2.9, 6.1, {growth_pct: 12, overlaps: 'Facilities management firms', spread: 'Report vendors say $3B to $11B', arithmetic: 'Firms x adoption x spend per firm', formula: 'firms * adoption * spend',
  params: {adoption: {value: 0.4, source: 'https://census.example/survey', judgement: false}, smb_share: {value: 0.35, judgement: true}},
  evidence: [{source_url: 'https://census.example/survey', source_kind: 'government', quote: 'Firm counts by size', our_read: 'counts'}]});
const officeTd = size('sz-office-td', 'segment/office', 4.0, 2.5, 6.0, {method: 'top_down', status: 'proposed', accepted: false, arithmetic: 'Headline total x share'});
const carpetA = size('sz-carpet-a', 'segment/carpet', 0.9, 0.4, 1.8, {confidence: 'low', growth_pct: 4, method: 'player_sum'});
const carpetB = size('sz-carpet-b', 'segment/carpet', 2.4, 1.0, 3.0, {confidence: 'low', method: 'top_down', status: 'proposed', accepted: false});
const buildData = (dimension = 'offering', basis = 'product') => {
  if (dimension === 'buyer') {
    const bsize = (id, segment, spend, low, high) => size(id, segment, spend, low, high, {dimension: 'buyer'});
    return {definition, year: 2025, dimension, basis, total: {spend_usd: 9 * B, low_usd: 6 * B, high_usd: 13 * B}, segments: [
      seg('tiny-firms', 'Firms under 10 people', null, bsize('b1', 'segment/tiny-firms', 5, 3, 7)),
      seg('mid-firms', 'Firms of 10 to 99', null, bsize('b2', 'segment/mid-firms', 4, 3, 6), {self: true})]};
  }
  const office = {...officeBu, ...(basis === 'platform' ? {spend_usd: 6 * B, high_usd: 8 * B} : {})};
  return {definition, year: 2025, dimension, basis, total: {spend_usd: 16.8 * B, low_usd: 10 * B, high_usd: 25 * B}, segments: [
    seg('office', 'Office cleaning', null, office, {self: true, bases: ['product', 'platform'], summary: 'Recurring cleaning for offices.',
      estimates: [office, officeTd],
      players: [{entity_id: 'company/acme', name: 'Acme Cleaning', figures: [{metric: 'revenue', value: 120e6, text: '', kind: 'reported', as_of: '2026-04', source_url: 'https://acme.example/investors'}]}]}),
    seg('day', 'Day cleaning', 'office', size('sz-day', 'segment/day', 2.5, 1.8, 3.4)),
    seg('night', 'Night cleaning', 'office', size('sz-night', 'segment/night', 1.6, 1.0, 2.6), {self: true}),
    seg('home', 'Home cleaning', null, size('sz-home', 'segment/home', 3.1, 2.2, 4.0, {confidence: 'high', growth_pct: 6})),
    seg('weekly', 'Weekly visits', 'home', size('sz-weekly', 'segment/weekly', 1.9, 1.2, 2.5)),
    seg('oneoff', 'One-off visits', 'home', size('sz-oneoff', 'segment/oneoff', 1.2, 0.8, 1.7)),
    seg('windows', 'Window and glass', null, size('sz-windows', 'segment/windows', 1.4, 0.8, 2.2, {growth_pct: 9})),
    seg('carpet', 'Carpet care', null, carpetA, {disagree: true, estimates: [carpetA, carpetB]}),
    seg('moveout', 'Move-out cleaning', null, size('sz-moveout', 'segment/moveout', 0.8, 0.5, 1.3)),
    seg('supplies', 'Supplies and equipment', null, size('sz-supplies', 'segment/supplies', 1.9, 1.1, 3.0, {growth_pct: 3})),
    seg('janitorial', 'Janitorial supplies', 'supplies', size('sz-jan', 'segment/janitorial', 1.2, 0.8, 1.9)),
    seg('rental', 'Equipment rental', 'supplies', size('sz-rent', 'segment/rental', 0.6, 0.3, 1.1, {confidence: 'low'})),
    seg('specialty', 'Specialty cleaning', null, size('sz-spec', 'segment/specialty', 0.35, 0.15, 0.9, {status: 'proposed', accepted: false, confidence: 'low', growth_pct: 30})),
    seg('eco', 'Eco products', null, null)]};
};
const chain = {layers: [
  {key: 'suppliers', title: 'Suppliers', nodes: [{id: 'company/soapco', name: 'SoapCo', type: 'company', self: false}]},
  {key: 'platforms', title: 'Platforms', nodes: [{id: 'company/bookit', name: 'BookIt', type: 'company', self: false}, {id: 'company/payly', name: 'Payly', type: 'company', self: false}]},
  {key: 'rivals', title: 'Us and rivals', nodes: [{id: 'company/self', name: 'Acme', type: 'company', self: true}, {id: 'company/cleanco', name: 'CleanCo', type: 'company', self: false}]},
  {key: 'channels', title: 'Channels', nodes: [{id: 'channel/managers', name: 'Property managers', type: 'channel', self: false}]},
  {key: 'buyers', title: 'Buyers', nodes: [{id: 'segment/offices', name: 'Small offices', type: 'segment', self: false}]}],
  edges: [{src: 'company/self', rel: 'integrates_with', dst: 'company/bookit'}, {src: 'company/self', rel: 'sells_to', dst: 'segment/offices'}]};
const bets = [{id: 'b-price', statement: 'Price at half the market.', status: 'active', cites: ['segment/home']},
  {id: 'b-offices', statement: 'Sell to offices through managers.', status: 'active', cites: []}, {id: 'b-old', statement: 'Offices only.', status: 'retired', cites: []}];

async function open(browser, {role = 'owner', route = '#/strategy/market', viewport = DESKTOP, empty = false, data} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', locale: 'en-GB'});
  const errors = [], sent = [], reads = [];
  page.on('pageerror', e => errors.push(e.message));
  const live = {data, task: false};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json([]);
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', name: role === 'owner' ? 'Ana Silva' : 'Cara Diaz', email: 'x@example.test',
      role: role === 'owner' ? 'owner' : 'viewer', company_role: role, cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}], org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/market/spend' && method === 'GET') {
      const dimension = url.searchParams.get('dimension') || 'offering', basis = url.searchParams.get('basis') || 'product';
      reads.push([dimension, basis]);
      if (empty) return json({definition: live.task ? {description: 'What offices spend on cleaning'} : null, year: 2025, dimension, basis, total: {spend_usd: 0, low_usd: 0, high_usd: 0}, segments: []});
      return json(live.data?.[dimension] || buildData(dimension, basis));
    }
    if (p === '/api/v2/market/spend/research') {
      sent.push(['POST', p, req.postDataJSON()]);
      live.task = true;
      return json({task_id: 'task-size'});
    }
    const params = p.match(/^\/api\/v2\/market\/sizes\/([^/]+)\/params$/);
    if (params) {
      const body = req.postDataJSON(); sent.push(['POST', p, body]);
      // The server recomputes the size: adoption 0.4 -> 0.5 lifts the Office estimate to 5.25B.
      const change = (live.data ||= {}).offering = buildData('offering', 'product');
      const office = change.segments[0];
      Object.assign(office.size, {spend_usd: 5.25 * B, low_usd: 3.6 * B, high_usd: 7.6 * B});
      office.size.params = {...office.size.params, adoption: {...office.size.params.adoption, value: body.params.adoption}};
      office.estimates[0] = office.size;
      return json({size: office.size});
    }
    const decide = p.match(/^\/api\/v2\/market\/sizes\/([^/]+)\/(accept|reject)$/);
    if (decide) { sent.push(['POST', p, req.postDataJSON()]); return json({size: {id: decide[1]}}); }
    if (p === '/api/v2/market/value-chain') return json(chain);
    if (p === '/api/v2/strategy' && method === 'GET') return json({bets, draft: {revision: 7}, current: null, may_edit: role === 'owner'});
    const bet = p.match(/^\/api\/v2\/strategy\/bets\/([^/]+)$/);
    if (bet && method === 'POST') { sent.push(['POST', p, req.postDataJSON()]); return json({bet: bets.find(b => b.id === bet[1]), revision: 8}); }
    if (p === '/api/v2/librarian/research') { sent.push(['POST', p, req.postDataJSON()]); return json({task_id: 'task-r'}); }
    if (p === '/api/v2/market/graph') return json({entities: [{id: 'company/self', type: 'company', name: 'Acme'}], edges: []});
    if (p === '/api/v2/market/entities') return json({entities: [{id: 'company/self', type: 'company', name: 'Acme'}], total: 1});
    if (p === '/api/company-docs') return json({documents: []});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + route);
  return {page, errors, sent, reads, live};
}

// A box's area against its size: the same ratio everywhere, within 2%.
async function areaRatios(scope) {
  return scope.evaluateAll(els => els.map(el => { const r = el.getBoundingClientRect(); return r.width * r.height / Number(el.dataset.value); }));
}
const within = (ratios, label) => {
  assert.ok(ratios.length > 1, label + ': boxes drawn');
  const mean = ratios.reduce((a, b) => a + b, 0) / ratios.length;
  ratios.forEach(r => assert.ok(Math.abs(r - mean) / mean < 0.02, `${label}: area off by ${(100 * Math.abs(r - mean) / mean).toFixed(1)}%`));
};
const until = async (fn, what) => {
  for (let i = 0; i < 100; i++) { if (fn()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail(what);
};
const noSideScroll = page => page.evaluate(() => ({page: document.documentElement.scrollWidth - innerWidth, host: (() => { const h = document.querySelector('.mkt-host'); return h ? h.scrollWidth - h.clientWidth : 0; })()}));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Spend: boxes are drawn to scale, and the map marks what it should.
    {
      const {page, errors} = await open(browser, {route: '#/strategy/market/spend'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      assert.equal(await page.locator('.mkt-views a[aria-current=page]').innerText(), 'Spend');
      assert.deepEqual(await page.locator('.mkt-views a').allInnerTexts(), ['Briefing', 'Players', 'Spend', 'Buyers', 'Value chain', 'Size vs growth', 'Listening']);
      assert.equal(await page.locator('.mkt-total strong').innerText(), '$16.8B');
      within(await areaRatios(page.locator('.mkt-map > .mkt-box')), 'top level');
      within(await areaRatios(page.locator('.mkt-group:has([data-seg="segment/day"]) .mkt-tile')), 'Office parts');
      within(await areaRatios(page.locator('.mkt-group:has([data-seg="segment/weekly"]) .mkt-tile')), 'Home parts');
      assert.ok(await page.locator('.mkt-tile.low[data-seg="segment/carpet"]').count(), 'low confidence is hatched');
      assert.match(await page.locator('[data-seg="segment/carpet"]').first().getAttribute('aria-label'), /estimates disagree/);
      assert.equal(await page.locator('.mkt-box.self').count(), 2, 'our segments are ringed (Office group, Night cleaning)');
      assert.equal(await page.locator('.mkt-map [data-seg="segment/eco"]').count(), 0, 'a segment with no size is not drawn');
      assert.deepEqual(await page.locator('.mkt-sec:has(h2:text("Not sized yet")) .mkt-pick').allInnerTexts(), ['Eco products']);
      assert.equal((await page.locator('.mkt-sure .mkt-row .mkt-lab').allInnerTexts())[0], 'Office cleaning', 'How sure are we is sorted by spend');
      assert.equal(await page.locator('.mkt-sure .mkt-row').count(), 7);
      assert.deepEqual(await noSideScroll(page), {page: 0, host: 0});
      await shot(page, 'desktop', 'light');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Parts that add up to less than their parent keep their own dollars and a plain "Rest of" tile takes the remainder, so
    // each area matches its label; a segment with no size of its own is drawn from the API's rolled-up spend.
    {
      const parts = [seg('big', 'Offices', null, size('sz-big', 'segment/big', 10, 6, 14)), seg('small', 'Homes', null, size('sz-small', 'segment/small', 5, 3, 8)),
        seg('p1', 'Day shifts', 'big', size('sz-p1', 'segment/p1', 3, 2, 4)), seg('p2', 'Night shifts', 'big', size('sz-p2', 'segment/p2', 2, 1, 3)),
        seg('group', 'Equipment', null, null, {rolled_usd: {spend_usd: 4 * B, low_usd: 2 * B, high_usd: 6 * B}}),
        seg('mid', 'Rental', 'group', null, {depth: 2, rolled_usd: {spend_usd: 4 * B, low_usd: 2 * B, high_usd: 6 * B}})];
      const data = {definition, year: 2025, dimension: 'offering', basis: 'product', total: {spend_usd: 19 * B, low_usd: 11 * B, high_usd: 28 * B}, segments: parts};
      const {page, errors} = await open(browser, {route: '#/strategy/market/spend', data: {offering: data}});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      const group = page.locator('.mkt-group:has([data-seg="segment/p1"])');
      assert.equal(await group.locator('.mkt-rest').count(), 1);
      assert.match(await group.locator('.mkt-rest').getAttribute('aria-label'), /^Rest of Offices, \$5B$/);
      assert.equal(await group.locator('.mkt-rest').getAttribute('data-seg'), null, 'the rest is not clickable');
      const area = await group.locator('.mkt-box').evaluateAll(els => els.map(el => { const r = el.getBoundingClientRect(); return r.width * r.height; }));
      const gb = await group.evaluate(el => { const r = el.getBoundingClientRect(); return r.width * (r.height - 26); });
      [0.3, 0.2, 0.5].forEach(share => assert.ok(area.some(a => Math.abs(a / gb - share) < 0.02 * share), `an area is ${share} of the box`));
      assert.equal(await page.locator('.mkt-map > .mkt-box[data-value="' + 4 * B + '"]').count(), 1, 'an unsized segment is drawn at its rolled-up spend');
      assert.equal(await page.locator('.mkt-total strong').innerText(), '$19B');
      await shot(page, 'rest', 'light');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Nothing sized: Players is the graph, and the Spend tab opens its empty state.
    {
      const {page, errors} = await open(browser, {route: '#/strategy/market/players', empty: true});
      await page.locator('.market-shell').waitFor();
      assert.equal(await page.locator('.mkt-views a[aria-current=page]').innerText(), 'Players');
      assert.equal(await page.locator('.mkt-map').count(), 0);
      await page.locator('.mkt-views a', {hasText: 'Spend'}).click();
      await page.locator('[data-mkt-size]').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/strategy/market/spend');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A link to a note, a note id route and an old #/market link open the graph even when Spend is sized.
    for (const [route, hash] of [['#/strategy/market?note=brief', '#/strategy/market?note=brief'], ['#/strategy/market/company/self', '#/strategy/market/company/self'],
      ['#/market?note=brief', '#/strategy/market?note=brief'], ['#/strategy/market/players', '#/strategy/market/players']]) {
      const {page, errors} = await open(browser, {route});
      await page.locator('.market-shell .market-graph').waitFor();
      assert.equal(await page.evaluate(() => location.hash), hash);
      assert.equal(await page.locator('.mkt-map').count(), 0, route);
      assert.equal(await page.locator('.mkt-views a[aria-current=page]').innerText(), 'Players');
      assert.deepEqual(errors, [], route);
      await page.close();
    }
    // The panel: each estimate by method with its arithmetic, params, sources, players; the owner edits a param, which posts and reads again.
    {
      const {page, errors, sent, reads} = await open(browser, {route: '#/strategy/market/spend'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      await page.locator('.mkt-gh[data-seg="segment/office"]').click();
      const panel = page.locator('.mkt-panel[open]');
      await panel.waitFor();
      assert.equal(await panel.locator('h2').innerText(), 'Office cleaning');
      await shot(page, 'panel', 'dark');
      assert.deepEqual(await panel.locator('.mkt-est-h b').allInnerTexts(), ['Bottom up', 'Top down']);
      assert.match(await panel.locator('.mkt-est').first().innerText(), /Firms x adoption x spend per firm[\s\S]*census\.example\/survey/);
      assert.match(await panel.innerText(), /Facilities management firms[\s\S]*\$3B to \$11B[\s\S]*Acme Cleaning[\s\S]*Revenue \$120M · Reported · 2026-04/);
      assert.equal(await panel.locator('.mkt-est').first().locator('.pill.active').innerText(), 'Accepted');
      const before = reads.length;
      await panel.locator('[data-mkt-param][data-name="adoption"] input').fill('0.5');
      await panel.locator('[data-mkt-param][data-name="adoption"] button').click();
      await page.waitForFunction(() => document.querySelector('.mkt-panel[open] .mkt-kv')?.innerText.includes('$5.25B'));
      assert.deepEqual(sent.find(x => x[1].endsWith('/params')), ['POST', '/api/v2/market/sizes/sz-office-bu/params', {params: {adoption: 0.5}}]);
      assert.ok(reads.length > before, 'the spend was read again');
      assert.equal(await page.locator('.mkt-gh[data-seg="segment/office"] b').innerText(), '$5.25B', 'the box moved');
      // Accept a proposed estimate.
      await panel.locator('[data-mkt-accept="sz-office-td"]').click();
      await until(() => sent.some(x => x[1] === '/api/v2/market/sizes/sz-office-td/accept'), 'the accept was posted');
      // Cite in a bet: only the active bet that does not cite the segment yet is offered; the draft's revision goes with it.
      await panel.locator('[data-mkt-cite-open]').click();
      await panel.locator('[data-mkt-cite] select').waitFor();
      assert.deepEqual(await panel.locator('[data-mkt-cite] option').allInnerTexts(), ['Price at half the market.', 'Sell to offices through managers.']);
      await panel.locator('[data-mkt-cite] select').selectOption('b-offices');
      await panel.locator('[data-mkt-cite] button').click();
      await page.waitForFunction(() => !document.querySelector('[data-mkt-cite]'));
      assert.deepEqual(sent.find(x => x[1] === '/api/v2/strategy/bets/b-offices'), ['POST', '/api/v2/strategy/bets/b-offices', {cites: ['segment/office'], revision: 7}]);
      // Research this segment opens the Librarian's research for the segment.
      await panel.locator('[data-mkt-research]').click();
      await page.locator('[data-research-form]').waitFor();
      assert.equal(await page.locator('[data-research-form]').count(), 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A member reads the same panel with nothing to change.
    {
      const {page, errors} = await open(browser, {role: 'member', route: '#/strategy/market/spend'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      await page.locator('.mkt-gh[data-seg="segment/office"]').click();
      const panel = page.locator('.mkt-panel[open]');
      await panel.waitFor();
      assert.ok(await panel.locator('.mkt-est').count() === 2, 'estimates are read');
      for (const sel of ['[data-mkt-param]', '[data-mkt-accept]', '[data-mkt-research]', '[data-mkt-cite-open]', 'input']) assert.equal(await panel.locator(sel).count(), 0, sel);
      assert.match(await panel.locator('.mkt-est').first().innerText(), /adoption\s*0\.4/);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.mkt-panel[open]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The Definition panel, and the basis switch asking for the other basis.
    {
      const {page, errors, reads} = await open(browser, {route: '#/strategy/market/spend'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      await page.locator('[data-mkt-def]').click();
      const panel = page.locator('.mkt-panel[open]');
      await panel.waitFor();
      assert.match(await panel.innerText(), /Includes[\s\S]*Recurring cleaning[\s\S]*Excludes[\s\S]*In-house staff[\s\S]*Counting rules[\s\S]*One vendor per segment[\s\S]*smb_share[\s\S]*Judgement/i);
      await page.keyboard.press('Escape');
      assert.deepEqual(await page.locator('.mkt-seg button').allInnerTexts(), ['Product', 'Platform']);
      await page.locator('.mkt-seg button', {hasText: 'Platform'}).click();
      await page.waitForFunction(() => document.querySelector('.mkt-gh[data-seg="segment/office"] b')?.textContent === '$6B');
      assert.deepEqual(reads.at(-1), ['offering', 'platform']);
      assert.equal(await page.locator('.mkt-seg button[aria-pressed=true]').innerText(), 'Platform');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The view switch: Buyers asks for the buyer dimension.
    {
      const {page, errors, reads} = await open(browser, {route: '#/strategy/market/spend'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      await page.locator('.mkt-views a', {hasText: 'Buyers'}).click();
      await page.locator('.mkt-map [data-seg="segment/tiny-firms"]').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/strategy/market/buyers');
      assert.deepEqual(reads.at(-1), ['buyer', 'product']);
      assert.equal(await page.locator('.mkt-map [data-seg="segment/office"]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Empty: the owner describes the market once; a member is told it is not sized.
    {
      const {page, errors, sent} = await open(browser, {route: '#/strategy/market/spend', empty: true});
      await page.locator('[data-mkt-size]').waitFor();
      await page.locator('[data-mkt-size] button').click();
      assert.equal(await page.locator('[data-mkt-error]').isVisible(), true, 'an empty sentence is not sent');
      assert.equal(sent.length, 0);
      await page.locator('#mkt-describe').fill('What offices spend on cleaning');
      await page.locator('[data-mkt-size] button').dblclick();
      await page.locator('[data-mkt-researching]').waitFor();
      assert.equal(sent.length, 1, 'one research request');
      assert.deepEqual(sent[0], ['POST', '/api/v2/market/spend/research', {description: 'What offices spend on cleaning'}]);
      assert.equal(await page.locator('[data-mkt-researching] a').getAttribute('href'), '#/task/task-size');
      assert.deepEqual(errors, []);
      await page.close();
      const member = await open(browser, {role: 'member', route: '#/strategy/market/buyers', empty: true});
      await member.page.locator('.mkt-host .market-none').waitFor();
      assert.equal(await member.page.locator('.mkt-host').innerText(), 'Not sized yet.');
      assert.equal(await member.page.locator('[data-mkt-size]').count(), 0);
      await member.page.close();
    }
    // The value chain: five layers, ours emphasized, each node a link to its entity.
    {
      const {page, errors} = await open(browser, {route: '#/strategy/market/value-chain'});
      await page.locator('.mkt-chain').waitFor();
      assert.deepEqual(await page.locator('.mkt-layer h2').allInnerTexts(), ['SUPPLIERS', 'PLATFORMS', 'US AND RIVALS', 'CHANNELS', 'BUYERS']);
      assert.equal(await page.locator('.mkt-node.self').innerText(), 'Acme');
      await shot(page, 'chain', 'light');
      assert.equal(await page.locator('.mkt-node.self').getAttribute('href'), '#/strategy/market?note=company%2Fself');
      await page.locator('.mkt-node.self').hover();
      assert.deepEqual(await page.locator('.mkt-node.near').allInnerTexts(), ['BookIt', 'Acme', 'Small offices']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Size vs growth: a dot per sized segment with a growth figure, ours filled; the rest listed.
    {
      const {page, errors} = await open(browser, {route: '#/strategy/market/growth'});
      await page.locator('.mkt-svg').waitFor();
      assert.equal(await page.locator('.mkt-dot').count(), 6);
      await shot(page, 'growth', 'dark');
      assert.deepEqual(await page.locator('.mkt-dot.self text').evaluateAll(els => els.map(e => e.textContent)), ['Office cleaning']);
      assert.deepEqual(await page.locator('.mkt-sec:has(h2:text("No growth figure")) .mkt-pick').allInnerTexts(), ['Move-out cleaning']);
      await page.locator('.mkt-dot', {hasText: 'Home cleaning'}).click();
      await page.locator('.mkt-panel[open] h2', {hasText: 'Home cleaning'}).waitFor();
      assert.equal(await page.locator('.mkt-svg text:text("log scale")').count(), 0, 'a narrow range stays linear');
      assert.deepEqual(errors, []);
      await page.close();
      // A range wider than 20x goes on a log scale.
      const wide = buildData();
      wide.segments.find(s => s.id === 'segment/specialty').size.spend_usd = 0.1 * B;
      const log = await open(browser, {route: '#/strategy/market/growth', data: {offering: wide}});
      await log.page.locator('.mkt-svg').waitFor();
      assert.equal(await log.page.locator('.mkt-svg text:text("log scale")').count(), 1);
      await log.page.close();
    }
    // A phone: nothing scrolls sideways in any view, with the panel open, and the map keeps its boxes.
    {
      const {page, errors} = await open(browser, {viewport: PHONE, route: '#/strategy/market/spend'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      assert.deepEqual(await noSideScroll(page), {page: 0, host: 0}, 'spend');
      within(await areaRatios(page.locator('.mkt-map > .mkt-box')), 'phone top level');
      await shot(page, 'phone', 'dark');
      await page.locator('.mkt-map .mkt-tile').first().click();
      await page.locator('.mkt-panel[open]').waitFor();
      const sheet = await page.locator('.mkt-panel[open]').evaluate(el => ({right: el.getBoundingClientRect().right, width: el.scrollWidth - el.clientWidth}));
      assert.ok(sheet.right <= PHONE.width && sheet.width <= 0, 'the sheet fits');
      await shot(page, 'phone-panel', 'dark');
      await page.keyboard.press('Escape');
      for (const view of ['buyers', 'value-chain', 'growth']) {
        await page.locator('.mkt-views a[data-market-view="' + view + '"]').click();
        await page.waitForFunction(v => location.hash === '#/strategy/market/' + v, view);
        await page.waitForTimeout(250);
        assert.deepEqual(await noSideScroll(page), {page: 0, host: 0}, view);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('market-spend: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
