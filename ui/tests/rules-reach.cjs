// Rules › Internal is Reach (docs/reach.md, ui/app/org-rules.js): the whole org chart with lines from the selected party
// (0 red, N amber, a rule alone blue and dashed, each with a chip), and the party's panel beside it: who it can reach and
// how much, the rules it follows (company and team rules folded), and its exceptions. Tapping a rule opens the rule's
// panel; + Add rule opens Add rule with the party as Who; + Add exception searches a team, person or bot and opens the line
// editor (direction, Up to N a day, rule). Every change writes one setting or one line; the bodies are checked here. A
// viewer who may change nothing sees it read-only. On a phone the chart gives way to a list, then the panel full width,
// with no sideways scroll. Every request is intercepted (the Acme fixture, fixtures/redesign-org.json).
// REACH_SHOTS=<dir> saves screenshots (1440x900 and 390x844).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const fx = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/redesign-org.json'), 'utf8'));
const SHOTS = process.env.REACH_SHOTS || '';
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const ORIGIN = 'http://tico-ui.test';

const scope = (k = {}) => ({everyone: false, all_bots: false, groups: [], bots: [], people: [], ...k});
const rule = (id, text, who) => ({id, kind: 'internal', text, description: '', who: scope(who), with: scope(), where: [], source: null,
  if_broken: 'block', reviewer: null, status: 'active', version: 1, created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-02T10:00:00Z',
  due: [], due_more: 0, can_edit: true});
const RULES = [
  rule('r-co', 'Be kind and brief with customers.', {everyone: true}),
  rule('r-eng', 'Ship behind a flag.', {groups: [{id: 'eng', name: 'Engineering'}]}),
  rule('r-bots', 'Say you are a bot when asked.', {all_bots: true}),
  rule('r-builder', 'Run the tests before asking for review.', {bots: [{id: 'builder', name: 'Builder'}]}),
  // A line's rule: listed with its line, not under Rules.
  rule('r-review', 'Only send finished pull requests.', {bots: [{id: 'builder', name: 'Builder'}]}),
];
const PARTIES = ['*', ...fx.groups.map(g => 'group:' + g.id), ...fx.people.map(p => 'human:' + p.id), ...fx.employees.map(e => 'bot:' + e.name)];

async function openReach(browser, {viewport = DESKTOP, hash = '#/rules', viewer = 'ana', mayEdit = PARTIES} = {}) {
  const person = fx.people.find(p => p.id === viewer);
  const me = viewer === fx.me.id ? fx.me : {id: viewer, name: person.name, email: viewer + '@acme.example', role: 'member', company_role: 'member', cloud: true};
  const config = {company_name: 'Acme'};
  const may = new Set(mayEdit);
  const reach = {
    settings: [{party: '*', mode: 'open', chats_per_day: null, tasks_per_day: null},
      {party: 'group:sales', mode: 'closed', chats_per_day: 40, tasks_per_day: null}],
    lines: [
      {id: 'l-1', sender: 'bot:builder', receiver: 'group:sales', per_day: 0, rule: null},
      {id: 'l-2', sender: 'bot:builder', receiver: 'bot:reviewer', per_day: 20, rule: {id: 'r-review', text: 'Only send finished pull requests.'}},
      {id: 'l-3', sender: 'bot:sdr', receiver: 'bot:builder', per_day: null, rule: {id: 'r-sdr', text: 'Ask for demo builds through the Sales Lead.'}},
    ],
  };
  let next = 4;
  const writes = [];
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== ORIGIN) return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method !== 'GET') writes.push({method, path: p, body});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(fx.employees);
    if (p === '/api/humans') return json({people: fx.people, org_groups: fx.org_groups});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json(fx.groups);
    // Rules (backend/org_rules.py shapes).
    if (p === '/api/v2/rules' && method === 'GET') return json({rules: url.searchParams.get('kind') === 'internal' ? RULES : [], next_cursor: null});
    if (p === '/api/v2/rules' && method === 'POST') return json({...rule('r-new', body.text, body.who), who: body.who});
    let m = p.match(/^\/api\/v2\/rules\/([^/]+)$/);
    if (m && RULES.some(r => r.id === m[1])) return json(RULES.find(r => r.id === m[1]));
    // Reach (docs/reach.md).
    if (p === '/api/v2/reach') return json({defaults: {mode: 'open', chats_per_day: 999, tasks_per_day: 999}, settings: reach.settings,
      lines: reach.lines.map(l => ({...l, may_edit: may.has(l.sender)})), may_edit: [...may]});
    m = p.match(/^\/api\/v2\/reach\/settings\/(.+)$/);
    if (m && method === 'PUT') {
      const party = decodeURIComponent(m[1]);
      let s = reach.settings.find(x => x.party === party);
      if (!s) reach.settings.push(s = {party, mode: null, chats_per_day: null, tasks_per_day: null});
      Object.assign(s, body);
      return json(s);
    }
    if (p === '/api/v2/reach/lines' && method === 'POST') {
      const l = {id: 'l-' + next++, sender: body.sender, receiver: body.receiver, per_day: body.per_day, rule: body.rule ? {id: 'r-l' + next, text: body.rule} : null};
      reach.lines.push(l);
      return json(l);
    }
    m = p.match(/^\/api\/v2\/reach\/lines\/([^/]+)$/);
    if (m && method === 'POST') {
      const l = reach.lines.find(x => x.id === m[1]);
      Object.assign(l, {sender: body.sender, receiver: body.receiver, per_day: body.per_day, rule: body.rule ? {id: l.rule?.id || 'r-l' + next++, text: body.rule} : null});
      return json(l);
    }
    if (m && method === 'DELETE') { reach.lines = reach.lines.filter(x => x.id !== m[1]); return route.fulfill({status: 204, body: ''}); }
    return json(unrouted(p));
  });
  await page.goto(ORIGIN + '/' + hash);
  await page.locator('.rr').waitFor();
  return {page, errors, writes, reach};
}

const panel = page => page.locator('.rr-side .rr-panel');
const noSideways = async (page, what) => {
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(wide <= 1, `${what}: no sideways scroll (${wide}px)`);
};
async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.waitForTimeout(150);
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
}
const lastWrite = (writes, method) => writes.filter(w => w.method === method).at(-1);
async function until(fn, what) {
  for (let i = 0; i < 50; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail(what);
}

(async () => {
  const browser = await chromium.launch();
  try {
    // ---- a computer, the owner
    {
      const {page, errors, writes} = await openReach(browser);
      // Everyone is selected first; its own rules are the company's.
      assert.equal(await panel(page).locator('h2').innerText(), 'Everyone');
      assert.equal(await page.locator('.rr-map .rr-all').getAttribute('aria-pressed'), 'true');
      assert.match(await panel(page).locator('a.rr-rule').first().innerText(), /Be kind and brief/);
      // The whole chart, every group open: each team's tab and each teammate is a party.
      for (const p of ['group:eng', 'group:web', 'group:sales', 'group:outbound', 'human:cara', 'bot:builder', 'bot:sdr'])
        assert.equal(await page.locator(`.rr-map [data-rr-party="${p}"]`).count(), 1, p);

      // Selecting a party: its lines are drawn, coloured, with chips.
      await page.locator('.rr-map [data-rr-party="bot:builder"]').click();
      await until(async () => (await panel(page).locator('h2').innerText()) === 'Builder', 'Builder selected');
      assert.match(page.url(), /party=bot%3Abuilder/);
      assert.equal(await page.locator('.rr-map [data-rr-party="bot:builder"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.rr-lines line').count(), 3);
      assert.equal(await page.locator('.rr-lines line.zero').count(), 1);
      assert.equal(await page.locator('.rr-lines line.cap').count(), 1);
      assert.equal(await page.locator('.rr-lines line.rule').count(), 1);
      assert.equal(await page.locator('.rr-map [data-rr-party="group:sales"]').getAttribute('data-rr-tone'), 'zero');
      const chips = await page.locator('.rr-chip').allInnerTexts();
      assert.deepEqual(chips.sort(), ['0 a day', '20 a day · Only send finished pull reque…', 'Ask for demo builds through t…'].sort());
      // Its exceptions, To first, then From; a rule alone has no count, and never "None".
      const rows = await panel(page).locator('.rr-line').allInnerTexts();
      assert.equal(rows.length, 3);
      assert.match(rows[0], /^To\s+Sales\s+0 a day/);
      assert.match(rows[1], /^To\s+Reviewer\s+20 a day\s+Only send finished pull requests/);
      assert.match(rows[2], /^From\s+SDR\s+Ask for demo builds/);
      assert.equal((await panel(page).innerText()).includes('None'), false);
      await shot(page, 'desktop-builder');

      // Company and team rules (and those for all bots) are folded; its own rule is shown; a line's rule is not repeated.
      const heads = panel(page).locator('.rr-inh-h');
      assert.deepEqual((await heads.allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()), ['› Company rules 1', '› Engineering rules 1', '› Rules for all bots 1']);
      assert.equal(await panel(page).locator('.rr-sub').count(), 0);
      const own = await panel(page).locator('a.rr-rule:not(.rr-sub)').allInnerTexts();
      assert.deepEqual(own, ['Run the tests before asking for review.']);
      await heads.nth(1).click();
      assert.equal(await heads.nth(1).getAttribute('aria-expanded'), 'true');
      assert.equal(await panel(page).locator('.rr-sub').innerText(), 'Ship behind a flag.');
      // Tapping a rule opens its panel; closing it comes back to the party.
      await panel(page).locator('a.rr-sub').click();
      await page.locator('#ru-side').getByText('Ship behind a flag.').first().waitFor();
      assert.match(page.url(), /party=bot%3Abuilder&rule=r-eng/);
      await page.keyboard.press('Escape');
      await until(async () => !/rule=/.test(page.url()), 'rule panel closed');
      assert.match(page.url(), /party=bot%3Abuilder/);
      await panel(page).waitFor();

      // + Add rule: Add rule with Builder as Who.
      await panel(page).locator('[data-rr-add-rule]').click();
      const dlg = page.locator('#ru-add');
      await dlg.locator('[data-scope="who"]').waitFor();
      assert.match(await dlg.locator('[data-scope="who"]').innerText(), /Builder/);
      assert.equal(await dlg.locator('[data-seg="kind"] [aria-pressed="true"], [data-ru-seg="kind"] [aria-pressed="true"]').innerText(), 'Internal');
      await dlg.locator('[data-ru-close]').first().click();

      // + Add exception: search a team, set the direction and the count, add.
      await panel(page).locator('[data-rr-add-line]').click();
      await panel(page).locator('.rr-xq').fill('web');
      const picks = await panel(page).locator('[data-rr-pick]').evaluateAll(b => b.map(x => x.dataset.rrPick));
      assert.ok(picks.includes('group:web') && picks.includes('bot:web-dev'), picks.join());
      await panel(page).locator('[data-rr-pick="group:web"]').click();
      const ed = panel(page).locator('.rr-ed');
      assert.deepEqual(await ed.locator('[data-rr-dir]').allInnerTexts(), ['Builder → Web', 'Web → Builder']);
      assert.equal(await ed.locator('[data-rr-dir="to"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await ed.locator('input[name=per_day]').inputValue(), '0', 'a new exception starts at 0 while Builder is open');
      await shot(page, 'desktop-editor');
      await ed.locator('[data-rr-dir="from"]').click();
      await ed.locator('input[name=per_day]').fill('5');
      await ed.locator('textarea[name=rule]').fill('Ask before changing the home page.');
      await ed.locator('[data-rr-save]').click();
      await until(async () => (await panel(page).locator('.ru-status').innerText()) === 'Saved', 'line added');
      assert.deepEqual(lastWrite(writes, 'POST'), {method: 'POST', path: '/api/v2/reach/lines',
        body: {sender: 'group:web', receiver: 'bot:builder', per_day: 5, rule: 'Ask before changing the home page.'}});
      assert.match((await panel(page).locator('.rr-line').allInnerTexts()).at(-1), /^From\s+Web\s+5 a day\s+Ask before changing/);
      assert.equal(await page.locator('.rr-lines line').count(), 4);

      // Edit a line: change its count and clear its rule.
      await panel(page).locator('[data-rr-line="l-2"]').click();
      assert.equal(await ed.locator('input[name=per_day]').inputValue(), '20');
      assert.equal(await ed.locator('[data-rr-save]').innerText(), 'Save');
      await ed.locator('input[name=per_day]').fill('');
      await ed.locator('[data-rr-save]').click();
      await until(async () => lastWrite(writes, 'POST')?.path === '/api/v2/reach/lines/l-2', 'line saved');
      assert.deepEqual(lastWrite(writes, 'POST').body, {sender: 'bot:builder', receiver: 'bot:reviewer', per_day: null, rule: 'Only send finished pull requests.'});
      await until(async () => (await panel(page).locator('[data-rr-line="l-2"] .rr-n').innerText()) === '', 'no count');
      assert.equal(await page.locator('.rr-lines line.rule').count(), 2);
      // A bad count is refused before it is sent.
      await panel(page).locator('[data-rr-line="l-2"]').click();
      await ed.locator('input[name=per_day]').fill('2.5');
      const before = writes.length;
      await ed.locator('[data-rr-save]').click();
      assert.match(await panel(page).locator('.ru-status').innerText(), /whole number/);
      assert.equal(writes.length, before);
      await ed.locator('[data-rr-cancel]').click();
      // Remove a line from the chip on the map.
      await page.locator('.rr-chip[data-rr-line="l-1"]').click();
      await ed.locator('[data-rr-remove]').click();
      await until(async () => (await panel(page).locator('.ru-status').innerText()) === 'Removed', 'line removed');
      assert.equal(lastWrite(writes, 'DELETE').path, '/api/v2/reach/lines/l-1');
      assert.equal(await panel(page).locator('[data-rr-line="l-1"]').count(), 0);
      assert.equal(await page.locator('.rr-map [data-rr-party="group:sales"]').getAttribute('data-rr-tone'), null);

      // Who it can reach and how much: one PUT per field; an empty cap is taken from above.
      await panel(page).locator('[data-ru-seg="rr-mode"] [data-v="closed"]').click();
      await until(async () => lastWrite(writes, 'PUT'), 'mode saved');
      assert.deepEqual(lastWrite(writes, 'PUT'), {method: 'PUT', path: '/api/v2/reach/settings/bot%3Abuilder', body: {mode: 'closed'}});
      await until(async () => (await panel(page).locator('[data-ru-seg="rr-mode"] [data-v="closed"]').getAttribute('aria-pressed')) === 'true', 'closed shown');
      const chats = panel(page).locator('[data-rr-cap="chats_per_day"]');
      assert.equal(await chats.getAttribute('placeholder'), '999');
      await chats.fill('30'); await chats.press('Tab');
      await until(async () => lastWrite(writes, 'PUT').body.chats_per_day === 30, 'cap saved');
      assert.deepEqual(lastWrite(writes, 'PUT'), {method: 'PUT', path: '/api/v2/reach/settings/bot%3Abuilder', body: {chats_per_day: 30}});
      await until(async () => (await panel(page).locator('.ru-status').innerText()) === 'Saved', 'cap read back');
      assert.equal(await panel(page).locator('[data-rr-cap="chats_per_day"]').inputValue(), '30');
      await panel(page).locator('[data-rr-cap="chats_per_day"]').fill('');
      await panel(page).locator('[data-rr-cap="chats_per_day"]').press('Tab');
      await until(async () => lastWrite(writes, 'PUT').body.chats_per_day === null, 'cap cleared');
      const tasks = panel(page).locator('[data-rr-cap="tasks_per_day"]');
      await tasks.fill('-1'); await tasks.press('Tab');
      assert.match(await panel(page).locator('.ru-status').innerText(), /whole number/);
      assert.equal(lastWrite(writes, 'PUT').body.tasks_per_day, undefined);

      // The header search marks matches on the map; Enter selects the first. A team's tab and Everyone are parties too.
      await page.locator('.ru-acts .rr-q').fill('sales');
      assert.deepEqual(await page.locator('.ru-acts .rr-results [data-rr-party]').evaluateAll(b => b.map(x => x.dataset.rrParty)), ['group:sales', 'bot:sales-lead']);
      assert.equal(await page.locator('.rr-map .hit').count(), 2);
      await shot(page, 'desktop-search');
      await page.locator('.ru-acts .rr-q').press('Enter');
      await until(async () => (await panel(page).locator('h2').innerText()) === 'Sales', 'Sales selected');
      assert.equal(await page.locator('.ru-acts .rr-results').isHidden(), true);
      assert.equal(await panel(page).locator('[data-ru-seg="rr-mode"] [data-v="closed"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await panel(page).locator('[data-rr-cap="chats_per_day"]').inputValue(), '40');
      await page.locator('.rr-map .rdo-tab[data-rr-party="group:eng"]').click();
      await until(async () => (await panel(page).locator('h2').innerText()) === 'Engineering', 'Engineering selected');
      assert.equal(await panel(page).locator('.rr-inh-h').allInnerTexts().then(t => t.length), 1, 'a top team follows the company rules only');
      assert.deepEqual(await panel(page).locator('a.rr-rule:not(.rr-sub)').allInnerTexts(), ['Ship behind a flag.']);
      await page.locator('.rr-map .rr-all').click();
      await until(async () => (await panel(page).locator('h2').innerText()) === 'Everyone', 'Everyone selected');
      await noSideways(page, 'desktop');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // ---- a viewer who may change nothing: read-only
    {
      const {page, errors} = await openReach(browser, {viewer: 'cara', mayEdit: [], hash: '#/rules?party=bot%3Abuilder'});
      await until(async () => (await panel(page).locator('h2').innerText()) === 'Builder', 'Builder');
      for (const b of await panel(page).locator('[data-ru-seg="rr-mode"] button').all()) assert.equal(await b.isDisabled(), true);
      assert.equal(await panel(page).locator('[data-rr-cap="chats_per_day"]').isDisabled(), true);
      assert.equal(await panel(page).locator('[data-rr-cap="tasks_per_day"]').isDisabled(), true);
      assert.equal(await panel(page).locator('[data-rr-add-line]').count(), 0);
      assert.equal(await panel(page).locator('button.rr-line').count(), 0);
      assert.equal(await panel(page).locator('.rr-line.ro').count(), 3);
      await panel(page).locator('.rr-line.ro').first().click();
      assert.equal(await panel(page).locator('.rr-ed').count(), 0);
      assert.equal(await page.locator('.rr-chip[aria-disabled="true"]').count(), 3);
      // Rules still open (their panel decides what the viewer may change).
      await panel(page).locator('a.rr-rule:not(.rr-sub)').click();
      await page.locator('#ru-side').getByText('Run the tests before asking for review.').first().waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }

    // ---- a phone: a list, then the party's panel full width
    {
      const {page, errors, writes} = await openReach(browser, {viewport: PHONE});
      assert.equal(await page.locator('.rr-map').isVisible(), false);
      assert.equal(await page.locator('.rr-side').isVisible(), false);
      assert.equal(await page.locator('.rr-pick').isVisible(), true);
      assert.equal(await page.locator('.rr-pick [data-rr-party]').first().innerText().then(t => t.split('\n')[0]), 'Everyone');
      await noSideways(page, 'phone list');
      await shot(page, 'phone-list');
      await page.locator('.ru-acts .rr-q').fill('build');
      assert.deepEqual(await page.locator('.rr-pick [data-rr-party]').evaluateAll(b => b.map(x => x.dataset.rrParty)), ['bot:builder']);
      assert.equal(await page.locator('.ru-acts .rr-results').isVisible(), false);
      await page.locator('.rr-pick [data-rr-party="bot:builder"]').tap();
      await until(async () => await page.locator('.rr-side').isVisible(), 'panel shown');
      assert.equal(await page.locator('.rr-pick').isVisible(), false);
      assert.equal(await panel(page).locator('h2').innerText(), 'Builder');
      const box = await panel(page).boundingBox();
      assert.ok(box.width >= PHONE.width - 2, `full width (${box.width})`);
      await noSideways(page, 'phone panel');
      await shot(page, 'phone-panel');
      await panel(page).locator('[data-rr-add-line]').tap();
      await panel(page).locator('.rr-xq').fill('cara');
      await panel(page).locator('[data-rr-pick="human:cara"]').tap();
      await noSideways(page, 'phone editor');
      await shot(page, 'phone-editor');
      await panel(page).locator('.rr-ed [data-rr-save]').tap();
      await until(async () => lastWrite(writes, 'POST'), 'added on a phone');
      assert.deepEqual(lastWrite(writes, 'POST').body, {sender: 'bot:builder', receiver: 'human:cara', per_day: 0, rule: null});
      // Back to the list.
      await panel(page).locator('.rr-back').tap();
      await until(async () => await page.locator('.rr-pick').isVisible(), 'list again');
      assert.equal(await page.locator('.rr-side').isVisible(), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('rules-reach: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
