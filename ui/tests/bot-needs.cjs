// A bot's task rail is one Tasks section with four tabs, each with its count: Needs You (what this bot needs from the
// person: a question it asked on another bot's task, even a finished one, a task of its own
// waiting on them, a question outside any task), Doing (Open/Doing tasks it holds, newest first), Blocked (each saying
// what it waits on) and Done Recently (the last 14 days), and Assigned to Others, a filter tab the server counts for
// the person looking. The org tree's number is the Needs you rows, and stays so when the list moves. The Tasks header's
// pencil edits the sidebar and there is no board icon; a bot with one tab shows its list without a tab bar. Fixtures
// only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const bots = ['lead', 'qa', 'senior'].map(name => ({name, display_name: {lead: 'Head of Engineering', qa: 'QA Engineer',
  senior: 'Senior Engineer'}[name], org_parent: '', host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}));
const task = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:lead', requester: 'human:ana', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(600), updated_at: ago(60), ...over});
const T = {
  own: task('t-own', 'Ship the release notes'),
  forAna: task('t-ana', 'Pick the release day', {owner: 'human:ana', requester: 'bot:lead', status: 'open'}),
  waits: task('t-waits', 'Turn on the screen default', {status: 'waiting', waiting_on: 'human:ana', note: 'Waiting on Ana for the bucket keys.', created_at: ago(480)}),
  onQa: task('t-on-qa', 'Merge the export fix', {status: 'waiting', waiting_on: 'bot:qa', created_at: ago(300)}),
  vendor: task('t-vendor', 'Renew the domain', {status: 'waiting', note: "Waiting for the vendor's invoice.\nThen pay it.", created_at: ago(200)}),
  roadmap: task('t-roadmap', 'Plan the import page', {status: 'open', type_id: 'roadmap', created_at: ago(30)}),
  doneNow: task('t-done-now', 'Send the weekly summary', {status: 'done', done_at: ago(2 * 1440), created_at: ago(3 * 1440)}),
  doneOld: task('t-done-old', 'Close the old mailbox', {status: 'done', done_at: ago(20 * 1440), created_at: ago(21 * 1440)}),
  asked: task('t-asked', 'Support access fixes', {owner: 'bot:senior', requester: 'bot:lead', status: 'done',
    ask: {id: 'q-1', body: 'Should full access refuse credential changes?', from_actor: 'bot:lead', to_actor: 'human:ana', created_at: ago(30)}}),
  qa: task('t-qa', 'Run the release check', {owner: 'bot:qa', requester: 'bot:lead', status: 'doing'}),
};
const LOOSE = {kind: 'ask', id: 'm-loose', message_id: 'm-loose', title: 'Is Friday fine for the summary?', first_line: 'Is Friday fine for the summary?',
  body: 'Is Friday fine for the summary?', requester: 'bot:lead', origin_actor: 'bot:lead', created_at: ago(20),
  ask: {id: 'm-loose', from_actor: 'bot:lead', to_actor: 'human:ana', body: 'Is Friday fine for the summary?', created_at: ago(20)}};
const NEEDS = [{...T.asked, kind: 'question'}, {...T.forAna, kind: 'task'}, {...T.waits, kind: 'waiting'}, LOOSE,
  {kind: 'approval', id: 'approval-1', what: 'send', requested_by: 'bot:lead', requester: 'bot:lead', title: 'Approve the announcement', created_at: ago(10)},
  {...task('t-qa-ana', 'Approve the test plan', {owner: 'human:ana', requester: 'bot:qa', status: 'open'}), kind: 'task'}];

// Head of Engineering keeps Done Recently beside the new defaults (a layout saved before them).
const LAYOUT = [{builtin: 'needs', name: 'Needs You'}, {builtin: 'active', name: 'Doing'}, {builtin: 'blocked', name: 'Blocked'},
  {builtin: 'done', name: 'Done Recently'}, {name: 'Assigned to Others', show_count: true, conditions: [{field: 'asked_by', value: 'this_bot'},
    {field: 'assigned_to', value: 'others'}, {field: 'status', value: ['open', 'doing', 'waiting']}]}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let needs = NEEDS, filterExtras = null, releaseFilterRead = null;
    const filterURLs = [];
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      // A list, as the server sends it: the catch-all {} broke the tree drawn before Needs you whenever it landed ahead
      // of /api/v2/status, and the page never opened (v0.3.54 release check).
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
      if (p === '/api/v2/needs-you') return json({items: needs, went_away: null});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') {
        const all = Object.values(T);
        // The rail reads open and finished tasks apart, finished ones since a day; the server filters both.
        const st = q.get('status'), since = q.get('updated_since');
        const fits = t => (!st || st === 'all' || st.split(',').includes(t.status)) && (!since || t.updated_at >= since);
        if (q.get('owner') === 'lead') return json({tasks: all.filter(t => t.owner === 'bot:lead' && fits(t))});
        if (q.get('requester') === 'lead') return json({tasks: all.filter(t => t.requester === 'bot:lead' && t.status !== 'done')});
        return json({tasks: []});
      }
      // The built-in tabs and Assigned to Others, which Ana may change on Head of Engineering only.
      const tabs = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs$/);
      if (tabs) {
        if (route.request().method() === 'PUT') LAYOUT.splice(0, LAYOUT.length, ...route.request().postDataJSON().tabs);
        return json({bot: tabs[1], tabs: LAYOUT, widgets: null, saved: false, personal: false, can_edit: tabs[1] === 'lead'});
      }
      // What the server counts for Ana: what Head of Engineering asked of anyone but her and itself, still open.
      if (p === '/api/v2/bots/lead/task-tabs/tasks') {
        if (filterExtras) {
          filterURLs.push(url.href);
          if (releaseFilterRead) await releaseFilterRead;
          const requested = (q.get('match_ids') || '').split(',');
          return json({bot: 'lead', tabs: [{key: 'needs', name: 'Needs You', count: filterExtras.length,
            ids: [], next: null, matched_ids: filterExtras.filter(it => requested.includes(it.id)).map(it => it.id)}], tasks: []});
        }
        const others = Object.values(T).filter(t => t.requester === 'bot:lead' && !['human:ana', 'bot:lead'].includes(t.owner)
          && ['open', 'doing', 'waiting'].includes(t.status));
        const owned = Object.values(T).filter(t => t.owner === 'bot:lead');
        const groups = [
          {key: 'needs', name: 'Needs You', rows: (LAYOUT[0].conditions?.[0]?.field === 'status'
            ? Object.values(T).filter(t => (t.owner === 'bot:lead' || t.requester === 'bot:lead') && LAYOUT[0].conditions[0].value.includes(t.status))
            : Object.values(T).filter(t => (t.owner === 'bot:lead' || t.requester === 'bot:lead')
              && ((t.owner === 'human:ana' && ['open', 'doing', 'waiting'].includes(t.status)) || (t.status === 'waiting' && t.waiting_on === 'human:ana'))))},
          {key: 'active', name: 'Doing', rows: owned.filter(t => ['open', 'doing'].includes(t.status)).sort((a,b) => b.created_at.localeCompare(a.created_at))},
          {key: 'blocked', name: 'Blocked', rows: owned.filter(t => t.status === 'waiting' && t.waiting_on !== 'human:ana').sort((a,b) => b.created_at.localeCompare(a.created_at))},
          {key: 'c4', name: 'Assigned to Others', rows: others},
        ];
        return json({bot: 'lead', tabs: groups.map(({key, name, rows}) => ({key, name, count: rows.length, ids: rows.map(t => t.id), next: null})), tasks: Object.values(T)});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/lead/tasks');
    const rail = page.locator('#pane-tasks');
    const titles = async list => (await rail.locator(`${list} .trow .ttl`).allInnerTexts()).map(s => s.trim());
    const tab = key => rail.locator(`#bot-tab-t-${key}`);
    await rail.locator('#t-needs .trow').first().waitFor();

    // One Tasks section with its tabs and their counts, before Updates and Files; no folded section after it.
    const order = await rail.evaluate(el => [...el.querySelectorAll(':scope>.bot-w>.rail-sec')].map(c => c.id));
    assert.deepEqual(order, ['bot-tasks', 'bot-latest', 'bot-files'], JSON.stringify(order));
    assert.equal(await rail.locator('#bot-assigned').count(), 0);
    assert.equal(await rail.locator('#bot-tasks .rail-h').innerText().then(s => s.trim().toLowerCase()), 'tasks');
    await page.waitForFunction(() => document.querySelector('#cnt-c4')?.textContent.trim() === '1'
      && document.querySelector('#cnt-needs')?.textContent.trim() === '5');
    const tabs = (await rail.locator('[data-bot-task-tab]').allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim());
    assert.deepEqual(tabs, ['Needs You 5', 'Doing 2', 'Blocked 2', 'Done Recently 1', 'Assigned to Others 1'], JSON.stringify(tabs));
    assert.equal(await tab('needs').getAttribute('aria-selected'), 'true', 'Needs You leads while it has rows');

    // Needs You keeps matching waits, questions and approvals; ordinary assigned tasks obey its conditions.
    const rows = await rail.locator('#t-needs .trow .ttl').allInnerTexts();
    assert.deepEqual(rows.map(r => r.trim()).sort(), ['Is Friday fine for the summary?', 'Approve the announcement',
      'Support access fixes', 'Turn on the screen default', 'Pick the release day'].sort(), JSON.stringify(rows));
    assert.equal(await rail.locator('#bot-active').isHidden(), true, 'one tab at a time');
    const badge = page.locator('#tree a[href="#/bot/lead"] .cnt.needs');
    assert.equal((await badge.innerText()).trim(), '5', 'the tree number is the Needs you rows');
    assert.equal(await page.evaluate(() => needsMeCount('lead')), 5);
    assert.equal(await badge.getAttribute('title'), '5 things Head of Engineering needs from you');
    assert.ok(!(await titles('#t-open')).includes('Pick the release day'));
    assert.ok(!(await titles('#t-blocked')).includes('Pick the release day'));
    assert.ok(!(await titles('#t-c4')).includes('Pick the release day'));
    // An approval or a question opens its Needs you card over the rail; nothing unfolds in the rail.
    assert.equal(await rail.locator('#t-needs details').count(), 0);
    await rail.locator('#t-needs .trow', {hasText: 'Approve the announcement'}).locator('.ttl').click();
    await page.locator('#need-card .dc-card[data-key="approval:approval-1"]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/bot/lead/tasks', 'the rail stays where it was');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#need-card')?.open);

    // Editing Needs You changes its task filter, while the org number stays a fixed attention count.
    await rail.locator('#bot-sidebar-edit').click();
    await tab('needs').click();
    const dialog = page.locator('#bot-filter-dialog');
    await dialog.waitFor();
    assert.equal(await dialog.locator('[data-cond-field]').inputValue(), 'needs');
    assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'you');
    await dialog.locator('[data-cond-field]').selectOption('status');
    assert.ok((await dialog.innerText()).includes('The org chart number still counts everything Head of Engineering needs from you, not this tab.'));
    await dialog.locator('[data-cond-value="0"][value="open"]').uncheck();
    await dialog.locator('[data-cond-value="0"][value="waiting"]').uncheck();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await rail.locator('#bot-sidebar-done').click();
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '5'
      && document.querySelector('#t-needs')?.textContent.includes('Ship the release notes'));
    assert.ok(!(await titles('#t-needs')).includes('Pick the release day'));
    assert.equal((await badge.innerText()).trim(), '5', 'editing a tab does not change fixed attention');
    // Pick a different count, then Reset visibly restores the single Needs: You condition.
    await rail.locator('#bot-sidebar-edit').click();
    await tab('needs').click(); await dialog.waitFor();
    await dialog.locator('[data-cond-value="0"][value="open"]').check();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await rail.locator('#bot-sidebar-done').click();
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '7');
    assert.equal((await badge.innerText()).trim(), '5');
    await rail.locator('#bot-sidebar-edit').click();
    await tab('needs').click(); await dialog.waitFor();
    await dialog.getByRole('button', {name: 'Reset to default'}).click();
    assert.equal(await dialog.locator('[data-cond-field]').inputValue(), 'needs');
    assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'you');
    assert.ok(!(await dialog.innerText()).includes('The org chart number still counts'));
    await dialog.getByRole('button', {name: 'Save'}).click();
    await rail.locator('#bot-sidebar-done').click();
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '5');


    // Doing defaults to Open/Doing; waiting tasks have their own tabs.
    await tab('active').click();
    assert.deepEqual(await titles('#t-open'), ['Plan the import page', 'Ship the release notes']);
    assert.equal(await rail.locator('#bot-needs').isHidden(), true);

    // Blocked: each row says what it waits on, from whom it names or else its note's first line.
    await tab('blocked').click();
    assert.deepEqual(await titles('#t-blocked'), ['Renew the domain', 'Merge the export fix']);
    assert.deepEqual((await rail.locator('#t-blocked .trow-waits').allInnerTexts()).map(s => s.trim()),
      ["Waiting for the vendor's invoice.", 'Waits on QA Engineer']);

    // Done Recently: finished in the last 14 days; the one from three weeks ago is not listed. It is drawn only once its
    // tab opens, 50 rows at a time (paging, at the end).
    assert.equal(await rail.locator('#t-done .trow').count(), 0, 'Done is drawn when its tab opens');
    await tab('done').click();
    assert.deepEqual(await titles('#t-done'), ['Send the weekly summary']);

    // Assigned to Others keeps what it asked of another bot; its task for Ana is in Needs You, not here.
    await tab('c4').click();
    assert.deepEqual(await titles('#t-c4'), ['Run the release check']);

    // A redraw keeps the picked tab while Needs You has rows, and leaves the rail scrolled where it was.
    await tab('done').click();
    const scrolled = await page.evaluate(() => {
      const r = document.querySelector('#pane-tasks');
      r.style.overflowY = 'auto'; r.style.height = '80px'; r.scrollTop = r.scrollHeight; return r.scrollTop;
    });
    assert.ok(scrolled > 0);
    await page.evaluate(() => botNeedsChanged('lead'));
    assert.equal(await page.evaluate(() => document.querySelector('#pane-tasks').scrollTop), scrolled);
    assert.equal(await tab('done').getAttribute('aria-selected'), 'true');
    assert.equal(await rail.locator('#bot-needs').isHidden(), true);
    await page.evaluate(() => { const r = document.querySelector('#pane-tasks'); r.style.height = r.style.overflowY = ''; r.scrollTop = 0; });

    // A half-typed answer on the card of the question outside any task survives a redraw of the rail, and the picked
    // tab stays.
    await tab('needs').click();
    await rail.locator('#t-needs [data-need-card="ask:m-loose"] .ttl').click();
    await page.locator('#need-card .dc-note textarea').fill('Friday works');
    await page.evaluate(() => botNeedsChanged('lead'));
    assert.equal(await page.locator('#need-card .dc-note textarea').inputValue(), 'Friday works');
    assert.equal(await tab('needs').getAttribute('aria-selected'), 'true');
    await page.locator('#need-card [data-dc="close"]').click();
    await page.waitForFunction(() => !document.querySelector('#need-card')?.open);

    // A supplemental question matching a later task-filter page is counted once, even before that page loads.
    await page.evaluate(() => {
      const tab = BOT_TASK_FILTERED.get('lead').tabs.needs;
      tab.count = 3; tab.matched_ids = ['t-asked'];
      botNeedsPaint('lead');
    });
    assert.equal((await tab('needs').locator('.cnt').innerText()).trim(), '5');
    await page.evaluate(() => botTaskFilterRead('lead'));
    // The question answered elsewhere leaves Needs You: the tab's count and the tree's number move together.
    needs = needs.filter(it => !['t-asked', 'm-loose', 'approval-1'].includes(it.id));
    await page.evaluate(async () => { await v2Refresh(); renderTree(); });
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '2');
    assert.equal((await badge.innerText()).trim(), '2');
    assert.equal(await rail.locator('#t-needs .trow').count(), 2);

    // Nothing needs Ana: the tab says so, and the tree's number goes.
    needs = [];
    T.waits.status = T.forAna.status = 'done';
    await page.evaluate(async () => { await v2Refresh(); await loadBotTasksV2('lead'); renderTree(); });
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '0');
    assert.equal(await page.locator('#tree a[href="#/bot/lead"] .cnt.needs').count(), 0);
    assert.equal((await rail.locator('#t-needs').innerText()).trim(), 'Nothing needs you.');
    assert.deepEqual((await rail.locator('#t-c4 .trow .ttl').allTextContents()).map(s => s.trim()), ['Run the release check']);

    // Many supplemental questions keep requests bounded and deduplicate beyond the first chunk.
    // A question arriving during the read must not be added twice to that read's total.
    filterExtras = Array.from({length: 240}, (_, i) => ({...T.asked, kind: 'question',
      id: 'question-' + String(i).padStart(36, '0')}));
    await page.evaluate(items => {
      S.v2.needs = items; window.needCountsSeen = [];
      window.needCountObserver = new MutationObserver(() => needCountsSeen.push(document.querySelector('#cnt-needs').textContent.trim()));
      needCountObserver.observe(document.querySelector('#cnt-needs'), {childList: true, subtree: true});
    }, filterExtras);
    let release;
    releaseFilterRead = new Promise(resolve => { release = resolve; });
    const requested = page.waitForRequest(r => r.url().includes('/task-tabs/tasks?match_ids='));
    const reading = page.evaluate(() => botTaskFilterRead('lead'));
    await requested;
    const late = {...T.asked, kind: 'question', id: 'question-late'};
    filterExtras.push(late);
    await page.evaluate(item => { S.v2.needs.push(item); }, late);
    releaseFilterRead = null; release();
    await reading;
    assert.ok(filterURLs.length > 1, 'a large supplement list uses more than one bounded read');
    assert.ok(filterURLs.every(url => url.length < 4200), 'no request exceeds common proxy header limits');
    assert.equal((await tab('needs').locator('.cnt').innerText()).trim(), '241', 'late question is already in the task total');
    await page.evaluate(() => botTaskFilterRead('lead'));
    assert.equal((await tab('needs').locator('.cnt').innerText()).trim(), '241');
    assert.equal(await rail.locator('#t-needs .trow').count(), 241, 'the next read includes the new question once');
    assert.ok(!(await page.evaluate(() => { needCountObserver.disconnect(); return needCountsSeen; })).includes('242'), 'no transient double count');
    filterExtras = null;

    // Done Recently pages 50 rows at a time.
    await tab('done').click();
    await page.evaluate(() => { const d = BOT_DONE.get('lead'), row = d.rows[0]; d.rows = Array.from({length: 120}, (_, i) => ({...row, id: 'many-' + i})); botDonePaint('lead'); });
    assert.equal(await rail.locator('#t-done .trow').count(), 50);
    await rail.getByRole('button', {name: 'Show 50 more'}).click();
    assert.equal(await rail.locator('#t-done .trow').count(), 100);
    await rail.getByRole('button', {name: 'Show 20 more'}).click();
    assert.equal(await rail.locator('#t-done .trow').count(), 120);
    assert.equal(await rail.locator('.bot-done-more').count(), 0);

    // The Tasks header has no board icon; the sidebar's one edit icon is for whoever may change this bot's
    // layout, and Ana manages Head of Engineering here. The old tab settings gear is gone.
    assert.equal(await rail.locator('#bot-tasks-board').count(), 0);
    const reopen = async () => {
      await page.evaluate(() => { location.hash = '#/bot/qa'; });
      await page.waitForFunction(() => BOT?.slug === 'qa');
      await page.evaluate(() => { location.hash = '#/bot/lead/tasks'; });
      await rail.locator('#t-open .trow').first().waitFor({state: 'attached'});
    };
    await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
    assert.equal(await rail.locator('#bot-tasks-settings').count(), 0);

    // Spec test 11: a bot with one tab shows that one list, with no tab bar over it.
    await page.evaluate(() => { window.fourTabs = botTaskTabs; window.botTaskTabs = () => [['active', 'Doing', 'cnt-active']]; });
    await reopen();
    assert.equal(await rail.locator('.bot-task-tabs').isHidden(), true, 'no tab bar over a single list');
    assert.equal(await rail.locator('#bot-active').isVisible(), true);
    for (const key of ['needs', 'blocked', 'done']) assert.equal(await rail.locator(`#bot-${key}`).isHidden(), true, key);
    assert.equal(await rail.locator('#t-open .trow').count(), 2);
    await page.evaluate(() => { window.botTaskTabs = window.fourTabs; });
    await reopen();
    assert.equal(await rail.locator('.bot-task-tabs').isVisible(), true);
    assert.equal(await rail.locator('[data-bot-task-tab]').count(), 5);

    assert.deepEqual(errors, []);
    console.log('bot-needs: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
