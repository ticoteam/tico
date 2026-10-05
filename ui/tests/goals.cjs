// The Goals page (ui/goals-kpis.js): one tree of every person and bot, nested as the org chart nests them, a line each
// with the goal to the right (cut short, whole in its tooltip) and KPI chips; built-in and message bots apart; tapping a line opens
// that owner's panel, where goals and KPIs are added and edited. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const screenshotDir = process.env.TICO_SCREENSHOT_DIR;

const LONG = 'Publish one deeply researched comparison page every week for each of the twelve competitor keywords we track, with fresh pricing, screenshots and a customer quote on every page';
const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [
  bot('cmo', 'AI CMO', 'p:ana'), bot('seo', 'SEO', 'b:cmo'), bot('sales', 'Sales', 'p:ana'),
  bot('support', 'Support', 'p:ana', {status: 'paused', onboarding_state: 'needs_setup'}),
  bot('old', 'Old bot', 'p:ana', {status: 'archived'}),
  bot('coo', 'Assistant', ''), bot('librarian', 'Librarian', ''), bot('botops', 'BotOps', ''), bot('goal-manager', 'Goal Manager', ''), bot('inbox', 'Inbox Manager', 'b:botops', {template: 'inbox'}),
  bot('channel', 'Channel Inbox', 'b:botops', {helper: true}),
];
const day = Date.now() - 2 * 86400000;
const kpi = (id, name, over) => ({id, name, unit: '%', direction: 'up', cadence: 'weekly', owner: 'bot:cmo', definition: 'Signed-up accounts that finish setup',
  definition_version: 2, source_note: 'Product database', freshness: 'fresh', status: 'yellow', reason: name + ' is behind', spark: [44, 47, 50, 52],
  latest: {value: 52, period_end: new Date(day).toISOString(), quality: 'measured'}, target_label: '→ 65% by Dec 31',
  link: {goal_id: 'g-cmo', kind: 'improve', target: 65, baseline: 40, deadline: '2027-12-31'}, ...over});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [], posted = [];
    page.on('dialog', dialog => dialog.accept());
    let gmExtra = [];
    let gmLive = false;
    let gmQueued = false;   // the question waits on a missing AI provider
    page.on('pageerror', e => errors.push(e.message));
    const activation = kpi('k-act', 'Activation', {reason: 'Activation 52% vs 58% needed on pace'});
    const nps = kpi('k-nps', 'NPS', {freshness: 'stale', status: 'gray', reason: 'NPS is stale', target_label: 'range 40–60', link: {goal_id: 'g-cmo', kind: 'maintain', min: 40, max: 60}});
    const goals = [
      {id: 'g-top', title: 'Grow revenue 30% this year', owner: 'company', parent_id: null, status: 'red', status_source: 'person', status_by: 'human:ana',
       status_note: 'Launch slipped', rank: 0, kpis: []},
      {id: 'g-cmo', title: 'Double organic signups', owner: 'bot:cmo', parent_id: 'g-top', status: 'yellow', status_source: 'auto', rank: 0, kpis: [activation, nps],
       checkin: {id: 'c1', body: 'The onboarding email went out late.', signal: 'at_risk', source_actor: 'bot:cmo', ts: new Date(day).toISOString()}},
      {id: 'g-cmo2', title: LONG, owner: 'bot:cmo', parent_id: null, status: 'green', status_source: 'auto', rank: 1, kpis: []},
      {id: 'g-gm', title: 'Every KPI read on time', owner: 'bot:goal-manager', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: []},
    ];
    const other = [kpi('k-cash', 'Cash runway', {owner: 'human:ana', unit: 'months', target_label: '', link: undefined, status: 'none', latest: {value: 11, period_end: new Date(day).toISOString(), quality: 'measured'}})];
    const needs = [{kind: 'kpi_red', goal_id: 'g-cmo', goal_title: 'Double organic signups', kpi_id: 'k-act', kpi_name: 'Activation', reason: 'Activation 52% vs 58% needed on pace'},
      {kind: 'proposal', proposal: {id: 'p1', kind: 'kpi_target', goal_id: 'g-cmo', kpi_id: 'k-act', payload: {kind: 'improve', target: 60, deadline: '2027-12-31'},
       reason: 'The deadline cannot be met at this pace', proposed_by: 'bot:goal-manager'}, kpi_name: 'Activation', goal_title: 'Double organic signups'}];
    const people = [{id: 'ana', name: 'Ana Silva', org_parent: '', inbox_bot: 'inbox'}, {id: 'ben', name: 'Ben Park', org_parent: 'p:ana', goals: 'Keep the board honest.'}];
    const detail = {kpi: activation, links: [{...activation.link, goal_title: 'Double organic signups', goal_owner: 'bot:cmo', target_label: activation.target_label, status: 'yellow', reason: activation.reason}],
      readings: [47, 50, 52].map((v, i) => ({id: 'r' + i, value: v, period_end: new Date(day - (2 - i) * 7 * 86400000).toISOString(), quality: 'measured', evidence: i === 2 ? 'https://analytics.example/q/12' : '', actor: 'bot:goal-manager', superseded_by: null})),
      definitions: [], checkins: [], proposals: [], may_edit: true, may_log: true};
    const archivedKpi = kpi('k-retired', 'Retired conversion', {owner: 'human:ana', archived_at: new Date(day).toISOString(), link: undefined});
    let archivedMayEdit = true;
    const requests = [], liveStreams = [];
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname, post = req.method() === 'POST';
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p === '/api/v2/events') { liveStreams.push(route); return; }   // held until the test sends a change
      if (p.startsWith('/api/')) requests.push(req.method() + ' ' + p);
      const body = post ? req.postDataJSON() : null;
      if (post) posted.push({path: p, body});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@example.test', role: 'owner', mover: true, cloud: true});
      if (p === '/api/humans') return json({people});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: [{bot: 'goal-manager', state: 'idle', last_turn_at: '2026-10-01T09:00:00Z', last_result: 'Updated KPI readings.'}]});
      if (p === '/api/v2/bots/goal-manager/routines') return json({routines: [{id: 'gm-review', title: 'Checks goals', cron: '0 9 * * *', active: true, enabled: true, timezone: 'UTC', next: '2026-10-02T09:00:00Z'}]});
      if (p === '/api/v2/routines/gm-review/occurrences') return json({occurrences: [{started: '2026-10-01T09:00:00Z', title: 'Checks goals', exit: 'completed'}]});
      if (p === '/api/v2/conversations' && posted.some(r => r.path === '/api/v2/chat/goal-manager')) return json({conversations: [{id: 'gm-chat', kind: 'chat', scope: 'personal', owner_actor: 'human:ana', room_key: 'goal-manager', participants: ['human:ana', 'bot:goal-manager']}]});
      if (p === '/api/v2/goal-manager/turn-on' && post) { bots.find(b => b.name === 'goal-manager').status = 'active'; return json({state: 'active'}); }
      if (p === '/api/v2/chat/goal-manager' && post) return json({conversation: {id: 'gm-chat'}, message: {id: 'gm-q', from_actor: 'human:ana', body: body.text}});
      if (p === '/api/v2/conversations/gm-chat/snapshot') return json(gmLive ? {messages: [{id:'gm-q', from_actor:'human:ana', body:'Change the revenue goal'}], execution:{state:'running', text:'Checking **progress** <script>bad()</script>'}} : gmQueued
        ? {messages: [{id: 'gm-q', from_actor: 'human:ana', body: 'Change the revenue goal'}], execution: {message_id: 'gm-q', state: 'queued', readiness_reason: 'missing_provider', label: 'Saved — no AI provider is chosen'}}
        : {messages: [{id: 'gm-q', from_actor: 'human:ana', body: 'Change the revenue goal'}, {id: 'gm-a', from_actor: 'bot:goal-manager', body: 'Updated the goal.'}, ...gmExtra]});
      if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: other, proposals: []});
      if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: needs});
      if (p === '/api/v2/goals' && post) {
        const goal = {id: 'g-new-' + goals.length, status: null, rank: 5, kpis: [], ...body};
        goals.push(goal); return json({goal});
      }
      if (p === '/api/v2/kpis' && !post) return json({kpis: url.searchParams.get('include_archived') === 'true' && archivedKpi.archived_at ? [archivedKpi] : []});
      if (p === '/api/v2/kpis' && post) { other.push(kpi('k-new-' + other.length, body.name, {...body, freshness: 'missing', latest: null, status: 'gray'})); return json({kpi: other.at(-1)}); }
      if (p === '/api/v2/kpis/k-act' && !post) return json(detail);
      if (p === '/api/v2/kpis/k-retired' && !post) return json({...detail, kpi: archivedKpi, links: [], may_edit: archivedMayEdit});
      if (p === '/api/v2/kpis/k-retired/restore' && post) { archivedKpi.archived_at = null; return json({kpi: archivedKpi}); }
      if (p === '/api/v2/kpis/k-retired/archive' && post) { archivedKpi.archived_at = new Date().toISOString(); return json({kpi: archivedKpi}); }
      if (/^\/api\/v2\/kpis\/[^/]+\/readings$/.test(p) && post) return json({reading: {id: 'r-new'}});
      const link = p.match(/^\/api\/v2\/goals\/([^/]+)\/kpis$/);
      if (link && post) { goals.find(g => g.id === link[1]).kpis.push(kpi('k-link', body.name, {freshness: 'missing', latest: null, status: 'gray'})); return json({kpi: {}}); }
      if (/^\/api\/v2\/goals\/[^/]+\/checkins$/.test(p)) return json({checkins: [goals[1].checkin, {id: 'c0', body: 'Started the email series.', source_actor: 'bot:cmo', ts: new Date(day - 7 * 86400000).toISOString()}]});
      if (/\/status\/auto$/.test(p) && post) return json({goal: {}});
      if (/^\/api\/v2\/proposals\/[^/]+\/decide$/.test(p) && post) { needs.splice(1); return json({proposal: {}}); }
      const edit = p.match(/^\/api\/v2\/goals\/([^/]+)$/);
      if (edit && post) { Object.assign(goals.find(g => g.id === edit[1]), body); return json({goal: {}}); }
      return json({});
    });
    const until = async (fn, what) => { for (let i = 0; i < 80; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); } assert.fail('timed out: ' + what); };
    const last = () => posted.at(-1);

    await page.goto('http://tico-ui.test/#/goals');
    await page.locator('#goal-tree .gt-row').first().waitFor();
    // Nothing to add from the page itself: adding lives in each owner's panel.
    assert.equal(await page.locator('#goal-body button').filter({hasText: /Goal|KPI/}).count(), 0, 'no + Goal, + Company goal or + KPI');
    assert.doesNotMatch(await page.locator('#main').innerText(), /\+ ?(Goal|Company goal|KPI)/);
    // Two requests draw the page: the tree and Needs you.
    assert.deepEqual(requests.filter(r => /goals|kpis/.test(r)).sort(), ['GET /api/v2/goals/needs-you', 'GET /api/v2/goals/tree']);

    // Every person and every bot that is not archived, goal or not, nested as the org chart nests them; built-in and message bots apart.
    const lines = await page.locator('#goal-tree > li').evaluateAll(els => els.map(el => el.classList.contains('gt-sep') ? el.textContent : el.classList.contains('gt-cont') ? '+' + el.dataset.goal : `${el.dataset.owner}@${el.style.getPropertyValue('--d')}`));
    assert.deepEqual(lines, ['company@0', 'human:ana@0', 'human:ben@1', 'bot:cmo@1', '+g-cmo2', 'bot:seo@2', 'bot:sales@1', 'bot:support@1',
      'Message bots', 'bot:inbox@0', 'bot:channel@0']);
    // The sidebar separates the same built-in and message bots, in the same order.
    assert.deepEqual(await page.locator('#tree a.node[data-helper]').evaluateAll(els => els.map(el => el.dataset.org)),
      ['b:inbox', 'b:channel']);
    assert.deepEqual(await page.locator('#tree .noderow[data-helper] .dept-label').allTextContents(), ['Message bots']);
    const row = owner => page.locator(`#goal-tree .gt-row[data-owner="${owner}"]:not(.gt-cont)`);
    assert.equal(await row('bot:sales').locator('.gt-goal').count(), 0, 'no goal: nothing written');
    assert.equal(await row('bot:goal-manager').count(), 0, 'existing built-in goals are hidden');
    assert.equal(await page.locator('#nav-assistant').getAttribute('href'), '#/assistant');
    assert.equal(await page.locator('#nav-botops').getAttribute('href'), '#/bot/botops');
    await page.locator('.gm-routines li b').first().waitFor();
    assert.match(await page.locator('#goal-manager-panel').innerText(), /Checks goals.*every day/s);
    assert.match(await page.locator('#goal-manager-panel').innerText(), /Last run/);
    await page.locator('.gm-form textarea').fill('Change the revenue goal');
    await page.locator('.gm-form button').click();
    await page.locator('[data-gm-thread]', {hasText: 'Updated the goal.'}).waitFor();
    assert.equal(last().path, '/api/v2/chat/goal-manager');
    assert.deepEqual(last().body, {text: 'Change the revenue goal', refs: {}});
    assert.equal(await page.locator('.gm-history').count(), 0, 'history stays inline');
    assert.deepEqual(await page.locator('[data-gm-thread] .bubble').evaluateAll(els => els.map(el => { const copy = el.cloneNode(true); copy.querySelectorAll('[data-chat-copy]').forEach(button => button.remove()); return copy.textContent; })), ['anaChange the revenue goal', 'Goal ManagerUpdated the goal.']);
    // A reload shows the same room the bot page uses (the viewer's personal room), with its latest exchange.
    await page.reload();
    await page.locator('[data-gm-thread]', {hasText: 'Updated the goal.'}).waitFor();
    gmExtra = Array.from({length:24}, (_, i) => ({id:'history-'+i, from_actor:'bot:goal-manager', body:'Earlier update '+i}));
    await page.reload();
    await page.locator('[data-gm-thread]', {hasText:'Earlier update 23'}).waitFor();
    const thread=page.locator('[data-gm-thread]');
    assert.equal(await thread.evaluate(el=>el.scrollHeight>el.clientHeight),true,'long conversations scroll inside the rail');
    await thread.evaluate(el=>{el.scrollTop=0;});
    gmExtra.push({id:'new-update',from_actor:'bot:goal-manager',body:'Incoming update'});
    while (!liveStreams.length) await page.waitForTimeout(50);
    await liveStreams.splice(0).at(-1).fulfill({contentType: 'text/event-stream',
      body: 'id: 7\nevent: messages\ndata: ' + JSON.stringify({seq: 7, id: 'new-update', conversation_id: 'gm-chat'}) + '\n\n'});
    await page.locator('[data-gm-thread]', {hasText:'Incoming update'}).waitFor({timeout:12000});
    assert.equal(await thread.evaluate(el=>el.scrollTop),0,'an incoming reply does not move older messages being read');
    gmExtra = [];
    gmLive = true;
    await page.reload();
    await page.locator('[data-gm-live]', {hasText:'Checking'}).waitFor();
    assert.equal(await page.locator('[data-gm-thread] .bubble.you').count(),1,'human message remains beside live reply');
    assert.equal(await page.locator('[data-gm-live] script').count(),0,'live Markdown is sanitized');
    gmLive = false;
    // With no AI provider the question waits: the panel says so and links to Settings > AI providers.
    gmQueued = true;
    await page.reload();
    await page.locator('[data-gm-provider]').waitFor();
    assert.equal(await page.locator('[data-gm-provider] a').getAttribute('data-gs-tab'), 'providers');
    if (screenshotDir) await page.screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-gm-no-provider-test.png')});
    gmQueued = false;
    await page.reload();
    await page.locator('[data-gm-thread]', {hasText: 'Updated the goal.'}).waitFor();

    assert.match(await row('human:ben').innerText(), /Keep the board honest\./, 'a profile goal shows');
    // One line each, about 36px, the goals lined up in one column.
    for (const owner of ['company', 'bot:cmo', 'bot:seo', 'bot:sales']) {
      const box = await row(owner).boundingBox();
      assert(box.height >= 34 && box.height <= 40, owner + ' is one line: ' + box.height);
    }
    const lefts = await page.locator('#goal-tree .gt-row:not(.gt-cont) .gt-goal').evaluateAll(els => [...new Set(els.map(el => Math.round(el.getBoundingClientRect().left)))]);
    assert.equal(lefts.length, 1, 'goals line up: ' + lefts);
    if (screenshotDir) await page.screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-tree-test.png')});
    // A long goal is cut short with an ellipsis; the whole of it is in the tooltip.
    const long = page.locator('#goal-tree .gt-cont[data-goal="g-cmo2"] .gt-goal');
    assert.equal(await long.getAttribute('title'), LONG);
    assert(await long.locator('.gt-title').evaluate(el => getComputedStyle(el).textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth), 'truncated');
    // KPIs are chips: the dot and the latest value; no fresh data, no number.
    const chips = row('bot:cmo').locator('.gt-chip');
    assert.deepEqual(await chips.evaluateAll(els => els.map(el => el.querySelector('.gdot').className.replace('gdot ', '') + ' ' + el.innerText.trim())), ['yellow 52%', 'gray –']);
    assert.equal(await row('company').locator('.gt-goal .gdot').getAttribute('class'), 'gdot red');
    // Needs you: a count and its lines; a proposal is confirmed there.
    assert.match(await page.locator('#goal-needs').innerText(), /Needs you\s*2[\s\S]*Activation on Double organic signups/i);
    await page.locator('#goal-needs [data-decide=confirm]').click();
    await until(() => page.locator('#goal-needs .kpi-prop').count().then(n => n === 0), 'proposal gone');
    assert.deepEqual(last(), {path: '/api/v2/proposals/p1/decide', body: {decision: 'confirm'}});

    // A bot with no goal: tapping it opens its panel on a new goal, the owner fixed.
    const panel = page.locator('#goal-panel[open]');
    await row('bot:sales').click();
    const fresh = panel.locator('[data-goal-form="new"]');
    await fresh.waitFor();
    assert.equal(await fresh.locator('select[name=owner]').count(), 0, 'the owner is whoever was tapped');
    assert.equal(await fresh.locator('select[name=parent]').inputValue(), '', 'supports nothing by default');
    await fresh.locator('input[name=title]').fill('Book 20 demos a month');
    await fresh.locator('[type=submit]').click();
    await until(() => row('bot:sales').locator('.gt-goal').count().then(n => n === 1), 'sales has a goal');
    assert.deepEqual(last(), {path: '/api/v2/goals', body: {title: 'Book 20 demos a month', owner: 'bot:sales'}});
    // Then a KPI on that goal, from the panel.
    const added = goals.at(-1).id;
    await panel.locator(`[data-gp-kpi-add="${added}"]`).click();
    const kform = panel.locator(`[data-kpi-add="${added}"]`);
    await kform.locator('input[name=name]').fill('Demos booked');
    await kform.locator('input[name=unit]').fill('demos');
    await kform.locator('input[name=target]').fill('20');
    await kform.locator('[type=submit]').click();
    await until(() => row('bot:sales').locator('.gt-chip').count().then(n => n === 1), 'the KPI shows as a chip');
    assert.equal(last().path, `/api/v2/goals/${added}/kpis`);
    assert.equal(last().body.name, 'Demos booked');
    assert.equal(last().body.target, 20);
    // And a KPI of its own, on no goal.
    await panel.locator('[data-gp-kpi-new]').click();
    await panel.locator('[data-kpi-add="@page"] input[name=name]').fill('Reply time');
    assert.equal(await panel.locator('[data-kpi-add="@page"] select[name=owner]').inputValue(), 'bot:sales');
    await panel.locator('[data-kpi-add="@page"] [type=submit]').click();
    await until(() => panel.locator('.kpi-line', {hasText: 'Reply time'}).count().then(n => n === 1), 'the KPI is under Other KPIs');
    assert.equal(last().path, '/api/v2/kpis');
    assert.equal(last().body.owner, 'bot:sales');
    await panel.locator('[data-gp-close]').click();
    assert.equal(await page.locator('#goal-panel[open]').count(), 0);

    // A bot with goals: each goal, its KPIs and its check-in; a KPI opens in the panel, with a way back.
    await row('bot:cmo').click();
    await panel.locator('[data-gp-goal="g-cmo"]').waitFor();
    assert.equal(await panel.locator('.gp-goal').count(), 2);
    assert.match(await panel.locator('[data-gp-goal="g-cmo"]').innerText(), /The onboarding email went out late/);
    await panel.locator('[data-gp-more="g-cmo"]').click();
    await until(() => panel.locator('[data-gp-checkins="g-cmo"] .kpi-check').count().then(n => n === 2), 'all check-ins');
    await panel.locator('.kpi-line[data-kpi="k-act"]').click();
    await panel.locator('.kchart').waitFor();
    assert.equal(await panel.locator('[data-kpi-archive]').innerText(), 'Archive KPI', 'editable KPI can be archived');
    assert.match((await panel.innerText()).replace(/\s+/g, ' '), /Activation v2[\s\S]*Signed-up accounts that finish setup[\s\S]*Product database/);
    assert.equal(await panel.locator('.kpi-r a[href="https://analytics.example/q/12"]').count(), 1, 'evidence');
    await panel.locator('[data-kpi-log] input[name=value]').fill('53');
    await panel.locator('[data-kpi-log] [type=submit]').click();
    await until(() => last()?.path === '/api/v2/kpis/k-act/readings', 'reading logged');
    assert.deepEqual(last().body, {value: 53, quality: 'measured'});
    await panel.locator('[data-kpi-back]').click();
    await panel.locator('[data-gp-edit="g-cmo"]').waitFor();
    // Tapping a goal edits it: its words, what it supports, its colour.
    await panel.locator('[data-gp-edit="g-cmo"]').click();
    const words = panel.locator('[data-goal-form="g-cmo"] input[name=title]');
    assert.equal(await panel.locator('[data-goal-form="g-cmo"] select[name=parent]').inputValue(), 'g-top');
    await words.fill('Double organic signups by December');
    await panel.locator('[data-goal-form="g-cmo"] [type=submit]').click();
    await until(() => row('bot:cmo').innerText().then(t => t.includes('by December')), 'the tree has the new words');
    assert.deepEqual(last(), {path: '/api/v2/goals/g-cmo', body: {title: 'Double organic signups by December'}});
    await panel.locator('[data-gp-close]').click();

    // The company line edits the company goal; a colour a person set is handed back from there.
    await row('company').click();
    await panel.locator('[data-goal-form="g-top"]').waitFor();
    assert.equal(await panel.locator('[data-goal-form="g-top"] select[name=parent]').count(), 0, 'a company goal supports nothing');
    await panel.locator('[data-goal-form="g-top"] [data-goal-auto]').click();
    await until(() => last()?.path === '/api/v2/goals/g-top/status/auto', 'handed back');
    await panel.locator('[data-gp-close]').click();
    // The owner's panel holds the KPIs no goal uses.
    await row('human:ana').click();
    assert.match(await panel.locator('.gp-body').innerText(), /Other KPIs[\s\S]*Cash runway/i);
    assert.equal(await panel.locator('[data-kpi="k-retired"]').count(), 0, 'archived KPI is absent from the default active view');
    await panel.locator('[data-gp-history]').click();
    await panel.locator('.gp-history-list [data-kpi="k-retired"]').waitFor();
    assert.match(requests.join('\n'), /GET \/api\/v2\/kpis$/);
    assert.match(await panel.locator('.gp-history-list').innerText(), /Archived KPIs[\s\S]*Retired conversion/i);
    await panel.locator('.gp-history-list [data-kpi="k-retired"]').click();
    await panel.locator('[data-kpi-archive]').waitFor();
    assert.equal(await panel.locator('[data-kpi-archive]').innerText(), 'Restore KPI');
    assert.match(await panel.locator('.kpi-archived').innerText(), /Readings and definition history are retained/);
    archivedMayEdit = false;
    await panel.locator('[data-kpi-back]').click();
    await panel.locator('.gp-history-list [data-kpi="k-retired"]').click();
    await panel.locator('.kpi-archived').waitFor();
    assert.equal(await panel.locator('[data-kpi-archive]').count(), 0, 'read-only viewer cannot restore an archived KPI');
    archivedMayEdit = true;
    await panel.locator('[data-kpi-back]').click();
    await panel.locator('.gp-history-list [data-kpi="k-retired"]').click();
    await panel.locator('[data-kpi-archive]').click();
    await until(() => last()?.path === '/api/v2/kpis/k-retired/restore', 'KPI restored');
    assert.deepEqual(last().body, {});
    await until(() => panel.locator('[data-kpi-archive]').innerText().then(t => t === 'Archive KPI'), 'restore refreshes the controls');
    assert.equal(await panel.locator('[data-kpi-archive]').innerText(), 'Archive KPI', 'restore returns to the active state');
    await panel.locator('[data-kpi-archive]').click();
    await until(() => last()?.path === '/api/v2/kpis/k-retired/archive', 'KPI archived again');
    await panel.locator('[data-kpi-back]').click();
    await panel.locator('[data-gp-close]').click();
    // A link to one goal opens its owner's panel on it.
    await page.goto('http://tico-ui.test/#/goals/g-cmo2');
    await page.locator('#goal-panel[open] [data-goal-form="g-cmo2"] input[name=title]').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.name), 'title');
    await page.keyboard.press('Escape');   // Escape leaves the goal; the panel stays
    await panel.locator('[data-gp-edit="g-cmo2"]').waitFor();
    assert.equal(new URL(page.url()).hash, '#/goals');
    await page.keyboard.press('Escape');
    await until(() => page.locator('#goal-panel[open]').count().then(n => n === 0), 'closed');

    // A phone: two lines where needed, a smaller indent, no sideways scroll; the panel is a sheet at the bottom.
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    await page.locator('[data-gm-thread]').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('[data-gm-thread] .bubble.you').isVisible(),true);
    assert.equal(await page.locator('[data-gm-thread] .bubble.bot').isVisible(),true);
    assert.equal(await page.locator('.gm-form textarea').isVisible(),true,'composer stays below inline thread');
    await page.evaluate(() => setDrawer(false));
    if (screenshotDir) {
      await page.locator('[data-gm-thread]', {hasText: 'Updated the goal.'}).waitFor();
      await page.screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-phone-test.png')});
    }
    const cmo = await row('bot:cmo').boundingBox(), seo = await row('bot:seo').locator('.gt-av').boundingBox(), ana = await row('human:ana').locator('.gt-av').boundingBox();
    assert(cmo.height >= 44 && cmo.height <= 80, 'name and goal on two lines: ' + cmo.height);
    assert(seo.x - ana.x <= 22, 'a smaller indent: ' + (seo.x - ana.x));
    assert.equal(await row('bot:cmo').locator('.gt-kn').isVisible(), true, 'a count instead of chips');
    await row('bot:sales').click();
    const sheet = await panel.boundingBox();
    assert(Math.abs(sheet.y + sheet.height - 844) <= 2 && sheet.width >= 388, 'a sheet at the bottom');
    await page.keyboard.press('Escape');
    // Turn on uses the existing setup method, without disturbing the goals tree.
    bots.find(b => b.name === 'goal-manager').status = 'paused';
    await page.setViewportSize({width: 1440, height: 900});
    await page.reload();
    await page.locator('[data-gm-result]', {hasText: 'Updated KPI readings'}).waitFor();
    // Turning on mounts the chat before the separate routine result request completes.
    let releaseResult;
    const resultGate = new Promise(resolve => { releaseResult = resolve; });
    await page.route('**/api/v2/routines/gm-review/occurrences', async route => {
      await resultGate;
      await route.fallback();
    });
    const resultRequested = page.waitForRequest('**/api/v2/routines/gm-review/occurrences');
    await page.locator('[data-gm-on]').click();
    await page.locator('.gm-form').waitFor();
    await resultRequested;
    assert.deepEqual(posted.findLast(r => r.path === '/api/v2/goal-manager/turn-on').body, {});
    assert.equal(bots.find(b => b.name === 'goal-manager').status, 'active');
    assert.equal(await page.locator('#goal-tree [data-owner="bot:goal-manager"]').count(), 0);
    assert.equal(await page.locator('[data-gm-result]').innerText(), '', 'the chat is ready while the routine result is pending');
    releaseResult();
    await page.locator('[data-gm-result]', {hasText: 'Updated KPI readings'}).waitFor();
    assert.match(await page.locator('[data-gm-result]').innerText(), /Updated KPI readings/);

    assert.deepEqual(errors, []);
    console.log('PASS: goals tree, built-in and message bots, panel adds and edits goals and KPIs, truncation, phone.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
