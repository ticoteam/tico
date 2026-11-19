// No browser: Company › Risk's words and rows (ui/app/risk.ts). Money reads exact on a row and compact in a total, live
// risks open with their matters while open and quiet ones are one line, the rules icon shows only on a risk with rules,
// a matter in the viewer's Needs you carries the Flow dot, and a typed amount becomes minor units. Dates sit in four
// groups in the tab's order, read in ink or grey by their basis, and show only the proof the viewer may open.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const esc = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const context = vm.createContext({
  Intl, Date, Number, Math, Set, Map, URL, URLSearchParams, encodeURIComponent, API: '/api',
  document: {addEventListener: () => {}, hidden: true, documentElement: {}},
  setInterval: () => 0, requestAnimationFrame: () => 0, addEventListener: () => {}, matchMedia: () => ({matches: false}),
  MutationObserver: class { observe() {} }, HTMLInputElement: class {},
  S: {v2: {needs: []}},
  RISK_ROUTE: '#/risk', RULES_ROUTE: '#/rules', DIR_ROUTE: '#/library/contacts',
  esc,
  needsMarkHTML: () => '<span class="needs-mark"></span>',
  actorAvatar: (id: string) => `<i data-face="${id}"></i>`,
});
// The app's own date words (ui/app/format.ts dayWords): a risk's next date is a day (YYYY-MM-DD, from its obligations)
// or a time (a task's due_at), and both read as the same local day.
Object.defineProperty((context as {HTMLInputElement: {prototype: object}}).HTMLInputElement.prototype, 'value', {get: () => '', set: () => {}});
for (const file of ['format.js', 'risk.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../../build/ui/app/', file), 'utf8'), context);
const {rkMoney, rkClaimed, rkGroups, rkMinor, rkRisksHTML, rkDisputesHTML, rkDatesHTML, rkDueSort, rkProofHTML} = context;

// Money: exact on a row, compact in a total, one total per currency, nothing when nothing is claimed.
assert.equal(rkMoney(5_000_000, 'USD'), '$50,000');
assert.equal(rkMoney(1_924_488, 'usd'), '$19,244.88');
assert.equal(rkMoney(18_200_000, 'USD', true), '$182k');
assert.equal(rkClaimed([{currency: 'USD', amount: 1_200_000}, {currency: 'EUR', amount: 300_000}]), '$12k + €3k claimed');
assert.equal(rkClaimed([{currency: 'USD', amount: 0}]), '');
assert.equal(rkClaimed([]), '');

// A typed amount: commas and a symbol are fine, a blank is none, anything else is refused.
assert.equal(rkMinor('1,240.50'), 124050);
assert.equal(rkMinor('$50000'), 5_000_000);
assert.equal(rkMinor('  '), null);
assert.ok(Number.isNaN(rkMinor('about 5k')));
assert.ok(Number.isNaN(rkMinor('1.234')));

const DAY = 86_400_000;
const risk = (key: string, label: string, level: 'live' | 'open' | 'quiet', more: Record<string, unknown> = {}) => ({
  id: 'r-' + key, key: 'risk:' + key, label, level, open: 0, closed: 0, claimed: [], next_date: null, waits: false, has_rules: false, ...more});
const row = (id: string, riskKey: string, name: string, more: {days?: number; status?: string; amount?: number; forum?: string} = {}) => {
  const at = new Date(Date.now() - (more.days ?? 30) * DAY).toISOString();
  return {
    task: {id, title: name + ' task', status: more.status || 'open', stage: 'Doing', owner_id: 'bot:contracts-manager', created_at: at, due_at: null},
    matter: {kind: 'dispute', party: {id: 'p-' + id, name}, amount: more.amount ?? null, currency: 'USD', claim: null, forum: more.forum || null, created_at: at, version: 1},
    risks: [{id: 'r-' + riskKey, key: 'risk:' + riskKey, label: riskKey}],
  };
};

// The groups keep the server's order within each level.
const risks = [
  risk('theft', 'Customer theft claims', 'live', {open: 2, closed: 3, claimed: [{currency: 'USD', amount: 14_040_000}], has_rules: true, waits: true}),
  risk('pros', 'Pro disputes', 'open', {open: 1}),
  risk('marks', 'Trademarks', 'quiet', {closed: 1, next_date: '2031-03-06'}),
  risk('privacy', 'Data privacy', 'quiet'),
];
const g = rkGroups(risks);
assert.deepEqual(JSON.parse(JSON.stringify([g.live.map((r: {label: string}) => r.label), g.open.map((r: {label: string}) => r.label), g.quiet.map((r: {label: string}) => r.label)])),
  [['Customer theft claims'], ['Pro disputes'], ['Trademarks', 'Data privacy']]);

const open = [row('t1', 'theft', 'Randazzo', {days: 2, amount: 5_000_000, forum: 'Demand'}), row('t2', 'theft', 'Murphy', {days: 21, forum: 'AAA'}), row('t3', 'pros', 'Flores')];
const html: string = rkRisksHTML(risks, open, new Set(['t2']));
// The live risk opens: its name large with its counts and the rules icon, its matters under it, a way to the closed ones.
const live = html.slice(html.indexOf('data-rk-level="live"'), html.indexOf('</section>'));
assert.match(live, /Customer theft claims<\/a><em><span>2 open<\/span><span>\$140k claimed<\/span><\/em><span class="nav-icon rk-rules"/);
assert.match(live, /Randazzo<\/a><span class="pill rk-pill">New<\/span>/, 'a matter from two days ago is New');
assert.match(live, /Murphy<\/a><span class="pill rk-pill">AAA<\/span>/, 'an older one shows its forum');
assert.ok(!live.includes('Flores'), 'a matter shows under its own risk only');
assert.match(live, /3 closed/);
// The Flow dot on the matter in the viewer's Needs you, and only there.
assert.equal((live.match(/needs-mark/g) || []).length, 1);
assert.match(live, /data-rk-task="t2"><span class="rk-mk"><span class="needs-mark">/);
// Open and quiet: one line each, under a label; open names its parties, quiet its next date or what it closed.
assert.match(html, /<div class="rk-lbl">Open<\/div>.*Pro disputes<\/span><span class="rk-x">Flores<\/span><span class="rk-c">1 open<\/span>/s);
assert.match(html, /<div class="rk-lbl">Quiet<\/div>.*Trademarks<\/span><span class="rk-x">Thu, Mar 6, 2031<\/span><span class="rk-c">1 closed<\/span>/s);
assert.match(html, /Data privacy<\/span><span class="rk-x"><\/span><span class="rk-c">None<\/span><span class="rk-r"><\/span>/, 'no rules, no icon');
assert.equal((html.match(/rk-rules/g) || []).length, 1, 'the rules icon on the risk with rules alone');
assert.match(rkRisksHTML([], [], new Set()), /No risks yet\./);
// A day and a time on that day read alike, whichever shape the server sends.
const quietOn = (next: string) => (rkRisksHTML([risk('q', 'Quiet one', 'quiet', {next_date: next})], [], new Set()) as string).match(/<span class="rk-x">([^<]*)</)?.[1];
assert.equal(quietOn('2031-03-06'), 'Thu, Mar 6, 2031');
assert.equal(quietOn('2031-03-06T12:00:00Z'), 'Thu, Mar 6, 2031');

// Disputes: the count and the amount claimed head the columns; a closed matter is under Closed with no Next.
const closed = [row('t9', 'pros', 'Maple', {status: 'done', amount: 95_000})];
const d: string = rkDisputesHTML(open, closed, new Set());
assert.match(d, /<span class="rk-tw">3 open<\/span><span class="rk-k">Risk<\/span>\s*<span class="rk-a">\$50k claimed<\/span>/);
assert.match(d, /data-label="Amount">\$50,000<\/span>\s*<span class="rk-f" data-label="Forum">Demand<\/span>/);
assert.match(d, /<div class="rk-lbl">Closed<\/div><div class="rk-row rk-matter rk-done" data-rk-task="t9">.*<span class="rk-m">Closed<\/span><span class="rk-d" data-label="Next"><\/span>/s);
assert.ok(!d.includes('needs-mark'), 'nothing waits on this viewer');
assert.match(rkDisputesHTML([], [], new Set()), /No disputes\./);

// Dates: one or two per group, out of order, as a live patch may leave them.
type Proof = {kind: string; ref: string | null; name: string | null};
type Due = {id: string; due_on: string; title: string; basis: string; state: string | null; group: string; owner: string;
  rule: {id: string; kind: string; text: string; source: null}; risks: {id: string; key: string; label: string}[]; task: null;
  mark: {satisfied_by: string; satisfied_at: string; outcome: string; proof: Proof | null; note: string} | null};
const date = (id: string, due_on: string, group: string, state: string | null, basis: string, more: Partial<Due> = {}): Due => ({
  id, due_on, title: id, basis, state, group, owner: 'bot:legal-owner', rule: {id: 'rule-1', kind: 'internal', text: 'Meet every date', source: null},
  risks: [{id: 'r-theft', key: 'risk:theft', label: 'Customer theft claims'}], task: null, mark: null, ...more});
const dates = [
  date('Hearing', '2031-03-20', 'later', 'due', 'order'),
  date('Production', '2031-02-25', 'past', 'met', 'order', {mark: {satisfied_by: 'human:dana', satisfied_at: '2031-02-27T12:00:00Z', outcome: 'met',
    proof: {kind: 'link', ref: 'https://www.files.acme.example/p', name: null}, note: ''}}),
  date('Reply', '2031-02-26', 'past', 'passed', 'asked'),
  date('Discovery', '2031-03-09', 'next', 'due', 'theirs'),
  date('Premium', '2031-02-01', 'unconfirmed', 'unconfirmed', 'contract'),
  date('Records', '2031-03-07', 'next', 'due', 'order'),
];
// Unconfirmed, then next and later soonest first, then past newest first by its day or its mark.
assert.deepEqual(JSON.parse(JSON.stringify(rkDueSort(dates).map((x: Due) => x.id))), ['Premium', 'Records', 'Discovery', 'Hearing', 'Production', 'Reply']);
const tab: string = rkDatesHTML(dates);
assert.deepEqual([...tab.matchAll(/data-rk-group="\w+">([^<]+)</g)].map(m => m[1]), ['Unconfirmed', 'Next 14 days', 'Later', 'Past 30 days']);
const rowOf = (id: string) => { const at = tab.lastIndexOf('<div', tab.indexOf(`data-rk-due="${id}"`)); return tab.slice(at, tab.indexOf('</div>', at)); };
// An unconfirmed binding date is loud; a binding one is ink; one the other side asked for or must meet is grey.
assert.match(rowOf('Premium'), /class="rk-row rk-due rk-bind rk-loud"/);
assert.match(rowOf('Premium'), /<span class="rk-un">Unconfirmed<\/span>/);
assert.match(rowOf('Premium'), /<span class="rk-k">Customer theft claims<\/span>/, 'the tab names the risk');
assert.match(rowOf('Records'), /class="rk-row rk-due rk-bind"/);
assert.match(rowOf('Discovery'), /class="rk-row rk-due rk-soft"/);
assert.match(rowOf('Discovery'), /<span class="rk-os"><\/span>/, 'a date theirs to meet shows no owner of ours');
assert.match(rowOf('Reply'), /<span>Passed<\/span>/);
// A due date shows its owner; a met one a tick, the day it was met, who met it, and its link by host.
assert.match(rowOf('Records'), /<span class="rk-os"><i data-face="bot:legal-owner"><\/i><\/span>/);
assert.match(rowOf('Production'), /aria-label="Met">check<\/span><span>Thu, Feb 27, 2031<\/span><i data-face="human:dana">/);
assert.match(rowOf('Production'), /href="https:\/\/www\.files\.acme\.example\/p"[^>]*>.*<span>files\.acme\.example<\/span><\/a>/);
assert.match(rkDatesHTML([]), /No dates\./);
// A tab cut at its cap offers More; a whole one does not.
assert.doesNotMatch(tab, /data-rk-dates-more/);
assert.match(rkDatesHTML(dates, true), /data-rk-dates-more>More</);
// Proof the viewer cannot open keeps its kind and no link; a file opens by its id; a sent email reads Sent mail.
assert.equal(rkProofHTML({kind: 'file', ref: null, name: null}), '<span class="rk-pf"><span class="nav-icon" aria-hidden="true">description</span><span>File</span></span>');
assert.match(rkProofHTML({kind: 'file', ref: 'f1', name: 'receipt.pdf'}), /href="\/api\/v2\/files\/f1".*receipt\.pdf/);
assert.match(rkProofHTML({kind: 'email', ref: 'e1', name: 'Premium paid'}), /title="Premium paid".*Sent mail/);
assert.equal(rkProofHTML(null), '');
// A live risk: its dates beside its matters, the unconfirmed count in its head; a live risk with no dates has no column.
const withDates: string = rkRisksHTML([risk('theft', 'Customer theft claims', 'live', {open: 1, unconfirmed: 1})], [], new Set(), dates);
assert.match(withDates, /<em><span>1 open<\/span><span>1 unconfirmed<\/span><\/em>/);
assert.match(withDates, /<div class="rk-rows rk-dts" data-rk-dates>.*data-rk-due="Premium".*data-rk-due="Records"/s);
assert.ok(!withDates.includes('All dates'), 'six dates fit');
assert.ok(!(rkRisksHTML([risk('pros', 'Pro disputes', 'live', {open: 1})], [], new Set(), dates) as string).includes('rk-dts'));

console.log('risk-unit: ok');
