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

// The overview the Factories section reads: the factories, their pipelines (with the server's counts), and the edges and inputs.
interface OrgPipe {id: string; name: string; goal: string | null; manager_id: string | null; owner: string; visibility: string;
  factory_id: string | null; cards: number; overdue: number; for_you: number; live: number}
interface OrgFactory {id: string; name: string; goal: string | null; manager_id: string | null; done: number; overdue: number; live: number}
interface OrgOverview {factories: OrgFactory[]; pipelines: OrgPipe[]; edges: unknown[]; inputs: unknown[]}

const APP = path.join(__dirname, '..', 'app');
const CSS = path.join(__dirname, '..', 'styles', 'org-factories.css');

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

function context(get: () => Promise<unknown> = async () => { throw new Error('the test sets get'); }) {
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
    get,
  });
  for (const file of ['overview-factories.ts', 'org-factories.ts', 'redesign-org.ts']) {
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

// The markup of one box, from its opening tag to its close (boxes hold no nested section).
const box = (html: string, id: string) => {
  const hit = html.match(new RegExp(`<section class="rdo-g org-fac-box" data-org-fac="${id}"[\\s\\S]*?</section>`));
  assert.ok(hit, `a box for ${id}`);
  return hit[0];
};
// One group of the people chart, from its opening tag to the next group's.
const chartGroup = (chart: string, id: string, nextId?: string) => {
  const from = chart.indexOf(`data-rdo-group="${id}"`);
  assert.ok(from >= 0, `the people chart has group ${id}`);
  return chart.slice(from, nextId ? chart.indexOf(`data-rdo-group="${nextId}"`) : undefined);
};
const count = (html: string, re: RegExp) => (html.match(re) || []).length;
const decode = (s: string) => s.replace(/&amp;/g, '&');
const ref = (id: string) => `data-rdo-ref="bot:${id}"`;

// The combined page: the Factories section mounted, then the people chart drawn, as redesign-org.ts does.
async function page(map: OrgOverview = overview(), {mounted = true}: {mounted?: boolean} = {}) {
  const ctx = context(async () => map);
  const host = {isConnected: true, innerHTML: ''};
  let changed = false;
  if (mounted) changed = await ctx.orgFactoriesMount(host);
  return {ctx, section: host.innerHTML, chart: ctx.rdoChartHtml(), changed};
}

const tests: [string, () => unknown][] = [];
const test = (name: string, fn: () => unknown) => tests.push([name, fn]);

test('one box per factory that has pipelines', async () => {
  const {ctx} = await page();
  const html = ctx.orgFactoriesHtml(overview());
  assert.equal(count(html, /data-org-fac="/g), 3);
  assert.match(html, /<h2 id="org-fac-title">Factories<\/h2>/);
});

test('a Factory Manager sits on top only for a factory with two or more pipelines', async () => {
  const {ctx} = await page();
  const html = ctx.orgFactoriesHtml(overview());
  assert.equal(count(html, /data-org-card="fm"/g), 2);
  assert.match(box(html, 'f-growth'), /data-org-card="fm"[\s\S]*href="#\/bot\/growth-fm"/);
  assert.match(box(html, 'f-product'), /data-org-card="fm"[\s\S]*href="#\/bot\/product-fm"/, 'a bot: prefixed manager');
  assert.doesNotMatch(box(html, 'f-success'), /data-org-card="fm"/, 'one pipeline: no Factory Manager');
  assert.match(box(html, 'f-success'), /href="#\/bot\/success-mgr"/, 'its pipeline manager still shows');
});

test('a chip opens its pipeline by the route the map uses', async () => {
  const {ctx} = await page();
  const html = ctx.orgFactoriesHtml(overview());
  const chip = box(html, 'f-growth').match(/<a class="org-fac-chip" href="([^"]*)"[^>]*>Marketing/);
  assert.ok(chip, 'the Marketing card has its chip');
  assert.equal(decode(chip[1]!), vm.runInContext('ofPipelineHref("marketing")', ctx));
  assert.equal(decode(chip[1]!), '#/tasks?type=marketing&view=board');
});

test('a manager stored as a slug is shown by its roster id, name and avatar', async () => {
  const map = overview();   // Marketing's manager is stored as bot:marketing-manager (its slug)
  const {ctx} = await page(map);
  const html = ctx.orgFactoriesHtml(map);
  const card = box(html, 'f-growth');
  assert.match(card, /href="#\/bot\/b-42"/, 'the link names the roster id');
  assert.match(card, /<b>Marketing Manager<\/b>/, 'the name is the bot\'s display name');
  assert.match(card, /data-av="b-42"/, 'the avatar is the bot\'s');
  assert.doesNotMatch(html, /marketing-manager/, 'the slug appears nowhere on the card');
  // A bare slug resolves the same way.
  map.pipelines[0]!.manager_id = 'marketing-manager';
  assert.match(ctx.orgFactoriesHtml(map), /href="#\/bot\/b-42"/);
});

test('the section is locked: no people-chart controls, buttons or edit menus', async () => {
  const {ctx} = await page();
  const html = ctx.orgFactoriesHtml(overview([legal]));
  assert.doesNotMatch(html, /data-rdo-|<button|<select|<input|draggable/);
  assert.equal(count(html, /data-org-card="/g), count(html, /class="[^"]*org-fac-lock/g), 'every card carries the lock');
});

test('no factories: nothing to draw, even with pipelines outside them', async () => {
  const {ctx} = await page();
  assert.equal(ctx.orgFactoriesHtml({factories: [], pipelines: [], edges: [], inputs: []}), '');
  assert.equal(ctx.orgFactoriesHtml({factories: [], pipelines: [legal], edges: [], inputs: []}), '');
});

test('a private pipeline shows only when the overview returned it', async () => {
  const {ctx} = await page();
  const without = ctx.orgFactoriesHtml(overview());
  assert.doesNotMatch(without, /Legal|General Counsel|data-org-loose/);
  const withIt = ctx.orgFactoriesHtml(overview([legal]));
  assert.match(withIt, /data-org-loose[\s\S]*Not in a factory[\s\S]*General Counsel/);
  assert.ok(withIt.indexOf('data-org-loose') > withIt.indexOf('data-org-fac="f-success"'), 'after the factories');
});

test('a pipeline with no manager keeps its chip', async () => {
  const {ctx} = await page();
  const map = overview();
  map.pipelines[0] = pipe('marketing', 'Marketing', null, 'f-growth');
  const html = ctx.orgFactoriesHtml(map);
  assert.match(box(html, 'f-growth'), /No manager/);
  assert.match(box(html, 'f-growth'), /<a class="org-fac-chip" href="#\/tasks\?type=marketing&amp;view=board"/);
});

test('names from the overview are escaped', async () => {
  const {ctx} = await page();
  const map = overview();
  map.pipelines[1] = pipe('sales', '<img src=x onerror=alert(1)>', 'sales-mgr', 'f-growth');
  assert.doesNotMatch(ctx.orgFactoriesHtml(map), /<img/);
});

test('a factory with four pipelines draws them in one row under its Factory Manager', async () => {
  const map = overview();
  map.pipelines.push(pipe('ads', 'Ads', 'ads-mgr', 'f-growth'), pipe('seo', 'SEO', 'seo-mgr', 'f-growth'));
  const {ctx} = await page(map);
  const growth = box(ctx.orgFactoriesHtml(map), 'f-growth');
  assert.match(growth, /<div class="rdo-br"><div class="rdo-top has-row"><div class="rdo-card org-fac-card fm"/);
  assert.equal(count(growth, /<ul class="rdo-row"><li>/g), 1);
  assert.equal(count(growth, /<li><div class="rdo-card org-fac-card pm"/g), 4);
});

test('the phone rule stacks that row: the branch is held to the box and its cards go down a column', () => {
  const css = fs.readFileSync(CSS, 'utf8');
  const at = css.indexOf('@media (max-width:760px){');
  assert.ok(at >= 0, 'a phone block exists');
  const phone = css.slice(at);   // the phone block is the file's last
  assert.match(phone, /\.org-fac \.rdo-br\{[^}]*max-width:100%/, 'the branch is held to the box width');
  assert.match(phone, /\.org-fac \.rdo-br>\.rdo-row\{[^}]*flex-direction:column/, 'the four cards stack in a column');
  assert.match(phone, /\.org-fac \.rdo-br>\.rdo-row>li::before[^}]*display:none/, 'their side connectors go');
});

test('mount draws into a connected host, draws nothing when the read fails or lacks its lists, and skips a host the page has left', async () => {
  let ctx = context(async () => overview());
  const host = {isConnected: true, innerHTML: ''};
  assert.equal(await ctx.orgFactoriesMount(host), true, 'it drew bots, so the chart is drawn again');
  assert.equal(count(host.innerHTML, /data-org-fac="/g), 3);

  ctx = context(async () => { throw new Error('offline'); });
  const failed = {isConnected: true, innerHTML: ''};
  assert.equal(await ctx.orgFactoriesMount(failed), false);
  assert.equal(failed.innerHTML, '');

  ctx = context(async () => ({}));
  const bare = {isConnected: true, innerHTML: ''};
  await ctx.orgFactoriesMount(bare);
  assert.equal(bare.innerHTML, '', 'a reply without the overview lists draws nothing');

  ctx = context(async () => overview());
  const gone = {isConnected: false, innerHTML: ''};
  assert.equal(await ctx.orgFactoriesMount(gone), false);
  assert.equal(gone.innerHTML, '');
});

// ---- the combined page: the people chart and the section together

test('the drawn managers leave the people chart once the section is mounted, and their reports stay', async () => {
  const map = overview([legal]);
  const before = (await page(map, {mounted: false})).chart;
  for (const id of ['b-42', 'growth-fm', 'sales-mgr', 'product-fm', 'product-mgr', 'dev-mgr', 'success-mgr'])
    assert.match(before, new RegExp(ref(id)), `before the section, ${id} is on the chart`);

  const {chart, section, changed} = await page(map);
  assert.equal(changed, true);
  for (const id of ['b-42', 'growth-fm', 'sales-mgr', 'product-fm', 'product-mgr', 'dev-mgr', 'success-mgr', 'gc'])
    assert.doesNotMatch(chart, new RegExp(ref(id)), `${id} is not on the people chart`);
  // Marketing Manager's report, the copywriter, moves up under Growth, where its manager stood.
  assert.match(chartGroup(chart, 'g-growth', 'g-product'), new RegExp(ref('b-7')));
  // A factory with one pipeline draws no Factory Manager, so its bot stays on the chart.
  assert.match(chart, new RegExp(ref('success-fm')));
  // And every drawn bot is on the section exactly once.
  assert.equal(count(section, /data-org-card="fm"/g) + count(section, /data-org-card="pm"/g), 8);
  assert.equal(count(section, /href="#\/bot\/b-42"/g), 1, 'Marketing Manager is drawn once, on the section');
});

test('the combined page names each drawn manager once, and the chart keeps the rest', async () => {
  const {chart, section} = await page(overview([legal]));
  assert.equal(count(chart + section, /Marketing Manager/g), 1);
  assert.equal(count(chart + section, /General Counsel/g), 1);
  assert.match(chart, new RegExp(ref('b-7')));
});

test('no factories: the people chart keeps every bot', async () => {
  const {chart, changed} = await page({factories: [], pipelines: [], edges: [], inputs: []});
  assert.equal(changed, false);
  for (const id of ['b-42', 'growth-fm', 'sales-mgr', 'product-fm', 'success-mgr', 'b-7'])
    assert.match(chart, new RegExp(ref(id)), `${id} stays on the chart`);
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
