// Goal suggestion cards say what yes does (ui/goals-kpis.js goalPropHtml, propDoHtml, propNowProposedHtml; spec "Clear
// Strategy Bot suggestion cards"): Add a KPI names the measure, says what it counts, its target or "No target yet" and
// a
// missing data source, and folds away the goal editor's target fields; each yes button says what it does, the no button
// reads "No, leave it", a Flag has one "Got it", and new wording or a new definition reads Now and Proposed.
// "No, leave it" rejects in one click, then the card reads "Left as it is. Tell it why? (optional)" with a
// one-line box and Send: Send saves the reason (POST .../note), a refusal shows beside the box, leaving it does nothing.
// Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const screenshotDir = process.env.TICO_SCREENSHOT_DIR;

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('coo', 'Assistant', ''), bot('botops', 'BotOps', ''), bot('librarian', 'Librarian', ''),
  bot('goal-manager', 'Strategy Bot', 'p:ana', {reports_to: ''}), bot('cmo', 'Head of Marketing', 'p:ana')];
const GOAL = 'Double organic signups';
const LONG = 'Signed-up accounts that finish setup, counted once per account, in the week the last setup step is done, '.repeat(8);   // long enough to wrap past four lines on Goals' full width (no rail beside it)
const prop = (id, kind, payload, card, over) => ({id, kind, goal_id: 'g-cmo', kpi_id: null, payload, reason: 'No measure on this goal yet',
  proposed_by: 'bot:goal-manager', proposed_at: new Date().toISOString(), status: 'pending', card: {goal_title: GOAL, ...card}, ...over});
const measure = (name, over) => ({name, definition: '', source_note: '', unit: '', direction: 'up', existing: false, ...over});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}, serviceWorkers: 'block'});
    const errors = [], posted = [], noted = [];
    page.on('pageerror', e => errors.push(e.message));
    const needs = [
      prop('p-new', 'goal_kpi', {kpi: {name: 'Weekly demos', definition: 'Demos held, per week'}},
        {measure: measure('Weekly demos', {definition: 'Demos held, per week'}), target_label: ''}),
      prop('p-reuse', 'goal_kpi', {kpi_id: 'k-act'},
        {measure: measure('Activation', {definition: 'Signed-up accounts that finish setup', source_note: 'Product database', unit: '%', existing: true}), target_label: ''}),
      prop('p-hidden', 'goal_kpi', {kpi_id: 'k-secret'},
        {measure: measure('a measure you cannot see', {hidden: true, existing: true}), target_label: ''}),
      prop('p-blank', 'goal_kpi', {kpi: {name: 'Trials started'}}, {measure: measure('Trials started'), target_label: ''}),
      prop('p-tgt', 'kpi_target', {kind: 'improve', target: 60, deadline: '2027-12-31'}, {kpi_name: 'Activation', now_label: '→ 65% by Dec 31', target_label: '→ 60% by Dec 31, 2027'}, {kpi_id: 'k-act'}),
      prop('p-def', 'kpi_definition', {definition: 'Finish setup within 7 days'},
        {kpi_name: 'Activation', changes: [{label: 'Definition', now: 'Signed-up accounts that finish setup', proposed: 'Finish setup within 7 days'}]}, {goal_id: null, kpi_id: 'k-act'}),
      prop('p-word', 'goal_wording', {title: 'Double organic signups by June'},
        {changes: [{label: 'Title', now: GOAL, proposed: 'Double organic signups by June'}, {label: 'Description', now: '', proposed: LONG}]}),
      prop('p-flag', 'flag', {issue: 'duplicate', note: 'Same words as Grow signups'}, {}),
    ].map(p => ({kind: 'proposal', proposal: p, goal_title: GOAL, kpi_name: p.card.kpi_name || null, task: {id: 't-' + p.id, title: 'x', status: 'open'}}));
    const goals = [{id: 'g-cmo', title: GOAL, owner_id: 'bot:cmo', parent_id: null, status: 'yellow', status_source: 'auto', rank: 0, kpis: [], why: 'More landlords find us.'}];
    const panelProp = {...prop('p-panel', 'goal_kpi', {kpi: {name: 'Signups'}, target: {kind: 'maintain', min: 40}},
      {measure: measure('Signups', {definition: 'New accounts', source_note: 'Stripe'}), target_label: 'range 40+'}), may_decide: true};
    // A recovery is out of this card's scope: in a goal's panel it keeps the recovery card's words.
    const recovery = {...prop('p-rec', 'recovery', {pick: 0, options: [{label: 'Move the demo task to Sales'}]}, {}), may_decide: true};
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/v2/events') return;
      if (p === '/api/employees') return json(bots);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@example.test', role: 'owner', mover: true, cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}]});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
      if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: [], proposals: [...posted.some(x => x.id === 'p-panel') ? [] : [panelProp], recovery]});
      if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: needs});
      const decide = p.match(/^\/api\/v2\/proposals\/([^/]+)\/decide$/);
      if (decide) {
        posted.push({id: decide[1], body: req.postDataJSON()});
        const at = needs.findIndex(i => i.proposal.id === decide[1]);
        if (at >= 0) needs.splice(at, 1);
        // The server offers a reason to whoever turned it down, unless it is about a bot they may not read.
        const no = req.postDataJSON().decision === 'reject';
        return json({proposal: {id: decide[1], status: no ? 'rejected' : 'confirmed', decision_note: '', may_reason: no && decide[1] !== 'p-hidden'}});
      }
      const note = p.match(/^\/api\/v2\/proposals\/([^/]+)\/note$/);
      if (note) {
        noted.push({id: note[1], body: req.postDataJSON()});
        if (note[1] === 'p-tgt') return route.fulfill({status: 422, contentType: 'application/json', body: JSON.stringify({error: {code: 'duplicate', detail: 'It already has a reason; a reason is added once'}})});
        return json({proposal: {id: note[1], status: 'rejected', decision_note: req.postDataJSON().note, may_reason: false}});
      }
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/goals');
    await page.locator('#goal-needs .kpi-prop').first().waitFor();
    const card = id => page.locator(`#goal-needs [data-proposal="${id}"]`);
    const buttons = id => card(id).locator('.kpi-prop-do button').allInnerTexts();
    const decided = async id => { for (let n = 0; n < 80 && !posted.some(x => x.id === id); n++) await page.waitForTimeout(50); return posted.find(x => x.id === id)?.body; };

    // No goal suggestion card reads Confirm or Reject; each yes says what it does, each no is "No, leave it".
    assert.doesNotMatch(await page.locator('#goal-needs').innerText(), /\b(Confirm|Reject)\b/);
    assert.deepEqual(await buttons('p-new'), ['Add measure', 'No, leave it']);
    assert.deepEqual(await buttons('p-tgt'), ['Use this target', 'No, leave it']);
    assert.deepEqual(await buttons('p-def'), ['Use new definition', 'No, leave it']);
    assert.deepEqual(await buttons('p-word'), ['Use new wording', 'No, leave it']);
    assert.deepEqual(await buttons('p-flag'), ['Got it']);

    // Add a KPI: the measure and the goal named, what it counts on one line, then "No target yet", and no data source.
    const added = await card('p-new').innerText();
    assert.match(added, /Add the measure Weekly demos to Double organic signups\s+Demos held, per week\s+No target yet/);
    assert.match(added, /No data source yet: it will show no numbers until one is set/);
    // A reused measure is named with what it counts, never "existing KPI"; it has a source, so no warning.
    const reused = await card('p-reuse').innerText();
    assert.match(reused, /Add the measure Activation to Double organic signups\s+Signed-up accounts that finish setup/);
    assert.doesNotMatch(reused, /existing KPI|No data source/);
    // A hidden reused measure must not be described as missing a source or definition.
    const hidden = await card('p-hidden').innerText();
    assert.match(hidden, /Add the measure a measure you cannot see to Double organic signups/);
    assert.doesNotMatch(hidden, /What it counts is not written down yet|No data source/);
    // Nobody adds a measure they may not read (the server refuses it): only no, and no target fields.
    assert.deepEqual(await buttons('p-hidden'), ['No, leave it']);
    assert.equal(await card('p-hidden').locator('details.kpi-prop-set').count(), 0);
    // The goal editor's target fields, folded away under "Set a target (optional)".
    const set = card('p-new').locator('details.kpi-prop-set');
    assert.equal(await set.locator('summary').innerText(), 'Set a target (optional)');
    assert.equal(await set.evaluate(d => d.open), false, 'folded until opened');
    assert.equal(await card('p-new').locator('select[name=kind]').isVisible(), false);

    // New definition and new wording read Now and Proposed; long text is cut to four lines until "Show all".
    assert.match(await card('p-def').innerText(), /Now\s+Signed-up accounts that finish setup\s+Proposed\s+Finish setup within 7 days/);
    const word = card('p-word');
    assert.match(await word.innerText(), /Title\s+Now\s+Double organic signups\s+Proposed\s+Double organic signups by June\s+Description\s+Now\s+Empty/);
    const longCell = word.locator('.prop-np-v').last();
    const cut = await longCell.evaluate(el => el.scrollHeight > el.clientHeight + 1);
    assert.ok(cut, 'long text is clamped');
    await word.locator('[data-np-all]').click();
    assert.equal(await longCell.evaluate(el => el.scrollHeight > el.clientHeight + 1), false, 'Show all opens it');
    assert.match(await card('p-tgt').innerText(), /New target for Activation on Double organic signups\s+Now\s+→ 65% by Dec 31\s+Proposed\s+→ 60% by Dec 31, 2027/);
    // A Flag says in one line what the Strategy Bot noticed.
    assert.match(await card('p-flag').innerText(), /Strategy Bot noticed: Double organic signups looks like a duplicate of another goal: Same words as Grow signups/);
    if (screenshotDir) await page.locator('#goal-needs').screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-suggestion-cards.png')});

    // Fields filled in: "Add measure" adds it with that target.
    await set.locator('summary').click();
    assert.equal(await card('p-new').locator('select[name=kind]').inputValue(), 'improve');
    await card('p-new').locator('input[name=target]').fill('25');
    await card('p-new').locator('input[name=deadline]').fill('2099-12-31');
    if (screenshotDir) await card('p-new').screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-suggestion-card-target.png')});
    await card('p-new').locator('[data-prop-yes]').click();
    assert.deepEqual(await decided('p-new'), {decision: 'confirm', target: {kind: 'improve', baseline: null, target: 25, deadline: '2099-12-31'}});
    // Opened but left blank, or never opened: added with no target (the card said so).
    await card('p-reuse').locator('details.kpi-prop-set summary').click();
    await card('p-reuse').locator('[data-prop-yes]').click();
    assert.deepEqual(await decided('p-reuse'), {decision: 'confirm'});
    await card('p-blank').locator('[data-prop-yes]').click();
    assert.deepEqual(await decided('p-blank'), {decision: 'confirm'});
    // "Got it" settles the flag as confirmed, and asks no reason.
    await card('p-flag').locator('[data-prop-yes]').click();
    assert.deepEqual(await decided('p-flag'), {decision: 'confirm'});
    // "No, leave it" rejects in one click, no reason asked first; then the card is one line, a box and Send.
    await card('p-def').locator('[data-decide=reject]').click();
    assert.deepEqual(await decided('p-def'), {decision: 'reject'});
    const left = card('p-def').locator('[data-prop-left]');
    await left.waitFor();
    assert.match(await card('p-def').innerText(), /^Left as it is\. Tell it why\? \(optional\)/);
    assert.equal(await left.locator('input[type=text][name=note]').count(), 1);
    assert.deepEqual(await left.locator('button').allInnerTexts(), ['Send']);
    assert.equal(await card('p-def').locator('[data-decide]').count(), 0, 'the yes and no buttons are gone');
    if (screenshotDir) await card('p-def').screenshot({animations: 'disabled', path: path.join(screenshotDir, 'goals-suggestion-card-left.png')});
    // An empty box sends nothing: the rejection stands with no note.
    await left.locator('input[name=note]').fill('   ');
    await left.locator('button[type=submit]').click();
    await page.waitForTimeout(150);
    assert.deepEqual(noted, []);
    // A reason typed and sent is saved as the suggestion's note; the card then goes with the list drawn again.
    await left.locator('input[name=note]').fill("no billing yet, don't track revenue");
    await left.locator('input[name=note]').press('Enter');
    for (let n = 0; n < 80 && !noted.length; n++) await page.waitForTimeout(50);
    assert.deepEqual(noted, [{id: 'p-def', body: {note: "no billing yet, don't track revenue"}}]);
    await card('p-def').waitFor({state: 'detached'});
    // Left alone, the card stays as it is and nothing more is sent.
    await card('p-word').locator('[data-decide=reject]').click();
    assert.deepEqual(await decided('p-word'), {decision: 'reject'});
    await card('p-word').locator('[data-prop-left]').waitFor();
    await page.waitForTimeout(150);
    assert.equal(noted.length, 1);
    // A refusal from the server is shown to the person, beside the box they can still use.
    await card('p-tgt').locator('[data-decide=reject]').click();
    await card('p-tgt').locator('[data-prop-left] input[name=note]').fill('We set targets each quarter');
    await card('p-tgt').locator('[data-prop-left] button[type=submit]').click();
    const err = card('p-tgt').locator('.kpi-prop-left-err');
    await err.waitFor();
    assert.equal(await err.innerText(), 'It already has a reason; a reason is added once');
    assert.equal(await card('p-tgt').locator('[data-prop-left] button[type=submit]').isDisabled(), false);
    // About a bot's measure she may not read: rejected, and no box (the server says she may not add its reason).
    await card('p-hidden').locator('[data-decide=reject]').click();
    assert.deepEqual(await decided('p-hidden'), {decision: 'reject'});
    await card('p-hidden').waitFor({state: 'detached'});

    // A goal's panel draws the same card; a proposed target shows, its fields folded under "Change the target".
    await page.locator('#goal-tree .gt-row[data-owner="bot:cmo"]:not(.gt-cont) .gt-name').click();
    const inPanel = page.locator('#goal-panel[open] [data-proposal="p-panel"]');
    await inPanel.waitFor();
    assert.match(await inPanel.innerText(), /Add the measure Signups to Double organic signups\s+New accounts\s+Target range 40\+/);
    assert.doesNotMatch(await inPanel.innerText(), /No data source|No target yet/);
    assert.equal(await inPanel.locator('details.kpi-prop-set summary').innerText(), 'Change the target');
    assert.deepEqual(await page.locator('#goal-panel[open] [data-proposal="p-rec"] .kpi-prop-do button').allInnerTexts(), ['Approve', 'Reject']);
    assert.equal(await page.locator('#goal-panel[open] [data-proposal="p-rec"] [data-prop-reason]').count(), 0, 'a recovery keeps its own Reject');
    await inPanel.locator('[data-prop-yes]').click();
    assert.deepEqual(await decided('p-panel'), {decision: 'confirm'});
    assert.deepEqual(errors, []);
    console.log('goals-suggestion-cards: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
