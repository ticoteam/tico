// The Factories section under the Org chart (ui/app/org-factories.ts) and the people chart beside it (ui/app/redesign-org.ts),
// checked without a browser: the classic scripts are compiled with esbuild's transform and run in one vm context, with the
// few shared globals they read stubbed. The sidebar's tree helpers (orgTreeByParent and friends) are stubbed to a fixed
// tree, so the combined page is checked from the chart's own functions.
// Run: node ui/tests/org-factories.test.cjs
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

// What the Factories section reads, kept in one shape here: the factories and their pipelines. The section reads them as
// GET /api/v2/factories (the factories and each pipeline's counts) and GET /api/v2/pipelines (the records); context()
// answers both from it.
interface OrgPipe {id: string; name: string; goal: string | null; manager_id: string | null; owner: string; visibility: string;
  factory_id: string | null; cards: number; overdue: number; for_you: number; live: number}
interface OrgFactory {id: string; name: string; goal: string | null; manager_id: string | null; done: number; overdue: number; live: number}
interface OrgOverview {factories: OrgFactory[]; pipelines: OrgPipe[]; edges: unknown[]; inputs: unknown[]}

const APP = path.join(__dirname, '..', 'app');

// The roster. A bot has an id (what the chart and the routes use) and a slug (what setup can store instead).
const EMPS = [
  {id: 'b-42', slug: 'marketing-manager', display_name: 'Marketing Manager', team: 'g-growth'},
  {id: 'b-7', slug: 'copywriter', display_name: 'Copywriter', team: 'g-growth'},
  {id: 'sales-mgr', slug: 'sales-manager', display_name: 'Sales Manager', team: 'g-growth'},
  {id: 'ads-mgr', slug: 'ads-manager', display_name: 'Ads Manager', team: 'g-growth'},
  {id: 'seo-mgr', slug: 'seo-manager', display_name: 'SEO Manager', team: 'g-growth'},
  {id: 'growth-fm', slug: 'growth-factory-manager', display_name: 'Growth Factory Manager', team: 'g-growth'},
  {id: 'product-fm', slug: 'product-factory-manager', display_name: 'Product Factory Manager', team: 'g-product'},
  {id: 'product-mgr', slug: 'product-manager', display_name: 'Product Manager', team: 'g-product'},
  {id: 'dev-mgr', slug: 'dev-pipeline-manager', display_name: 'Dev Pipeline Manager', team: 'g-product'},
  {id: 'success-fm', slug: 'success-factory-manager', display_name: 'Success Factory Manager', team: 'g-success'},
  {id: 'success-mgr', slug: 'success-manager', display_name: 'Success Manager', team: 'g-success'},
  {id: 'gc', slug: 'general-counsel', display_name: 'General Counsel', team: ''},
];
const GROUPS = [{id: 'g-growth', name: 'Growth', order: 1}, {id: 'g-product', name: 'Product', order: 2},
  {id: 'g-success', name: 'Success', order: 3}];
const bot = (id: string) => ({kind: 'bot', id});

// The people chart's map as the sidebar builds it: the owner, three groups under them, and Marketing Manager's report.
const tree = () => ({
  '': [{kind: 'person', id: 'ana', person: {id: 'ana', name: 'Ana', role: 'owner'}}],
  'p:ana': GROUPS.map(g => ({kind: 'group', id: g.id, name: g.name, order: g.order})),
  'g:g-growth': [bot('b-42'), bot('sales-mgr'), bot('ads-mgr'), bot('seo-mgr'), bot('growth-fm')],
  'b:b-42': [bot('b-7')],
  'g:g-product': [bot('product-fm'), bot('product-mgr'), bot('dev-mgr')],
  'g:g-success': [bot('success-fm'), bot('success-mgr'), bot('gc')],
});

const botRow = (id: string) => { const key = String(id || '').replace(/^bot:/, ''); return key ? EMPS.find(e => e.id === key) : undefined; };
// As issues.js: a ref is a roster id, with or without `bot:`, or a slug.
const botByRef = (ref: string) => { const key = String(ref || '').replace(/^bot:/, ''); return key ? botRow(key) || EMPS.find(e => e.slug === key) : undefined; };
// As issues.js: named by the roster row's display name, which a slug or id alone does not reach.
const botDisplayName = (id: string) => { const row = botRow(id); return row?.display_name || row?.slug || id; };

function context(read: () => Promise<unknown> = async () => { throw new Error('the test sets get'); }) {
  // The two reads of one overview: an overview without its lists answers as the server's empty lists would.
  const get = async (route: string) => {
    const map = (await read()) as Partial<OrgOverview>;
    assert.match(route, /^\/v2\/factories\?period=/);
    return {factories: map.factories, pipelines: (map.pipelines || []).map(p => ({id: p.id, name: p.name, factory_id: p.factory_id, open: p.cards, files_into: [], mark: null}))};
  };
  const pipelinesAll = async () => ((await read()) as Partial<OrgOverview>).pipelines?.map(p => ({...p, retired_at: null, stages: []})) ?? [];
  const ctx = vm.createContext({
    esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
    TASKS: '#/tasks',
    BUILT_IN_GROUP: 'built-in',
    S: {emps: EMPS, people: [], me: {id: 'ana', owner_id: 'ana'}, orgGroups: GROUPS},
    botRow, botByRef, botDisplayName,
    avatar: (id: string, size: number) => `<span class="av" data-av="${id}" style="width:${size}px"></span>`,
    personAvatar: (p: {id?: string; name?: string}, size: number) => `<span class="av" data-person="${p.id || p.name}" style="width:${size}px"></span>`,
    orgTreeByParent: () => tree(),
    orgTreeWithHelpers: (by: unknown) => by,
    orgChartShape: () => {},
    orgChartTop: () => ({id: 'ana'}),
    document: {addEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false, addEventListener() {}})},
    get, pipelinesAll,
  });
  // pages-shell.ts: the names the shell keeps for every page (the page parts' ui/parts.json) load first, as in the app.
  for (const file of ['pages-shell.ts', 'pipeline-api.ts', 'overview-factories.ts', 'org-factories.ts', 'redesign-org.ts']) {
    const loader = file.endsWith('.ts') ? 'ts' : 'js';
    const js = transformSync(fs.readFileSync(path.join(APP, file), 'utf8'), {loader, target: 'es2022'}).code;
    vm.runInContext(js, ctx, {filename: file});
  }
  vm.runInContext('RDO = {folded: new Set(), groups: null, zoom: null, manual: false}', ctx);
  return ctx;
}

const pipe = (id: string, name: string, manager: string | null, factory: string | null, extra: Partial<OrgPipe> = {}): OrgPipe => ({id, name, goal: null, manager_id: manager, owner: 'human:ana',
  visibility: 'company', factory_id: factory, cards: 1, overdue: 0, for_you: 0, live: 0, ...extra});
const factory = (id: string, name: string, manager: string | null): OrgFactory => ({id, name, goal: null, manager_id: manager, done: 0, overdue: 0, live: 0});
// Two factories with two pipelines and one with one. Managers are stored three ways: a bot: prefixed slug, a bare id and a
// bare slug (setup can return either). The private Legal pipeline is left out, as the server leaves it for others.
const overview = (extra: OrgPipe[] = []): OrgOverview => ({
  factories: [factory('f-growth', 'Growth', 'growth-fm'), factory('f-product', 'Product development', 'bot:product-fm'),
    factory('f-success', 'Success', 'success-fm')],
  pipelines: [pipe('marketing', 'Marketing', 'bot:marketing-manager', 'f-growth', {goal: 'Find leads'}),
    pipe('sales', 'Sales', 'sales-mgr', 'f-growth'), pipe('product', 'Product', 'bot:product-mgr', 'f-product'),
    pipe('engineering', 'Engineering', 'dev-mgr', 'f-product'), pipe('success', 'Success', 'success-manager', 'f-success'), ...extra],
  edges: [], inputs: [],
});
const legal = pipe('legal', 'Legal', 'gc', null, {visibility: 'members'});

// The section drawn from an overview, through the same grouping the map uses.
const sectionOf = (ctx: import('node:vm').Context, map: OrgOverview): string => ctx.orgFactoriesHtml(ctx.ofGroups(
  {factories: map.factories, pipelines: map.pipelines.map(p => ({id: p.id, name: p.name, factory_id: p.factory_id, open: p.cards, files_into: [], mark: null}))},
  map.pipelines.map(p => ({...p, retired_at: null, stages: []}))));

// The markup of one box, from its opening tag to its close (boxes hold no nested section).
const box = (html: string, id: string) => {
  const hit = html.match(new RegExp(`<section class="rdo-g org-fac-box" data-org-fac="${id}"[\\s\\S]*?</section>`));
  assert.ok(hit, `a box for ${id}`);
  return hit[0];
};
const count = (html: string, re: RegExp) => (html.match(re) || []).length;
const decode = (s: string) => s.replace(/&amp;/g, '&');
const ref = (id: string) => `data-rdo-ref="bot:${id}"`;

// The combined page: the Factories section mounted, then the people chart drawn, as redesign-org.ts does.
async function page(map: OrgOverview = overview(), {mounted = true}: {mounted?: boolean} = {}) {
  const ctx = context(async () => map);
  const host = {isConnected: true, innerHTML: ''};
  if (mounted) await ctx.orgFactoriesMount(host);
  return {ctx, section: host.innerHTML, chart: ctx.rdoChartHtml()};
}

const tests: [string, () => unknown][] = [];
const test = (name: string, fn: () => unknown) => tests.push([name, fn]);


test('a Factory Manager sits on top only for a factory with two or more pipelines', async () => {
  const {ctx} = await page();
  const html = sectionOf(ctx, overview());
  assert.equal(count(html, /data-org-card="fm"/g), 2);
  assert.match(box(html, 'f-growth'), /data-org-card="fm"[\s\S]*href="#\/bot\/growth-fm"/);
  assert.match(box(html, 'f-product'), /data-org-card="fm"[\s\S]*href="#\/bot\/product-fm"/, 'a bot: prefixed manager');
  assert.doesNotMatch(box(html, 'f-success'), /data-org-card="fm"/, 'one pipeline: no Factory Manager');
  assert.match(box(html, 'f-success'), /href="#\/bot\/success-mgr"/, 'its pipeline manager still shows');
});

test('a chip opens its pipeline by the route the map uses', async () => {
  const {ctx} = await page();
  const html = sectionOf(ctx, overview());
  const chip = box(html, 'f-growth').match(/<a class="org-fac-chip" href="([^"]*)"[^>]*>Marketing/);
  assert.ok(chip, 'the Marketing card has its chip');
  assert.equal(decode(chip[1]!), vm.runInContext('pipelineHref("marketing")', ctx));
  assert.equal(decode(chip[1]!), '#/tasks?pipeline=marketing');
});

test('a manager stored as a slug is shown by its roster id, name and avatar', async () => {
  const map = overview();   // Marketing's manager is stored as bot:marketing-manager (its slug)
  const {ctx} = await page(map);
  const html = sectionOf(ctx, map);
  const card = box(html, 'f-growth');
  assert.match(card, /href="#\/bot\/b-42"/, 'the link names the roster id');
  assert.match(card, /<b>Marketing Manager<\/b>/, 'the name is the bot\'s display name');
  assert.match(card, /data-av="b-42"/, 'the avatar is the bot\'s');
  assert.doesNotMatch(html, /marketing-manager/, 'the slug appears nowhere on the card');
  // A bare slug resolves the same way.
  map.pipelines[0]!.manager_id = 'marketing-manager';
  assert.match(sectionOf(ctx, map), /href="#\/bot\/b-42"/);
});

test('the section is locked: no people-chart controls, buttons or edit menus', async () => {
  const {ctx} = await page();
  const html = sectionOf(ctx, overview([legal]));
  assert.doesNotMatch(html, /data-rdo-|<button|<select|<input|draggable/);
  assert.equal(count(html, /data-org-card="/g), count(html, /class="[^"]*org-fac-lock/g), 'every card carries the lock');
});

test('no factories: nothing to draw, even with pipelines outside them', async () => {
  const {ctx} = await page();
  assert.equal(sectionOf(ctx, {factories: [], pipelines: [], edges: [], inputs: []}), '');
  assert.equal(sectionOf(ctx, {factories: [], pipelines: [legal], edges: [], inputs: []}), '');
});


test('a pipeline with no manager is titled by its name over an empty manager slot', async () => {
  const {ctx} = await page();
  const map = overview();
  map.pipelines[0] = pipe('marketing', 'Marketing', null, 'f-growth');
  const html = sectionOf(ctx, map);
  assert.match(box(html, 'f-growth'), /<a class="org-fac-open" href="#\/tasks\?pipeline=marketing"[^>]*><b>Marketing<\/b><\/a>/);
  assert.match(box(html, 'f-growth'), /class="org-fac-empty"/);
  assert.doesNotMatch(box(html, 'f-growth'), /<b>No manager<\/b>/);
});

test('names from the overview are escaped', async () => {
  const {ctx} = await page();
  const map = overview();
  map.pipelines[1] = pipe('sales', '<img src=x onerror=alert(1)>', 'sales-mgr', 'f-growth');
  assert.doesNotMatch(sectionOf(ctx, map), /<img/);
});



test('mount draws into a connected host, draws nothing when the read fails or lacks its lists, and skips a host the page has left', async () => {
  let ctx = context(async () => overview());
  const host = {isConnected: true, innerHTML: ''};
  await ctx.orgFactoriesMount(host);
  assert.equal(count(host.innerHTML, /data-org-fac="/g), 3);

  ctx = context(async () => { throw new Error('offline'); });
  const failed = {isConnected: true, innerHTML: ''};
  await ctx.orgFactoriesMount(failed);
  assert.equal(failed.innerHTML, '');

  ctx = context(async () => ({}));
  const bare = {isConnected: true, innerHTML: ''};
  await ctx.orgFactoriesMount(bare);
  assert.equal(bare.innerHTML, '', 'a reply with no factories draws nothing');

  ctx = context(async () => overview());
  const gone = {isConnected: false, innerHTML: ''};
  await ctx.orgFactoriesMount(gone);
  assert.equal(gone.innerHTML, '');
});

// ---- the combined page: the people chart and the section together

test('a manager of a pipeline stays on the people chart with its reports, and is a card on the section', async () => {
  const {chart, section} = await page(overview([legal]));
  for (const id of ['b-42', 'growth-fm', 'sales-mgr', 'product-fm', 'product-mgr', 'dev-mgr', 'success-mgr', 'success-fm', 'b-7'])
    assert.match(chart, new RegExp(ref(id)), `${id} is on the people chart`);
  assert.equal(count(section, /data-org-card="fm"/g) + count(section, /data-org-card="pm"/g), 8);
  assert.equal(count(section, /href="#\/bot\/b-42"/g), 1, 'Marketing Manager is one card on the section');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`  PASS  ${name}`); }
    catch (error) { failed++; console.log(`  FAIL  ${name}\n        ${String((error as Error).message).split('\n').join('\n        ')}`); }
  }
  console.log(`org-factories: ${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
