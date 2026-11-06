// Tools (ui/app/tools.js, tools-api.js and the inbox pages under it) over a fake API in backend/api_tools.py's shapes: GET
// /api/v2/tool-index (rows, counts), one API tool (GET/PATCH /api/v2/api-tools/<id>, PUT …/access/<subject>, POST
// …/check, GET …/activity) and the mailboxes fake (support/mailboxes-fake.cjs) for a mailbox's page and its Limits.
// Covers: the list, its filters and Needs you; an API tool's level change, Check now and Move to proxy; the Activity
// tab's filter; Who can see it (Private pills, Only listed's Who | Why, Remove, Add through the audience picker, back
// to Everyone) and Finance's read-only row; the mailbox Limits card; the old addresses' redirects; 390px with no
// sideways scroll; light and dark.
// TICO_UI_SHOTS=<dir> saves a screenshot of each page in each theme and width.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {mailboxesFake, open} = require('./support/mailboxes-fake.cjs');
const {googleFake} = require('./support/google-fake.cjs');

const at = mins => new Date(Date.UTC(2026, 10, 5, 12, 0) - mins * 60000).toISOString();
// Rows as backend/api_tools.py index_page sends them: `who` is {kind: 'bot'|'person', id, name, initials}.
const who = (kind, id, name) => ({kind, id, name, initials: name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()});
const ROWS = [
  {id: 'api:t-crm', kind: 'api', name: 'Acme CRM', detail: 'api.crm.acme.example', way: 'Proxy', status: {tone: 'ok', text: 'Working'},
    who: [who('person', 'ana', 'Ana'), who('bot', 'support-bot', 'Support bot'), who('bot', 'sales', 'Sales'), who('bot', 'ops', 'Ops'), who('bot', 'books', 'Books')],
    who_total: 7, needs_you: false, href: '#/tools/api/t-crm', service: 'acme-crm'},
  {id: 'api:t-billing', kind: 'api', name: 'Acme Billing', detail: 'billing.acme.example', way: 'On computers', status: {tone: 'fail', text: 'Key rejected'},
    who: [who('bot', 'books', 'Books')], who_total: 1, needs_you: true, href: '#/tools/api/t-billing', service: 'acme-billing', private: true},
  {id: 'mailbox:support@acme.example', kind: 'mailbox', name: 'support@acme.example', detail: '', way: 'Google', status: {tone: 'ok', text: 'Connected'},
    who: [who('person', 'cara', 'Cara'), who('bot', 'support-bot', 'Support bot')], who_total: 2, needs_you: false,
    href: '#/tools/mailbox/support@acme.example', mailbox_id: 'mb-support'},
  {id: 'number:ibx-1', kind: 'number', name: 'Mobile number', detail: '+1 555 0100', way: 'Tico', status: {tone: 'ok', text: 'On'},
    who: [], who_total: 0, needs_you: false, href: '#/tools/number/ibx-1'},
  {id: 'slack', kind: 'slack', name: 'Slack', detail: 'acme.slack.com', way: 'Slack app', status: {tone: 'wait', text: 'Waiting'},
    who: [who('bot', 'support-bot', 'Support bot')], who_total: 12, needs_you: true, href: '#/tools/slack'},
  {id: 'github', kind: 'github', name: 'GitHub', detail: '', way: 'GitHub App', status: {tone: 'idle', text: 'Off'},
    who: [], who_total: 0, needs_you: false, href: '#/tools/github'},
];
const COUNTS = {all: 6, needs_you: 2, api: 2, mail: 2, websites: 0};

// One API tool's document as GET /api/v2/api-tools/<id> sends it (backend/api_tools.py detail), and the writes that
// change it, answered as the hub answers them. A logged path keeps the query's keys only.
function apiFake() {
  const checks = (n, key) => Array.from({length: n}, (_, i) => ({ok: i !== 3, status_code: i === 3 ? 401 : 200,
    detail: i === 3 ? 'Key rejected' : 'Working', checked_at: at(i * 30), credential_id: key}));
  const call = (i, result, extra = {}) => ({id: 'c' + i, kind: 'call', actor: 'bot:support-bot', bot: 'support-bot', access_level: 'read',
    method: 'GET', path: '/v1/leads?q=', status_code: result === 'sent' ? 200 : null, result, detail: '', duration_ms: 120, at: at(i * 7), ...extra});
  const refused = i => call(i, 'refused', {actor: 'bot:ops', bot: 'ops', access_level: '', method: 'POST', path: '/v1/leads', status_code: 403,
    detail: 'Ops has no access to Acme CRM. Set a level in Tools > Acme CRM > Who can use it'});
  const key = (id, name, preview, level, extra) => ({id, name, preview, access_level: level, expires_on: null, env: 'ACME_CRM_API_KEY', username: '',
    stored: true, created_by: 'human:ana', created_at: at(60 * 24 * 30), last_used_at: null, last_used_by: '', ...extra});
  const tools = {
    't-crm': {tool: {id: 't-crm', name: 'Acme CRM', service: 'acme-crm', base_url: 'https://api.crm.acme.example', auth_scheme: 'bearer', check_path: '/v1/me', delivery: 'proxy'},
      keys: [key('k-read', 'Read key', 'sk_…4f2a', 'read', {expires_on: '2027-01-31', last_used_at: at(4), last_used_by: 'support-bot'}),
        key('k-write', 'Write key', 'sk_…9c1d', 'read_write', {created_at: at(60 * 24 * 20), last_used_at: at(90), last_used_by: 'sales'})],
      access: [{subject: 'human:ana', kind: 'person', id: 'ana', name: 'Ana', initials: 'A', level: 'read_write', last_used_at: at(200)},
        {subject: 'bot:support-bot', kind: 'bot', id: 'support-bot', name: 'Support bot', initials: 'SB', level: 'read', last_used_at: at(4)},
        {subject: 'bot:sales', kind: 'bot', id: 'sales', name: 'Sales', initials: 'S', level: 'read_write', last_used_at: at(90)}],
      checks: checks(24, 'k-read'), recent: [call(1, 'sent'), refused(2), call(3, 'sent')],
      status: {tone: 'ok', text: 'Working'}, needs_you: false, can_manage: true, private: false, can_set_audience: false},
    't-billing': {tool: {id: 't-billing', name: 'Acme Billing', service: 'acme-billing', base_url: 'https://billing.acme.example', auth_scheme: 'header:X-Api-Key', check_path: '/status', delivery: 'computer'},
      keys: [key('k-bill', 'Key', 'ak_…77e0', 'read_write', {env: 'ACME_BILLING_API_KEY', created_at: at(60 * 24 * 90)})],
      access: [{subject: 'bot:books', kind: 'bot', id: 'books', name: 'Books', initials: 'B', level: 'read_write', last_used_at: null},
        {subject: 'finance', kind: 'finance', id: 'finance', name: 'Finance', initials: 'F', level: 'read', last_used_at: at(30), fixed: true}],
      checks: checks(5, 'k-bill').map(c => ({...c, ok: false, status_code: 401, detail: 'Key rejected'})), recent: [],
      status: {tone: 'fail', text: 'Key rejected'}, needs_you: true, can_manage: true,
      private: true, can_set_audience: true, audience: {everyone: false, people: ['cara'], teams: [], bots: []}},
  };
  // Who can see it as backend/api_tools.py seen_by lists it: the owner, the list, then whoever can use it.
  const NAMES = {ana: 'Ana', cara: 'Cara', 'g-books': 'Bookkeeping'};
  const seenBy = t => !t.audience ? [] : [{subject: 'human:ana', kind: 'person', id: 'ana', name: 'Ana', why: 'Owner'},
    ...t.audience.people.filter(id => id !== 'ana').map(id => ({subject: 'human:' + id, kind: 'person', id, name: NAMES[id] || id, why: 'Listed'})),
    ...t.audience.teams.map(id => ({subject: 'group:' + id, kind: 'group', id, name: NAMES[id] || id, why: 'Listed'})),
    ...t.audience.bots.map(id => ({subject: 'bot:' + id, kind: 'bot', id, name: id, why: 'Listed'})),
    ...t.access.map(a => ({subject: a.subject, kind: a.kind, id: a.id, name: a.name, why: 'Can use it'}))];
  for (const t of Object.values(tools)) t.seen_by = seenBy(t);
  const calls = [call(1, 'sent'), refused(2), call(3, 'sent'), call(4, 'failed', {status_code: null, detail: 'ConnectTimeout: timed out'})];
  const fake = {tools, writes: [], reads: []};
  fake.answer = (p, method, body, url) => {
    if (p === '/api/v2/tool-index') return {rows: structuredClone(ROWS), counts: COUNTS, next: null};
    if (p === '/api/v2/tools') return {integrations: []};
    if (p === '/api/v2/groups') return {groups: [{id: 'g-books', name: 'Bookkeeping'}]};
    if (p.startsWith('/api/v2/tools/')) return {service: decodeURIComponent(p.split('/').pop()), title: 'GitHub', access: '', writes: 'read', owner: 'tico', declared_as: '', body: '', queries: [], learnings: [], read_only: true};
    const m = p.match(/^\/api\/v2\/api-tools\/([^/]+)(\/.*)?$/);
    if (!m) return undefined;
    if (method !== 'GET') fake.writes.push({method, path: p, body});
    const t = tools[decodeURIComponent(m[1])], rest = m[2] || '';
    if (!t) return {status: 404, body: {error: {code: 'not_found', detail: 'No such tool', retryable: false}}};
    if (!rest && method === 'GET') return structuredClone(t);
    if (!rest && method === 'PATCH') {
      const {audience, ...fields} = body;
      Object.assign(t.tool, fields);
      if ('audience' in body) { t.audience = audience; t.private = t.tool.private = !!audience; t.seen_by = seenBy(t); }
      return structuredClone(t.tool);
    }
    const acc = rest.match(/^\/access\/(.+)$/);
    if (acc && method === 'PUT') {
      const subject = decodeURIComponent(acc[1]), row = t.access.find(a => a.subject === subject);
      if (body.level === 'none') t.access = t.access.filter(a => a !== row); else if (row) row.level = body.level;
      return {subject, level: body.level === 'none' ? null : body.level};
    }
    if (rest === '/check' && method === 'POST') {
      const rows = t.keys.map(k => ({credential_id: k.id, ok: true, status_code: 200, detail: 'Working', checked_at: at(-5)}));
      t.checks.unshift(...rows);
      return {ok: true, status_code: 200, detail: 'Working', checked_at: at(-5), rows};
    }
    if (rest === '/activity') {
      fake.reads.push(url.search);
      const result = url.searchParams.get('result');
      return {rows: calls.filter(c => !result || c.result === result), next: null};
    }
    return undefined;
  };
  return fake;
}

const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme);
    await page.evaluate(() => Promise.all(document.getAnimations()
      .filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({animations: 'disabled', path: path.join(dir, `tools-${name}-${theme}.png`), fullPage: true});
  }
}
async function start(browser, hash, {width = 1440, ready} = {}) {
  const api = apiFake(), mail = mailboxesFake();
  const got = await open(browser, mail, googleFake(), {hash, width, height: width < 500 ? 844 : 900, ready, extra: (p, method, body, url) => api.answer(p, method, body, url)});
  return {...got, api, mail};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const width of [1440, 390]) {
      const tag = width < 500 ? 'phone' : 'desktop';
      // The list: every row, its faces and "+N more", the filters and Needs you; Websites stays hidden at zero.
      {
        const {page, seen} = await start(browser, '#/tools', {width, ready: 'tr[data-tl-row]'});
        const rows = page.locator('tr[data-tl-row]');
        assert.equal(await rows.count(), 6, `${tag}: all rows`);
        assert.equal(await page.locator('[data-tl-show="websites"]').count(), 0, `${tag}: no Websites filter at zero`);
        const crm = page.locator('tr[data-tl-row="api:t-crm"]');
        assert.equal(await crm.locator('.tt-face').count(), 4, `${tag}: four faces`);
        assert.match(await crm.locator('.tt-more').innerText(), /\+3 more/, `${tag}: the rest counted`);
        assert.equal(await crm.locator('.tt-face').first().getAttribute('title'), 'Ana', `${tag}: named on hover`);
        await page.locator('[data-tl-show="needs"]').click();
        assert.deepEqual(await rows.evaluateAll(r => r.map(x => x.dataset.tlRow)), ['api:t-billing', 'slack'], `${tag}: Needs you`);
        assert.deepEqual(await page.locator('tr[data-tl-row] [data-private]').evaluateAll(p => p.map(x => [x.closest('tr').dataset.tlRow, x.textContent])),
          [['api:t-billing', 'Private']], `${tag}: the Private pill on the private tool only`);
        assert.equal(await page.evaluate(() => location.hash), '#/tools?show=needs', `${tag}: the filter is in the address`);
        await page.locator('[data-tl-show="mail"]').click();
        assert.deepEqual(await rows.evaluateAll(r => r.map(x => x.dataset.tlKind)), ['mailbox', 'number'], `${tag}: Mail and numbers`);
        await page.locator('[data-tl-show="api"]').click();
        assert.equal(await rows.count(), 2, `${tag}: APIs`);
        await page.locator('[data-tl-show=""]').click();
        assert.equal(await sideways(page), false, `${tag}: the list has no sideways scroll`);
        if (tag === 'desktop') await page.locator('#side a[data-nav="tools"].cur').waitFor({state: 'attached'});
        await shot(page, `list-${tag}`);
        await rows.filter({hasText: 'Acme CRM'}).locator('.tt-st').click();
        await page.waitForFunction(() => location.hash === '#/tools/api/t-crm');
        await page.locator('#tool-access').waitFor();
        assert.deepEqual(seen.errors, [], `${tag}: list errors`);
        await page.close();
      }
      // An API tool: Who can use it with levels, Recent, Keys, Check, Limits not yet.
      {
        const {page, seen, api} = await start(browser, '#/tools/api/t-crm', {width, ready: '#tool-access tr'});
        assert.equal(await page.locator('#tool-access tr').count(), 3, `${tag}: who can use it`);
        assert.equal(await page.locator('#tool-recent tbody tr').count(), 3, `${tag}: recent calls`);
        assert.equal(await page.locator('#tool-keys [data-ta-key]').count(), 2, `${tag}: keys`);
        assert.match(await page.locator('#tool-keys').innerText(), /Through Tico · logged/, `${tag}: proxied`);
        assert.equal(await page.locator('#tool-keys [data-ta-proxy]').count(), 0, `${tag}: nothing to move`);
        assert.equal(await page.locator('#tool-check .tt-sq').count(), 24, `${tag}: 24 squares`);
        assert.equal(await page.locator('#tool-check .tt-sq.fail').count(), 1, `${tag}: the failed check`);
        assert.match(await page.locator('#tool-limits').innerText(), /Not available yet/, `${tag}: Limits placeholder`);
        assert.equal(await page.locator('#tool-see').count(), 0, `${tag}: Who can see it only for whoever may change it`);
        assert.equal(await page.locator('#tool-api [data-private]').count(), 0, `${tag}: no Private pill on an Everyone tool`);
        assert.equal(await page.locator('#tool-limits input').isDisabled(), true, `${tag}: Limits switch off`);
        // Read → Read and write calls PUT access.
        await page.locator('[data-ta-subject="bot:support-bot"][data-ta-level="read_write"]').click();
        await page.locator('[data-ta-subject="bot:support-bot"][data-ta-level="read_write"][aria-checked="true"]').waitFor();
        assert.deepEqual(api.writes.at(-1), {method: 'PUT', path: '/api/v2/api-tools/t-crm/access/bot%3Asupport-bot', body: {level: 'read_write'}}, `${tag}: level write`);
        // Check now posts and redraws the squares with the new ones last.
        const before = await page.locator('#tool-check .tt-squares > :last-child').getAttribute('title');
        await page.locator('[data-ta-check]').click();
        await page.waitForFunction(t => document.querySelector('#tool-check .tt-squares')?.lastElementChild?.title !== t, before);
        assert.equal(api.writes.at(-1).path, '/api/v2/api-tools/t-crm/check', `${tag}: check write`);
        assert.equal(await page.locator('#tool-check .tt-sq.fail').count(), 1, `${tag}: still 24 squares, one failed`);
        assert.equal(await sideways(page), false, `${tag}: the API page has no sideways scroll`);
        await shot(page, `api-${tag}`);
        // Activity: the log and its filter.
        await page.locator('.tt-tabs a[data-ta-tab="activity"]').click();
        await page.locator('#tool-api [data-ta-call]').first().waitFor();
        assert.equal(await page.locator('#tool-api [data-ta-call]').count(), 4, `${tag}: every call`);
        await page.locator('[data-ta-show="refused"]').click();
        await page.waitForFunction(() => document.querySelectorAll('#tool-api [data-ta-call]').length === 1);
        assert.equal(api.reads.at(-1), '?limit=50&result=refused', `${tag}: refused read`);
        assert.equal(await page.locator('#tool-api [data-ta-call] [data-result="refused"]').count(), 1, `${tag}: the refused call`);
        assert.equal(await sideways(page), false, `${tag}: Activity has no sideways scroll`);
        await shot(page, `api-activity-${tag}`);
        assert.deepEqual(seen.errors, [], `${tag}: API page errors`);
        await page.close();
      }
      // A key on computers: Move to proxy patches delivery.
      {
        const {page, seen, api} = await start(browser, '#/tools/api/t-billing', {width, ready: '#tool-keys [data-ta-proxy]'});
        assert.match(await page.locator('#tool-keys').innerText(), /Key on computers/, `${tag}: on computers`);
        assert.match(await page.locator('#tool-api .tt-title').innerText(), /Key rejected/, `${tag}: status`);
        await page.locator('#tool-keys [data-ta-proxy]').click();
        await page.locator('dialog [data-confirm-ok]').click();
        await page.locator('#tool-keys [data-ta-delivery="proxy"]').waitFor();
        assert.deepEqual(api.writes.at(-1), {method: 'PATCH', path: '/api/v2/api-tools/t-billing', body: {delivery: 'proxy'}}, `${tag}: delivery write`);
        assert.deepEqual(seen.errors, [], `${tag}: billing errors`);
        await page.close();
      }
      // Who can see it: Only listed with Who | Why, Remove on listed rows only, Add through the audience picker, back
      // to Everyone after a confirm, then Only listed again; Finance reads a key and cannot be changed here.
      {
        const {page, seen, api} = await start(browser, '#/tools/api/t-billing', {width, ready: '#tool-see tbody tr'});
        const card = page.locator('#tool-see');
        assert.equal(await page.locator('#tool-api .tt-title h1 [data-private]').innerText(), 'Private', `${tag}: Private in the header`);
        assert.equal(await page.evaluate(() => document.querySelector('#tool-see').nextElementSibling?.id), 'tool-keys', `${tag}: above Keys`);
        assert.deepEqual(await card.locator('thead th').allTextContents(), ['Who', 'Why', ''], `${tag}: Who | Why`);
        assert.deepEqual(await card.locator('tbody tr').evaluateAll(r => r.map(x => [x.dataset.taSeen, x.dataset.why])),
          [['human:ana', 'Owner'], ['human:cara', 'Listed'], ['bot:books', 'Can use it'], ['finance', 'Can use it']], `${tag}: who sees it and why`);
        assert.deepEqual(await card.locator('[data-ta-unlist]').evaluateAll(b => b.map(x => x.dataset.taUnlist)), ['human:cara'], `${tag}: Remove on listed rows only`);
        assert.equal(await card.locator('[data-ta-see="only"]').getAttribute('class'), 'primary small', `${tag}: Only listed selected`);
        assert.equal(await card.locator('[data-ta-see="everyone"]').getAttribute('class'), 'ghost small', `${tag}: Everyone not selected`);
        const fin = page.locator('#tool-access tr[data-ta-subject-row="finance"]');
        assert.match(await fin.innerText(), /Finance[\s\S]*Read/, `${tag}: Finance · Read`);
        assert.equal(await fin.locator('button').count(), 0, `${tag}: Finance is read-only`);
        assert.equal(await sideways(page), false, `${tag}: Only listed has no sideways scroll`);
        await shot(page, `api-private-${tag}`);
        // Remove Cara.
        await card.locator('[data-ta-unlist="human:cara"]').click();
        await page.locator('#tool-see tr[data-ta-seen="human:cara"]').waitFor({state: 'detached'});
        assert.deepEqual(api.writes.at(-1), {method: 'PATCH', path: '/api/v2/api-tools/t-billing',
          body: {audience: {everyone: false, people: [], teams: [], bots: []}}}, `${tag}: remove write`);
        // Add a group through the picker the page audience uses.
        await card.locator('[data-ta-see-add]').click();
        const picker = page.locator('#access-editor');
        await picker.locator('[data-access-pick="teams"][value="g-books"]').check();
        await picker.locator('[type=submit]').click();
        await page.locator('#tool-see tr[data-ta-seen="group:g-books"][data-why="Listed"]').waitFor();
        assert.deepEqual(api.writes.at(-1).body, {audience: {everyone: false, people: [], teams: ['g-books'], bots: []}}, `${tag}: add write`);
        // Everyone asks first, then clears the list and the pill.
        await card.locator('[data-ta-see="everyone"]').click();
        await page.locator('dialog [data-confirm-ok]').click();
        await page.locator('#tool-see [data-ta-see="everyone"].primary').waitFor();
        assert.deepEqual(api.writes.at(-1).body, {audience: null}, `${tag}: Everyone write`);
        assert.equal(await card.locator('table').count(), 0, `${tag}: no list for Everyone`);
        assert.equal(await card.locator('[data-ta-see-add]').count(), 0, `${tag}: no Add for Everyone`);
        assert.equal(await page.locator('#tool-api .tt-title [data-private]').count(), 0, `${tag}: no Private pill`);
        await shot(page, `api-everyone-${tag}`);
        // Only listed again: one click hides it (the hub keeps the owner and a non-owner setter).
        await card.locator('[data-ta-see="only"]').click();
        await page.locator('#tool-see tr[data-why="Owner"]').waitFor();
        assert.deepEqual(api.writes.at(-1).body, {audience: {everyone: false, people: [], teams: [], bots: []}}, `${tag}: Only listed write`);
        assert.equal(await page.locator('#tool-api .tt-title [data-private]').count(), 1, `${tag}: Private again`);
        assert.equal(await sideways(page), false, `${tag}: Who can see it has no sideways scroll`);
        assert.deepEqual(seen.errors, [], `${tag}: Who can see it errors`);
        await page.close();
      }
      // A mailbox: Who can use it, Keys, and Limits (Bot | Sends | What | A day, then the summary).
      {
        const {page, seen} = await start(browser, '#/tools/mailbox/support@acme.example', {width, ready: '#inbox-sending [data-send-bot-row]'});
        const limits = page.locator('#inbox-sending');
        assert.deepEqual(await limits.locator('thead th').allTextContents(), ['Bot', 'Sends', 'What', 'A day'], `${tag}: Limits columns`);
        assert.match(await limits.locator('[data-send-bot-row="support-bot"]').innerText(), /Replies only[\s\S]*50/, `${tag}: the bot's limits`);
        const summary = await limits.locator('.ib-send-summary').innerText();
        for (const word of ['Always ask for', 'This mailbox', 'Company', 'Pacing', 'Undo', 'Who approves', '200 a day', '1,000 a day', '2 min', 'Bot managers'])
          assert.match(summary, new RegExp(word), `${tag}: summary ${word}`);
        assert.match(await limits.innerText(), /People send as themselves, with no limits\./, `${tag}: the footer`);
        assert.equal(await limits.locator('[data-send-edit-body]').isHidden(), true, `${tag}: the form waits behind Edit`);
        await limits.locator('[data-send-edit]').click();
        await limits.locator('[data-send-edit-body] [data-send-form]').waitFor();
        await limits.locator('[data-send-edit]').click();
        assert.equal(await page.locator('#mailbox-access .tt-who-table tr').count() >= 2, true, `${tag}: who can use it`);
        assert.match(await page.locator('#mailbox-keys').innerText(), /Google Workspace/, `${tag}: its key`);
        assert.equal(await page.locator('.tt-tabs a[data-ib-tab-link="activity"]').getAttribute('href'), '#/tools/mailbox/support%40acme.example/activity', `${tag}: Activity tab`);
        assert.equal(await sideways(page), false, `${tag}: the mailbox page has no sideways scroll`);
        await shot(page, `mailbox-${tag}`);
        assert.deepEqual(seen.errors, [], `${tag}: mailbox errors`);
        await page.close();
      }
    }
    // The old addresses land on their Tools homes.
    {
      const {page, seen} = await start(browser, '#/tools');
      for (const [from, to] of [
        ['#/library/connections', '#/tools'], ['#/library/tools', '#/tools'], ['#/library/tools/github', '#/tools/github'],
        ['#/library/tools?who=bot:ops', '#/tools/keys?who=bot:ops'], ['#/integrations/close-crm', '#/tools/close-crm'],
        ['#/credentials', '#/tools/keys?show=credentials'], ['#/settings/inboxes', '#/tools?show=mail'],
        ['#/settings/inboxes/settings/email', '#/tools/settings/email'], ['#/settings/inboxes/mailbox/mb-support', '#/tools/mailbox/mb-support'],
        ['#/settings/mailboxes', '#/tools?show=mail'], ['#/settings/mail', '#/tools/settings/letters-in'], ['#/settings/google', '#/tools/settings/email'],
      ]) {
        await page.evaluate(h => { location.hash = h; }, from);
        await page.waitForFunction(h => location.hash === h, to, {timeout: 3000}).catch(async () => {
          assert.fail(`${from} went to ${await page.evaluate(() => location.hash)}, not ${to}`);
        });
      }
      // Settings › Inboxes is gone; phone alerts are in Settings › General.
      await page.evaluate(() => { location.hash = '#/settings/general'; });
      await page.locator('#settings-notifications #inboxes-alerts').waitFor();
      assert.equal(await page.locator('[data-settings-tab="inboxes"]').count(), 0, 'no Inboxes tab');
      assert.deepEqual(seen.errors, [], 'redirect errors');
      await page.close();
    }
    console.log('Tools: list, filters, Needs you, API levels, Check now, Move to proxy, Activity, Who can see it, Finance row, mailbox Limits, redirects, phone, themes');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
