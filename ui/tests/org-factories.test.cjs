// The Factories section under the Org chart (ui/app/org-factories.ts), checked without a browser: the two classic scripts
// are compiled with esbuild's transform and run in one vm context, with the few shared globals they read stubbed.
// Run: node ui/tests/org-factories.test.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {transformSync} = require('esbuild');

const APP = path.join(__dirname, '..', 'app');
const NAMES = {'growth-fm': 'Growth Factory Manager', 'marketing-mgr': 'Marketing Manager', 'sales-mgr': 'Sales Manager',
  'product-fm': 'Product Factory Manager', 'product-mgr': 'Product Manager', 'dev-mgr': 'Dev Pipeline Manager',
  'success-mgr': 'Success Manager', gc: 'General Counsel'};

function context(get = async () => { throw new Error('the test sets get'); }) {
  const ctx = vm.createContext({
    esc: s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]),
    TASKS: '#/tasks',
    botDisplayName: id => NAMES[id] ?? id,
    avatar: (id, size) => `<span class="av" data-av="${id}" style="width:${size}px"></span>`,
    get,
  });
  for (const file of ['overview-factories.ts', 'org-factories.ts']) {
    const js = transformSync(fs.readFileSync(path.join(APP, file), 'utf8'), {loader: 'ts', target: 'es2022'}).code;
    vm.runInContext(js, ctx, {filename: file});
  }
  return ctx;
}

const pipe = (id, name, manager, factory, extra = {}) => ({id, name, goal: null, manager_id: manager, owner: 'human:ana',
  visibility: 'company', factory_id: factory, cards: 1, overdue: 0, for_you: 0, live: 0, ...extra});
const factory = (id, name, manager) => ({id, name, goal: null, manager_id: manager, done: 0, overdue: 0, live: 0});
// Two factories with two pipelines, one with one; the private Legal pipeline left out, as the server leaves it for others.
const overview = (extra = []) => ({
  factories: [factory('f-growth', 'Growth', 'growth-fm'), factory('f-product', 'Product development', 'product-fm'),
    factory('f-success', 'Success', 'success-fm')],
  pipelines: [pipe('marketing', 'Marketing', 'bot:marketing-mgr', 'f-growth', {goal: 'Find leads'}),
    pipe('sales', 'Sales', 'sales-mgr', 'f-growth'), pipe('product', 'Product', 'product-mgr', 'f-product'),
    pipe('engineering', 'Engineering', 'bot:dev-mgr', 'f-product'), pipe('success', 'Success', 'success-mgr', 'f-success'), ...extra],
  edges: [], inputs: [],
});
const legal = pipe('legal', 'Legal', 'gc', null, {visibility: 'members'});

// The markup of one box, from its opening tag to its close (boxes hold no nested section).
const box = (html, id) => {
  const hit = html.match(new RegExp(`<section class="rdo-g org-fac-box" data-org-fac="${id}"[\\s\\S]*?</section>`));
  assert.ok(hit, `a box for ${id}`);
  return hit[0];
};
const count = (html, re) => (html.match(re) || []).length;
const decode = s => s.replace(/&amp;/g, '&');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('one box per factory that has pipelines', () => {
  const ctx = context();
  const html = ctx.orgFactoriesHtml(overview());
  assert.equal(count(html, /data-org-fac="/g), 3);
  assert.match(html, /<h2 id="org-fac-title">Factories<\/h2>/);
});

test('a Factory Manager sits on top only for a factory with two or more pipelines', () => {
  const ctx = context();
  const html = ctx.orgFactoriesHtml(overview());
  assert.equal(count(html, /data-org-card="fm"/g), 2);
  assert.match(box(html, 'f-growth'), /data-org-card="fm"[\s\S]*href="#\/bot\/growth-fm"/);
  assert.doesNotMatch(box(html, 'f-success'), /data-org-card="fm"/, 'one pipeline: no Factory Manager');
  assert.match(box(html, 'f-success'), /href="#\/bot\/success-mgr"/, 'its pipeline manager still shows');
});

test('a chip opens its pipeline by the map\'s route, and a name opens its bot', () => {
  const ctx = context();
  const html = ctx.orgFactoriesHtml(overview());
  const chip = box(html, 'f-growth').match(/<a class="org-fac-chip" href="([^"]*)"[^>]*>Marketing/);
  assert.ok(chip, 'the Marketing card has its chip');
  assert.equal(decode(chip[1]), vm.runInContext('ofPipelineHref("marketing")', ctx));
  assert.equal(decode(chip[1]), '#/tasks?type=marketing&view=list');
  assert.match(box(html, 'f-growth'), /href="#\/bot\/marketing-mgr"/, 'the bot id is given without its bot: prefix');
});

test('the section is locked: no people-chart controls, buttons or edit menus', () => {
  const ctx = context();
  const html = ctx.orgFactoriesHtml(overview([legal]));
  assert.doesNotMatch(html, /data-rdo-|<button|<select|<input|draggable/);
  assert.equal(count(html, /data-org-card="/g), count(html, /class="[^"]*org-fac-lock/g), 'every card carries the lock');
});

test('no factories: nothing to draw, even with pipelines outside them', () => {
  const ctx = context();
  assert.equal(ctx.orgFactoriesHtml({factories: [], pipelines: [], edges: [], inputs: []}), '');
  assert.equal(ctx.orgFactoriesHtml({factories: [], pipelines: [legal], edges: [], inputs: []}), '');
});

test('a private pipeline shows only when the overview returned it', () => {
  const ctx = context();
  const without = ctx.orgFactoriesHtml(overview());
  assert.doesNotMatch(without, /Legal|General Counsel|data-org-loose/);
  const withIt = ctx.orgFactoriesHtml(overview([legal]));
  assert.match(withIt, /data-org-loose[\s\S]*Not in a factory[\s\S]*General Counsel/);
  assert.ok(withIt.indexOf('data-org-loose') > withIt.indexOf('data-org-fac="f-success"'), 'after the factories');
});

test('a pipeline with no manager keeps its chip', () => {
  const ctx = context();
  const map = overview();
  map.pipelines[0] = pipe('marketing', 'Marketing', null, 'f-growth');
  const html = ctx.orgFactoriesHtml(map);
  assert.match(box(html, 'f-growth'), /No manager/);
  assert.match(box(html, 'f-growth'), /<a class="org-fac-chip" href="#\/tasks\?type=marketing&amp;view=list"/);
});

test('names from the overview are escaped', () => {
  const ctx = context();
  const map = overview();
  map.pipelines[1] = pipe('sales', '<img src=x onerror=alert(1)>', 'sales-mgr', 'f-growth');
  const html = ctx.orgFactoriesHtml(map);
  assert.doesNotMatch(html, /<img/);
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
  assert.equal(bare.innerHTML, '', 'a reply without the overview lists draws nothing');

  ctx = context(async () => overview());
  const gone = {isConnected: false, innerHTML: ''};
  await ctx.orgFactoriesMount(gone);
  assert.equal(gone.innerHTML, '');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`  PASS  ${name}`); }
    catch (error) { failed++; console.log(`  FAIL  ${name}\n        ${String(error.message).split('\n').join('\n        ')}`); }
  }
  console.log(`org-factories: ${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
