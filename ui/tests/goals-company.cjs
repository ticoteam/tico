// The Company section at the top of Goals and each goal's Why line (ui/goals-kpis.js goalCompanyHtml, goalWhyHtml), on every
// install (backend/company.py): empty, it asks the owner what the company is for with the two fields; the owner saves
// them; an admin or a member reads them with no Edit; History lists who changed what and when. A goal with no Why says
// "Not tied to the vision"; the CEO Bot's proposal is accepted as it is or edited, and an Accept on a line the CEO Bot has
// since replaced is refused and shows the new one. At level 2 a recovery in Needs you is approved one option at a time.
// Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const PROMPT = 'Tell your CEO Bot what this company is for.';
const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('coo', 'Assistant', ''), bot('botops', 'BotOps', ''), bot('librarian', 'Librarian', ''),
  bot('goal-manager', 'CEO Bot', 'p:ana', {reports_to: ''}), bot('cmo', 'Head of Marketing', 'b:goal-manager', {reports_to: ''})];

async function open(browser, {role = 'owner', company = {vision: '', description: ''}, history = [], needs = []} = {}) {
  const server = {};
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [], posted = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = {
    company: {...company, updated: null, updated_by: null, updated_by_name: ''},
    history: [...history],
    goals: [
      {id: 'g-top', title: 'Grow rentals 30%', owner: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: [], why: ''},
      {id: 'g-cmo', title: 'Double organic signups', owner: 'bot:cmo', parent_id: 'g-top', status: 'yellow', status_source: 'auto', rank: 0, kpis: [],
       why: 'More landlords find Tico on their own.'},
    ],
    proposals: [{id: 'p-why', kind: 'goal_why', goal_id: 'g-top', kpi_id: null, payload: {why: 'More rentals is the vision\'s first step.'},
      reason: 'The vision is about landlords.', proposed_by: 'bot:goal-manager', proposed_at: new Date().toISOString(), status: 'pending',
      may_decide: role === 'owner'}],
  };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', name: role === 'owner' ? 'Ana Silva' : 'Cara Diaz', email: 'x@example.test',
      role: role === 'owner' ? 'owner' : 'viewer', company_role: role, mover: role === 'owner', cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}, {id: 'cara', name: 'Cara Diaz', org_parent: 'p:ana'}]});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') return json({goals: state.goals, owners: {}, other_kpis: [], proposals: state.proposals.filter(x => x.status === 'pending')});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: needs});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    if (p === '/api/v2/company' && req.method() === 'GET') {
      return json({company: state.company, may_edit: role === 'owner'});
    }
    if (p === '/api/v2/company' && req.method() === 'POST') {
      const body = req.postDataJSON();
      posted.push(['company', body]);
      const ts = new Date().toISOString();
      for (const field of ['vision', 'description']) if (body[field] !== undefined && body[field] !== state.company[field])
        state.history.unshift({id: 'e' + state.history.length, ts, actor: 'human:ana', actor_name: 'Ana Silva', field, old: state.company[field], new: body[field]});
      state.company = {...state.company, ...body, updated: ts, updated_by: 'human:ana', updated_by_name: 'Ana Silva'};
      return json({company: state.company, may_edit: true});
    }
    if (p === '/api/v2/company/history') return json({events: state.history});
    const decide = p.match(/^\/api\/v2\/proposals\/([^/]+)\/decide$/);
    if (decide) {
      const body = req.postDataJSON(), prop = state.proposals.find(x => x.id === decide[1]);
      posted.push(['decide', decide[1], body]);
      if (prop.status !== 'pending') return route.fulfill({status: 422, contentType: 'application/json',
        body: JSON.stringify({error: {code: 'duplicate', detail: 'That Why was replaced by a newer one; read the new one before you decide'}})});
      prop.status = body.decision === 'confirm' ? 'confirmed' : 'rejected';
      if (body.decision === 'confirm') state.goals.find(g => g.id === prop.goal_id).why = body.why ?? prop.payload.why;
      return json({proposal: prop});
    }
    const why = p.match(/^\/api\/v2\/goals\/([^/]+)\/why$/);
    if (why) {
      const body = req.postDataJSON(), g = state.goals.find(x => x.id === why[1]);
      posted.push(['why', why[1], body]);
      g.why = body.why;
      return json({goal: g});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/goals');
  await page.locator('#goal-tree .gt-row').first().waitFor();
  return {page, errors, posted, state, server};
}
// The CEO Bot proposes a new line for a goal: a new proposal, and the one waiting is closed (backend/goals.py propose).
function replaceWhy(state, goalId, why) {
  for (const p of state.proposals) if (p.goal_id === goalId && p.kind === 'goal_why' && p.status === 'pending') p.status = 'rejected';
  const p = {...state.proposals.find(x => x.goal_id === goalId), id: 'p-why-' + state.proposals.length, payload: {why}, status: 'pending'};
  state.proposals.push(p);
  return p;
}
const panelFor = async (page, owner) => {
  await page.locator(`#goal-tree .gt-row[data-owner="${owner}"]`).first().click();
  await page.locator('#goal-panel[open] .gp-body').waitFor();
  return page.locator('#goal-panel');
};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Spec test 1: no vision yet, the owner sees the prompt and the two empty fields above the goals.
    {
      const {page, errors, posted} = await open(browser);
      const section = page.locator('#goal-company');
      await section.waitFor();
      assert.equal(await section.locator('h2').textContent(), 'Company');
      assert.equal(await section.locator('.gco-prompt').innerText(), PROMPT);
      assert.equal(await section.locator('textarea[name="vision"]').inputValue(), '');
      assert.equal(await section.locator('textarea[name="description"]').inputValue(), '');
      const above = await page.evaluate(() => {
        const co = document.getElementById('goal-company'), tree = document.getElementById('goal-tree');
        return Boolean(co.compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING);
      });
      assert.ok(above, 'the Company section is above the goals');
      // The owner saves both; the section reads them back with Edit, and History says who and when.
      await section.locator('textarea[name="vision"]').fill('Every small landlord runs their rentals on Tico.');
      await section.locator('textarea[name="description"]').fill('Tico runs a rental business with bots, for landlords with 1-20 units.');
      await section.locator('button[type="submit"]').click();
      await section.locator('[data-gco-vision]').waitFor();
      assert.deepEqual(posted[0], ['company', {vision: 'Every small landlord runs their rentals on Tico.',
        description: 'Tico runs a rental business with bots, for landlords with 1-20 units.'}]);
      assert.equal(await section.locator('[data-gco-vision]').innerText(), 'Every small landlord runs their rentals on Tico.');
      assert.equal(await section.locator('.gco-prompt').count(), 0, 'the prompt goes once it is written');
      assert.equal(await section.locator('[data-gco-edit]').count(), 1);
      // A draft survives a redraw: typing, then something else redraws the page, the words stay.
      await section.locator('[data-gco-edit]').click();
      await section.locator('textarea[name="vision"]').fill('Every landlord, any size.');
      await page.evaluate(() => goalsRender(GOALS_ST));
      assert.equal(await page.locator('#goal-company textarea[name="vision"]').inputValue(), 'Every landlord, any size.');
      await page.locator('#goal-company [data-gco-cancel]').click();
      await page.locator('#goal-company [data-gco-history]').click();
      await page.locator('#goal-company [data-gco-event]').first().waitFor();
      const events = await page.locator('#goal-company [data-gco-event]').allInnerTexts();
      assert.equal(events.length, 2);
      assert.match(events.join('\n'), /Vision changed by Ana Silva/);
      assert.match(events.join('\n'), /Description changed by Ana Silva/);
      assert.ok(await page.locator('#goal-company [data-gco-event] time[datetime]').count() === 2, 'each change says when');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Spec test 2 (v3): an admin or a member reads both, with no Edit and no form; History is theirs to read.
    for (const role of ['admin', 'member']) {
      const {page, errors} = await open(browser, {role,
        company: {vision: 'Every small landlord runs their rentals on Tico.', description: 'Bots that run rentals.'},
        history: [{id: 'e1', ts: new Date(Date.now() - 3600000).toISOString(), actor: 'human:ana', actor_name: 'Ana Silva', field: 'vision', old: '', new: 'Every small landlord runs their rentals on Tico.'}]});
      const section = page.locator('#goal-company');
      await section.waitFor();
      assert.equal(await section.locator('[data-gco-vision]').innerText(), 'Every small landlord runs their rentals on Tico.');
      assert.equal(await section.locator('[data-gco-description]').innerText(), 'Bots that run rentals.');
      assert.equal(await section.locator('[data-gco-edit]').count(), 0);
      assert.equal(await section.locator('textarea').count(), 0);
      await section.locator('[data-gco-history]').click();
      await section.locator('[data-gco-event]').waitFor();
      assert.match(await section.locator('[data-gco-event]').innerText(), /Vision changed by Ana Silva · 1h ago/);
      // A member sees the CEO Bot's proposal on a goal but has no buttons for it.
      const panel = await panelFor(page, 'company');
      assert.equal(await panel.locator('[data-why-prop] [data-why-accept]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Spec test 4: "Not tied to the vision" until a Why is accepted; accepted as it is, or edited.
    {
      const {page, errors, posted} = await open(browser, {company: {vision: 'Every landlord on Tico.', description: ''}});
      assert.match(await page.locator('#goal-tree .gt-row[data-goal="g-top"] .gt-goal').getAttribute('title'), /Why: Not tied to the vision/);
      let panel = await panelFor(page, 'company');
      const why = panel.locator('[data-why="g-top"]');
      assert.equal(await why.innerText(), 'Not tied to the vision', 'a note, not an error, and no Write button while a proposal waits');
      assert.equal(await why.locator('.err').count(), 0);
      assert.match(await panel.locator('[data-why-prop="p-why"]').innerText(), /CEO Bot proposes More rentals is the vision's first step\./);
      assert.equal(await panel.locator('.kpi-props [data-proposal="p-why"]').count(), 0, 'shown once, as the Why line');
      // Edit, then accept as edited.
      await panel.locator('[data-why-prop="p-why"] [data-why-edit]').click();
      const input = panel.locator('[data-why-form="g-top"] input[name="why"]');
      assert.equal(await input.inputValue(), "More rentals is the vision's first step.");
      await input.fill('More rentals put more landlords on Tico.');
      await panel.locator('[data-why-form="g-top"] button[type="submit"]').click();
      await panel.locator('[data-why="g-top"] [data-why-text]').waitFor();
      assert.deepEqual(posted.at(-1), ['decide', 'p-why', {decision: 'confirm', why: 'More rentals put more landlords on Tico.'}]);
      assert.equal(await panel.locator('[data-why="g-top"] [data-why-text]').innerText(), 'More rentals put more landlords on Tico.');
      assert.equal(await panel.locator('[data-why-prop]').count(), 0);
      await panel.locator('[data-gp-close]').click();
      await page.locator('#goal-panel[open]').waitFor({state: 'detached'});
      // A goal that has a Why shows it, and the owner may rewrite it by hand.
      panel = await panelFor(page, 'bot:cmo');
      assert.equal(await panel.locator('[data-why="g-cmo"] [data-why-text]').innerText(), 'More landlords find Tico on their own.');
      await panel.locator('[data-why="g-cmo"] [data-why-edit]').click();
      await panel.locator('[data-why-form="g-cmo"] input[name="why"]').fill('');
      await panel.locator('[data-why-form="g-cmo"] button[type="submit"]').click();
      await panel.locator('[data-why="g-cmo"] [data-why-none]').waitFor();
      assert.deepEqual(posted.at(-1), ['why', 'g-cmo', {why: ''}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Accepted as it is.
    {
      const {page, errors, posted} = await open(browser, {company: {vision: 'Every landlord on Tico.', description: ''}});
      const panel = await panelFor(page, 'company');
      await panel.locator('[data-why-prop="p-why"] [data-why-accept]').click();
      await panel.locator('[data-why="g-top"] [data-why-text]').waitFor();
      assert.deepEqual(posted.at(-1), ['decide', 'p-why', {decision: 'confirm'}]);
      assert.equal(await panel.locator('[data-why="g-top"] [data-why-text]').innerText(), "More rentals is the vision's first step.");
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The owner reads one line; the CEO Bot replaces it before they click. Accept on the first is refused (nothing
    // is saved as them) and the panel shows the line now waiting, which they accept; an edit typed against the old
    // line keeps their words when it is refused.
    {
      const {page, errors, posted, state} = await open(browser, {company: {vision: 'Every landlord on Tico.', description: ''}});
      const panel = await panelFor(page, 'company');
      await panel.locator('[data-why-prop="p-why"] [data-why-accept]').waitFor();
      const newer = replaceWhy(state, 'g-top', 'Landlords who rent more stay on Tico.');
      await panel.locator('[data-why-prop="p-why"] [data-why-accept]').click();
      await panel.locator(`[data-why-prop="${newer.id}"]`).waitFor();
      assert.deepEqual(posted.at(-1), ['decide', 'p-why', {decision: 'confirm'}]);
      assert.equal(state.goals[0].why, '', 'the replaced line is never saved');
      assert.equal(await panel.locator('[data-why="g-top"] [data-why-none]').count(), 1);
      assert.match(await page.locator('.toast.err').last().innerText(), /replaced by a newer one/);
      // Edited against the newer line, which is replaced again meanwhile: refused, and the typed words stay.
      await panel.locator(`[data-why-prop="${newer.id}"] [data-why-edit]`).click();
      await panel.locator('[data-why-form="g-top"] input[name="why"]').fill('Our own line.');
      const newest = replaceWhy(state, 'g-top', 'A third line.');
      await panel.locator('[data-why-form="g-top"] button[type="submit"]').click();
      await page.waitForFunction(id => !!document.querySelector(`#goal-panel [data-why-form="g-top"][data-why-prop="${id}"]`), newest.id);
      assert.equal(await panel.locator('[data-why-form="g-top"] input[name="why"]').inputValue(), 'Our own line.');
      await panel.locator('[data-why-form="g-top"] button[type="submit"]').click();
      await panel.locator('[data-why="g-top"] [data-why-text]').waitFor();
      assert.deepEqual(posted.at(-1), ['decide', newest.id, {decision: 'confirm', why: 'Our own line.'}]);
      assert.equal(state.goals[0].why, 'Our own line.');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Needs you (backend/goal_asks.py): the vision ask and the bundled Why ask read as themselves, not as empty KPI
    // rows; the vision row takes the owner to the Vision field, a goal in the Why row opens its panel at that goal.
    {
      const needs = [{kind: 'vision', task: {id: 't-vision', title: 'Tell your CEO Bot what this company is for', status: 'open'}},
        {kind: 'why', goals: [{goal_id: 'g-top', goal_title: 'Grow rentals 30%', why: 'x'}, {goal_id: 'g-cmo', goal_title: 'Double organic signups', why: 'y'}],
         task: {id: 't-why', title: 'These goals need a Why line', status: 'open'}}];
      const {page, errors} = await open(browser, {needs});
      const rows = page.locator('#goal-needs li');
      assert.equal(await rows.count(), 2);
      assert.match(await rows.nth(0).innerText(), /^Tell your CEO Bot what this company is for/);
      assert.match(await rows.nth(1).innerText(), /These goals need a Why line: Grow rentals 30%, Double organic signups/);
      assert.equal(await page.locator('#goal-needs [data-kpi]').count(), 0);
      await rows.nth(0).click();
      assert.equal(await page.evaluate(() => document.activeElement?.name), 'vision');
      await rows.nth(1).locator('[data-need-why-goal="g-cmo"]').click();
      await page.locator('#goal-panel[open] .gp-body').waitFor();
      assert.match(await page.locator('#goal-panel').innerText(), /Double organic signups/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Level 2 (spec test 8): the CEO Bot's recovery for a red goal shows its evidence, each option in plain words with
    // its pick marked and what can't happen now; Approve on an option sends exactly that option.
    {
      const recovery = {id: 'p-rec', kind: 'recovery', goal_id: 'g-cmo', kpi_id: null, status: 'pending', reason: 'Red two weeks running.',
        proposed_by: 'bot:goal-manager', proposed_at: new Date().toISOString(),
        payload: {evidence: 'Signups 40 against 120.', pick: 1, options: [{label: 'Pause the newsletter', actions: []}, {label: 'Push SEO first', actions: []}]}};
      const needs = [{kind: 'recovery', ref: 'p-rec', owner: 'human:ana', proposal: recovery, goal_id: 'g-cmo', goal_title: 'Double organic signups',
        task: {id: 't-rec'}, options: [
          {label: 'Pause the newsletter', pick: false, actions: [{text: 'Move "Weekly newsletter" to the bottom', note: 'Head of Marketing is paused right now'}]},
          {label: 'Push SEO first', pick: true, actions: [{text: 'New task for Head of Marketing: "Fix the pricing page SEO"', note: ''}]}]}];
      const {page, errors, posted, state} = await open(browser, {needs});
      state.proposals.push(recovery);
      const row = page.locator('#goal-needs li.rec');
      const text = await row.innerText();
      assert.match(text, /A recovery · Double organic signups/);
      assert.match(text, /Signups 40 against 120\./);
      assert.match(text, /Push SEO first · the CEO Bot's pick/);
      assert.match(text, /Head of Marketing is paused right now; this part would come back to you/);
      assert.equal(await row.locator('[data-option="1"]').getAttribute('class'), 'primary');
      await Promise.all([page.waitForResponse(r => r.url().endsWith('/proposals/p-rec/decide')), row.locator('[data-option="0"]').click()]);
      assert.deepEqual(posted.find(x => x[0] === 'decide'), ['decide', 'p-rec', {decision: 'confirm', option: 0}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('goals-company: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
