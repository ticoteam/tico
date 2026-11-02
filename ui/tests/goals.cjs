// The Goals page (ui/goals-kpis.js): one tree of every person and bot, nested as the org chart nests them, a line each
// with the goal to the right (cut short, whole in its tooltip) and KPI chips; helper bots off it; tapping a line opens
// that owner's panel, where goals and KPIs are added and edited. No helper chat sits beside it; Ask is the way in.
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const screenshotDir = process.env.TICO_SCREENSHOT_DIR;

const LONG = 'Publish one deeply researched comparison page every week for each of the twelve competitor keywords we track, with fresh pricing, screenshots and a customer quote on every page';
const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [
  bot('cmo', 'AI CMO', 'p:ana'), bot('seo', 'SEO', 'b:cmo'), bot('sales', 'Sales', 'p:ana'),
  bot('support', 'Support', 'p:ana', {status: 'paused', onboarding_state: 'needs_setup'}),
  bot('old', 'Old bot', 'p:ana', {status: 'archived'}),
  bot('coo', 'Assistant', ''), bot('librarian', 'Librarian', ''), bot('botops', 'BotOps', ''), bot('goal-manager', 'Strategy Bot', 'p:ana', {reports_to: ''}), bot('inbox', 'Inbox Manager', 'b:botops', {template: 'inbox'}),
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
    const confirms = [];
    page.on('dialog', dialog => { confirms.push(dialog.message()); dialog.accept(); });
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
    const needs = [{kind: 'kpi_red', goal_id: 'g-cmo', goal_title: 'Double organic signups', kpi_id: 'k-act', kpi_name: 'Activation', reason: 'Activation 52% vs 58% needed on pace',
       task: {id: 't-red', title: 'Check why Activation is off track on Double organic signups', status: 'open'}},
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
      if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: other, proposals: []});
      if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: needs});
      if (p === '/api/v2/goals' && post) {
        const goal = {id: 'g-new-' + goals.length, status: null, rank: 5, kpis: [], ...body};
        goals.push(goal); return json({goal});
      }
      if (p === '/api/v2/kpis' && !post) return json({kpis: url.searchParams.get('include_archived') === 'true' && archivedKpi.archived_at ? [archivedKpi] : []});
      if (p === '/api/v2/kpis' && post) { other.push(kpi('k-new-' + other.length, body.name, {...body, freshness: 'missing', latest: null, status: 'gray'})); return json({kpi: other.at(-1)}); }
      if (p === '/api/v2/kpis/k-act' && !post) return json(detail);
      if (p === '/api/v2/kpis/k-retired' && !post) return json({...detail, kpi: archivedKpi, links: goals.filter(g => g.kpis.some(k => k.id === archivedKpi.id)).map(g => ({...activation.link, goal_id: g.id, goal_title: g.title})), may_edit: archivedMayEdit});
      if (p === '/api/v2/kpis/k-retired/restore' && post) { archivedKpi.archived_at = null; return json({kpi: archivedKpi}); }
      if (p === '/api/v2/kpis/k-retired/archive' && post) { archivedKpi.archived_at = new Date().toISOString();
        // The reply lands while the test has moved on (back, closed, a link to another goal): it must not reopen the KPI.
        await new Promise(r => setTimeout(r, 250)); return json({kpi: archivedKpi}); }
      if (/^\/api\/v2\/kpis\/[^/]+\/readings$/.test(p) && post) return json({reading: {id: 'r-new'}});
      const link = p.match(/^\/api\/v2\/goals\/([^/]+)\/kpis$/);
      if (link && post) { goals.find(g => g.id === link[1]).kpis.push(kpi('k-link', body.name, {freshness: 'missing', latest: null, status: 'gray'})); return json({kpi: {}}); }
      if (/^\/api\/v2\/goals\/[^/]+\/checkins$/.test(p)) return json({checkins: [goals[1].checkin, {id: 'c0', body: 'Started the email series.', source_actor: 'bot:cmo', ts: new Date(day - 7 * 86400000).toISOString()}]});
      if (/\/status\/auto$/.test(p) && post) return json({goal: {}});
      if (/^\/api\/v2\/proposals\/[^/]+\/decide$/.test(p) && post) { needs.splice(1); return json({proposal: {}}); }
      const edit = p.match(/^\/api\/v2\/goals\/([^/]+)$/);
      if (edit && post) { Object.assign(goals.find(g => g.id === edit[1]), body); return json({goal: {}}); }
      return json(unrouted(p));
    });
    const until = async (fn, what) => { for (const end = Date.now() + t(4000); Date.now() < end;) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); } if (!await fn()) assert.fail('timed out: ' + what); };
    const last = () => posted.at(-1);

    await page.goto('http://tico-ui.test/#/goals');
    await page.locator('#goal-tree .gt-row').first().waitFor();
    // The tree adds nothing itself: "+ Goal" heads the Strategy tab, and the rest lives in each owner's panel.
    assert.equal(await page.locator('#goal-body button').filter({hasText: /Goal|KPI/}).count(), 0, 'no + Goal, + Company goal or + KPI');
    assert.doesNotMatch(await page.locator('#goal-body').innerText(), /\+ ?(Goal|Company goal|KPI)/);
    assert.equal(await page.locator('#strat-actions #goal-new').innerText(), '+ Goal');
    // Three requests draw the page: the tree, Needs you and the KPI views.
    assert.deepEqual(requests.filter(r => /goals|kpis/.test(r)).sort(), ['GET /api/v2/goals/needs-you', 'GET /api/v2/goals/tree', 'GET /api/v2/growth/kpis']);

    // Every person and every bot that is not archived, goal or not, nested as the org chart nests them; helper bots off it.
    const lines = await page.locator('#goal-tree > li').evaluateAll(els => els.map(el => el.classList.contains('gt-cont') ? '+' + el.dataset.goal : `${el.dataset.owner}@${el.style.getPropertyValue('--d')}`));
    assert.deepEqual(lines, ['company@0', 'human:ana@0', 'human:ben@1', 'bot:cmo@1', '+g-cmo2', 'bot:seo@2', 'bot:sales@1', 'bot:support@1'], JSON.stringify(lines));   // the Strategy Bot is a helper too
    // The sidebar leaves the same helper bots off.
    assert.equal(await page.locator('#tree [data-org="b:inbox"], #tree [data-org="b:channel"], #tree [data-org="b:goal-manager"]').count(), 0);
    const row = owner => page.locator(`#goal-tree .gt-row[data-owner="${owner}"]:not(.gt-cont)`);
    assert.equal(await row('bot:sales').locator('.gt-goal').count(), 0, 'no goal: nothing written');
    assert.equal(await page.locator('#goal-tree [data-owner="bot:goal-manager"]').count(), 0, 'its built-in goals are hidden, and no line of its own');
    assert.equal(await page.locator('#nav-assistant, #nav-botops').count(), 0, 'Ask is the way in to both');
    // No Strategy Bot chat beside the tree: Ask (the person's Assistant) is the way in.
    assert.equal(await page.locator('#sbot, .gm-form, [data-gm-thread], [data-gm-on]').count(), 0, 'no Strategy Bot chat');

    assert.match(await row('human:ben').innerText(), /Keep the board honest\./, 'a profile goal shows');
    // KPIs are chips: the dot and the latest value; no fresh data, no number.
    const chips = row('bot:cmo').locator('.gt-chip');
    assert.deepEqual(await chips.evaluateAll(els => els.map(el => el.querySelector('.gdot').className.replace('gdot ', '') + ' ' + el.innerText.trim())), ['yellow 52%', 'gray –']);
    assert.equal(await row('company').locator('.gt-goal .gdot').getAttribute('class'), 'gdot red');
    // Needs you: a count and its lines; a proposal is confirmed there.
    assert.match(await page.locator('#goal-needs').innerText(), /Needs you\s*2[\s\S]*Activation on Double organic signups/i);
    // Each line is the Strategy Bot's task, linked; the link opens the task, not the KPI.
    assert.equal(await page.locator('#goal-needs .need-row [data-need-task]').getAttribute('href'), '#/task/t-red');
    await page.locator('#goal-needs [data-decide=confirm]').click();
    await until(() => page.locator('#goal-needs .kpi-prop').count().then(n => n === 0), 'proposal gone');
    assert.deepEqual(last(), {path: '/api/v2/proposals/p1/decide', body: {decision: 'confirm'}});

    // A bot with no goal: tapping it opens its panel on a new goal, the owner fixed.
    const panel = page.locator('#goal-panel[open]');
    await row('bot:sales').locator('.gt-name').click();
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
    await row('bot:cmo').locator('.gt-name').click();
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
    await row('company').locator('.gt-name').click();
    await panel.locator('[data-goal-form="g-top"]').waitFor();
    assert.equal(await panel.locator('[data-goal-form="g-top"] select[name=parent]').count(), 0, 'a company goal supports nothing');
    await panel.locator('[data-goal-form="g-top"] [data-goal-auto]').click();
    await until(() => last()?.path === '/api/v2/goals/g-top/status/auto', 'handed back');
    await panel.locator('[data-gp-close]').click();
    // The owner's panel holds the KPIs no goal uses.
    await row('human:ana').locator('.gt-name').click();
    assert.match(await panel.locator('.gp-body').innerText(), /Other KPIs[\s\S]*Cash runway/i);
    assert.equal(await panel.locator('[data-kpi="k-retired"]').count(), 0, 'archived KPI is absent from the default active view');
    await panel.locator('[data-gp-history]').click();
    await panel.locator('.gp-history-list [data-kpi="k-retired"]').waitFor();
    assert.match(requests.join('\n'), /^GET \/api\/v2\/kpis$/m);
    assert.match(await panel.locator('.gp-history-list').innerText(), /Archived KPIs[\s\S]*Retired conversion/i);
    const historyRow = panel.locator('.gp-history-list [data-kpi="k-retired"]');
    assert.equal(await historyRow.evaluate(li => li.classList.contains('kpi-archived-line')), false, 'the archive list is not a goal');
    assert.match(await historyRow.locator('.kpi-val').innerText(), /52%/, 'an archived KPI keeps its last reading in the archive list');
    assert.equal(await historyRow.locator('.kspark').count(), 1, 'and its sparkline');
    assert.ok(await historyRow.locator('.kpi-owner').innerText(), 'and its owner');
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
    assert.equal(confirms.at(-1), 'Archive “Retired conversion”? Its readings and definition history will stay available.', 'unlinked archive keeps its wording');
    await panel.locator('[data-kpi-back]').click();
    await panel.locator('[data-gp-close]').click();
    // Archived links stay on goals as muted, keyboard-openable lines; show only three.
    goals[1].kpis.push(archivedKpi);
    goals[2].kpis.push(archivedKpi, ...[1, 2, 3].map(i => ({...archivedKpi, id: 'old-' + i, name: 'Old measure ' + i})));
    await page.goto('http://tico-ui.test/#/goals/g-cmo2');
    const archivedLines = panel.locator('[data-gp-goal="g-cmo2"] .kpi-line.kpi-archived-line');
    await archivedLines.first().waitFor();
    assert.equal(await archivedLines.count(), 3, 'at most three archived measures');
    assert.match(await archivedLines.first().innerText(), /Archived .+, not counting/);
    assert.equal(await archivedLines.locator('.gdot, .kspark, .kpi-target').count(), 0, 'archived lines carry no colour or old reading');
    assert.match(await panel.locator('[data-gp-goal="g-cmo2"]').innerText(), /\+1 archived/);
    assert.equal(await page.locator('[data-goal="g-cmo2"] [data-kpi-chip="k-retired"]').count(), 0, 'archived measures never colour tree chips');
    await archivedLines.first().focus();
    await page.keyboard.press('Enter');
    await panel.locator('[data-kpi-archive]').waitFor();
    assert.equal(await panel.locator('[data-kpi-archive]').innerText(), 'Restore KPI');
    await panel.locator('[data-kpi-archive]').click();
    await until(() => panel.locator('[data-kpi-archive]').innerText().then(t => t === 'Archive KPI'), 'linked restore completed');
    await panel.locator('[data-kpi-archive]').click();
    assert.match(confirms.at(-1), /It stops counting toward 2 goals:/);
    assert.ok(confirms.at(-1).includes(goals[1].title));
    assert.ok(confirms.at(-1).includes(goals[2].title));
    await until(() => panel.locator('[data-kpi-archive]').innerText().then(t => t === 'Restore KPI'), 'linked archive completed');
    await panel.locator('[data-kpi-back]').click();
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, {recursive: true});
      await page.screenshot({path: path.join(screenshotDir, 'goals-archived-desktop.png')});
      await page.setViewportSize({width: 390, height: 844});
      await page.screenshot({path: path.join(screenshotDir, 'goals-archived-phone.png')});
      await page.setViewportSize({width: 1440, height: 900});
    }
    await panel.locator('[data-gp-close]').click();
    await page.goto('http://tico-ui.test/#/goals');
    // The page settles first: a render still loading would redraw over the goal the link opens.
    await until(() => page.evaluate(() => location.hash === '#/strategy/goals'), 'goals redirect');
    await page.locator('#goal-tree .gt-row').first().waitFor();
    // A link to one goal opens its owner's panel on it.
    await page.goto('http://tico-ui.test/#/goals/g-cmo2');
    await page.locator('#goal-panel[open] [data-goal-form="g-cmo2"] input[name=title]').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.name), 'title');
    await page.keyboard.press('Escape');   // Escape leaves the goal; the panel stays
    await panel.locator('[data-gp-edit="g-cmo2"]').waitFor();
    assert.equal(new URL(page.url()).hash, '#/strategy/goals');
    await page.keyboard.press('Escape');
    await until(() => page.locator('#goal-panel[open]').count().then(n => n === 0), 'closed');

    // A phone: two lines where needed, a smaller indent, no sideways scroll; the panel is a sheet at the bottom.
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    await page.evaluate(() => setDrawer(false));
    if (screenshotDir) await page.screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-phone-test.png')});
    // Measured in one step: a refresh may redraw the tree between separate reads.
    const {cmo, seo, ana} = await page.evaluate(() => {
      const box = (owner, inner = '') => {
        const r = document.querySelector(`#goal-tree .gt-row[data-owner="${owner}"]:not(.gt-cont) ${inner}`).getBoundingClientRect();
        return {x: r.x, height: r.height};
      };
      return {cmo: box('bot:cmo'), seo: box('bot:seo', '.gt-av'), ana: box('human:ana', '.gt-av')};
    });
    assert(cmo.height >= 44 && cmo.height <= 80, 'name and goal on two lines: ' + cmo.height);
    // two levels down: each a half avatar, an elbow and its gap (goals.css), the elbow shorter than on a desktop
    assert(seo.x - ana.x <= 40, 'a smaller indent: ' + (seo.x - ana.x));
    assert.equal(await row('bot:cmo').locator('.gt-kn').isVisible(), true, 'a count instead of chips');
    await row('bot:sales').locator('.gt-name').click();
    const sheet = await panel.boundingBox();
    assert(Math.abs(sheet.y + sheet.height - 844) <= 2 && sheet.width >= 388, 'a sheet at the bottom');
    await page.keyboard.press('Escape');

    assert.deepEqual(errors, []);
    console.log('PASS: goals tree, helpers off it, panel adds and edits goals and KPIs, truncation, phone.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
