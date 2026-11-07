// A stand-in hub for the Strategy content scripts (ui/tests/strategy-*.cjs, segments.cjs): the org chart's groups and
// moves (redesign CONTRACT §3), Market's spend, value chain, companies and a note, Listening's stats with `daily` and
// `recent`, the latest market briefing, the company's segments (their list, their strategy and the segment markets),
// over fixtures/redesign-{org,market,listening,briefing}.json (the Acme example) and goals, plus the boot stubs.
// Writes change what a page reads back and are recorded in `writes`.
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./page.cjs');
const {unrouted} = require('./routes.cjs');
const uiDir = path.join(__dirname, '../..');
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures', name), 'utf8'));

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

// Acme's strategy as a segment reads it (backend/segments.py strategy_view): the company's text per field. A segment's
// own text is in its `own` (a key present = the segment changed it).
const SEG_TITLES = {summary: 'Summary', serve: 'Who we serve', sell: 'What we sell, and for how much', not: "What we won't do"};
const COMPANY_TEXT = {
  summary: 'Acme makes release checks that run inside the CI plans teams already pay for.',
  serve: 'Engineering teams of 20 to 200 who ship every week.',
  sell: 'A seat at $19 a month, bundled into the CI plan.',
  not: 'No on-premise installs, no custom checks for one customer.',
};
// The company's segments in list order (GET /api/v2/segments), with the teammates dedicated to each. Release checks is
// the market fixture's segment of the same id, so its market page has the Acme numbers.
const SEGMENTS = [
  {id: 'segment/checks', name: 'Release checks', who: 'Platform teams that ship several times a day', color: 'blue',
    faces: [{ref: 'person:ana', name: 'Ana Silva', kind: 'person'}, {ref: 'bot:cmo', name: 'CMO', kind: 'bot'}], teammates: 3,
    own: {serve: 'Platform teams of 50+ engineers who ship several times a day.'},
    bets: [{id: 'b-bundle', statement: 'Bundle the checks into CI plans', status: 'active'}]},
  {id: 'segment/deploy', name: 'Deploy pipelines', who: 'CI plans that bundle the checks', color: 'green',
    faces: [], teammates: 0, own: {}, bets: []},
];
const COMPARE = {
  'segment/checks': {money: 700000000, yearly_spend: 700000000, growth: 22, our_share: 4.2},
  'segment/deploy': {money: null, yearly_spend: 500000000, growth: null, our_share: null},
};

// The listening fixture's days and hours are counted back from now (a day's posts at noon UTC), so the chart's last fourteen days hold them.
function listeningStats(now = new Date()) {
  const {stats} = fixture('redesign-listening.json');
  const day = ago => new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - ago)).toISOString().slice(0, 10);
  return {...stats, since: day(13) + 'T00:00:00',
    daily: stats.daily.map(d => ({day: day(d.ago), sources: d.sources})),
    recent: stats.recent.map(({agoHours, agoDays, ...r}) => {
      const at = agoDays != null ? day(agoDays) + 'T12:00:00.000Z' : new Date(now - agoHours * 3600e3).toISOString();
      return {...r, published_at: at, at};
    })};
}

// One page on the fake hub. viewer: who is signed in (ana owns Acme; ben leads Sales and so Outbound below it; cara
// is in Engineering).
// briefing: false answers {briefing: null}. listening: 403 answers the stats with that status. market: false is a
// market with nothing in it yet.
// segments: the company's segments (SEGMENTS by default; [] for none). refusals: {'METHOD /api/path': {status, detail}}
// answers that write with that refusal, for a test of the server's text on failure.
async function openStrategy(browser, {viewport = PHONE, hash = '#/strategy/org', viewer = 'ana', briefing = true, listening = 200, market = true,
  segments = SEGMENTS, refusals = {}} = {}) {
  const mk = fixture('redesign-market.json');
  const fx = fixture('redesign-org.json');
  const person = fx.people.find(p => p.id === viewer);
  const me = viewer === fx.me.id ? fx.me : {id: viewer, name: person?.name || viewer, email: viewer + '@acme.example',
    role: 'member', company_role: 'member', cloud: true};
  const config = {company_name: 'Acme'};
  // Goals: the company's, one with a measure that has a target, and Sales Lead's (goal_routes.py shapes, no rights).
  const goals = [
    {id: 'g-co', title: 'Reach 100 paying customers', owner_id: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 1,
      kpis: [{id: 'k-cust', name: 'Paying customers', status: 'green', link: {kind: 'improve', baseline: 20, baseline_at: '2026-09-01', target: 100, deadline: '2026-12-31'}}]},
    {id: 'g-sales', title: 'Book 40 demos a month', owner_id: 'bot:sales-lead', parent_id: 'g-co', status: 'yellow', status_source: 'auto', rank: 1, kpis: []},
  ];
  const db = {people: fx.people, emps: fx.employees, groups: fx.groups, needs: [], goals, revision: 7,
    segments: segments.map(s => ({...s, own: {...s.own}, bets: [...s.bets]}))};
  // A segment as the list shows it, its rank its place in the list.
  const segmentItem = s => ({id: s.id, name: s.name, who: s.who, color: s.color, rank: db.segments.indexOf(s), faces: s.faces, teammates: s.teammates});
  const segmentStrategy = (s, draft) => ({draft, revision: draft ? db.revision : null, version: draft ? null : 7,
    fields: Object.keys(SEG_TITLES).map(key => {
      const changed = key in s.own;
      return {key, title: SEG_TITLES[key], text: changed ? s.own[key] : COMPANY_TEXT[key], company: COMPANY_TEXT[key], changed, from: changed ? 'segment' : 'company'};
    })});
  // The Market's compare row: no faces (the page reads them from GET /api/v2/segments).
  const compareRow = s => ({id: s.id, name: s.name, color: s.color, ...COMPARE[s.id], briefing_week: '2026-W44'});
  // The newest briefing's items, tagged: the first is the whole market's, the rest belong to Release checks.
  const changedItems = () => fixture('redesign-briefing.json').briefing.items.map((it, i) => ({...it, segment_id: i === 0 ? '' : 'segment/checks', week: '2026-W44'}));
  // One segment's market: the market fixture's numbers and five answers for the segments it has.
  const segmentPage = id => {
    const s = db.segments.find(x => x.id === id), fx = fixture('redesign-market.json').spend.offering.segments.find(x => x.id === id);
    return {segment: {id, name: s?.name || id, who: s?.who || '', color: s?.color || 'gray', rank: db.segments.indexOf(s)},
      answers: fx ? {changed: 'Two rivals now bundle release checks into their CI plans.', money: 'Most of it is CI plans, not stand-alone checks.',
        players: 'Northwind leads on bundles; Globex competes on price.'} : {},
      briefing: fx ? fixture('redesign-briefing.json').briefing : null,
      sizes: {year: 2025, dimension: 'offering', basis: 'product', chosen: fx?.size || null, rolled_usd: fx?.rolled_usd || null,
        estimates: fx?.estimates || [], disagree: false, bases: []},
      players: fx?.players || [], what_changed: []};
  };
  const writes = [];
  // A teammate joins a group and leaves the one it was in (backend/groups.py); `remove` leaves it in no group.
  const move = (kind, id, to) => {
    for (const g of db.groups) g[kind] = g[kind].filter(x => x !== id);
    if (to) db.groups.find(g => g.id === to)[kind].push(id);
    const row = kind === 'people' ? db.people.find(p => p.id === id) : db.emps.find(e => e.name === id);
    if (row) row.team = to || '';
  };

  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const refuse = (status, code, detail) => json({error: {code, detail}}, status);
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    const owner = me.role === 'owner', refuseOwner = () => refuse(403, 'forbidden', 'Only the owner');
    if (method !== 'GET') writes.push({method, path: p, body});
    const refusal = refusals[method + ' ' + p];
    if (refusal) return refuse(refusal.status, 'validation', refusal.detail);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(db.emps);
    if (p === '/api/humans') return json({people: db.people, org_groups: fx.org_groups});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: db.needs, version: 'v' + writes.length, count: db.needs.length, next: null});

    // Groups (CONTRACT §3): a bare list. The owner changes any group; a lead only adds a teammate whose group and the
    // target are both ones it leads or below them (backend/groups.py _lead_move); anyone else gets 403.
    if (p === '/api/v2/groups') return json(db.groups);
    let m = p.match(/^\/api\/v2\/groups\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const g = db.groups.find(x => x.id === decodeURIComponent(m[1]));
      if (!g) return refuse(404, 'not_found', 'No such group');
      const led = gid => { for (let x = db.groups.find(y => y.id === gid); x; x = db.groups.find(y => y.id === x.parent)) if (x.lead === 'person:' + me.id) return true; return false; };
      const homeOf = (kind, id) => (kind === 'people' ? db.people.find(q => q.id === id) : db.emps.find(e => e.name === id))?.team || '';
      const addsOnly = Object.keys(body).every(k => k === 'add') && body.add;
      const leadMove = addsOnly && led(g.id) && ['people', 'bots'].every(kind => (body.add[kind] || []).every(id => led(homeOf(kind, id))));
      if (me.role !== 'owner' && !leadMove) return refuse(403, 'forbidden', 'Only the owner, or a lead inside its own groups');
      for (const kind of ['people', 'bots']) {
        for (const id of body.add?.[kind] || []) move(kind, id, g.id);
        for (const id of body.remove?.[kind] || []) move(kind, id, '');
      }
      return json(g);
    }
    // Market.
    if (p === '/api/v2/listening/stats') return listening === 200 ? json(listeningStats()) : refuse(listening, 'forbidden', 'Not yours');
    if (p === '/api/v2/listening/destinations') return json(fixture('redesign-listening.json').destinations);
    if (p === '/api/v2/market/briefings/latest') return json(briefing ? fixture('redesign-briefing.json') : {briefing: null});
    if (p === '/api/v2/market/graph') return json({entities: [], edges: []});
    if (p === '/api/v2/market/entities') return json(market ? mk.entities : {entities: [], total: 0});
    if (p.startsWith('/api/v2/market/entities/')) return json(mk.note);
    if (p === '/api/v2/market/spend') return json(market ? mk.spend[url.searchParams.get('dimension') || 'offering'] : {segments: [], total: {spend_usd: 0}});
    if (p === '/api/v2/market/value-chain') return json(market ? mk.chain : {layers: [], edges: []});
    if (p === '/api/company-docs') return json({documents: []});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    // The Strategy Bot beside the tabs, and Goals.
    // The company's strategy: the owner reads the draft (its revision is the segments' write); anyone else the published version.
    if (p === '/api/v2/strategy') {
      const doc = status => ({id: 'v-' + status, n: status === 'draft' ? null : 7, status, guide: 'default', summary: COMPANY_TEXT.summary,
        vision: '', sections: {serve: COMPANY_TEXT.serve, sell: COMPANY_TEXT.sell, not: COMPANY_TEXT.not, extra: {}}, bets: [], note: '',
        revision: db.revision, updated_at: '2026-11-03T09:00:00Z', updated_by: 'human:ana', published_at: '2026-11-03T09:00:00Z', published_by: 'human:ana'});
      return json({...(owner ? {draft: doc('draft')} : {}), current: doc('published'), bets: [], label: 'Bets', label_one: 'Bet', may_edit: owner,
        website: 'https://acme.example', goals_by_bet: {}, live_by_bet: {}, health_by_bet: {}});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    if (p === '/api/v2/goals/tree') return json({goals: db.goals, owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({items: []});
    if (p === '/api/v2/goals' && method === 'POST') {
      const g = {id: 'g-' + (db.goals.length + 1), title: body.title, owner_id: body.owner, parent_id: body.parent_id || null, status: 'gray', rank: 9, kpis: []};
      db.goals.push(g);
      return json({goal: g});
    }
    m = p.match(/^\/api\/v2\/goals\/([^/]+)(\/status|\/kpis(?:\/([^/]+))?)?$/);
    const goal = m && db.goals.find(g => g.id === decodeURIComponent(m[1]));
    if (m && method === 'POST' && goal) {
      if (m[2] === '/status') goal.status = body.status;
      else if (m[3]) Object.assign(goal.kpis.find(k => k.id === m[3]).link, body);
      else if (m[2]) goal.kpis.push({id: 'k-' + (goal.kpis.length + 1), name: body.name, status: 'gray', link: {kind: body.kind, target: body.target ?? null, deadline: body.deadline ?? null}});
      else Object.assign(goal, body, body.owner ? {owner_id: body.owner} : {}, body.parent_id === '' ? {parent_id: null} : {});
      return json({goal});
    }
    // The company's segments (backend/segments.py): the list, one segment, its order, its strategy and its remove. The
    // owner writes; anyone else is refused.
    if (p === '/api/v2/segments' && method === 'GET') return json({segments: db.segments.map(segmentItem)});
    if (p === '/api/v2/segments' && method === 'POST') {
      if (!owner) return refuseOwner();
      const made = {id: 'segment/' + body.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), name: body.name, who: body.who || '', color: 'gray',
        faces: [], teammates: 0, own: {}, bets: []};
      db.segments.push(made);
      return json({segment: segmentItem(made)});
    }
    if (p === '/api/v2/segments/order' && method === 'POST') {
      if (!owner) return refuseOwner();
      db.segments = body.ids.map(id => db.segments.find(s => s.id === id));
      return json({segments: db.segments.map(segmentItem)});
    }
    if (p === '/api/v2/segments/remove' && method === 'POST') {
      if (!owner) return refuseOwner();
      db.segments = db.segments.filter(s => s.id !== body.id);
      return json({id: body.id, removed: true});
    }
    if (p === '/api/v2/segments/strategy' && method === 'PUT') {
      if (!owner) return refuseOwner();
      const s = db.segments.find(x => x.id === body.id);
      if (body.revision !== db.revision) return refuse(409, 'conflict', 'The draft changed');
      for (const [key, text] of Object.entries(body.sections || {})) { if (text === '') delete s.own[key]; else s.own[key] = text; }
      db.revision++;
      return json({strategy: segmentStrategy(s, true)});
    }
    const one = p.startsWith('/api/v2/segments/') ? db.segments.find(s => s.id === p.slice('/api/v2/segments/'.length)) : null;
    if (one && method === 'GET') return json({...segmentItem(one), leads: [], strategy: segmentStrategy(one, owner), bets: one.bets});
    if (one && method === 'POST') {
      if (!owner) return refuseOwner();
      Object.assign(one, {name: body.name ?? one.name, who: body.who ?? one.who, color: body.color ?? one.color});
      return json({segment: segmentItem(one)});
    }
    // The segments' markets: the compare table, one segment's five answers, and Librarian research. Sizes write through
    // their own routes (the page reads the segment again after a size write).
    if (p === '/api/v2/market/segments/compare') return json({segments: db.segments.map(compareRow), changed: changedItems()});
    const segPage = p.match(/^\/api\/v2\/market\/segments\/(.+)\/page$/);
    if (segPage && method === 'GET') return json(segmentPage(segPage[1]));
    if (/^\/api\/v2\/market\/segments\/.+\/research$/.test(p) && method === 'POST') return json({task_id: 't-research', created: true});
    if (p.startsWith('/api/v2/market/sizes/') && method === 'POST') return json({});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, writes, db, me};
}

// No sideways scroll on the page or the main column (the chart pans inside its own pane).
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
// A screenshot for the build notes, when TICO_UI_SHOTS names a folder.
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png')});
}

module.exports = {openStrategy, PHONE, DESKTOP, sideways, shot};
