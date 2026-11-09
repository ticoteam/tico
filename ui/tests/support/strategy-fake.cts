// A stand-in hub for the Strategy content scripts (ui/tests/strategy-*.cjs): the org chart's groups and moves
// (redesign CONTRACT §3), Market's spend, value chain, companies and a note, Listening's stats with `daily` and
// `recent`, and the latest market briefing, over fixtures/redesign-{org,market,listening,briefing}.json (the Acme
// example) and goals, plus the boot stubs. Writes change what a page reads back and are recorded in `writes`.
import type {Browser, Page} from 'playwright';
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./page.cts') as import('./page.cts').Api;
const {unrouted} = require('./routes.cts') as import('./routes.cts').Api;
const uiDir = path.join(__dirname, '../..');

// The browser's global S (ui/app), read by the page-ready wait.
declare const S: {me?: unknown; emps?: unknown[]} | undefined;

export interface Viewport {
  width: number;
  height: number;
}

type Json = Record<string, unknown>;

export interface StrategyWrite {
  method: string;
  path: string;
  body: Json | null;
}

export interface Person extends Json {
  id: string;
  name?: string;
  team?: string;
}

// A group's members: people and bots, each a list of ids (names for employees, as the org lists them).
export interface Group extends Json {
  id: string;
  lead?: string;
  parent?: string;
  people: string[];
  bots: string[];
}

export interface Kpi extends Json {
  id: string;
  link: Json;
}

export interface Goal extends Json {
  id: string;
  kpis: Kpi[];
}

export interface StrategyDb {
  people: Person[];
  emps: Json[];
  groups: Group[];
  needs: Json[];
  goals: Goal[];
}

export interface OpenStrategyOptions {
  viewport?: Viewport;
  hash?: string;
  viewer?: string;
  briefing?: boolean;
  listening?: number;
  market?: boolean;
}

export interface StrategyPage {
  page: Page;
  errors: string[];
  writes: StrategyWrite[];
  db: StrategyDb;
  me: Json;
}

interface OrgFixture {
  me: Json & {id: string};
  people: Person[];
  employees: Json[];
  groups: Group[];
  org_groups: unknown;
}

interface MarketFixture {
  entities: unknown;
  note: unknown;
  spend: Record<string, unknown>;
  chain: unknown;
}

interface ListeningStats extends Json {
  daily: {ago: number; sources: unknown}[];
  recent: ({agoHours?: number; agoDays?: number} & Json)[];
}

interface ListeningFixture {
  stats: ListeningStats;
  destinations: unknown;
}

function fixture<T = unknown>(name: string): T { return JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures', name), 'utf8')) as T; }

const PHONE: Viewport = {width: 390, height: 844}, DESKTOP: Viewport = {width: 1440, height: 900};

// The listening fixture's days and hours are counted back from now (a day's posts at noon UTC), so the chart's last fourteen days hold them.
function listeningStats(now = new Date()) {
  const {stats} = fixture<ListeningFixture>('redesign-listening.json');
  const day = (ago: number) => new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - ago)).toISOString().slice(0, 10);
  return {...stats, since: day(13) + 'T00:00:00',
    daily: stats.daily.map(d => ({day: day(d.ago), sources: d.sources})),
    recent: stats.recent.map(({agoHours, agoDays, ...r}) => {
      const at = agoDays != null ? day(agoDays) + 'T12:00:00.000Z' : new Date(now.getTime() - Number(agoHours) * 3600e3).toISOString();
      return {...r, published_at: at, at};
    })};
}

// One page on the fake hub. viewer: who is signed in (ana owns Acme; ben leads Sales and so Outbound below it; cara
// is in Engineering).
// briefing: false answers {briefing: null}. listening: 403 answers the stats with that status. market: false is a
// market with nothing in it yet.
async function openStrategy(browser: Browser, {viewport = PHONE, hash = '#/strategy/org', viewer = 'ana', briefing = true, listening = 200, market = true}: OpenStrategyOptions = {}): Promise<StrategyPage> {
  const mk = fixture<MarketFixture>('redesign-market.json');
  const fx = fixture<OrgFixture>('redesign-org.json');
  const person = fx.people.find(p => p.id === viewer);
  const me: Json = viewer === fx.me.id ? fx.me : {id: viewer, name: person?.name || viewer, email: viewer + '@acme.example',
    role: 'member', company_role: 'member', cloud: true};
  const config = {company_name: 'Acme'};
  // Goals: the company's, one with a measure that has a target, and Sales Lead's (goal_routes.py shapes, no rights).
  const goals: Goal[] = [
    {id: 'g-co', title: 'Reach 100 paying customers', owner_id: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 1,
      kpis: [{id: 'k-cust', name: 'Paying customers', status: 'green', link: {kind: 'improve', baseline: 20, baseline_at: '2026-09-01', target: 100, deadline: '2026-12-31'}}]},
    {id: 'g-sales', title: 'Book 40 demos a month', owner_id: 'bot:sales-lead', parent_id: 'g-co', status: 'yellow', status_source: 'auto', rank: 1, kpis: []},
  ];
  const db: StrategyDb = {people: fx.people, emps: fx.employees, groups: fx.groups, needs: [], goals};
  const writes: StrategyWrite[] = [];
  // A teammate joins a group and leaves the one it was in (backend/groups.py); `remove` leaves it in no group.
  const move = (kind: 'people' | 'bots', id: string, to: string) => {
    for (const g of db.groups) g[kind] = g[kind].filter(x => x !== id);
    if (to) db.groups.find(g => g.id === to)![kind].push(id);
    const row: Json | undefined = kind === 'people' ? db.people.find(p => p.id === id) : db.emps.find(e => e.name === id);
    if (row) row.team = to || '';
  };

  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const refuse = (status: number, code: string, detail: string) => json({error: {code, detail}}, status);
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : (req.postDataJSON?.() ?? null) as Json | null;
    const b: Json = body ?? {};
    if (method !== 'GET') writes.push({method, path: p, body});
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
    const groupMatch = p.match(/^\/api\/v2\/groups\/([^/]+)$/);
    if (groupMatch && method === 'PATCH') {
      const g = db.groups.find(x => x.id === decodeURIComponent(groupMatch[1] ?? ''));
      if (!g) return refuse(404, 'not_found', 'No such group');
      const led = (gid: string | undefined): boolean => { for (let x = db.groups.find(y => y.id === gid); x; x = db.groups.find(y => y.id === x.parent)) if (x.lead === 'person:' + me.id) return true; return false; };
      const homeOf = (kind: 'people' | 'bots', id: string): string => String((kind === 'people' ? db.people.find(q => q.id === id) : db.emps.find(e => e.name === id))?.team || '');
      const add = b.add as Partial<Record<'people' | 'bots', string[]>> | undefined;
      const remove = b.remove as Partial<Record<'people' | 'bots', string[]>> | undefined;
      const addsOnly = Object.keys(b).every(k => k === 'add') && !!add;
      const leadMove = addsOnly && led(g.id) && (['people', 'bots'] as const).every(kind => (add?.[kind] || []).every(id => led(homeOf(kind, id))));
      if (me.role !== 'owner' && !leadMove) return refuse(403, 'forbidden', 'Only the owner, or a lead inside its own groups');
      for (const kind of ['people', 'bots'] as const) {
        for (const id of add?.[kind] || []) move(kind, id, g.id);
        for (const id of remove?.[kind] || []) move(kind, id, '');
      }
      return json(g);
    }
    // Market.
    if (p === '/api/v2/listening/stats') return listening === 200 ? json(listeningStats()) : refuse(listening, 'forbidden', 'Not yours');
    if (p === '/api/v2/listening/destinations') return json(fixture<ListeningFixture>('redesign-listening.json').destinations);
    if (p === '/api/v2/market/briefings/latest') return json(briefing ? fixture('redesign-briefing.json') : {briefing: null});
    if (p === '/api/v2/market/graph') return json({entities: [], edges: []});
    if (p === '/api/v2/market/entities') return json(market ? mk.entities : {entities: [], total: 0});
    if (p.startsWith('/api/v2/market/entities/')) return json(mk.note);
    if (p === '/api/v2/market/spend') return json(market ? mk.spend[url.searchParams.get('dimension') || 'offering'] : {segments: [], total: {spend_usd: 0}});
    if (p === '/api/v2/market/value-chain') return json(market ? mk.chain : {layers: [], edges: []});
    if (p === '/api/company-docs') return json({documents: []});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    // The Strategy Bot beside the tabs, and Goals.
    if (p === '/api/v2/strategy') return json({current: null, bets: [], may_edit: true});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    if (p === '/api/v2/goals/tree') return json({goals: db.goals, owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({items: []});
    if (p === '/api/v2/goals' && method === 'POST') {
      const g: Goal = {id: 'g-' + (db.goals.length + 1), title: b.title, owner_id: b.owner, parent_id: b.parent_id || null, status: 'gray', rank: 9, kpis: []};
      db.goals.push(g);
      return json({goal: g});
    }
    const goalMatch = p.match(/^\/api\/v2\/goals\/([^/]+)(\/status|\/kpis(?:\/([^/]+))?)?$/);
    const goal = goalMatch && db.goals.find(g => g.id === decodeURIComponent(goalMatch[1] ?? ''));
    if (goalMatch && method === 'POST' && goal) {
      const kpiId = goalMatch[3];
      if (goalMatch[2] === '/status') goal.status = b.status;
      else if (kpiId) Object.assign(goal.kpis.find(k => k.id === kpiId)!.link, b);
      else if (goalMatch[2]) goal.kpis.push({id: 'k-' + (goal.kpis.length + 1), name: b.name, status: 'gray', link: {kind: b.kind, target: b.target ?? null, deadline: b.deadline ?? null}});
      else Object.assign(goal, b, b.owner ? {owner_id: b.owner} : {}, b.parent_id === '' ? {parent_id: null} : {});
      return json({goal});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, writes, db, me};
}

// No sideways scroll on the page or the main column (the chart pans inside its own pane).
const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
// A screenshot for the build notes, when TICO_UI_SHOTS names a folder.
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png')});
}

const api = {openStrategy, PHONE, DESKTOP, sideways, shot};
export type Api = typeof api;
module.exports = api;
