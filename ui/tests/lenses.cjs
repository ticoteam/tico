// The Lenses tab (ui/lenses-page.js) against a stand-in for /api/v2/lenses and the strategy it reads (fixtures only, the
// Acme example): the owner sees the listed lenses in Strategy and Market groups with Run, a member the same rows without
// it; a run asks the steps one at a time (a list adds rows on Enter, the scores grid is built from the earlier answers,
// Back keeps what was typed, a skipped step is left out of the answers) and goes to the run page, which polls until done and
// draws the value curve (Us thick and in the accent, end labels that never overlap, a legend), the 2x2, the tables and the
// cascade; Add to draft sends the draft's revision and a 409 reloads the run; Ask (to sharpen a finding) files once; a draft run
// carries its chip, and a member cannot see one; failed, no-model and nothing-published runs say so; no page scrolls sideways
// at 400px.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots (desktop in both themes, and a phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');

const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page, name, themes = ['dark', 'light']) => {
  if (!shots) return;
  for (const theme of themes) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.screenshot({animations: 'disabled', path: path.join(shots, `lenses-${name}-${theme}.png`)});
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
};
const PHONE = {width: 400, height: 844}, DESKTOP = {width: 1280, height: 900};

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('coo', 'Assistant', ''), bot('botops', 'BotOps', ''), bot('librarian', 'Librarian', ''), bot('goal-manager', 'Strategy Bot', 'p:ana', {reports_to: ''})];
const bets = () => [
  {id: 'b-price', statement: 'Price at half the market and still make money.', why: '', rules_out: 'Premium add-ons.',
   wrong_if: {kind: 'text', text: 'Margin under 25%', check_by: '2027-01-31'}, cites: [], status: 'active', rank: 0, since_n: 1, until_n: null},
  {id: 'b-offices', statement: 'Sell to small offices through property managers.', why: '', rules_out: 'Ads to tenants.',
   wrong_if: {kind: 'text', text: 'Fewer than 5 managers signed', check_by: '2027-02-01'}, cites: [], status: 'active', rank: 1, since_n: 1, until_n: null},
  {id: 'b-old', statement: 'Clean only offices.', why: '', rules_out: '', wrong_if: null, cites: [], status: 'retired', rank: 2, since_n: null, until_n: 1},
];

const step = (key, ask, input, over = {}) => ({key, ask, input, ...over});
const LENSES = [
  {id: 'playing-to-win', name: 'Playing to Win', applies_to: 'strategy', description: 'In the style of Playing to Win: where to play and how to win.', minutes: 15,
   steps: [step('aspiration', 'What does winning look like?', 'text')], output: {chart: 'cascade', sections: []}, sharpen: []},
  {id: 'blue-ocean', name: 'Blue Ocean', applies_to: 'strategy', description: 'In the style of Blue Ocean Strategy: what to eliminate, reduce, raise and create.', minutes: 10,
   steps: [step('rivals', 'Who do buyers compare you to today?', 'list', {hint: 'Include "do nothing" if that is the real alternative.'}),
     step('factors', 'What do buyers weigh when they choose?', 'list'),
     step('scores', 'Score you and each rival 0-5 on each factor.', 'scores', {rows_from: 'factors', columns_from: 'rivals'}),
     step('horizon', 'How far ahead are you planning?', 'choice', {options: ['One year', 'Three years', 'Five years']})],
   output: {chart: 'value_curve', sections: []}, sharpen: []},
  {id: 'scenarios', name: 'Scenarios', applies_to: 'strategy', description: 'In the style of scenario planning: four worlds from two uncertainties.', minutes: 12,
   steps: [step('u1', 'The first uncertainty, with its two ends?', 'text')], output: {chart: 'matrix_2x2', sections: []}, sharpen: []},
  {id: 'where-to-play', name: 'Where to play', applies_to: 'market', description: 'Which two or three segments to serve first.', minutes: 8,
   steps: [step('segments', 'Which segments could you serve?', 'text')], output: {chart: 'table', sections: []}, sharpen: []},
];

const curve = {kind: 'value_curve', factors: ['Price', 'Setup time', 'Support', 'Reporting', 'Integrations'], series: [
  {name: 'Us', scores: [4, 5, 3, 1, 2]}, {name: 'Acme Clean', scores: [2, 2, 4, 5, 5]}, {name: 'Doing nothing', scores: [5, 1, 0, 0, 0]},
  {name: 'Freelancer', scores: [4, 2, 1, 0, 0]}, {name: 'Example Co', scores: [3, 3, 3, 3, 4]}]};
const matrix = {kind: 'matrix_2x2', x: {name: 'Pace of rules', low: 'Slow', high: 'Fast'}, y: {name: 'Buyer budgets', low: 'Shrinking', high: 'Growing'}, cells: [
  {q: 'll', title: 'Hold on', text: 'Slow rules, shrinking budgets.'}, {q: 'lh', title: 'Grow quietly', text: 'Slow rules, growing budgets.'},
  {q: 'hl', title: 'Brace', text: 'Fast rules, shrinking budgets.'}, {q: 'hh', title: 'Race', text: 'Fast rules, growing budgets.'}]};
const power = {kind: 'power_table', rows: [
  {bet_id: 'b-price', bet: 'Price at half the market.', power: 'process', how: 'Bots run the office.', when: 'Now'},
  {bet_id: 'b-offices', bet: 'Sell through property managers.', power: 'none', how: '', when: ''}]};
const cascade = {kind: 'cascade', steps: [{title: 'Aspiration', text: 'Every home in Austin.', weak: false}, {title: 'Where to play', text: 'Too wide.', weak: true}]};
const table = {kind: 'table', columns: ['Segment', 'Size', 'Fit'], rows: [['Small offices', '4,200', 'High'], ['Homes', '90,000', 'Medium']]};

const done = (id, lens, over = {}, result = {}) => ({id, lens, name: LENSES.find(l => l.id === lens)?.name || lens, applies_to: LENSES.find(l => l.id === lens)?.applies_to || 'strategy',
  on_draft: false, strategy_n: 1, status: 'done', answers: {rivals: ['Acme Clean']}, applied: [], sharpen_tasks: {}, proposal_id: null, created_by: 'human:ana',
  created_at: '2026-10-24T09:00:00Z', finished_at: '2026-10-24T09:01:00Z', error: null,
  result: {summary: 'Acme wins on price and setup, and loses on reporting.', sections: [{title: 'The curve today', body: 'Rivals lead on **reporting**.\n\n- Price: we lead\n- Reporting: they lead'}],
    chart: curve, findings: [{kind: 'challenges', bet_id: 'b-price', text: 'Two rivals already score 5 on price.'}, {kind: 'supports', bet_id: 'b-offices', text: 'Nobody serves managers.'}, {kind: 'gap', bet_id: null, text: 'No data on churn.'}],
    assumptions: ['Rivals prices assumed from their websites.'],
    changes: [{label: "Add 'per-seat pricing' to What we won't do", payload: {sections: {not: 'No per-seat pricing.'}}}, {label: 'Add a Reporting bet', payload: {}}],
    sharpen: ['Competitor pricing for Acme Clean', 'Review-site complaints'], ...result}, ...over});

async function open(browser, {role = 'owner', route = '#/strategy/lenses', viewport = DESKTOP, published = true, runs = [], noPublished = false, slow = false} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', locale: 'en-GB'});
  // The clock is fixed in October: a run the stub starts is stamped with it, and the page shows its date ("27 Oct").
  await page.clock.install({time: new Date('2026-10-27T12:00:00Z')});
  const errors = [], sent = [];
  page.on('pageerror', e => errors.push(e.message));
  const owner = role === 'owner';
  const st = {revision: 7, runs: runs.map(r => ({...r})), gets: {}, conflictOnce: true, tasks: 0};
  const v1 = {id: 'v-1', n: 1, status: 'published', summary: 'Acme cleans homes and small offices.', vision: '', sections: {}, bets: bets().filter(b => b.status !== 'retired'), revision: 7};
  const strategy = () => ({...(owner ? {draft: {...v1, id: 'v-d', n: null, status: 'draft', revision: st.revision}} : {}), current: published ? v1 : null,
    bets: owner ? bets() : v1.bets, label: 'Bets', label_one: 'Bet', may_edit: owner});
  const visible = r => owner || !r.on_draft;
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/me') return json({id: owner ? 'ana' : 'cara', name: owner ? 'Ana Silva' : 'Cara Diaz', email: 'x@example.test', role: owner ? 'owner' : 'viewer', company_role: role, cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}, {id: 'cara', name: 'Cara Diaz', org_parent: 'p:ana'}], org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/strategy' && method === 'GET') return json(strategy());
    if (p === '/api/v2/lenses') return json({lenses: LENSES});
    if (p === '/api/v2/lenses/runs') return json({runs: st.runs.filter(visible)});
    const one = p.match(/^\/api\/v2\/lenses\/runs\/([^/]+)$/);
    if (one) {
      const run = st.runs.find(r => r.id === one[1]);
      if (!run || !visible(run)) return json({error: {code: 'not_found', detail: 'No such run'}}, 404);
      // A run that is running answers once more as running, then as done (the page polls every two seconds).
      st.gets[run.id] = (st.gets[run.id] || 0) + 1;
      if (run.status === 'running' && st.gets[run.id] > 1) Object.assign(run, done(run.id, run.lens, {answers: run.answers, on_draft: run.on_draft, created_at: run.created_at}));
      return json(run);
    }
    const start = p.match(/^\/api\/v2\/lenses\/([^/]+)\/runs$/);
    if (start && method === 'POST') {
      const body = req.postDataJSON();
      sent.push(['POST', `/lenses/${start[1]}/runs`, body]);
      if (!owner) return json({error: {code: 'forbidden', detail: 'Only the owner'}}, 403);
      if (body.on === 'published' && (!published || noPublished)) return json({error: {code: 'no_published', detail: 'Nothing is published'}}, 422);
      const lens = LENSES.find(l => l.id === start[1]);
      const run = {id: 'r-new' + st.runs.length, lens: lens.id, name: lens.name, applies_to: lens.applies_to, on_draft: body.on === 'draft', strategy_n: body.on === 'draft' ? null : 1,
        status: slow ? 'no_model' : 'running', answers: body.answers, result: null, applied: [], sharpen_tasks: {}, proposal_id: null, created_by: 'human:ana', created_at: '2026-10-27T12:00:00.000Z', finished_at: null, error: null};
      st.runs.unshift(run);
      return json(run, 202);
    }
    const apply = p.match(/^\/api\/v2\/lenses\/runs\/([^/]+)\/apply$/);
    if (apply) {
      const body = req.postDataJSON(), run = st.runs.find(r => r.id === apply[1]);
      sent.push(['POST', '/apply', body]);
      // The first write finds the draft changed elsewhere.
      if (st.conflictOnce) { st.conflictOnce = false; st.revision++; return json({error: {code: 'conflict', detail: 'The draft changed', revision: st.revision}}, 409); }
      if (body.revision !== st.revision) return json({error: {code: 'conflict', detail: 'The draft changed'}}, 409);
      run.applied = [...run.applied, body.index]; st.revision++;
      return json({run, strategy: strategy()});
    }
    const sharpen = p.match(/^\/api\/v2\/lenses\/runs\/([^/]+)\/sharpen\/(\d+)$/);
    if (sharpen) {
      const run = st.runs.find(r => r.id === sharpen[1]);
      sent.push(['POST', `/sharpen/${sharpen[2]}`, req.postDataJSON()]);
      if (!run.sharpen_tasks[sharpen[2]]) run.sharpen_tasks[sharpen[2]] = 'task-' + ++st.tasks;
      return json({run, task_id: run.sharpen_tasks[sharpen[2]]});
    }
    if (p === '/api/v2/kpis') return json({kpis: []});
    if (p === '/api/v2/goals/tree') return json({goals: [], owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: []});
    if (p === '/api/company-docs') return json({documents: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + route);
  return {page, errors, sent, st};
}

const sideways = page => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
const PAST = [done('r-blue', 'blue-ocean'), done('r-draft', 'scenarios', {on_draft: true, strategy_n: null}, {chart: matrix}),
  done('r-where', 'where-to-play', {strategy_n: null, created_by: 'bot:librarian'}, {chart: table, changes: [], sharpen: []})];

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // The owner sees the lenses in two groups, each with Run, and the past runs with their chips.
    {
      const {page, errors} = await open(browser, {runs: PAST});
      await page.locator('[data-lens="blue-ocean"]').waitFor();
      assert.equal(await page.locator('#main h1').innerText(), 'Strategy');
      assert.deepEqual(await page.locator('.strat-tabs a').allInnerTexts(), ['Market', 'Strategy', 'Org', 'Goals', 'Budget']);
      assert.equal(await page.locator('.strat-tabs a.cur').innerText(), 'Strategy', 'Lenses are a view of the Strategy tab');
      assert.equal(await page.locator('.strat-sub').innerText(), 'Strategy', 'the way back to the strategy');
      assert.equal(await page.locator('#side [data-nav="strategy"]').getAttribute('aria-current'), 'page');
      assert.deepEqual(await page.locator('[data-lens-group="strategy"] .lens-name').allInnerTexts(), ['Playing to Win', 'Blue Ocean', 'Scenarios']);
      assert.deepEqual(await page.locator('[data-lens-group="market"] .lens-name').allInnerTexts(), ['Where to play']);
      assert.equal(await page.locator('[data-lens="blue-ocean"] .lens-min').innerText(), '10 min');
      assert.equal(await page.locator('[data-lens-run]').count(), 4);
      const draft = page.locator('[data-lens-run-row="r-draft"]');
      assert.match(await draft.innerText(), /Scenarios[\s\S]*draft[\s\S]*Done[\s\S]*Acme wins on price/i);
      assert.equal(await page.locator('[data-lens-run-row="r-blue"] .pill', {hasText: 'draft'}).count(), 0);
      await shot(page, 'list');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A member reads the rows without Run, and sees only the runs the server shows them.
    {
      const {page, errors} = await open(browser, {role: 'member', runs: PAST});
      await page.locator('[data-lens="blue-ocean"]').waitFor();
      assert.equal(await page.locator('[data-lens-run]').count(), 0);
      assert.deepEqual(await page.locator('[data-lens-run-row]').evaluateAll(a => a.map(x => x.dataset.lensRunRow)), ['r-blue', 'r-where']);
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-draft');
      await page.locator('.err', {hasText: 'not here'}).waitFor();
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-blue');
      await page.locator('[data-lens-run-page]').waitFor();
      assert.equal(await page.locator('[data-lens-apply], [data-lens-sharpen]').count(), 0, 'only the owner adds or asks');
      assert.equal(await page.locator('[data-lens-change]').count(), 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A full run: lists, Back keeps words, the scores grid comes from earlier answers, a step is skipped, then it runs on the draft.
    {
      const {page, errors, sent} = await open(browser, {runs: PAST});
      await page.locator('[data-lens-run="blue-ocean"]').click();
      const dlg = page.locator('#lens-flow');
      await dlg.locator('.lens-ask', {hasText: 'Who do buyers compare you to'}).waitFor();
      assert.equal(await dlg.locator('.lens-n').innerText(), '1 of 4');
      assert.match(await dlg.locator('.lens-hint').innerText(), /do nothing/);
      assert.equal(await dlg.locator('[data-lens-back]').isDisabled(), true);
      assert.equal(await dlg.locator('[data-lens-on]').count(), 0, 'Run on is asked at the end');
      await dlg.locator('.lens-rows input').first().fill('Acme Clean');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Doing nothing');
      await page.keyboard.press('Enter');
      assert.equal(await dlg.locator('.lens-rows input').count(), 3, 'Enter adds a row');
      await page.keyboard.press('Enter');   // an empty last row moves on
      await dlg.locator('.lens-ask', {hasText: 'What do buyers weigh'}).waitFor();
      assert.deepEqual(await dlg.locator('[data-lens-rows] input').evaluateAll(a => a.map(x => x.value)), ['']);
      await dlg.locator('[data-lens-rows] input').fill('Price');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Setup time');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Reporting');
      await dlg.locator('[data-lens-next]').click();
      // Scores: rows are the factors, columns Us and the rivals.
      await dlg.locator('.lens-scores').waitFor();
      assert.deepEqual(await dlg.locator('.lens-scores thead th:not(:first-child)').allInnerTexts(), ['Us', 'Acme Clean', 'Doing nothing']);
      assert.deepEqual(await dlg.locator('.lens-scores tbody th').allInnerTexts(), ['Price', 'Setup time', 'Reporting']);
      const cell = (r, c) => dlg.locator(`.lens-scores input[data-r="${r}"][data-c="${c}"]`);
      await cell(0, 0).fill('4');
      await dlg.locator('[data-lens-next]').click();
      assert.match(await dlg.locator('[data-lens-error]').innerText(), /every column/i, 'a half-filled row is refused');
      assert.equal(await cell(0, 1).getAttribute('class'), 'bad');
      await cell(0, 1).fill('9'); await cell(0, 1).blur();
      assert.equal(await cell(0, 1).inputValue(), '5', 'a score is 0 to 5');
      await cell(0, 2).fill('3');
      await cell(2, 0).fill('1'); await cell(2, 1).fill('5'); await cell(2, 2).fill('0');
      // Back keeps the list, and coming forward again keeps the grid.
      await dlg.locator('[data-lens-back]').click();
      assert.deepEqual(await dlg.locator('[data-lens-rows] input').evaluateAll(a => a.map(x => x.value)), ['Price', 'Setup time', 'Reporting']);
      await dlg.locator('[data-lens-next]').click();
      assert.equal(await cell(0, 0).inputValue(), '4');
      await dlg.locator('[data-lens-next]').click();
      // The last step is skipped; the button says Run and Run on is offered with both choices.
      await dlg.locator('.lens-choices').waitFor();
      assert.equal(await dlg.locator('[data-lens-next]').innerText(), 'Run');
      assert.deepEqual(await dlg.locator('[data-lens-on]').allInnerTexts(), ['Published', 'Draft']);
      assert.equal(await dlg.locator('[data-lens-on="published"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await dlg.locator('[data-lens-skip]').innerText(), 'Skip, let the Assistant assume');
      await dlg.locator('.lens-pill', {hasText: 'Three years'}).click();
      await dlg.locator('[data-lens-back]').click();
      await dlg.locator('[data-lens-skip]').click();     // skips Scores: the grid's answer is dropped
      await dlg.locator('.lens-choices').waitFor();
      assert.equal(await dlg.locator('input[name="choice"]:checked').count(), 1, 'the choice was kept');
      await shot(page, 'flow');
      await dlg.locator('[data-lens-on="draft"]').click();
      await dlg.locator('[data-lens-next]').click();
      await page.locator('[data-lens-running]').waitFor();
      assert.equal(await page.locator('[data-lens-running]').innerText(), 'Running…');
      assert.deepEqual(sent[0], ['POST', '/lenses/blue-ocean/runs', {answers: {rivals: ['Acme Clean', 'Doing nothing'], factors: ['Price', 'Setup time', 'Reporting'], horizon: 'Three years'}, on: 'draft'}],
        'the skipped step is left out of the answers');
      assert.match(page.url(), /#\/strategy\/lenses\/r-new\d+$/);
      // Polling finishes it.
      await page.locator('.lens-summary').waitFor({timeout: 10000});
      assert.match(await page.locator('.lens-meta').innerText(), /^draft\s+\d+ Oct( \d{4})? · Ana Silva/);
      await page.locator('[data-lens-chart="value_curve"] .lens-curve').waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The scores step with the earlier steps skipped says so and lets the person skip; a market lens sends no "on".
    {
      const {page, errors, sent} = await open(browser, {runs: PAST});
      await page.locator('[data-lens-run="blue-ocean"]').click();
      const dlg = page.locator('#lens-flow');
      await dlg.locator('[data-lens-skip]').click();
      await dlg.locator('[data-lens-skip]').click();
      await dlg.locator('[data-lens-blocked]').waitFor();
      await dlg.locator('[data-lens-skip]').click();
      await dlg.locator('[data-lens-next]').click();
      await page.locator('[data-lens-run-page]').waitFor();
      assert.deepEqual(sent[0][2], {answers: {}, on: 'published'});
      await page.goto('http://tico-ui.test/#/strategy/lenses');
      await page.locator('[data-lens-run="where-to-play"]').click();
      await page.locator('#lens-flow textarea').fill('Small offices');
      assert.equal(await page.locator('#lens-flow [data-lens-on]').count(), 0, 'a market lens has no Run on');
      await page.locator('#lens-flow [data-lens-next]').click();
      await page.locator('[data-lens-run-page]').waitFor();
      assert.deepEqual(sent[1][2], {answers: {segments: 'Small offices'}});
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Nothing published: only Draft is offered. A publish that vanished meanwhile (422 no_published) offers the draft.
    {
      const {page, errors, sent} = await open(browser, {published: false});
      await page.locator('[data-lens-run="scenarios"]').click();
      const dlg = page.locator('#lens-flow');
      await dlg.locator('textarea').fill('Rules');
      // Scenarios has one step, so the Run on choice is already there and Run would submit.
      await dlg.locator('[data-lens-on]').first().waitFor();
      assert.deepEqual(await dlg.locator('[data-lens-on]').allInnerTexts(), ['Draft']);
      assert.equal(await dlg.locator('[data-lens-on="draft"]').getAttribute('aria-pressed'), 'true');
      await dlg.locator('[data-lens-next]').click();
      await page.locator('[data-lens-run-page]').waitFor();
      assert.deepEqual(sent.at(-1)[2], {answers: {u1: 'Rules'}, on: 'draft'});
      assert.deepEqual(errors, []);
      await page.close();
      const racy = await open(browser, {noPublished: true});
      await racy.page.locator('[data-lens-run="scenarios"]').click();
      const d2 = racy.page.locator('#lens-flow');
      await d2.locator('[data-lens-next]').click();
      await d2.locator('[data-lens-notice]').waitFor();
      assert.match(await d2.locator('[data-lens-notice]').innerText(), /Nothing is published/);
      assert.deepEqual(await d2.locator('[data-lens-on]').allInnerTexts(), ['Draft']);
      await d2.locator('[data-lens-next]').click();
      await racy.page.locator('[data-lens-run-page]').waitFor();
      assert.equal(racy.sent.at(-1)[2].on, 'draft');
      assert.deepEqual([...errors, ...racy.errors], []);
      await racy.page.close();
    }
    // The value curve and its run page; Add to draft with a 409; Ask once.
    {
      const {page, errors, sent} = await open(browser, {runs: PAST, route: '#/strategy/lenses/r-blue'});
      await page.locator('.lens-curve').waitFor();
      assert.match(await page.locator('.lens-meta').innerText(), /Oct.*Ana Silva · v1/);
      assert.equal(await page.locator('.lens-meta .pill').count(), 0, 'a published run has no chip');
      assert.equal(await page.locator('.lens-summary').innerText(), 'Acme wins on price and setup, and loses on reporting.');
      // Every series is a line over five factors on a 0-5 axis; Us is thicker and in the accent.
      assert.equal(await page.locator('.lens-series').count(), 5);
      assert.deepEqual(await page.locator('.lens-curve .lens-grid').count(), 6);
      assert.equal(await page.locator('.lens-series.us .lens-marks > g').count(), 5);
      const width = sel => page.locator(sel).evaluate(e => parseFloat(getComputedStyle(e).strokeWidth));
      assert.ok(await width('.lens-series.us .lens-line') > 2 * await width('.lens-series.s1 .lens-line') * 0.9);
      const stroke = sel => page.locator(sel).evaluate(e => getComputedStyle(e).stroke);
      const accent = await page.evaluate(() => { const e = document.createElement('i'); e.style.color = 'var(--accent)'; document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; });
      assert.equal(await stroke('.lens-series.us .lens-line'), accent);
      const others = new Set(await page.locator('.lens-series:not(.us) .lens-line').evaluateAll(a => a.map(e => getComputedStyle(e).stroke)));
      assert.equal(others.size, 4, 'the other lines are four distinct colours');
      assert.ok(!others.has(accent));
      assert.deepEqual(await page.locator('.lens-legend li').allInnerTexts(), ['Us', 'Acme Clean', 'Doing nothing', 'Freelancer', 'Example Co']);
      // End labels: Us always, and none within 13px of another (Doing nothing and Freelancer both end at 0).
      const ys = await page.locator('.lens-name-t').evaluateAll(a => a.map(e => [e.dataset.endLabel, parseFloat(e.getAttribute('y'))]));
      assert.ok(ys.some(([n]) => n === 'Us'));
      assert.ok(ys.length < 5 && ys.length >= 3, 'labels that would collide are left to the legend');
      for (const [i, [, a]] of ys.entries()) for (const [, b] of ys.slice(i + 1)) assert.ok(Math.abs(a - b) >= 13);
      await shot(page, 'run');
      // Sections are markdown; findings name the bet; the retired one is not counted.
      assert.equal(await page.locator('[data-lens-sec="section"] strong').innerText(), 'reporting');
      assert.deepEqual(await page.locator('.lens-finding').evaluateAll(a => a.map(e => [e.querySelector('.pill').textContent, e.querySelector('.lens-bet')?.textContent || ''])),
        [['Challenges', 'Bet 1'], ['Supports', 'Bet 2'], ['Gap', '']]);
      assert.equal(await page.locator('.lens-bet').first().getAttribute('href'), '#/strategy?bet=b-price');
      assert.equal(await page.locator('[data-lens-sec="assumptions"] li').innerText(), 'Rivals prices assumed from their websites.');
      // Add to draft: the first write meets a changed draft (409), the run is read again, the second sends the new revision.
      const add = page.locator('[data-lens-apply="0"]');
      await add.click();
      await page.locator('.toast', {hasText: 'The draft changed'}).waitFor();
      await page.locator('[data-lens-apply="0"]').waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/apply')[0], ['POST', '/apply', {index: 0, revision: 7}]);
      await page.locator('[data-lens-apply="0"]').click();
      await page.locator('[data-lens-change="0"] .lens-done', {hasText: 'Added'}).waitFor();
      assert.deepEqual(sent.filter(x => x[1] === '/apply')[1], ['POST', '/apply', {index: 0, revision: 8}]);
      assert.equal(await page.locator('[data-lens-change="0"] [data-lens-apply]').count(), 0);
      assert.equal(await page.locator('[data-lens-apply="1"]').count(), 1, 'the other change is still open');
      // Ask: once, and the button reads Ask (no helper named).
      assert.equal(await page.locator('[data-lens-sharpen="0"]').innerText(), 'Ask');
      await page.locator('[data-lens-sharpen="0"]').click();
      const asked = page.locator('[data-lens-sharpen-row="0"] .lens-done');
      await asked.waitFor();
      assert.match(await asked.innerText(), /^Asked/);
      assert.equal(await asked.locator('a').getAttribute('href'), '#/task/task-1');
      assert.equal(await page.locator('[data-lens-sharpen="0"]').count(), 0);
      assert.deepEqual(sent.filter(x => x[1].startsWith('/sharpen')), [['POST', '/sharpen/0', {}]]);
      assert.equal(await page.locator('[data-lens-sharpen="1"]').count(), 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A draft run carries its chip; the 2x2, the power table, the cascade and the table draw.
    {
      const runs = [done('r-draft', 'scenarios', {on_draft: true, strategy_n: null, proposal_id: 'p-1', created_by: 'bot:goal-manager'}, {chart: matrix}),
        done('r-power', 'blue-ocean', {}, {chart: power}), done('r-cascade', 'playing-to-win', {}, {chart: cascade}), done('r-table', 'where-to-play', {strategy_n: null}, {chart: table})];
      const {page, errors} = await open(browser, {runs, route: '#/strategy/lenses/r-draft'});
      await page.locator('[data-lens-chart="matrix_2x2"]').waitFor();
      assert.match(await page.locator('.lens-meta').innerText(), /^draft\s+\d+ Oct/);
      assert.match(await page.locator('.lens-meta').innerText(), /Strategy Bot/);
      assert.match(await page.locator('[data-lens-proposal]').innerText(), /^Sent to you as a proposal/);
      assert.equal(await page.locator('[data-lens-proposal] a').getAttribute('href'), '#/tasks?view=foryou');
      assert.equal(await page.locator('[data-lens-change]').count(), 2);
      assert.equal(await page.locator('[data-lens-apply]').count(), 0, 'a proposed run is decided on its card');
      assert.deepEqual(await page.locator('.lens-quad').evaluateAll(a => a.map(e => [e.dataset.q, e.querySelector('b').textContent])),
        [['lh', 'Grow quietly'], ['hh', 'Race'], ['ll', 'Hold on'], ['hl', 'Brace']]);
      assert.deepEqual(await page.locator('[data-axis]').allInnerTexts(), ['Buyer budgets', 'Pace of rules']);
      assert.deepEqual(await page.locator('.lens-end').allInnerTexts(), ['Growing', 'Shrinking', 'Slow', 'Fast']);
      // The quadrants sit by their ends: x high is the right column, y high the top row.
      const at = q => page.locator(`.lens-quad[data-q="${q}"]`).evaluate(e => { const r = e.getBoundingClientRect(); return [r.left, r.top]; });
      const [hh, ll, lh] = [await at('hh'), await at('ll'), await at('lh')];
      assert.ok(hh[0] > ll[0] && hh[1] < ll[1] && lh[0] < hh[0] && lh[1] === hh[1]);
      await shot(page, 'matrix');
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-power');
      await page.locator('[data-lens-chart="power_table"]').waitFor();
      assert.deepEqual(await page.locator('.lens-table thead th').allTextContents(), ['Bet', 'Power', 'How', 'When']);
      const rows = await page.locator('.lens-table tbody tr').evaluateAll(a => a.map(r => [...r.children].map(c => c.textContent)));
      assert.deepEqual(rows, [['Bet 1 Price at half the market.', 'Process', 'Bots run the office.', 'Now'], ['Bet 2 Sell through property managers.', 'None', '', '']]);
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-cascade');
      await page.locator('[data-lens-chart="cascade"]').waitFor();
      assert.deepEqual(await page.locator('.lens-step-card').evaluateAll(a => a.map(e => e.classList.contains('weak'))), [false, true]);
      assert.match(await page.locator('[data-lens-weak]').innerText(), /Weakest/);
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-table');
      await page.locator('[data-lens-chart="table"]').waitFor();
      assert.deepEqual(await page.locator('.lens-table tbody tr').evaluateAll(a => a.map(r => [...r.children].map(c => c.textContent))), [['Small offices', '4,200', 'High'], ['Homes', '90,000', 'Medium']]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Failed, and no model: each says so, and the owner may run again.
    {
      const runs = [done('r-fail', 'blue-ocean', {status: 'failed', result: null, error: {code: 'interrupted', message: 'The run was cut off'}}),
        done('r-nomodel', 'blue-ocean', {status: 'no_model', result: null})];
      const {page, errors, sent} = await open(browser, {runs, route: '#/strategy/lenses/r-fail'});
      await page.locator('[data-lens-failed]').waitFor();
      assert.equal(await page.locator('[data-lens-failed]').innerText(), 'The run was cut off');
      await page.locator('[data-lens-again]').click();
      await page.locator('[data-lens-running]').waitFor();
      assert.deepEqual(sent[0], ['POST', '/lenses/blue-ocean/runs', {answers: {rivals: ['Acme Clean']}, on: 'published'}]);
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-nomodel');
      await page.locator('[data-lens-nomodel]').waitFor();
      assert.match(await page.locator('[data-lens-nomodel]').innerText(), /No model provider is set up/);
      assert.equal(await page.locator('[data-lens-nomodel] a').getAttribute('href'), '#/settings/computers');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Phone: nothing scrolls sideways on the list, the panel, or any run page.
    {
      const runs = [...PAST, done('r-power', 'blue-ocean', {}, {chart: power}), done('r-cascade', 'playing-to-win', {}, {chart: cascade}), done('r-table', 'where-to-play', {}, {chart: table})];
      const long = done('r-long', 'blue-ocean', {}, {chart: {...curve, factors: ['Price per cleaning visit', 'Setup time in days', 'Support', 'Monthly reporting', 'Integrations with accounting', 'Training', 'Contract flexibility']
        , series: curve.series.map((s, i) => ({name: s.name + (i ? ' with a long name' : ''), scores: [...s.scores, 2, 3]}))}});
      const {page, errors} = await open(browser, {runs: [...runs, long], viewport: PHONE});
      await page.locator('[data-lens="blue-ocean"]').waitFor();
      assert.equal(await sideways(page), false, 'list');
      await shot(page, 'list-phone', ['dark']);
      await page.locator('[data-lens-run="blue-ocean"]').click();
      await page.locator('#lens-flow .lens-rows input').first().fill('A very long rival name that keeps going and going');
      await page.keyboard.press('Enter'); await page.keyboard.type('Another');
      await page.locator('#lens-flow [data-lens-next]').click();
      await page.locator('#lens-flow .lens-rows input').first().fill('A rather long buying factor that wraps');
      await page.keyboard.press('Enter'); await page.keyboard.type('Setup');
      await page.keyboard.press('Enter'); await page.keyboard.type('Reporting');
      await page.locator('#lens-flow [data-lens-next]').click();
      await page.locator('#lens-flow .lens-scores').waitFor();
      assert.equal(await sideways(page), false, 'panel');
      assert.equal(await page.locator('#lens-flow').evaluate(d => d.scrollWidth <= d.clientWidth + 1), true, 'panel contents');
      assert.equal(await page.locator('#lens-flow .lens-scores').evaluate(t => t.getBoundingClientRect().right <= innerWidth), true);
      await shot(page, 'flow-phone', ['dark']);
      await page.locator('#lens-flow [data-lens-close]').click();
      for (const id of ['r-blue', 'r-long', 'r-draft', 'r-power', 'r-cascade', 'r-table']) {
        await page.goto('http://tico-ui.test/#/strategy/lenses/' + id);
        await page.locator('.lens-summary').waitFor();
        if (id.startsWith('r-b') || id === 'r-long') await page.locator('.lens-curve').waitFor();
        assert.equal(await sideways(page), false, id);
        await shot(page, `run-${id}-phone`, ['dark']);
      }
      // The chart is drawn at the width it has: the curve is no wider than the screen and keeps its text size.
      await page.goto('http://tico-ui.test/#/strategy/lenses/r-long');
      const svg = await page.locator('.lens-curve').evaluate(e => [e.getBoundingClientRect().width, parseFloat(getComputedStyle(e.querySelector('.lens-t')).fontSize)]);
      assert.ok(svg[0] <= 400 && svg[1] >= 10, 'the curve keeps its text size: ' + svg);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('lenses: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
