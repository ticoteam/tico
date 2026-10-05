// The Tasks page as one list (Linear-like). Offline: every request is answered from the fixture below, and the fake
// server checks each update's version as backend/app.py does (a stale one is a 409).
//  - one 32px line per task: status icon, title, chips, the owner's face, the age; "Added by" only in the tooltip;
//    a note as one muted line (amber only when it waits on you); a parked bot task's date reads "Wakes …", muted;
//  - labelled view tabs with counts; Status groups "Needs you", "Needs <Name>", "Needs someone", then Waiting and Doing;
//    groups fold, count, add; Group by is a chip menu, remembered; a subtask nests only inside its parent's group;
//  - filter chips kept in the address; old saved filters come over once; an unknown type falls back to General;
//  - the side peek (j/k, Esc, Open full, ←/→ fold) with a properties panel: every row saves at once, a conflict shows
//    the server's value; the full task shows Properties, then Code, then Subtasks;
//  - bulk changes touch only visible selected rows, retry once on a stale version, ask each bot request for its own
//    note, and Close can be undone; the peek follows; no bulk for someone who cannot move tasks;
//  - the board: per-person "Needs" columns, empty columns fold to a strip, cards can be selected;
//  - a phone: full-screen modal peek that Back closes, Select mode, stacked properties.
// TASKS_SHOTS=<dir> also saves the screenshots for the owner (dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const SHOTS = process.env.TASKS_SHOTS || '';

const NOW = Date.now();
const at = minutes => new Date(NOW - minutes * 60000).toISOString();
const inDays = days => new Date(NOW + days * 86400000).toISOString();
function fixtures() {
  const t = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:engineer', requester: 'human:ana', status: 'doing',
    lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3,
    created: at(60 * 30), updated: at(30), ...over});
  const ask = (from, body) => ({ask: {id: 'q-' + from, body, from_actor: from, to_actor: 'human:ana'}});
  const pr = (repo, n, title, state = 'open') => ({id: `pr-${repo}-${n}`, kind: 'pr', repo, number: n, url: `https://github.com/${repo}/pull/${n}`, title, state,
    checks: state === 'merged' ? 'passing' : 'pending'});
  return [
    // Needs you
    t('t-copy', 'Approve the Q4 pricing page copy', {owner: 'human:ana', requester: 'bot:writer', status: 'open', updated: at(120), labels: ['pricing'],
      ...ask('bot:writer', 'Two headline options are in the doc. Pick one so the page can ship Friday.')}),
    t('t-refund', 'Choose a refund policy for annual plans', {owner: 'human:ana', requester: 'bot:support', status: 'open', updated: at(300), labels: ['billing'],
      ...ask('bot:support', 'Three customers asked this week: a full refund within 30 days, or prorated?')}),
    t('t-access', 'Review Engineer access to the billing repo', {owner: 'human:ana', requester: 'bot:botops', status: 'open', updated: at(60 * 26)}),
    t('t-terms', 'Approve the new checkout terms', {owner: 'human:ana', requester: 'bot:engineer', status: 'open', parent_id: 't-checkout', updated: at(150)}),
    t('t-onboard', 'Sign off the onboarding email sequence', {owner: 'human:ana', requester: 'bot:writer', status: 'open', updated: at(200), labels: ['email'], due: inDays(1)}),
    t('t-deck', 'Review the launch deck', {owner: 'human:ana', requester: 'human:sam', status: 'open', updated: at(400), labels: ['launch']}),
    // Needs Sam
    t('t-demo', 'Record the product demo video', {owner: 'human:sam', requester: 'human:ana', status: 'open', updated: at(60 * 50), due: inDays(-1), labels: ['launch']}),
    // Waits on a person this install does not know: Needs someone
    t('t-vendor', 'Sign the vendor security questionnaire', {owner: 'human:jo', requester: 'bot:botops', status: 'open', updated: at(60 * 6)}),
    // Waiting
    t('t-inbox', 'Migrate the support inbox to the new helpdesk', {owner: 'bot:support', requester: 'bot:botops', status: 'waiting', updated: at(480), labels: ['support'],
      note: 'Waiting on Sam to export the old ticket archive (about 12k tickets) before the import can start.'}),
    t('t-checkout', 'Ship the checkout redesign', {owner: 'bot:engineer', requester: 'human:ana', status: 'waiting', updated: at(60 * 22), labels: ['checkout'],
      blocked_by: 't-idem', blocker: {id: 't-idem', title: 'Add idempotency keys to the payments API', status: 'doing'}, pr_state: 'changes_requested',
      children_summary: {total: 4, done: 1, direct_total: 4, direct_done: 1}, parts: {total: 4, done: 1}, links: [pr('acme/web', 412, 'Checkout redesign behind a flag')]}),
    // a bot's parked task: its date is when it looks again, not a deadline
    t('t-changelog', 'Publish the September changelog', {owner: 'bot:writer', requester: 'bot:botops', status: 'waiting', updated: at(900), note: 'Waiting for the 0.3 release tag.', due: inDays(2)}),
    // Doing (two are subtasks of the waiting checkout; one is a subtask of the newsletter)
    t('t-idem', 'Add idempotency keys to the payments API', {parent_id: 't-checkout', updated: at(40), labels: ['payments', 'backend', 'api'], pr_state: 'failing', links: [pr('acme/api', 88, 'Idempotency keys for charges')]}),
    t('t-summary', 'Checkout summary step', {parent_id: 't-checkout', status: 'review', updated: at(180), pr_state: 'open', links: [pr('acme/web', 415, 'Summary step')]}),
    t('t-triage', 'Triage new bug reports from the widget', {owner: 'bot:support', requester: 'bot:botops', updated: at(20), labels: ['bugs']}),
    t('t-digest', 'Weekly metrics digest', {owner: 'bot:analyst', requester: 'human:sam', status: 'open', updated: at(10)}),
    t('t-rotate', 'Rotate the staging database credentials', {owner: 'bot:botops', requester: 'bot:botops', updated: at(70), labels: ['security']}),
    t('t-news', 'Draft the October newsletter', {owner: 'bot:writer', requester: 'human:ana', updated: at(360), labels: ['newsletter'], parts: {total: 1, done: 0}}),
    t('t-img', 'Pick the newsletter header image', {owner: 'bot:writer', requester: 'human:ana', parent_id: 't-news', updated: at(380)}),
    t('t-flaky', 'Fix the flaky login test on CI', {requester: 'bot:botops', updated: at(60 * 47), pr_state: 'merged', labels: ['ci'], links: [pr('acme/web', 409, 'Stabilise the login test', 'merged')]}),
    // Done
    t('t-receipt', 'Checkout receipt email copy', {parent_id: 't-checkout', owner: 'bot:writer', status: 'done', done_at: at(240), updated: at(240)}),
    t('t-sso', 'Turn on SSO for the admin console', {status: 'done', done_at: at(60 * 30), updated: at(60 * 30), pr_state: 'merged'}),
    t('t-faq', 'Refresh the billing FAQ', {owner: 'bot:support', status: 'closed', closed_at: at(60 * 70), updated: at(60 * 70)}),
  ];
}
const OPEN_COUNT = 19, NEEDS_YOU = 6;
const BOTS = [['engineer', 'Engineer', 'engineering'], ['support', 'Support', 'support'], ['writer', 'Writer', 'marketing'],
  ['analyst', 'Analyst', 'operations'], ['botops', 'BotOps', '']].map(([name, display_name, team]) =>
  ({name, display_name, team, host: 'keeper', status: 'active', state: 'active', can_chat: true}));

async function open(browser, {viewport = {width: 1440, height: 900}, theme = 'dark', hash = '#/issues', failIds = [], prefs = null, mover = true,
  types = [], local = {}, people = null, baseTasks = null, extraTasks = []} = {}) {
  const tasks = [...(baseTasks ?? fixtures()), ...extraTasks];
  const posts = [];
  let pref = prefs;
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(process.env.TASKS_DEBUG ? e.stack : e.message));
  await page.addInitScript(({theme, local}) => {
    try {
      if (!sessionStorage.getItem('seeded')) { for (const [k, v] of Object.entries(local)) localStorage.setItem(k, v); sessionStorage.setItem('seeded', '1'); }
      localStorage.setItem('tico.theme', theme);
    } catch {}
  }, {theme, local});
  await page.addInitScript(() => { navigator.clipboard.writeText = async text => { window.copied = text; }; });
  const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: mover ? 'owner' : 'member', mover, cloud: true, registered: true};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/humans') return json({people: people || [{id: 'ana', name: 'Ana', team: 'operations'}, {id: 'sam', name: 'Sam', team: 'marketing'}],
      org_groups: [{id: 'engineering', name: 'Engineering'}, {id: 'support', name: 'Support'}, {id: 'marketing', name: 'Marketing'}, {id: 'operations', name: 'Operations'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/routines') return json({routines: [
      {id: 'digest', title: 'Weekly metrics digest', employee: 'analyst', cron: '0 9 * * 1', active: true, enabled: true, next: inDays(3)},
      {id: 'sweep', title: 'Inbox sweep', employee: 'support', cron: '*/30 * * * *', active: true, enabled: true, next: inDays(0.01)}]});
    if (p === '/api/v2/tasks/labels') return json({labels: [...new Set(tasks.flatMap(x => x.labels))].sort(), tags: []});
    if (p === '/api/v2/task-types') return json({types});
    if (p.startsWith('/api/v2/preferences/')) {
      if (method === 'POST') { pref = req.postDataJSON().value; posts.push({p, body: req.postDataJSON()}); return json({value: pref}); }
      return json({key: 'tasks.view', value: pref});
    }
    if (p === '/api/v2/tasks' && method === 'GET') {
      const wanted = (url.searchParams.get('status') || '').split(',').filter(Boolean);
      const rows = tasks.filter(x => !wanted.length || wanted.includes(x.status));
      const donePage = url.searchParams.get('sort') === 'finished';
      if (donePage) rows.sort((a, b) => String(b.closed_at || b.updated).localeCompare(String(a.closed_at || a.updated)));
      const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || rows.length);
      return json({tasks: donePage ? rows.slice(offset, offset + limit) : rows,
        next_offset: donePage && rows.length > offset + limit ? offset + limit : null});
    }
    if (p === '/api/v2/tasks' && method === 'POST') { posts.push({p, body: req.postDataJSON()}); return json({task: {id: 't-new', ...req.postDataJSON()}}); }
    const tree = p.match(/^\/api\/v2\/tasks\/([^/]+)\/tree$/);
    if (tree) return json(tasks.filter(x => x.parent_id === tree[1]).map(x => ({...x, children: []})));
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one) {
      const id = decodeURIComponent(one[1]), task = tasks.find(x => x.id === id);
      if (!task) return json({error: {detail: 'Not found'}}, 404);
      if (method === 'POST') {
        const body = req.postDataJSON(); posts.push({p, body});
        if (failIds.includes(id) || body.version !== task.version) return json({error: {code: 'version_conflict', detail: 'Task changed; fetch it and retry your update'}}, 409);
        const {version, close, note, ...fields} = body;
        if (fields.owner && !fields.owner.includes(':')) fields.owner = 'bot:' + fields.owner;   // as the server resolves a bot's name
        Object.assign(task, fields, {version: task.version + 1});
        if (close) task.status = 'closed';
        for (const k of ['blocked_by', 'parent_id', 'due']) if (fields[k] === '') delete task[k];
        if ('blocked_by' in fields) task.blocker = fields.blocked_by ? {id: fields.blocked_by, title: tasks.find(x => x.id === fields.blocked_by)?.title} : null;
        if (fields.type) { const type = types.find(x => x.id === fields.type); task.type_id = fields.type; task.type = type ? {id: type.id, name: type.name} : null; }
        if ('step' in fields) { const step = types.flatMap(x => x.steps).find(s => s.id === fields.step); task.step_id = fields.step || null; task.step = step || null; if (step) task.status = step.status; }
        return json({task});
      }
      return json({task, children: tasks.filter(x => x.parent_id === id), parent: tasks.find(x => x.id === task.parent_id) || null,
        comments: [{id: 'm1', kind: 'say', from_actor: 'human:ana', body: 'Keep the old flow behind a flag for a week.', created: at(90), refs: {}}],
        events: [{id: 'e1', ts: at(60), actor: 'bot:engineer', field: 'status', old: 'open', new: 'doing'}], messages: []});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/messages')) return json({messages: []});
    return json({});
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body .tl-row, #task-body .bcard, #task-body .tl-empty'))
    .catch(e => { throw new Error(`${e.message}\npage errors: ${errors.join(' | ')}`); });
  return {page, errors, posts, tasks, pref: () => pref};
}
const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.waitForTimeout(120);
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
};
const rowKeys = page => page.locator('#task-body .tl-row').evaluateAll(rows => rows.map(r => r.dataset.taskKey));
const groupNames = page => page.locator('#task-body .tl-gname').allInnerTexts();
const taskWrites = posts => posts.filter(x => /\/api\/v2\/tasks\/t-/.test(x.p));
const groupBy = async (page, by) => {
  await page.locator('#task-group').click();
  await page.locator(`#task-filter-pop [data-pick-group="${by}"]`).click();
  await page.waitForFunction(by => document.querySelector('#task-body .tl')?.dataset.groupBy === by, by);
};
const peekTitle = (page, text) => page.locator('#task-peek .tmodal-title', {hasText: text}).waitFor();
const focusedKey = page => page.evaluate(() => document.activeElement?.closest('[data-task-key]')?.dataset.taskKey || document.activeElement?.className);

async function listAndTabs(browser) {
  const {page, errors} = await open(browser);
  // Labelled tabs with counts, as a tab list; the count is part of each tab's name, after a comma.
  const tabs = await page.locator('#task-view [role=tab]').evaluateAll(ts => ts.map(t => [t.dataset.view, t.firstChild.textContent, t.querySelector('.cnt')?.textContent ?? null, t.getAttribute('aria-selected')]));
  assert.deepEqual(tabs, [['foryou', 'Needs you', String(NEEDS_YOU), 'false'], ['list', 'Open', String(OPEN_COUNT), 'true'], ['board', 'Board', null, 'false'],
    ['recurring', 'Recurring', '2', 'false'], ['done', 'Done', null, 'false']]);
  assert.equal(await page.locator('#task-view [data-view="foryou"]').getAttribute('aria-label'), `Needs you, ${NEEDS_YOU}`);
  assert.equal(await page.locator('#task-view').getAttribute('role'), 'tablist');
  // "Needs you", then one "Needs <Name>" per other person a task waits on, "Needs someone", then Waiting and Doing.
  assert.deepEqual((await groupNames(page)).map(s => s.toLowerCase()), ['needs you', 'needs sam', 'needs someone', 'waiting', 'doing']);
  assert.deepEqual(await page.locator('#task-body .tl-gcount').allInnerTexts(), [String(NEEDS_YOU), '1', '1', '3', '8']);
  assert.equal(await page.locator('[data-group="needsme"] .tl-gcount').innerText(), await page.locator('[data-view="foryou"] .cnt').innerText(), 'the tab and the group agree');
  const keys = await rowKeys(page);
  assert.equal(keys.length, OPEN_COUNT, 'every open task once');
  assert.equal(new Set(keys).size, keys.length, 'no task twice');
  // A subtask nests only inside its parent's own group; elsewhere it stays in its group with a "↳ parent" hint.
  const depth = key => page.locator(`[data-task-key="${key}"]`).evaluate(r => [r.closest('[data-group]').dataset.group, r.getAttribute('aria-level'), (r.querySelector('.tl-parent')?.innerText || '').replace(/\s+/g, ' ')]);
  assert.deepEqual(await depth('tt-img'), ['doing', '2', ''], 'the newsletter image nests under the newsletter (both Doing)');
  assert.deepEqual(await depth('tt-idem'), ['doing', '1', '↳ Ship the checkout redesign'], 'a Doing subtask of a Waiting task stays in Doing');
  assert.deepEqual(await depth('tt-terms'), ['needsme', '1', '↳ Ship the checkout redesign']);
  assert.equal(await page.locator('[data-group="waiting"] [data-task-key="tt-idem"]').count(), 0);
  // One Tab stop for the whole list: only the cursor row's title is tabbable; boxes and chevrons are reached by keys.
  assert.equal(await page.locator('#task-body [data-open-task][tabindex="0"]').count(), 1);
  assert.equal(await page.locator('#task-body .tl-check[tabindex="0"], #task-body .tl-chev[tabindex="0"]').count(), 0);
  // One line per task, ~32px; no "Added by" on the line; the tooltip has it.
  const heights = await page.locator('#task-body .tl-row').evaluateAll(rows => rows.map(r => Math.round(r.getBoundingClientRect().height)));
  assert.ok(heights.every(h => h >= 30 && h <= 36), 'rows are one 32px line: ' + heights);
  assert.doesNotMatch(await page.locator('#task-body').innerText(), /Added by/);
  assert.match(await page.locator('[data-task-key="tt-copy"]').getAttribute('title'), /Added by Writer \(bot\)/);
  // The section supplies status context, including derived Needs-you groups.
  assert.equal(await page.locator('#task-body .tl-row > .task-status').count(), 0);
  // Chips: tags (two, then +N), the PR badge, the subtask ring; dates: a person's deadline, or a parked bot task's wake.
  const idem = page.locator('[data-task-key="tt-idem"]');
  assert.deepEqual(await idem.locator('.tlabel').allInnerTexts(), ['payments', 'backend', '+1']);
  assert.equal(await idem.locator('.pr-badge').innerText(), 'PR ✕');
  const checkout = page.locator('[data-task-key="tt-checkout"]');
  assert.equal(await checkout.locator('.tl-sub').innerText(), '1/4');
  assert.equal(await checkout.locator('.tl-sub').getAttribute('aria-label'), '1 of 4 subtasks done');
  assert.equal(await checkout.locator('.pr-badge').innerText(), 'PR changes');
  assert.match(await page.locator('[data-task-key="tt-demo"] .tl-due').innerText(), /^Overdue /);
  assert.match(await page.locator('[data-task-key="tt-onboard"] .tl-due').innerText(), /^Due /);
  const wake = page.locator('[data-task-key="tt-changelog"] .tl-due');
  assert.match(await wake.innerText(), /^Wakes /, 'a parked bot task says when it wakes');
  assert.equal(await wake.evaluate(el => el.classList.contains('late')), false);
  // A note is one muted line; amber only when it waits on you.
  const color = async sel => page.locator(sel).evaluate(n => getComputedStyle(n).color);
  const tokens = await page.evaluate(() => ['--muted', '--wait'].map(v => { const d = document.createElement('i'); d.style.color = `var(${v})`; document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c; }));
  assert.equal(await color('[data-task-key="tt-inbox"] .tl-note'), tokens[0], 'a waiting note is muted');
  assert.equal(await color('[data-task-key="tt-copy"] .tl-note'), tokens[1], 'a note that waits on you is amber');
  assert.equal(await color('[data-task-key="tt-changelog"] .tl-due'), tokens[0], 'a wake date is muted');
  // The requester's tiny face shows when someone asked you.
  assert.equal(await page.locator('[data-task-key="tt-copy"] .tl-asker').getAttribute('title'), 'Asked by Writer');
  assert.equal(await page.locator('[data-task-key="tt-triage"] .tl-asker').count(), 0, 'not on a bot-to-bot task');
  await shot(page, 'list-status-dark');
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  await shot(page, 'list-status-light');
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });

  // Fold a parent with ▸ (and with ←/→ on its row); fold a group, and that is remembered.
  const news = page.locator('[data-task-key="tt-news"]');
  await news.locator('[data-expand]').click();
  assert.equal(await page.locator('[data-task-key="tt-img"]').count(), 0);
  await news.locator('.tl-open').focus();
  await page.keyboard.press('ArrowRight');
  await page.locator('[data-task-key="tt-img"]').waitFor();
  await page.keyboard.press('ArrowLeft');
  await page.locator('[data-task-key="tt-img"]').waitFor({state: 'detached'});
  assert.equal(await focusedKey(page), 'tt-news', 'the focus stays on the parent');
  await page.keyboard.press('ArrowRight');
  await page.locator('[data-task-key="tt-img"]').waitFor();
  const waitingToggle = page.locator('[data-group-toggle="list:status:waiting"]');
  await waitingToggle.click();
  assert.equal(await waitingToggle.getAttribute('aria-expanded'), 'false');
  assert.equal(await page.locator('#task-body [data-group="waiting"] .tl-rows').isHidden(), true);
  // + in a group: a new task already set up for it (Needs you: for you; Needs Sam: for Sam).
  await page.locator('[data-group-add="needsme"]').click();
  await page.locator('#task-create[open]').waitFor();
  assert.equal(await page.locator('#task-create select[name=owner]').inputValue(), 'human:ana');
  await page.locator('#task-create [data-modal-close]').click();
  await page.locator('[data-group-add="needs:human:sam"]').click();
  assert.equal(await page.locator('#task-create select[name=owner]').inputValue(), 'human:sam');
  await page.locator('#task-create [data-modal-close]').click();

  // Group by is a chip with a menu (↑/↓, Enter) like the filters. Owner: You first; subtasks nest under a parent of the same owner.
  assert.equal(await page.locator('#task-group').getAttribute('aria-label'), 'Group by: Status. Change');
  await page.locator('#task-group').click();
  assert.equal(await page.evaluate(() => document.activeElement.dataset.pickGroup), 'status', 'the menu opens on the current choice');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#task-body .tl')?.dataset.groupBy === 'owner');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'task-group', 'the focus comes back to the chip');
  assert.deepEqual(await groupNames(page), ['YOU', 'JO', 'SAM', 'ANALYST', 'BOTOPS', 'ENGINEER', 'SUPPORT', 'WRITER']);
  assert.equal(await page.locator('[data-task-key="tt-checkout"] > .task-status').getAttribute('data-status'), 'waiting', 'owner groups still need task status');
  assert.equal(new Set(await rowKeys(page)).size, OPEN_COUNT);
  assert.equal(await page.locator('.tl-group[data-group="o:bot:engineer"] [data-task-key="tt-idem"]').getAttribute('aria-level'), '2', 'nested under the checkout');
  assert.equal(await page.locator('.tl-group[data-group="o:human:ana"] [data-task-key="tt-terms"]').count(), 1, 'under You, not under its parent');
  await page.waitForTimeout(450);   // the preference is saved after a short pause
  await shot(page, 'list-owner-dark');
  await groupBy(page, 'tag');
  assert.ok((await groupNames(page)).includes('NO TAG'));
  await groupBy(page, 'team');
  assert.deepEqual(await groupNames(page), ['ENGINEERING', 'MARKETING', 'OPERATIONS', 'SUPPORT', 'NO TEAM']);
  await groupBy(page, 'parent');
  assert.deepEqual(await groupNames(page), ['DRAFT THE OCTOBER NEWSLETTER', 'SHIP THE CHECKOUT REDESIGN', 'NO PARENT']);
  await groupBy(page, 'owner');
  await page.waitForTimeout(450);
  await page.reload();
  await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body .tl')?.dataset.groupBy === 'owner');
  await groupBy(page, 'status');
  assert.equal(await page.locator('[data-group-toggle="list:status:waiting"]').getAttribute('aria-expanded'), 'false', 'a folded group stays folded');
  await page.locator('[data-group-toggle="list:status:waiting"]').click();
  assert.deepEqual(errors, []);
  console.log('rows, tabs, groups: ok');
  await page.close();
}

async function filters(browser) {
  const {page, errors} = await open(browser);
  await page.locator('#task-filter').click();
  await page.locator('#task-filter-pop [data-pick-field="owner"]').click();
  await page.locator('#task-filter-pop input[value="bot:engineer"]').check();
  await page.waitForFunction(() => location.hash.includes('owner=bot:engineer'));
  assert.equal(new URL(page.url()).hash, '#/tasks?owner=bot:engineer&type=general&view=list', 'one address per view');
  const owners = await page.locator('#task-body .tl-row .tl-face').evaluateAll(fs => fs.map(f => f.title));
  assert.ok(owners.length === 4 && owners.every(o => o === 'Engineer'), 'only Engineer: ' + owners);
  // ↑/↓ move through a menu's values.
  await page.locator('#task-filter-pop input[value="bot:engineer"]').focus();
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(() => document.activeElement.value), 'bot:support');
  await page.keyboard.press('Space');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#task-filter-pop').evaluate(p => p.matches(':popover-open')), false);
  assert.match(await page.locator('[data-chip="owner"]').innerText(), /Owner\s*Engineer, Support/);
  // A second filter: Has PR.
  await page.locator('#task-filter').click();
  await page.locator('#task-filter-pop [data-pick-field="pr"]').click();
  await page.locator('#task-filter-pop input[value="yes"]').check();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => location.hash.includes('pr=yes'));
  assert.deepEqual((await rowKeys(page)).sort(), ['tt-checkout', 'tt-flaky', 'tt-idem', 'tt-summary']);
  await shot(page, 'filters-dark');
  await page.locator('[data-chip-edit="owner"]').click();
  await page.locator('#task-filter-pop input[value="bot:engineer"]').waitFor();
  assert.equal(await page.locator('#task-filter-pop input[value="bot:engineer"]').isChecked(), true, 'a chip reopens its values');
  await shot(page, 'filters-menu-dark');
  await page.keyboard.press('Escape');
  // Remove a chip with its ×: the address follows.
  await page.locator('[data-chip-drop="owner"]').click();
  await page.waitForFunction(() => !location.hash.includes('owner='));
  assert.equal(await page.locator('[data-chip="owner"]').count(), 0);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'task-filter', 'focus lands on Filter');
  // A filtered address always names its view, so a Needs-you link made on the board reopens as Needs you.
  await page.goto('https://tico-ui.test/#/board');
  await page.waitForFunction(() => TASKS_ST?.view === 'board' && !TASKS_ST.loading);
  await page.locator('#task-view [data-view="foryou"]').click();
  await page.locator('#task-filter').click();
  await page.locator('#task-filter-pop [data-pick-field="tag"]').click();
  await page.locator('#task-filter-pop input[value="pricing"]').check();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => location.hash === '#/tasks?tag=pricing&type=general&view=foryou');
  await page.reload();
  await page.waitForFunction(() => TASKS_ST?.view === 'foryou' && !TASKS_ST.loading);
  // A shared address opens with its filters.
  await page.goto('https://tico-ui.test/#/tasks?owner=me&tag=pricing,billing&view=list');
  await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body .tl-row'));
  assert.equal(await page.locator('[role=tab][aria-selected=true]').getAttribute('data-view'), 'list');
  assert.match(await page.locator('[data-chip="owner"]').innerText(), /You/);
  assert.deepEqual((await rowKeys(page)).sort(), ['tt-copy', 'tt-refund']);
  // Nothing matches: one quiet line and a way out.
  await page.locator('#task-q').fill('zebra');
  await page.locator('#task-body .tl-empty').waitFor();
  assert.match(await page.locator('#task-body .tl-empty').innerText(), /No tasks match\.\s*Clear filters/);
  await page.locator('[data-clear-filters]').click();
  await page.waitForFunction(n => location.hash === '#/tasks?type=general&view=list' && document.querySelectorAll('#task-body .tl-row').length === n, OPEN_COUNT);   // the view stays in the link
  assert.equal(await page.locator('#task-q').inputValue(), '');
  assert.deepEqual(errors, []);
  console.log('filter chips and the address: ok');
  await page.close();
}

async function typeSelection(browser) {
  const types = [
    {id: 'general', name: 'General', steps: [{id: 'general-open', name: 'Open', status: 'open'}]},
    {id: 'dev-123', name: 'Dev ticket', steps: [{id: 'dev-backlog', name: 'Backlog', status: 'open'}]},
    {id: 'marketing', name: 'Marketing', steps: [{id: 'draft', name: 'Draft', status: 'open'}]},
  ];
  const extraTasks = types.slice(1).map(type => ({...fixtures()[0], id: type.id + '-task', title: type.name + ' only',
    type_id: type.id, type: {id: type.id, name: type.name}, step_id: type.steps[0].id, status: 'open'}));
  const {page, errors, posts} = await open(browser, {types, extraTasks});
  assert.equal(await page.locator('#task-type [aria-pressed=true]').innerText(), 'General');
  assert.equal(await page.locator('[data-task-key="tdev-123-task"]').count(), 0);
  await page.locator('#task-filter').click();
  assert.equal(await page.locator('[data-pick-field=type]').count(), 0);
  await page.keyboard.press('Escape');
  await page.locator('[data-task-type=dev-123]').click();
  assert.deepEqual(await rowKeys(page), ['tdev-123-task']);
  await page.locator('[data-view=board]').click();
  assert.deepEqual(await page.locator('#task-body .bcol h2').allTextContents(), ['Backlog']);
  await page.locator('#task-q').fill('General');
  await page.waitForFunction(() => !document.querySelector('#task-body .bcard'));
  await page.locator('#task-q').fill('');
  await page.waitForFunction(() => document.querySelector('#task-body .bcard'));
  await shot(page, 'type-selector-board-dark');
  await page.locator('#task-type-more').click();
  assert.equal(await page.locator('#task-type-more').getAttribute('aria-expanded'), 'true');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#task-type-more').innerText(), 'Marketing');
  assert.deepEqual(await page.locator('#task-body .bcol h2').allTextContents(), ['Draft']);
  await page.locator('[data-view=list]').click();
  assert.deepEqual(await rowKeys(page), ['tmarketing-task']);
  await page.locator('#task-q').fill('no matching task');
  await page.locator('[data-clear-filters]').click();
  assert.equal(await page.evaluate(() => TASKS_ST.type), 'marketing');
  assert.deepEqual(await rowKeys(page), ['tmarketing-task']);
  await page.locator('#task-filter').click();
  await page.locator('[data-pick-field=tag]').click();
  await page.locator('#task-filter-pop input[type=checkbox]').first().check();
  await page.keyboard.press('Escape');
  await page.locator('#task-filter-clear').click();
  assert.equal(await page.evaluate(() => TASKS_ST.type), 'marketing');
  assert.deepEqual(await rowKeys(page), ['tmarketing-task']);
  await page.locator('#task-new').click();
  await page.locator('#task-create select[name=type]').waitFor();
  assert.equal(await page.locator('#task-create select[name=type]').inputValue(), 'marketing');
  await page.locator('#task-create [name=title]').fill('New campaign');
  await page.locator('#task-create [name=owner]').selectOption('engineer');
  await page.locator('#task-create [name=body]').fill('Launch details');
  await page.locator('#task-create button[type=submit]').click();
  await page.locator('#task-create').waitFor({state: 'hidden'});
  assert.equal(posts.find(x => x.p === '/api/v2/tasks').body.type, 'marketing');
  await page.waitForTimeout(500);
  await page.goto('https://tico-ui.test/#/issues');
  await page.waitForFunction(() => !TASKS_ST.loading && TASKS_ST.type === 'marketing');
  assert.equal(await page.locator('#task-type-more').innerText(), 'Marketing');
  await page.evaluate(() => document.documentElement.dataset.theme = 'light');
  await shot(page, 'type-selector-list-light');
  await page.setViewportSize({width: 390, height: 844});
  await shot(page, 'type-selector-phone-light');
  const fits = await page.locator('.tl-head').evaluate(el => el.scrollWidth <= el.clientWidth + 1);
  assert.equal(fits, true, 'type and search fit the phone toolbar');
  // A late saved preference must not replace the choice just made in the header.
  await page.route('**/api/v2/preferences/tasks.view', async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    await new Promise(resolve => setTimeout(resolve, 1000));
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({value: {type: 'marketing', view: 'list', views: 2}})});
  });
  await page.goto('https://tico-ui.test/#/goals');
  await page.goto('https://tico-ui.test/#/issues');
  await page.locator('[data-task-type=dev-123]').click();
  await page.waitForTimeout(1200);
  assert.equal(await page.evaluate(() => TASKS_ST.type), 'dev-123');
  assert.deepEqual(await rowKeys(page), ['tdev-123-task']);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('required task type, persistence, search, creation and filter clearing: ok');
}

async function carriedOver(browser) {
  // Filters an older page saved ("Mine" here, "Asked by me" in the saved preference) come over once as chips.
  const {page, errors, pref} = await open(browser, {local: {'hub.tasks.filter': 'mine'}, prefs: {filter: 'asked', view: 'list', views: 2}});
  await page.waitForFunction(() => location.hash.includes('owner=me') && location.hash.includes('asked=me'));
  assert.match(await page.locator('[data-chip="owner"]').innerText(), /Owner\s*You/);
  assert.match(await page.locator('[data-chip="asked"]').innerText(), /Asked by\s*You/);
  assert.equal(await page.evaluate(() => localStorage.getItem('hub.tasks.filter')), null, 'the old key is gone');
  await page.waitForFunction(() => true);
  await page.waitForTimeout(500);
  assert.equal(pref().filter, undefined, 'the saved preference no longer carries it');
  assert.deepEqual(errors, []);
  await page.close();
  // A type that does not exist (deleted, or from another install) falls back to General.
  const second = await open(browser, {hash: '#/tasks?type=ghost&view=list'});
  await second.page.waitForFunction(() => TASKS_ST.type === 'general' && location.hash.includes('type=general'));
  assert.equal(await second.page.locator('#task-body .tl-row').count(), OPEN_COUNT);
  assert.equal(await second.page.locator('[data-chip="type"]').count(), 0);
  await second.page.close();
  const third = await open(browser, {prefs: {type: 'gone', view: 'list', views: 2}});
  await third.page.waitForFunction(() => TASKS_ST.typesLoaded && TASKS_ST.type === 'general');
  assert.equal(await third.page.locator('#task-body .tl-row').count(), OPEN_COUNT);
  assert.deepEqual([...second.errors, ...third.errors], []);
  await third.page.close();
  console.log('old filters and unknown types: ok');
}

async function peekAndKeys(browser) {
  const {page, errors, posts} = await open(browser);
  const peek = page.locator('#task-peek');
  await page.locator('[data-task-key="tt-inbox"] .tl-title').click();
  await peekTitle(page, 'Migrate the support inbox');
  assert.equal(await page.locator('#task-modal[open]').count(), 0, 'no covering modal');
  const list = await page.locator('#tasks-pane').boundingBox(), side = await peek.boundingBox();
  assert.ok(side.x >= list.x + list.width - 1 && side.width >= 380 && list.width >= 600, `list and peek side by side: ${JSON.stringify([list, side])}`);
  assert.equal(await page.locator('[data-task-key="tt-inbox"]').evaluate(r => r.classList.contains('peeked')), true);
  assert.equal(await peek.locator('.tmodal-head .tstatus').innerText(), 'Waiting');
  await peek.locator('.task-comments .tcomment').first().waitFor();
  // The focus stays in the list: ↓ / j move the peek with it; k / ↑ back.
  assert.equal(await focusedKey(page), 'tt-inbox');
  await page.keyboard.press('ArrowDown');
  await peekTitle(page, 'Publish the September changelog');
  await page.keyboard.press('j');
  await peekTitle(page, 'Ship the checkout redesign');
  await page.keyboard.press('k');
  await peekTitle(page, 'Publish the September changelog');
  await page.keyboard.press('ArrowDown');
  await peekTitle(page, 'Ship the checkout redesign');
  await peek.locator('.task-subs .sub-row').first().waitFor();
  assert.ok(await peek.locator('.task-subs .sub-row .task-status').count() >= 2, 'subtask rows name their status');
  await shot(page, 'peek-dark');
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  await shot(page, 'peek-light');
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  // A subtask opened from the peek moves the list's place too.
  await peek.locator('[data-sub-open="t-summary"]').click();
  await peekTitle(page, 'Checkout summary step');
  await page.waitForFunction(() => TASKS_ST.peek === 'tt-summary' && document.querySelector('[data-task-key="tt-summary"]').classList.contains('peeked'));
  // Esc closes the peek and keeps the place in the list.
  await page.keyboard.press('Escape');
  await peek.waitFor({state: 'hidden'});
  assert.equal(await focusedKey(page), 'tt-summary');
  // Enter opens it again (a second Enter takes the keyboard into it); Open full opens the modal.
  await page.keyboard.press('Enter');
  await peekTitle(page, 'Checkout summary step');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.matches('#task-peek .tmodal-title')), true);
  await peek.locator('[data-task-more]').click();
  await peek.locator('.prop-pop .tl-mi', {hasText: 'Open full'}).click();
  await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Checkout summary step'}).waitFor();
  assert.equal(await peek.isHidden(), true);
  await page.keyboard.press('j');
  assert.equal(await page.locator('#task-modal .tmodal-title').innerText(), 'Checkout summary step', 'keys stay quiet under a modal');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('#task-modal')?.open);
  // / searches; typing never moves the list; Esc leaves the field.
  await page.locator('body').click({position: {x: 5, y: 5}});
  await page.keyboard.press('/');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'task-q');
  await page.keyboard.type('jk x');
  assert.equal(await page.locator('#task-q').inputValue(), 'jk x');
  assert.equal(await page.locator('#task-peek[open]').count(), 0);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  assert.notEqual(await page.evaluate(() => document.activeElement.id), 'task-q');
  // x selects the row under the cursor; Esc clears.
  await page.locator('[data-task-key="tt-triage"] .tl-open').focus();
  await page.keyboard.press('x');
  assert.equal(await page.locator('[data-task-key="tt-triage"]').evaluate(r => r.classList.contains('sel')), true);
  assert.equal(await page.locator('#task-bulk').isVisible(), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#task-bulk').isHidden(), true);
  // c creates.
  await page.keyboard.press('c');
  await page.locator('#task-create[open]').waitFor();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('#task-create')?.open);
  // Done in the peek: the list moves on to the next row and the peek follows it.
  await page.locator('[data-task-key="tt-triage"] .tl-title').click();
  await peekTitle(page, 'Triage new bug reports');
  await peek.locator('[data-prop="status"]').click();
  await peek.locator('.prop-pop [data-prop-pick="done"]').click();
  await peekTitle(page, 'Add idempotency keys to the payments API');
  assert.equal(await focusedKey(page), 'tt-idem', 'the focus is on the next row');
  assert.equal(await page.locator('[data-task-key="tt-triage"]').count(), 0);
  assert.deepEqual(taskWrites(posts).map(x => [x.p, x.body]), [['/api/v2/tasks/t-triage', {version: 3, status: 'done'}]]);
  await page.keyboard.press('Escape');
  // Tabs: arrow keys move between them; the view follows.
  await page.locator('#task-view [data-view="list"]').focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForFunction(() => TASKS_ST.view === 'board');
  assert.equal(await page.locator('#task-view [data-view="board"]').getAttribute('aria-selected'), 'true');
  assert.deepEqual(errors, []);
  console.log('peek and keys: ok');
  await page.close();
}

async function properties(browser) {
  const {page, errors, posts, tasks} = await open(browser);
  const peek = page.locator('#task-peek'), props = peek.locator('[data-task-props]');
  await page.locator('[data-task-key="tt-checkout"] .tl-title').click();
  await peek.locator('.task-comments .tcomment').first().waitFor();
  // One row each: a muted label, then an icon and a value; empty ones read "Add …". In the peek they sit at the top.
  await peek.locator('.task-subs .sub-bar').waitFor();
  const rows = await props.locator('.prop').evaluateAll(rs => rs.map(r => [r.querySelector('.prop-k').textContent,
    [...r.querySelectorAll('.prop-txt, .tlabel > [data-tag-key]')].map(x => x.textContent.trim()).join(' ')]));
  assert.deepEqual(rows, [['Status', 'Waiting'], ['Owner', 'Engineer'], ['Asked by', 'You'], ['Private', ''], ['Due', 'Add due date'], ['Tags', 'checkout'],
    ['Part of', 'Add parent'], ['Blocked by', 'Add idempotency keys to the payments API']]);
  assert.match(await props.locator('[data-prop="status"]').innerText(), /Waiting/);
  // Detail names the status; the status-grouped list uses its heading. "blocked" is a small chip.
  assert.equal(await peek.locator('.tmodal-head .tstatus').innerText(), 'Waiting');
  assert.equal(await peek.locator('.tmodal-head .tchip-blocked').innerText(), 'blocked');
  assert.equal(await page.locator('[data-task-key="tt-checkout"] > .task-status').count(), 0);
  assert.match(await props.locator('[data-prop-row="blocked"]').innerText(), /Blocked by/);
  // The header: status · owner · position · age · … · ✕. No unexplained arrows.
  assert.equal(await peek.locator('.tmodal-head button').count(), 2);
  assert.match(await peek.locator('.tmodal-head .peek-pos').innerText(), /^\d+ \/ \d+$/);
  assert.match(await peek.locator('.tmeta').innerText(), /^Added by you · /);
  const top = await props.boundingBox(), main = await peek.locator('.tmodal-main').boundingBox();
  assert.ok(top.y < main.y, 'properties first in the peek');
  assert.equal(await peek.locator('.tmodal-main select, [data-task-props] select, [data-modal-task], .tcontrols').count(), 0, 'no boxed form, no Done/Close links');
  assert.equal(await peek.locator('.task-ask').count(), 0, 'the blocker is a property, not a second box');
  // Status: a small menu; ↑/↓ and Enter pick, the list's j/k wait while it is open, and the row keeps the focus after saving.
  await props.locator('[data-prop="status"]').focus();
  await page.keyboard.press('Enter');
  const menu = peek.locator('.prop-pop');
  await menu.locator('[data-prop-pick="waiting"]').waitFor();
  assert.equal(await page.evaluate(() => document.activeElement.dataset.propPick), 'waiting', 'opens on the current status');
  assert.equal(await props.locator('[data-prop="status"]').getAttribute('aria-expanded'), 'true');
  await shot(page, 'peek-status-menu-dark');
  await page.keyboard.press('j');
  assert.equal(await page.evaluate(() => TASKS_ST.cursor), 'tt-checkout', 'j does not move the list while the menu is open');
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.propPick), 'review');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop="status"]')?.textContent.includes('In review'));
  assert.deepEqual(taskWrites(posts).at(-1).body, {version: 3, status: 'review'});
  await page.waitForFunction(() => document.activeElement?.dataset.prop === 'status');
  // Esc closes a menu without changing anything; the focus goes back to its row.
  await page.keyboard.press('Enter');
  await menu.locator('.tl-mi').first().waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.prop), 'status');
  assert.equal(await peek.isVisible(), true, 'Esc in a menu leaves the peek open');
  // Owner: an avatar picker with a find box.
  await props.locator('[data-prop="owner"]').click();
  await page.keyboard.type('supp');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop="owner"]')?.textContent.includes('Support'));
  assert.deepEqual(taskWrites(posts).at(-1).body, {version: 4, owner: 'support'});
  // Due: a date picker; × clears it.
  await props.locator('[data-prop-date]').fill('2026-10-09');
  await page.waitForFunction(() => /Oct 9/.test(document.querySelector('#task-peek [data-prop="due"]')?.textContent || ''));
  assert.match(taskWrites(posts).at(-1).body.due, /^2026-10-\d\dT\d\d:00:00\.000Z$/);
  await props.locator('[data-prop-row="due"]').hover();
  await props.locator('[data-prop-clear="due"]').click();
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop="due"]')?.textContent.includes('Add due date'));
  assert.equal(taskWrites(posts).at(-1).body.due, '');
  // Tags: chips and a small + that opens an input with suggestions.
  await props.locator('[data-prop="tags"]').click();
  await page.keyboard.type('Urgent');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop-row="tags"]')?.textContent.includes('urgent'));
  assert.deepEqual(taskWrites(posts).at(-1).body.labels, ['checkout', 'urgent']);
  // Part of: a task picker with a find box; × clears. Blocked by: clear.
  await props.locator('[data-prop="parent"]').click();
  await page.keyboard.type('newsletter');
  await page.keyboard.press('ArrowDown');
  assert.match(await page.evaluate(() => document.activeElement.textContent), /Draft the October newsletter/);
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop-row="parent"] [data-open-task="tt-news"]')?.textContent.includes('Draft the October newsletter'));
  assert.equal(taskWrites(posts).at(-1).body.parent_id, 't-news');
  await props.locator('[data-prop-row="blocked"]').hover();
  await props.locator('[data-prop-clear="blocked"]').click();
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop="blocked"]')?.textContent.includes('Add blocker'));
  assert.equal(taskWrites(posts).at(-1).body.blocked_by, '');
  // A change made elsewhere: the save is refused, the server's value shows, with a short line. Never the stale value.
  Object.assign(tasks.find(x => x.id === 't-checkout'), {status: 'doing', version: 42});
  await props.locator('[data-prop="status"]').click();
  await menu.locator('[data-prop-pick="ready"]').click();
  await page.waitForFunction(() => document.querySelector('#task-peek [data-props-msg]')?.textContent === 'Changed elsewhere. Showing the latest.');
  assert.match(await props.locator('[data-prop="status"]').innerText(), /Doing/);
  // The "…" menu holds Close (and Copy link, Add subtask).
  await peek.locator('[data-task-more]').click();
  assert.deepEqual(await menu.locator('.tl-mi').allInnerTexts(), ['Open full', 'Copy link', 'Close task'], 'Add subtask is the rail\'s own field here');
  await menu.locator('.tl-mi', {hasText: 'Copy link'}).click();
  assert.match(await page.evaluate(() => window.copied), /#\/task\/t-checkout$/);
  // The full task: properties at the top of the side column, then Code, then Subtasks.
  await peek.locator('[data-task-more]').click();
  await menu.locator('.tl-mi', {hasText: 'Open full'}).click();
  const modal = page.locator('#task-modal');
  await modal.locator('.task-subs .sub-row').first().waitFor();
  const [p, c, s, m] = await Promise.all(['[data-task-props]', '.task-code', '.task-subs', '.tmodal-main'].map(sel => modal.locator(sel).boundingBox()));
  assert.ok(p.y < c.y && c.y < s.y && p.x > m.x + m.width - 1, 'Properties, Code, Subtasks, beside the details');
  await shot(page, 'modal-dark');
  await modal.locator('[data-task-more]').click();
  await modal.locator('.prop-pop .tl-mi', {hasText: 'Close task'}).click();
  await page.waitForFunction(() => document.querySelector('#task-modal [data-prop="status"]')?.textContent.includes('Closed'));
  assert.deepEqual(taskWrites(posts).at(-1).body, {version: 42, close: true});
  assert.deepEqual(errors, []);
  console.log('properties: ok');
  await page.close();
}

async function board(browser) {
  const {page, errors} = await open(browser, {hash: '#/board'});
  await page.locator('#task-body .bcard').first().waitFor();
  const cols = await page.locator('#task-body .bcol').evaluateAll(cs => cs.map(c => [c.dataset.col, c.classList.contains('is-empty'), Math.round(c.getBoundingClientRect().width)]));
  // The board names its columns the way the list names its groups: Needs you, Needs <Name>, Needs someone.
  assert.deepEqual(cols.map(c => c[0]), ['needsme', 'needs:human:sam', 'needs-someone', 'waiting', 'doing', 'scheduled']);
  assert.deepEqual(await page.locator('#task-body .bcol h2').allInnerTexts(), ['NEEDS YOU', 'NEEDS SAM', 'NEEDS SOMEONE', 'WAITING', 'DOING', 'SCHEDULED']);
  const [empty] = cols.filter(c => c[1]);
  assert.equal(empty[0], 'scheduled');
  assert.ok(empty[2] <= 40, 'an empty column is a thin strip: ' + empty[2]);
  const widths = cols.filter(c => !c[1]).map(c => c[2]);
  assert.ok(Math.max(...widths) - Math.min(...widths) <= 2, 'columns with work share the width: ' + widths);
  assert.equal(await page.locator('[data-col="scheduled"] h2').evaluate(h => getComputedStyle(h).writingMode), 'vertical-rl');
  const card = page.locator('.bcard[data-task-key="tt-checkout"]');
  assert.deepEqual(await card.locator('.bcard-chips > *').evaluateAll(cs => cs.map(c => c.className.split(' ')[0])), ['tlabel', 'pr-badge', 'tl-sub']);
  assert.doesNotMatch(await page.locator('#task-body').innerText(), /Added by|Waiting on Sam/);
  // Cards can be selected (x, ⌘-click) and show it.
  await card.locator('.bcard-open').focus();
  await page.keyboard.press('x');
  await page.locator('.bcard[data-task-key="tt-flaky"] .bcard-title').click({modifiers: [process.platform === 'darwin' ? 'Meta' : 'Control']});
  assert.deepEqual(await page.locator('.bcard.sel').evaluateAll(cs => cs.map(c => c.dataset.taskKey)).then(k => k.sort()), ['tt-checkout', 'tt-flaky']);
  assert.equal(await page.locator('.tl-bulk-n').innerText(), '2 selected');
  await shot(page, 'board-dark');
  await page.keyboard.press('Escape');
  await card.click();
  await peekTitle(page, 'Ship the checkout redesign');
  assert.deepEqual(errors, []);
  console.log('board: ok');
  await page.close();
}

async function bulk(browser) {
  const {page, errors, posts, tasks} = await open(browser, {failIds: ['t-news']});
  const box = key => page.locator(`[data-task-key="${key}"] .tl-check`);
  // Only what can be seen selected is acted on: a search that hides selected rows drops them.
  await box('tt-triage').click();
  await box('tt-rotate').click();
  await box('tt-flaky').click();
  assert.equal(await page.locator('.tl-bulk-n').innerText(), '3 selected');
  await page.locator('#task-q').fill('rotate');
  await page.waitForFunction(() => document.querySelector('.tl-bulk-n')?.textContent === '1 selected');
  await page.locator('#task-bulk [data-bulk="close"]').click();
  await page.waitForFunction(() => /1 closed/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
  assert.deepEqual(taskWrites(posts).map(x => [x.p, x.body]), [['/api/v2/tasks/t-rotate', {version: 3, close: true}]], 'only the visible task');
  // Close can be undone for ten seconds.
  await page.locator('#task-bulk [data-bulk="undo"]').click();
  assert.equal(await page.evaluate(() => TASKS_ST.bulkBusy), true, 'no other bulk action while Undo runs');
  await page.waitForFunction(() => /Reopened; bots were already told/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
  assert.deepEqual(taskWrites(posts).at(-1), {p: '/api/v2/tasks/t-rotate', body: {version: 4, status: 'doing'}}, 'back to what it was, not just Open');
  await page.locator('#task-q').fill('');
  await page.locator('#task-q').press('Escape');            // leaves the search, then Esc dismisses the result line
  await page.waitForFunction(() => document.querySelector('#task-bulk').hidden);
  // Range + one more; the peek follows a bulk change to its task; a stale version is fetched again and retried once;
  // one task is refused and stays selected and marked.
  await page.locator('[data-task-key="tt-digest"] .tl-title').click();
  await peekTitle(page, 'Weekly metrics digest');
  tasks.find(x => x.id === 't-idem').version = 7;                     // a bot changed it since the list loaded
  await box('tt-digest').click();
  await box('tt-news').click({modifiers: ['Shift']});
  await page.locator('[data-task-key="tt-flaky"] .tl-title').click({modifiers: [process.platform === 'darwin' ? 'Meta' : 'Control']});
  const selected = await page.locator('#task-body .tl-row.sel').evaluateAll(rs => rs.map(r => r.dataset.taskKey));
  assert.deepEqual(selected, ['tt-digest', 'tt-triage', 'tt-idem', 'tt-rotate', 'tt-summary', 'tt-news', 'tt-flaky']);
  assert.equal(await box('tt-triage').getAttribute('aria-label'), 'Select Triage new bug reports from the widget');
  await shot(page, 'bulk-dark');
  const before = taskWrites(posts).length;
  await page.locator('#task-bulk [data-bulk="status"]').click();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.bulkStatus), 'waiting', '↑/↓ move through the bulk menu');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => /not changed/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
  const writes = taskWrites(posts).slice(before).map(x => [x.p.split('/').pop(), x.body.version]);
  assert.deepEqual(writes, [['t-digest', 3], ['t-triage', 3], ['t-idem', 3], ['t-idem', 7], ['t-rotate', 5], ['t-summary', 3], ['t-news', 3], ['t-news', 3], ['t-flaky', 3]],
    'each task its own update; a stale one fetched again and retried once');
  assert.equal(await page.locator('.tl-bulk-msg').innerText(), '6 moved to Waiting, 1 not changed: Draft the October newsletter');
  assert.deepEqual(await page.locator('#task-body .tl-row.sel').evaluateAll(rs => rs.map(r => r.dataset.taskKey)), ['tt-news']);
  assert.match(await page.locator('[data-task-key="tt-news"] .tl-failed').getAttribute('aria-label'), /Not changed: Task changed/);
  await page.waitForFunction(() => document.querySelector('#task-peek [data-prop="status"]')?.textContent.includes('Waiting'));
  await page.locator('#task-bulk [data-bulk="clear"]').click();
  await page.keyboard.press('Escape');
  // Done on two bot requests: each gets its own note, and the prompt says which request it is for.
  await box('tt-copy').click();
  await box('tt-refund').click();
  await page.locator('#task-bulk [data-bulk="status"]').click();
  await page.locator('#task-filter-pop [data-bulk-status="done"]').click();
  for (const [title, note] of [['Approve the Q4 pricing page copy', 'Go with headline B.'], ['Choose a refund policy for annual plans', 'Prorated after 30 days.']]) {
    await page.locator('dialog.task-outcome .task-outcome-title', {hasText: title}).waitFor();
    await page.locator('dialog.task-outcome textarea').fill(note);
    await page.locator('dialog.task-outcome button[type=submit]').click();
  }
  await page.waitForFunction(() => /2 moved to Done/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
  assert.deepEqual(taskWrites(posts).slice(-2).map(x => x.body.note), ['Go with headline B.', 'Prorated after 30 days.']);
  assert.deepEqual(errors, []);
  console.log('bulk: ok');
  await page.close();
  // Someone who cannot move tasks gets no boxes, no x and no bulk bar.
  const viewer = await open(browser, {mover: false});
  assert.equal(await viewer.page.locator('#task-body .tl-check').count(), 0);
  await viewer.page.locator('[data-task-key="tt-triage"] .tl-open').focus();
  await viewer.page.keyboard.press('x');
  assert.equal(await viewer.page.locator('#task-bulk').isHidden(), true);
  assert.deepEqual(viewer.errors, []);
  await viewer.page.close();
}

async function views(browser) {
  // Needs you: what waits on you, grouped by who asks. A bot's heading has a visible Chat link; a person's has none.
  const {page, errors} = await open(browser, {hash: '#/tasks'});
  assert.equal(await page.locator('[role=tab][aria-selected=true]').getAttribute('data-view'), 'foryou');
  assert.deepEqual(await groupNames(page), ['WRITER', 'ENGINEER', 'SUPPORT', 'SAM', 'BOTOPS'], 'whoever asked most recently first');
  assert.equal(await page.locator('#task-body .tl-row').count(), NEEDS_YOU);
  const chat = page.locator('.tl-group[data-group="a:bot:writer"] a.tl-gchat');
  assert.equal(await chat.getAttribute('href'), '#/bot/writer');
  assert.equal(await chat.innerText(), 'Chat');
  assert.equal(await chat.isVisible(), true, 'shown without hovering');
  assert.equal(await page.locator('.tl-group[data-group="a:human:sam"] .tl-gchat').count(), 0, 'no chat link for a person');
  await shot(page, 'needs-you-dark');
  await page.locator('#task-view [data-view="done"]').click();
  await page.waitForFunction(() => document.querySelectorAll('#task-body .tl-row').length === 3);
  assert.deepEqual(await page.locator('#task-body .tl-row > .task-status').evaluateAll(s => s.map(x => x.dataset.status)), ['done', 'done', 'closed']);
  assert.match(await page.locator('#task-body .tl-age').first().getAttribute('title'), /^Done /);
  assert.match(await page.locator('#task-body .tl-age').first().innerText(), /^\d+h$/, 'the same short ages as every other row');
  assert.equal(await page.locator('#task-group-wrap').isHidden(), true);
  await shot(page, 'done-dark');
  await page.locator('#task-view [data-view="recurring"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.rrow').length === 2);
  assert.equal(await page.locator('#task-bar').isHidden(), true);
  assert.deepEqual(errors, []);
  console.log('Needs you, Done, Recurring: ok');
  await page.close();
}

async function waitingOnYou(browser) {
  // A bot's task set waiting on you (`--on`) is in Needs you under that bot, whoever filed it, with a short "Waiting" chip
  // and no buttons other rows lack.
  const host = {...fixtures().find(x => x.id === 't-inbox'), id: 't-host', title: 'Restart the build host', owner: 'bot:engineer',
    requester: 'bot:botops', waiting_on: 'human:ana', note: 'It refuses SSH since the update.', updated: at(5)};
  const sams = {...host, id: 't-sams', title: 'Rotate the deploy key', waiting_on: 'human:sam'};
  const {page, errors} = await open(browser, {hash: '#/tasks', extraTasks: [host, sams]});
  const row = page.locator('.tl-group[data-group="a:bot:engineer"] [data-task-key="tt-host"]');
  assert.equal(await row.count(), 1);
  assert.equal(await row.locator('.tl-waiting').innerText(), 'Waiting');
  assert.equal(await page.locator('#task-body .tl-waiting').count(), 1, 'only on what waits on you');
  assert.equal(await page.locator('[data-task-key="tt-sams"]').count(), 0, 'what waits on Sam is not in your Needs you');
  const buttons = r => page.locator(`[data-task-key="${r}"] button`).evaluateAll(b => b.map(x => x.className));
  assert.deepEqual(await buttons('tt-host'), await buttons('tt-access'), 'no status buttons of its own');
  assert.deepEqual(errors, []);
  console.log('Waiting on you: ok');
  await page.close();
}

async function donePagination(browser) {
  const types = [{id: 'general', name: 'General', steps: []}, {id: 'support', name: 'Support', steps: []}];
  const task = (id, type, updated) => ({...fixtures()[0], id, title: id, type_id: type, status: 'done', updated, done_at: updated});
  const firstPage = Array.from({length: 20}, (_, i) => task(`support-done-${i}`, 'support', at(i + 1)));
  const matchingOlder = task('older-general-done', 'general', at(200));
  const {page, errors} = await open(browser, {hash: '#/tasks?type=general&view=done', types, baseTasks: [],
    extraTasks: [...firstPage, matchingOlder]});
  await page.waitForFunction(() => TASKS_ST.doneLoaded && !TASKS_ST.doneLoading && TASKS_ST.doneNext === 20);
  assert.equal(await page.locator('#task-body .tl-row').count(), 0, 'the newest global page has only another type');
  assert.match(await page.locator('#task-body .tl-empty').innerText(), /No matching finished tasks on this page/);
  assert.equal(await page.locator('#task-body #board-more').count(), 1, 'an empty filtered page still offers older results');
  await page.locator('#task-body #board-more').click();
  await page.locator('#task-body [data-task-key="tolder-general-done"]').waitFor();
  assert.equal(await page.locator('#task-body .tl-row').count(), 1, 'loading the next global page reaches the older matching task');
  assert.equal(await page.locator('#task-body #board-more').count(), 0, 'the exhausted matching page has no more control');
  assert.deepEqual(errors, []);
  await page.close();

  const exhausted = await open(browser, {hash: '#/tasks?type=general&view=done', types, baseTasks: [],
    extraTasks: firstPage.slice(0, 3)});
  await exhausted.page.waitForFunction(() => TASKS_ST.doneLoaded && !TASKS_ST.doneLoading && TASKS_ST.doneNext == null);
  assert.equal(await exhausted.page.locator('#task-body .tl-row').count(), 0);
  assert.equal(await exhausted.page.locator('#task-body .tl-empty').count(), 1, 'an exhausted empty history keeps its empty state');
  assert.equal(await exhausted.page.locator('#task-body #board-more').count(), 0, 'an exhausted empty history has no more control');
  assert.deepEqual(exhausted.errors, []);
  await exhausted.page.close();
  console.log('Done pagination through filtered empty pages: ok');
}

async function polling(browser) {
  // The 30-second poll reloads the list: a changed row redraws, the others stay the same nodes, the focus and the
  // scroll stay put, and an open peek shows the newer task.
  const {page, errors, tasks} = await open(browser, {viewport: {width: 1440, height: 500}});
  await page.locator('[data-task-key="tt-inbox"] .tl-title').click();
  await peekTitle(page, 'Migrate the support inbox');
  await page.locator('#tasks-pane').evaluate(p => { p.scrollTop = 120; });
  await page.evaluate(() => { window.kept = document.querySelector('[data-task-key="tt-flaky"]'); });
  Object.assign(tasks.find(x => x.id === 't-inbox'), {title: 'Migrate the support inbox to Helpdesk Pro', version: 9, updated: new Date().toISOString()});
  await page.evaluate(() => refresh(false));
  await page.waitForFunction(() => document.querySelector('[data-task-key="tt-inbox"] .tl-title')?.textContent.includes('Helpdesk Pro'));
  await peekTitle(page, 'Helpdesk Pro');
  assert.equal(await page.evaluate(() => window.kept === document.querySelector('[data-task-key="tt-flaky"]')), true, 'unchanged rows are not redrawn');
  assert.equal(await focusedKey(page), 'tt-inbox', 'the focus stays');
  assert.equal(await page.locator('#tasks-pane').evaluate(p => p.scrollTop), 120, 'the scroll stays');
  assert.deepEqual(errors, []);
  console.log('poll reload: ok');
  await page.close();
}

async function phone(browser) {
  const {page, errors, tasks} = await open(browser, {viewport: {width: 390, height: 844}});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  assert.ok(overflow <= 1, 'no sideways scroll: ' + overflow);
  const heights = await page.locator('#task-body .tl-row').evaluateAll(rows => rows.map(r => Math.round(r.getBoundingClientRect().height)));
  assert.ok(heights.every(h => h >= 44 && h <= 48), 'thumb-sized rows: ' + heights);
  await shot(page, 'phone-list-dark');
  // Select mode: a tap selects; the bar works.
  await page.locator('#task-select-mode').click();
  await page.locator('[data-task-key="tt-triage"] .tl-title').click();
  await page.locator('[data-task-key="tt-rotate"] .tl-title').click();
  assert.equal(await page.locator('.tl-bulk-n').innerText(), '2 selected');
  assert.equal(await page.locator('#task-peek[open]').count(), 0);
  await page.locator('#task-select-mode').click();
  assert.equal(await page.locator('#task-bulk').isHidden(), true);
  // The peek is a full-screen modal sheet; Back closes it and stays on Tasks. Long titles wrap or end in "…".
  const long = 'Ship the checkout redesign with the new summary step, saved carts and the receipts email for every region';
  Object.assign(tasks.find(x => x.id === 't-checkout'), {title: long, blocker: {id: 't-idem', title: 'Add idempotency keys to the payments API so a retried charge never bills a customer twice'}});
  await page.evaluate(() => tasksLoad(TASKS_ST));
  await page.locator('[data-task-key="tt-checkout"] .tl-title', {hasText: 'saved carts'}).waitFor();
  await page.locator('[data-task-key="tt-checkout"] .tl-title').click();
  await peekTitle(page, 'saved carts');
  assert.equal(await page.locator('#task-peek').evaluate(d => d.matches(':modal')), true, 'the list behind it is inert');
  const box = await page.locator('#task-peek').boundingBox();
  assert.ok(box.x <= 0 && box.y <= 0 && box.width >= 389 && box.height >= 843, 'a full-screen sheet: ' + JSON.stringify(box));
  await page.locator('#task-peek .task-comments .tcomment').first().waitFor();
  assert.equal(await page.locator('#task-peek').evaluate(d => d.scrollWidth <= d.clientWidth + 1), true, 'no sideways scroll in the sheet');
  assert.ok((await page.locator('#task-peek .tmodal-title').boundingBox()).height > 30, 'a long title wraps');
  assert.equal(await page.locator('#task-peek [data-prop-row="blocked"] .prop-txt').evaluate(t => getComputedStyle(t).textOverflow), 'ellipsis');
  const head = await page.locator('#task-peek .tmodal-head button').evaluateAll(bs => bs.map(b => Math.round(b.getBoundingClientRect().height)));
  assert.ok(head.every(h => h >= 40), 'header buttons are 40px tap targets: ' + head);
  // Properties stack full-width.
  const prop = await page.locator('#task-peek .prop').first().boundingBox();
  assert.ok(prop.width >= 340 && prop.height >= 44, 'a full-width row: ' + JSON.stringify(prop));
  await shot(page, 'phone-peek-dark');
  await page.goBack();
  await page.locator('#task-peek').waitFor({state: 'hidden'});
  assert.match(new URL(page.url()).hash, /^#\/issues/);
  assert.deepEqual(errors, []);
  console.log('phone: ok');
  await page.close();
}

// ---- the re-check (scratchpad/r2/recheck/probe.cjs, as tests)
async function recheckSaves(browser) {
  // N1: a quick second edit is never lost. Saves queue per task, each with the version the last one returned; the
  // panel redraws at once from the server's answer while the (slow) list reload runs behind it.
  const {page, errors, posts, tasks} = await open(browser);
  await page.route('**/api/v2/tasks?*', async route => { await new Promise(r => setTimeout(r, 1500)); await route.fallback(); });
  const peek = page.locator('#task-peek'), props = peek.locator('[data-task-props]');
  await page.locator('[data-task-key="tt-triage"] .tl-title').click();
  await props.locator('[data-prop="status"]').waitFor();
  await page.waitForTimeout(400);
  await props.locator('[data-prop="status"]').click();
  await peek.locator('.prop-pop [data-prop-pick="waiting"]').click();
  await page.waitForTimeout(150);
  assert.match(await props.locator('[data-prop="status"]').innerText(), /Waiting/, 'the saved value shows at once');
  await props.locator('[data-prop="owner"]').click();
  await peek.locator('.prop-pop [data-prop-pick="writer"]').click();
  await page.waitForFunction(() => /Writer/.test(document.querySelector('#task-peek [data-prop="owner"]')?.textContent || ''));
  const writes = () => taskWrites(posts).filter(x => x.p.endsWith('/t-triage')).map(x => x.body);
  assert.deepEqual(writes(), [{version: 3, status: 'waiting'}, {version: 4, owner: 'writer'}], 'one at a time, each with the newest version');
  assert.equal(tasks.find(x => x.id === 't-triage').owner, 'bot:writer');
  await page.waitForTimeout(1800);
  assert.equal(await props.locator('[data-props-msg]').innerText(), '');
  assert.match(await props.locator('[data-prop="owner"]').innerText(), /Writer/, 'the slow reload does not draw an older copy');
  // A refused save's reason stays until that property saves; another property's save does not wipe it.
  tasks.find(x => x.id === 't-triage').version += 5;                     // changed elsewhere
  await props.locator('[data-prop="owner"]').click();
  await peek.locator('.prop-pop [data-prop-pick="support"]').click();
  await page.waitForFunction(() => document.querySelector('#task-peek [data-props-msg]')?.textContent === 'Changed elsewhere. Showing the latest.');
  await props.locator('[data-prop="status"]').click();
  await peek.locator('.prop-pop [data-prop-pick="doing"]').click();
  await page.waitForFunction(() => /Doing/.test(document.querySelector('#task-peek [data-prop="status"]')?.textContent || ''));
  assert.equal(await props.locator('[data-props-msg]').innerText(), 'Changed elsewhere. Showing the latest.', 'kept until Owner saves');
  await props.locator('[data-prop="owner"]').click();
  await peek.locator('.prop-pop [data-prop-pick="support"]').click();
  await page.waitForFunction(() => /Support/.test(document.querySelector('#task-peek [data-prop="owner"]')?.textContent || '') && !document.querySelector('#task-peek [data-props-msg]')?.textContent);
  // N11: one menu at a time, and only its anchor is marked expanded.
  await props.locator('[data-prop="status"]').click();
  await props.locator('[data-prop="owner"]').evaluate(b => b.click());   // a second menu opened while the first is up
  assert.equal(await props.locator('[data-prop="status"]').getAttribute('aria-expanded'), 'false');
  assert.equal(await props.locator('[data-prop="owner"]').getAttribute('aria-expanded'), 'true');
  await page.keyboard.press('Escape');
  // N8: a date saves only when it is whole and sensible.
  const before = writes().length;
  await props.locator('[data-prop-date]').fill('0002-10-09');
  assert.equal(await props.locator('[data-props-msg]').innerText(), 'That date is not valid.');
  assert.equal(writes().length, before, 'nothing saved');
  // N2: an unsent comment survives a property save and the poll's redraw.
  await peek.locator('.task-chat textarea').fill('Half a thought');
  await page.locator('#task-q').click();
  Object.assign(tasks.find(x => x.id === 't-triage'), {title: 'Triage the widget bug reports', version: tasks.find(x => x.id === 't-triage').version + 1});
  await page.evaluate(() => refresh(false));
  await peekTitle(page, 'Triage the widget bug reports');
  await peek.locator('.task-chat textarea').waitFor();
  assert.equal(await peek.locator('.task-chat textarea').inputValue(), 'Half a thought');
  assert.deepEqual(errors, []);
  console.log('re-check: saves, errors, menus, dates, drafts: ok');
  await page.close();
}
async function recheckLists(browser) {
  // N6: the pickers never offer a loop: Part of leaves out the task and everything under it; Blocked by leaves out
  // every task that already waits on it.
  const {page, errors, tasks} = await open(browser);
  const peek = page.locator('#task-peek');
  await page.locator('[data-task-key="tt-checkout"] .tl-title').click();
  await peek.locator('[data-prop="parent"]').click();
  const parents = await peek.locator('.prop-pop [data-prop-pick]').evaluateAll(bs => bs.map(b => b.dataset.propPick));
  for (const id of ['t-checkout', 't-idem', 't-summary', 't-terms']) assert.ok(!parents.includes(id), 'not offered as parent: ' + id);
  assert.ok(parents.includes('t-news'));
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.locator('[data-task-key="tt-idem"] .tl-title').click();
  await peekTitle(page, 'Add idempotency keys');
  await peek.locator('[data-prop="blocked"]').click();
  const blockers = await peek.locator('.prop-pop [data-prop-pick]').evaluateAll(bs => bs.map(b => b.dataset.propPick));
  assert.ok(!blockers.includes('t-checkout') && !blockers.includes('t-idem'), 'the task it blocks, and itself, are left out');
  await page.keyboard.press('Escape');
  // N10: an unchanged row stays the same node through polls, selected, under the cursor or not; the selection stays.
  await page.keyboard.press('Escape');
  await page.locator('[data-task-key="tt-flaky"] .tl-check').click();
  await page.locator('[data-task-key="tt-rotate"] .tl-open').focus();
  await page.evaluate(() => { window.kept = [...document.querySelectorAll('[data-task-key="tt-flaky"], [data-task-key="tt-rotate"]')]; });
  await page.evaluate(() => refresh(false));
  await page.evaluate(() => refresh(false));
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => window.kept.every(el => el.isConnected)), true, 'not redrawn');
  assert.equal(await page.locator('[data-task-key="tt-flaky"]').evaluate(r => r.classList.contains('sel')), true);
  assert.equal(await focusedKey(page), 'tt-rotate');
  // N4 / R2-4: Done keeps its own paging. While it shows, the poll fetches only its first page (picking up newly finished
  // tasks), never the rest; its selection survives.
  await page.locator('#task-bulk [data-bulk="clear"]').click();
  await page.locator('#task-view [data-view="done"]').click();
  await page.waitForFunction(() => document.querySelectorAll('#task-body .tl-row').length === 3);
  await page.locator('[data-task-key="tt-sso"] .tl-check').click();
  const doneAsks = [];
  page.on('request', r => { if (/status=done/.test(r.url())) doneAsks.push(new URL(r.url()).searchParams); });
  Object.assign(tasks.find(x => x.id === 't-flaky'), {status: 'done', done_at: new Date().toISOString(), version: 9});   // a bot finished it
  await page.evaluate(() => refresh(false));
  await page.evaluate(() => refresh(false));
  await page.locator('#task-body [data-task-key="tt-flaky"]').waitFor();
  assert.ok(doneAsks.length >= 1 && doneAsks.every(q => q.get('offset') === '0' && q.get('limit') === '20'), 'only the first page');
  assert.equal(await page.locator('#task-body .tl-row').count(), 4);
  assert.equal(await page.locator('.tl-bulk-n').innerText(), '1 selected');
  assert.deepEqual(errors, []);
  await page.close();
  // N9: handing a Needs-you task to someone else moves the list and the peek on to the next row.
  const second = await open(browser, {hash: '#/tasks'});
  await second.page.locator('[data-task-key="tt-access"] .tl-title').click();
  await second.page.locator('#task-peek [data-prop="owner"]').click();
  await second.page.locator('#task-peek .prop-pop [data-prop-pick="writer"]').click();
  await second.page.waitForFunction(() => !document.querySelector('[data-task-key="tt-access"]') && TASKS_ST.peek && TASKS_ST.peek !== 'tt-access');
  assert.equal(await focusedKey(second.page), await second.page.evaluate(() => TASKS_ST.peek), 'the focus is on the row the peek moved to');
  assert.deepEqual(second.errors, []);
  await second.page.close();
  // N5: two people with one first name get two different group names.
  const third = await open(browser, {people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam Ortiz'}, {id: 'sam2', name: 'Sam Lee'}],
    extraTasks: [{...fixtures().find(x => x.id === 't-demo'), id: 't-demo2', title: 'Record the onboarding walkthrough', owner: 'human:sam2'}]});
  const names = await groupNames(third.page);
  assert.ok(names.includes('NEEDS SAM O.') && names.includes('NEEDS SAM L.'), 'distinct: ' + names);
  assert.deepEqual(third.errors, []);
  await third.page.close();
  console.log('re-check: pickers, polls, Done, next row, names: ok');
}
async function recheckPhone(browser) {
  // Closing the phone sheet (✕ or Esc) stays on Tasks: no extra step back through history.
  for (const how of ['x', 'esc']) {
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/issues'});
    await page.evaluate(() => { location.hash = '#/goals'; });
    await page.waitForTimeout(400);
    await page.evaluate(() => { location.hash = '#/issues'; });
    await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body .tl-row'));
    await page.locator('[data-task-key="tt-checkout"] .tl-title').click();
    await page.locator('#task-peek[open]').waitFor();
    if (how === 'x') await page.locator('#task-peek [data-modal-close]').click(); else await page.keyboard.press('Escape');
    await page.waitForTimeout(600);
    assert.deepEqual(await page.evaluate(() => [location.hash, !!document.querySelector('.tasks-page'), !!document.querySelector('#task-peek[open]')]), ['#/issues', true, false], how);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('re-check: phone sheet close: ok');
}

// ---- re-check 2 (scratchpad/r2/recheck/probe2.cjs, and the four other lows)
async function recheckFinal(browser) {
  // R2-1: a menu opened while a save is on its way stays open when the save lands; the panel redraws once it closes.
  const {page, errors, posts, tasks} = await open(browser);
  const peek = page.locator('#task-peek'), props = peek.locator('[data-task-props]');
  await page.route('**/api/v2/tasks/t-triage', async route => { if (route.request().method() === 'POST') await new Promise(r => setTimeout(r, 1500)); await route.fallback(); });
  await page.locator('[data-task-key="tt-triage"] .tl-title').click();
  await props.locator('[data-prop="status"]').waitFor();
  await page.waitForTimeout(400);
  await props.locator('[data-prop="status"]').click();
  await peek.locator('.prop-pop [data-prop-pick="waiting"]').click();
  await page.waitForTimeout(200);
  await props.locator('[data-prop="owner"]').click();
  await page.waitForTimeout(2000);                                    // the save lands meanwhile
  assert.equal(await page.evaluate(() => !!document.querySelector('#task-peek .prop-pop:popover-open')), true, 'the menu is still open');
  assert.equal(await page.evaluate(() => document.activeElement?.closest('.prop-pop') != null), true, 'and keeps the focus');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => /Waiting/.test(document.querySelector('#task-peek [data-prop="status"]')?.textContent || ''));
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.prop), 'owner');
  await page.unroute('**/api/v2/tasks/t-triage');
  // R2-3: Undo says how many it could not reopen, and names them.
  await page.keyboard.press('Escape');
  await page.locator('[data-task-key="tt-rotate"] .tl-check').click();
  await page.locator('[data-task-key="tt-flaky"] .tl-check').click();
  await page.locator('#task-bulk [data-bulk="close"]').click();
  await page.waitForFunction(() => /2 closed/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
  await page.route('**/api/v2/tasks/t-flaky', route => route.request().method() === 'POST'
    ? route.fulfill({status: 403, contentType: 'application/json', body: JSON.stringify({error: {detail: 'Not allowed'}})}) : route.fallback());
  await page.locator('#task-bulk [data-bulk="undo"]').click();
  await page.waitForFunction(() => /not reopened/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
  assert.equal(await page.locator('.tl-bulk-msg').innerText(), '1 of 2 reopened; bots were already told; 1 not reopened: Fix the flaky login test on CI');
  assert.equal(tasks.find(x => x.id === 't-rotate').status, 'doing');
  assert.deepEqual(errors, []);
  await page.close();
  // R2-2: a poll that overtakes a save's reload still moves the list and the peek on when the task leaves the view.
  const second = await open(browser, {hash: '#/tasks'});
  await second.page.route('**/api/v2/tasks?*', async route => { await new Promise(r => setTimeout(r, 700)); await route.fallback(); });
  await second.page.locator('[data-task-key="tt-access"] .tl-title').click();
  await second.page.locator('#task-peek [data-prop="owner"]').click();
  await second.page.locator('#task-peek .prop-pop [data-prop-pick="writer"]').click();
  await second.page.waitForTimeout(200);
  await second.page.evaluate(() => refresh(false));                   // the poll starts while the save's reload is on its way
  await second.page.waitForFunction(() => !document.querySelector('[data-task-key="tt-access"]') && TASKS_ST.peek && TASKS_ST.peek !== 'tt-access', null, {timeout: 10000});
  assert.deepEqual(second.errors, []);
  await second.page.close();
  // R2-5: as much of the last name as tells people apart.
  const third = await open(browser, {people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam Ortiz'}, {id: 'sam2', name: 'Sam Lee'}, {id: 'sam3', name: 'Sam Long'},
    {id: 'sam4', name: 'Sam', email: 'sam.k@acme.example'}],
    extraTasks: ['sam2', 'sam3', 'sam4'].map((who, i) => ({...fixtures().find(x => x.id === 't-demo'), id: 't-demo-' + i, title: 'Demo ' + i, owner: 'human:' + who}))});
  const names = await groupNames(third.page);
  for (const want of ['NEEDS SAM O.', 'NEEDS SAM LE.', 'NEEDS SAM LO.', 'NEEDS SAM.K']) assert.ok(names.includes(want), `${want} in ${names}`);
  assert.deepEqual(third.errors, []);
  await third.page.close();
  console.log('re-check 2: menus, Undo failures, overtaken reloads, names: ok');
}

module.exports = {open, fixtures};
if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const only = process.env.TASKS_ONLY ? process.env.TASKS_ONLY.split(',') : null;
    for (const [name, run] of Object.entries({listAndTabs, typeSelection, filters, carriedOver, peekAndKeys, properties, board, bulk, views, waitingOnYou, donePagination, polling, phone, recheckSaves, recheckLists, recheckPhone, recheckFinal}))
      if (!only || only.includes(name)) await run(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
