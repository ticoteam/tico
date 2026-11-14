import type {Page, Route} from 'playwright';
// Offline browser check that every OK opens on what will happen, in words, before Approve (spec #427, "Approvals say
// what will happen"; ui/app/decide-content.ts):
// - a Finance payment: Finance's own sentence first, the amount in dollars (not cents), the bank it is paid from and the
//   line it posts to by name, and no id, key or brace above Approve, at 390 px;
// - a Finance rule: its examples with amounts in dollars and the line they post to;
// - an email: To, From, subject, body, attachments with sizes, and no hash anywhere;
// - a Company page edition: title, who it goes to, period, link, text; its content_sha256 only under Details;
// - a spend limit proposal: one line, from and to;
// - a webhook: its name, address and what it sends in the catalog's words;
// - a budget raise (a kind with no summary of its own): labelled lines;
// - Details (at least 44 px tall) holds the exact content, unchanged; Approve sends the same shown_hash as before.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const need = (rid: string, line: string, hour: number) => ({kind: 'ask', id: rid, task_id: 't-' + rid, requirement_id: rid, line,
  since: `2026-11-09T${String(hour).padStart(2, '0')}:00:00Z`, task: brief('t-' + rid, line),
  ask: {requirement_id: rid, kind: 'ok', line, who: 'human:ana', asked_by: 'bot:pm', choices: null, may: ['ok', 'no', 'thumbs'], met: false,
    shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

// The content each OK is bound to, as its module freezes it.
const HASH = (c: string) => c.repeat(64);
const payment = {source: 'tico', action_id: 'act_8f3e2b1c9d', kind: 'payment_create',
  what: 'Pay Keelson Freight Forwarding $1,920.00 for bill B-1042 (Finance action act_8f3e2b1c9d)',
  payload: {recipient_id: 'ct_91aa', amount: 192000, money_account_id: 'ma_op1', account_id: 'acc_5400', memo: 'Bill B-1042'},
  amount: 192000, account: 'ma_op1'};
const rule = {source: 'tico', action_id: 'act_77aa01', kind: 'rule_create',
  what: "Add the rule 'Freight bills go to 5400': bills from Keelson Freight Forwarding post to Freight in.",
  payload: {kind: 'posting', name: 'Freight bills go to 5400', when: {counterparty: 'Keelson Freight Forwarding'},
    outcome: {outcome: 'post', account_id: 'acc_5400'},
    examples: [{input: {counterparty: 'Keelson Freight Forwarding', amount: 192000}, expect: 'post', account_id: 'acc_5400'},
      {input: {counterparty: 'Acme Paper', amount: 4500}, expect: 'none'}]}};
const edition = {url: 'tico:editions/3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f/3', title: 'November update', text: 'We shipped the phone review.', content_sha256: HASH('9'),
  version: 3, audience: 'Investors', audience_id: 'aud_1', period: 'November 2026', disclosure_revision: 4, rules: HASH('8')};
const proposal = {kind: 'spend_limit', change: {bot: 'ads', monthly_usd: 3000}};
const webhook = {name: 'Ops alerts', url: 'https://hooks.acme.example/tico', events: ['task.created'], rate_per_hour: 60, allow_http: false, filter: {}};
const raise = {project: 'Launch video', usd: '250.00', task_id: 'T-42'};
// A stats board, as editions.py _board_content builds it: its url ends in a 64-character hash.
const board = {url: 'tico:stat-board/aud_1/' + HASH('c'), title: 'Investors stats board', text: 'Revenue: monthly, rounded',
  content_sha256: HASH('d'), audience: 'Investors', audience_id: 'aud_1', disclosure_revision: 4};
const cap = {kind: 'task_cap', change: {sender: 'bot:ads', receiver: 'bot:pm', per_day: 5}};
// A euro book, for an approver who may not read pay-from (a manager, a budget owner): payments.py's own sentence.
const euro = {source: 'tico', action_id: 'act_eu0001', kind: 'payment_create',
  what: 'Pay 1920.00 EUR to Keelson Freight Forwarding by ach (Finance action act_eu0001)',
  payload: {recipient_id: 'ct_91aa', amount: 192000, money_account_id: 'ma_eu1', account_id: 'acc_9999'}, amount: 192000, account: 'ma_eu1'};
const OKS: Record<string, {subject: string; content: unknown; line: string}> = {
  r1: {subject: 'finance:act_8f3e2b1c9d', content: payment, line: 'OK this payment in Finance?'},
  r2: {subject: 'finance:act_77aa01', content: rule, line: 'OK this rule in Finance?'},
  r3: {subject: 'email_send:S1', content: {}, line: 'Send this email?'},
  r4: {subject: 'edition:ed1', content: edition, line: 'Publish this edition?'},
  r5: {subject: 'proposal:p1', content: proposal, line: 'Change a spend limit?'},
  r6: {subject: 'webhook:abc', content: webhook, line: 'Make this webhook endpoint?'},
  r7: {subject: 'budget_raise:Launch video', content: raise, line: 'Raise the budget?'},
  r8: {subject: 'board:aud_1', content: board, line: 'Show these stats to Investors?'},
  r9: {subject: 'proposal:p2', content: cap, line: 'Change how many tasks a teammate may send a manager a day?'},
  r10: {subject: 'finance:act_eu0001', content: euro, line: 'OK this euro payment in Finance?'},
};
const SHOWN: Record<string, string> = {r1: HASH('1'), r2: HASH('2'), r3: HASH('3'), r4: HASH('4'), r5: HASH('5'), r6: HASH('6'), r7: HASH('7'), r8: HASH('8'), r9: HASH('9'), r10: HASH('e')};
const email = {preview_complete: true, shown_hash: SHOWN.r3, body: 'Your replacement ships today.',
  bodies: [{type: 'text/plain', text: 'Your replacement ships today.'}],
  send: {id: 'S1', subject: 'Your replacement order', mailbox: 'support@acme.example', recipients: ['jo@customer.example'],
    attachments: [{part: 1, name: 'label.pdf', size: 14336, sha256: HASH('b')}], content_sha256: HASH('a')}};

// The first screen (Details shut) has no raw JSON, no hash and no id.
async function firstScreenClean(page: Page, label: string) {
  const text = await page.locator('#need-card .dc-doc').innerText();
  assert.doesNotMatch(text, /[{}]|"\w+":/, `${label}: no JSON on the first screen`);
  assert.doesNotMatch(text, /\b[0-9a-f]{24,}\b/i, `${label}: no hash on the first screen`);
  assert.doesNotMatch(text, /\b(act|acc|ma|ct|aud)_[0-9a-z]+/i, `${label}: no id on the first screen`);
  return text;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const needs = Object.entries(OKS).map(([rid, o], i) => need(rid, o.line, 10 + i));
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  let payFrom = true;                                 // false: this approver may not read pay-from (403)
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: '1', next: null});
      const ok = /^\/api\/v2\/oks\/(r\d+)$/.exec(p);
      if (ok && OKS[ok[1]!]) return json({requirement_id: ok[1], subject: OKS[ok[1]!]!.subject, content: OKS[ok[1]!]!.content,
        shown_hash: SHOWN[ok[1]!], standing: 'waiting', task_id: 't-' + ok[1]});
      if (p === '/api/v2/email/sends/S1') return json(email);
      if (p === '/api/v2/finance/pay-from' && !payFrom) return json({error: 'forbidden'}, 403);
      if (p === '/api/v2/task-caps') return json({caps: [{sender: 'bot:ads', receiver: 'bot:pm', per_day: 3, count: 0}]});
      if (p === '/api/v2/finance/pay-from') return json({accounts: [{id: 'ma_op1', name: 'Operating', last4: '1010'}], currency: 'USD', view_version: 1});
      if (p === '/api/v2/finance/chart') return json({accounts: [{id: 'acc_5400', number: '5400', name: 'Freight in'}], more: false, view_version: 1});
      if (p === '/api/v2/usage/limits') return json({bots: {ads: {monthly_usd: 2000}}, default: {}, pools: null});
      if (p === '/api/v2/event-types') return json({items: [{name: 'task.created', label: 'A task is filed', group: 'task'}]});
      if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
        writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
        const m = /^\/api\/v2\/tasks\/(t-r\d+)\/asks\/(r\d+)\/(ok|answer)$/.exec(p);
        if (m) return json({task: brief(m[1]!, 'x'), answer_id: 'a-' + m[2], requirement_id: m[2]});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    const ready = () => dialog.locator('[data-dc="approve"]:not([disabled])').waitFor();
    const open = async (rid: string) => {
      const line = OKS[rid]!.line;
      if (!(await dialog.locator('.dc-q', {hasText: line}).count())) await dialog.locator('[data-dc="next"]').click();
      await dialog.locator('.dc-q', {hasText: line}).waitFor();
      await ready();
    };
    const details = async (rid: string) => {
      const toggle = dialog.locator('.dc-details > summary');
      const box = await toggle.boundingBox();
      assert.ok(box && box.height >= 44, `${rid}: Details is at least 44 px tall`);
      await toggle.click();
      assert.deepEqual(JSON.parse(await dialog.locator('.dc-details .dc-raw').innerText()), OKS[rid]!.content, `${rid}: Details holds the exact content`);
      await toggle.click();
    };

    // r1, at 390 px: Finance's sentence first, dollars, names; the content under Details.
    await open('r1');
    let text = await firstScreenClean(page, 'payment');
    assert.equal(await dialog.locator('.dc-doc .dc-title').first().innerText(), 'Pay Keelson Freight Forwarding $1,920.00 for bill B-1042');
    assert.match(text, /\$1,920\.00/);
    assert.doesNotMatch(text, /192000/);
    assert.match(text, /Operating ••1010/);
    assert.match(text, /Freight in · 5400/);
    await details('r1');

    // r2: a rule's examples in dollars, posting to the line by name.
    await open('r2');
    text = await firstScreenClean(page, 'rule');
    assert.match(text, /Keelson Freight Forwarding \$1,920\.00 → posts to Freight in · 5400/);
    assert.match(text, /Acme Paper \$45\.00 → not matched/);
    await details('r2');

    // r3: an email: To, From, subject, body, attachment with its size; no hash anywhere.
    await open('r3');
    text = await firstScreenClean(page, 'email');
    for (const s of ['Your replacement order', 'To jo@customer.example', 'From support@acme.example', 'Your replacement ships today.', 'label.pdf · 14 KB']) assert.ok(text.includes(s), `email shows ${s}`);
    assert.doesNotMatch(await dialog.locator('.dc-doc').innerHTML(), /aaaaaaaa|bbbbbbbb/, 'no email hash, not even hidden');

    // r4: an edition: title, audience, period, link, text; the hash only under Details.
    await open('r4');
    text = await firstScreenClean(page, 'edition');
    for (const s of ['November update', 'Investors', 'November 2026', 'We shipped the phone review.']) assert.ok(text.includes(s), `edition shows ${s}`);
    assert.doesNotMatch(text, /tico:|3f2a9c1e/, 'an app-internal link (and the id in it) stays under Details');
    await details('r4');

    // r5: a spend limit: from and to in one line.
    await open('r5');
    text = await firstScreenClean(page, 'proposal');
    assert.match(text, /Spend limit for .*: \$2,000\.00 → \$3,000\.00 a month/);
    await details('r5');

    // r6: a webhook: name, address, what it sends in words.
    await open('r6');
    text = await firstScreenClean(page, 'webhook');
    for (const s of ['Ops alerts', 'https://hooks.acme.example/tico', 'A task is filed']) assert.ok(text.includes(s), `webhook shows ${s}`);
    await details('r6');

    // r7: a budget raise: labelled lines.
    await open('r7');
    text = await firstScreenClean(page, 'budget raise');
    assert.match(text, /Project\s+Launch video/);
    assert.match(text, /Amount\s+\$250\.00/);
    assert.doesNotMatch(text, /T-42/, 'an id field stays under Details');
    await details('r7');

    payFrom = false;
    // Approve sends the same hash it always did.
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();
    for (let i = 0; i < 50 && !writes.some(w => w.p === '/api/v2/tasks/t-r7/asks/r7/ok'); i++) await page.waitForTimeout(100);
    assert.deepEqual(writes.find(w => w.p === '/api/v2/tasks/t-r7/asks/r7/ok')?.body, {shown_hash: SHOWN.r7});

    // r8: a stats board: its tico:stat-board link and the hash in it are not on the first screen. Approve moves on to it
    // by itself; clicking Next before it shows would skip it.
    await dialog.locator('.dc-q', {hasText: OKS.r8!.line}).waitFor();
    await ready();
    text = await firstScreenClean(page, 'board');
    for (const s of ['Investors stats board', 'Investors', 'Revenue: monthly, rounded']) assert.ok(text.includes(s), `board shows ${s}`);
    assert.doesNotMatch(text, /tico:|Link/);
    await details('r8');

    // r9: a task cap: from and to.
    await open('r9');
    text = await firstScreenClean(page, 'task cap');
    assert.match(text, /Tasks .+ may send .+: 3 a day → 5 a day/);
    await details('r9');

    // r10: a euro payment for an approver who may not read pay-from: never a dollar sign; the bank is named as unseen.
    await open('r10');
    text = await firstScreenClean(page, 'euro payment');
    assert.match(text, /Amount\s+€1,920\.00/, "the currency Finance's own sentence names");
    assert.doesNotMatch(text, /\$/, 'no guessed dollar sign');
    assert.match(text, /Paid from\s+A bank account you can.t see here/);
    await details('r10');

    assert.deepEqual(errors, []);
    console.log('PASS: every OK kind opens on what will happen in words; JSON and hashes only under Details; Approve sends the same hash.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
