// No browser: the Products list shows a column only when some product has a value in it, words not keys, and an
// agreement opens its file when it has one. Risks link to their pages, and open matters show as a count.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/products.js'), 'utf8');
const esc = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const context = vm.createContext({
  esc, API: '/api', PRODUCTS_ROUTE: '#/products',
  pageHref: (id: string) => '#/pages/' + id,
  actorLabel: (actor: string) => actor === 'human:ana' ? 'Ana' : actor,
  errorMessage: (_e: unknown, fallback: string) => fallback,
});
vm.runInContext(`${source};globalThis.prTable = prTable;`, context);
const prTable = (context as unknown as {prTable(rows: unknown[]): string}).prTable;

const product = (over: Record<string, unknown>) => ({
  id: 'product/x', key: 'x', name: 'X', kind: null, status: null, tax_kind: null, owner_teammate: null,
  repositories: [], agreements: [], depends_on: [], open_matters: 0, risks: [], ...over,
});
const heads = (html: string): string[] => [...html.matchAll(/<th>([^<]*)<\/th>/g)].map(m => m[1] ?? '');

// Only Name when nothing else is set.
assert.deepEqual(heads(prTable([product({name: 'Bare'})])), ['Name']);

const html = prTable([
  product({key: 'kit', name: 'Kit <b>', kind: 'goods', tax_kind: 'software_service', owner_teammate: 'human:ana',
    agreements: [{id: 'document/a', name: 'Supply', link_id: 'l1', file: {id: 'f1', title: 'Supply', page: true}}]}),
  product({key: 'app', name: 'App', agreements: [{id: 'document/b', name: 'Terms', link_id: 'l2', file: null}]}),
]);
assert.deepEqual(heads(html), ['Name', 'Kind', 'Tax kind', 'Agreement', 'Owner'], 'Status is empty everywhere, so it is left out');
assert.match(html, /href="#\/products\/kit"><strong>Kit &#60;b&#62;<\/strong>/, 'a name is escaped and links to its page');
assert.match(html, />Goods</);
assert.match(html, />Software</, 'a tax kind shows its word');
assert.match(html, /<a href="#\/pages\/f1">Supply<\/a>/, 'an agreement with a page opens it');
assert.match(html, /data-label="Agreement">Terms</, 'an agreement without a file is its name alone');
assert.match(html, />Ana</);
const risky = prTable([
  product({key: 'kit', open_matters: 2, risks: [{id: 't1', key: 'risk:supplier-recall', label: 'Supplier <recall>', via: ['matter']}]}),
  product({key: 'app'}),
]);
assert.deepEqual(heads(risky), ['Name', 'Risks', 'Matters']);
assert.match(risky, /<a href="#\/risk\/r\/supplier-recall">Supplier &#60;recall&#62;<\/a>/, 'a risk links to its page');
assert.match(risky, />2 open</);
assert.deepEqual(heads(prTable([product({open_matters: 0, risks: []})])), ['Name'], 'no risks and no open matters: no columns');
console.log('products-unit: ok');
