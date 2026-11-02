// Finished goals on the Goals page (ui/app/goals.js goalFinishedMount): under an owner's live goals, "Finished (N)",
// closed until opened, newest first, the latest 20 until "Show all"; a row opens read only; Reopen only for whoever may set
// the goal's colour; "Goal removed" offers Undo, which puts the colour back. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const bot = (name, display_name, org_parent) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true});
const bots = [bot('sales', 'Sales', 'p:ana'), bot('seo', 'SEO', 'p:ana')];
const people = [{id: 'ana', name: 'Ana Silva', org_parent: ''}, {id: 'ben', name: 'Ben Park', org_parent: 'p:ana'}];
const at = day => `2026-10-${day}T09:00:00Z`;
const finished = (id, title, owner, status, day, by) => ({id, title, owner_id: owner, parent_id: null, status, status_source: 'person', status_by_id: by,
  status_note: '', status_at: at(day), rank: null, kpis: []});
const goals = [
  {id: 'g-company', title: 'Grow revenue', owner_id: 'company', parent_id: null, status: 'green', status_source: 'person', status_by_id: 'human:ana', status_at: at(1), rank: 0, kpis: []},
  {id: 'g-ben', title: 'Ship the stats page', owner_id: 'human:ben', parent_id: null, status: 'yellow', status_source: 'person', status_by_id: 'human:ben',
   status_note: 'Backend is late', status_at: at(10), rank: 0, kpis: []},
  {...finished('g-proposed', 'Propose the stats page', 'human:ben', 'dropped', 26, 'human:ben'), parent_id: 'g-company', status_source: null},
  finished('g-b1', 'Hire a designer', 'human:ben', 'done', 20, 'human:ben'),
  finished('g-b2', 'Launch the beta', 'human:ben', 'dropped', 24, 'human:ana'),
  finished('g-b3', 'Write the handbook', 'human:ben', 'done', 22, 'human:ben'),
  {id: 'g-sales', title: 'Close ten deals', owner_id: 'bot:sales', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: []},
  finished('g-s1', 'Old pipeline', 'bot:sales', 'dropped', 15, 'human:ana'),
  ...Array.from({length: 22}, (_, i) => finished('g-seo' + i, 'Keyword ' + i, 'bot:seo', 'done', 10 + (i % 15), 'human:ana')),
];

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [], posted = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname, post = req.method() === 'POST';
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/v2/events') return;
      const body = post ? req.postDataJSON() : null;
      if (post) posted.push({path: p, body});
      if (p === '/api/employees') return json(bots);
      // Ben: a person, not the owner. He sets his own goals' colour; Sales and SEO report to Ana, not to him.
      if (p === '/api/me') return json({id: 'ben', name: 'Ben Park', email: 'ben@example.test', role: 'member', cloud: true});
      if (p === '/api/humans') return json({people});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: [], proposals: []});
      if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ben', items: []});
      const one = p.match(/^\/api\/v2\/goals\/([^/]+)(\/status|\/reopen)?$/), g = one && goals.find(x => x.id === one[1]);
      if (g && !post && !one[2]) return json({goal: {...g, kpis: [{id: 'k1', name: 'Beta signups', status: 'gray'}],
        checkins: [{id: 'c1', body: 'Paused for the redesign.', source_actor: 'human:ben', created_at: at(23)}],
        events: [{field: 'status', actor: 'human:ana', old: 'green', new: 'dropped', note: '', created_at: at(24)}]}});
      if (g && post && one[2] === '/status') { g.before = {status: g.status, status_note: g.status_note}; Object.assign(g, {status: body.status, status_note: body.note, rank: null}); return json({goal: g}); }
      if (g && post && one[2] === '/reopen' && body.undo) { Object.assign(g, g.before, {rank: 9}); return json({goal: g}); }
      if (g && post && one[2] === '/reopen') {
        Object.assign(g, {status: body.status === 'auto' ? 'gray' : body.status, status_source: body.status === 'auto' ? 'auto' : 'person',
          status_note: body.status === 'auto' ? '' : body.note, status_by_id: 'human:ben', rank: 9});
        return json({goal: g});
      }
      return json(unrouted(p));
    });
    const until = async (fn, what) => { for (const end = Date.now() + t(4000); Date.now() < end;) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); } if (!await fn()) assert.fail('timed out: ' + what); };
    const panel = page.locator('#goal-panel');
    const open = async owner => {
      if (await panel.evaluate(d => d.open).catch(() => false)) await panel.locator('[data-gp-close]').click();
      await page.locator(`#goal-tree .gt-row[data-owner="${owner}"]:not(.gt-cont) .gt-name`).click();
      await panel.locator('.gp-body').waitFor();
    };

    await page.goto('http://tico-ui.test/#/goals');
    await page.locator('#goal-tree .gt-row').first().waitFor();
    // The tree still shows live goals only.
    assert.equal(await page.locator('#goal-tree [data-goal="g-b1"]').count(), 0, 'a finished goal is not in the tree');

    // 1. Two done and two dropped: "Finished (4)", closed.
    await open('human:ben');
    const toggle = panel.locator('[data-fin-toggle]');
    assert.equal(await toggle.innerText(), 'Finished (4)');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(await panel.locator('.goal-fin-row').count(), 0, 'closed until opened');

    // 2. Open: title, Done or Dropped, date, who set it; newest first.
    await toggle.click();
    const rows = panel.locator('.goal-fin-row');
    assert.deepEqual(await rows.locator('.goal-fin-title').allInnerTexts(), ['Propose the stats page', 'Launch the beta', 'Write the handbook', 'Hire a designer']);
    assert.deepEqual(await rows.locator('.goal-fin-what').allInnerTexts(), ['Dropped', 'Dropped', 'Done', 'Done']);
    assert.match(await rows.first().locator('.goal-fin-when').innerText(), /Oct 26.*Ben Park/);
    assert.match(await rows.nth(2).locator('.goal-fin-when').innerText(), /Oct 22.*Ben Park/);
    // A row opens read only: its measures and its updates, nothing to edit.
    await rows.first().locator('[data-fin-open]').click();
    await panel.locator('.goal-fin-detail .goal-fin-kpis').waitFor();
    const detail = await panel.locator('.goal-fin-detail').innerText();
    assert.match(detail, /Beta signups/); assert.match(detail, /Paused for the redesign\./); assert.match(detail, /Ana Silva · dropped/);
    assert.equal(await panel.locator('.goal-fin-detail input, .goal-fin-detail select').count(), 0, 'read only');

    // A proposer may bring their dropped proposal back without silently accepting it themselves.
    await rows.first().locator('[data-fin-reopen]').click();
    const proposalForm = panel.locator('[data-fin-reopen-form="g-proposed"]');
    assert.deepEqual(await proposalForm.locator('select option').allInnerTexts(), ['Choose…', 'Let the Assistant set it']);
    await proposalForm.locator('select').selectOption('auto');
    await proposalForm.locator('input[name=note]').fill('The proposal is back');
    await proposalForm.locator('button[type=submit]').click();
    await until(() => posted.some(r => r.path === '/api/v2/goals/g-proposed/reopen'), 'proposal reopened automatically');
    assert.deepEqual(posted.find(r => r.path === '/api/v2/goals/g-proposed/reopen').body, {status: 'auto', note: 'The proposal is back'});

    // 4. Ben may set his goal's colour: Reopen asks for a colour and one line why, and the goal is live again.
    await rows.first().locator('[data-fin-reopen]').click();
    const form = panel.locator('[data-fin-reopen-form="g-b2"]');
    await form.locator('button[type=submit]').click();
    assert.equal(posted.filter(r => r.path === '/api/v2/goals/g-b2/reopen').length, 0, 'no colour chosen: nothing sent');
    assert.deepEqual(await form.locator('select option').allInnerTexts(), ['Choose…', 'On track', 'At risk', 'Off track', 'Let the Assistant set it']);
    await form.locator('select').selectOption('green');
    await form.locator('button[type=submit]').click();
    assert.equal(posted.filter(r => r.path === '/api/v2/goals/g-b2/reopen').length, 0, 'no reason: nothing sent');
    await form.locator('input[name=note]').fill('The beta is back on');
    await form.locator('button[type=submit]').click();
    await until(() => posted.some(r => r.path === '/api/v2/goals/g-b2/reopen'), 'reopen sent');
    assert.deepEqual(posted.find(r => r.path === '/api/v2/goals/g-b2/reopen').body, {status: 'green', note: 'The beta is back on'});
    await panel.locator('[data-gp-goal="g-b2"]').waitFor();
    await until(async () => await panel.locator('[data-fin-toggle]').innerText() === 'Finished (2)', 'two left');
    assert.equal(await page.locator('#goal-tree [data-goal="g-b2"]').count(), 1, 'back in the tree');

    // 6. Remove: the toast offers Undo, and the server puts back the colour and note it had.
    await panel.locator('[data-gp-edit="g-ben"]').click();
    await panel.locator('[data-goal-remove]').click();
    await until(() => posted.some(r => r.path === '/api/v2/goals/g-ben/status' && r.body.status === 'dropped'), 'dropped');
    const toast = page.locator('.toast', {hasText: 'Goal removed'});
    await toast.locator('button', {hasText: 'Undo'}).click();
    await until(() => posted.some(r => r.path === '/api/v2/goals/g-ben/reopen'), 'undo sent');
    assert.deepEqual(posted.find(r => r.path === '/api/v2/goals/g-ben/reopen').body, {undo: true}, 'the server reads what it had');
    await panel.locator('[data-gp-goal="g-ben"]').waitFor();

    // 5. Ben may not set the colour of Sales's goals: no Reopen. 3. An owner with no finished goal has no "Finished" line.
    await open('bot:sales');
    await panel.locator('[data-fin-toggle]').click();
    await panel.locator('.goal-fin-row').first().waitFor();
    assert.equal(await panel.locator('[data-fin-reopen]').count(), 0, 'no Reopen without the right to set the colour');
    await open('human:ana');
    assert.equal(await panel.locator('[data-fin-toggle]').count(), 0, 'no finished goals: no line');

    // The latest 20, then Show all.
    await open('bot:seo');
    await panel.locator('[data-fin-toggle]').click();
    assert.equal(await panel.locator('.goal-fin-row').count(), 20);
    await panel.locator('[data-fin-all]').click();
    assert.equal(await panel.locator('.goal-fin-row').count(), 22);

    assert.deepEqual(errors, []);
    console.log('goals-finished: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
