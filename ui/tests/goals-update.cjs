// "Post update" in an owner's panel on Goals (ui/goals-kpis.js goalUpdateHtml): each live goal the viewer may colour (its
// owner, the owner of the goal it supports, anyone above the owner: backend/goal_routes.py may_colour) offers a form with
// what is going on and "How is it going?". Empty words send nothing; an answer on a goal with no judged measure colours it
// without a page reload; no answer saves words only; a goal its measures colour says so; a refusal or a dropped line
// keeps the words and shows why. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();
const screenshotDir = process.env.TICO_SCREENSHOT_DIR;

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});
const bots = [bot('coo', 'Assistant', ''), bot('botops', 'BotOps', ''), bot('librarian', 'Librarian', ''),
  bot('goal-manager', 'Strategy Bot', 'p:ana', {reports_to: ''})];
const people = [{id: 'ana', name: 'Ana Silva', org_parent: ''}, {id: 'ben', name: 'Ben Park', org_parent: 'p:ana'},
  {id: 'cara', name: 'Cara Diaz', org_parent: 'p:ana'}];
const SIGNAL_COLOUR = {on_track: 'green', at_risk: 'yellow', off_track: 'red'};
const measure = {id: 'k-demos', name: 'Demos booked', unit: '', direction: 'up', cadence: 'weekly', owner_id: 'human:ben', freshness: 'fresh',
  status: 'green', reason: 'On pace', spark: [3, 4, 5], latest: {value: 5, period_end: new Date().toISOString(), quality: 'measured'},
  target_label: '→ 20 by Dec 31', link: {goal_id: 'g-demos', kind: 'improve', target: 20, deadline: '2027-12-31'}};
// A targeted measure with no recent reading judges nothing: the server colours that goal from its latest check-in.
const stale = {...measure, id: 'k-calls', name: 'Calls made', freshness: 'stale', status: 'gray', reason: 'No reading in 3 weeks',
  link: {...measure.link, goal_id: 'g-stale'}};
const judged = g => g.kpis.some(k => ['red', 'yellow', 'green'].includes(k.status));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [], posted = [];
    page.on('pageerror', e => errors.push(e.message));
    const goals = [
      {id: 'g-ana', title: 'Raise the seed round', owner_id: 'human:ana', parent_id: null, status: 'green', status_source: 'auto', rank: 0, kpis: []},
      {id: 'g-ben', title: 'Sign three pilot landlords', owner_id: 'human:ben', parent_id: null, status: null, status_source: 'auto', rank: 0, kpis: []},
      {id: 'g-demos', title: 'Book twenty demos', owner_id: 'human:ben', parent_id: null, status: 'green', status_source: 'auto', rank: 1, kpis: [measure]},
      {id: 'g-hand', title: 'Hire a designer', owner_id: 'human:ben', parent_id: null, status: 'red', status_source: 'person', status_by_id: 'human:ana', rank: 3,
        kpis: [{...measure, id: 'k-interviews', name: 'Interviews', link: {...measure.link, goal_id: 'g-hand'}}]},
      {id: 'g-stale', title: 'Call every landlord', owner_id: 'human:ben', parent_id: null, status: null, status_source: 'auto', rank: 4, kpis: [stale]},
      {id: 'g-ben-old', title: 'Finished pilot', owner_id: 'human:ben', parent_id: null, status: 'done', status_source: 'person', rank: 2, kpis: []},
      {id: 'g-cara', title: 'Ship the listing page', owner_id: 'human:cara', parent_id: null, status: null, status_source: 'auto', rank: 0, kpis: []},
      {id: 'g-cara-pilot', title: 'Write the pilot contract', owner_id: 'human:cara', parent_id: 'g-ben', status: null, status_source: 'auto', rank: 1, kpis: []},
    ];
    let refuse = '';   // '' answers; 'forbidden' refuses; 'drop' drops the line
    let hold = null;   // a promise the next reply waits for: a slow server
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/v2/events') return;
      if (p === '/api/v2/config') return json({});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/me') return json({id: 'ben', name: 'Ben Park', email: 'ben@example.test', role: 'member', company_role: 'member', mover: false, cloud: true, config: {}});
      if (p === '/api/humans') return json({people});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/goals/tree') return json({goals, owners: {}, other_kpis: [], proposals: []});
      if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ben', items: []});
      if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
      if (p === '/api/v2/strategy') return json({current: null, bets: [], label: 'Bets', label_one: 'Bet', may_edit: false, goals_by_bet: {}, health_by_bet: {}});
      const add = p.match(/^\/api\/v2\/goals\/([^/]+)\/checkins$/);
      if (add && req.method() === 'POST') {
        const body = req.postDataJSON();
        posted.push({goal: add[1], body, key: req.headers()['idempotency-key']});
        if (hold) await hold;
        if (refuse === 'drop') return route.abort('failed');
        if (refuse === 'forbidden') return json({error: {code: 'forbidden', detail: 'The goal\'s owner, the owner of the goal it serves, someone above them or the Strategy Bot records a check-in'}}, 403);
        // The server's colour (backend/goals.py score): a judged measure wins, else the latest check-in that answered.
        const g = goals.find(x => x.id === add[1]);
        const checkin = {id: 'c' + posted.length, goal_id: g.id, ts: new Date().toISOString(), author: 'human:ben', source_actor: 'human:ben', body: body.body, signal: body.signal || null};
        g.checkin = checkin;
        if (body.signal && !judged(g) && g.status_source !== 'person') g.status = SIGNAL_COLOUR[body.signal];
        return json({checkin});
      }
      return json(unrouted(p));
    });
    const until = async (fn, what) => { for (const end = Date.now() + t(6000); Date.now() < end;) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); } if (!await fn()) assert.fail('timed out: ' + what); };

    await page.goto('http://tico-ui.test/#/goals');
    await page.locator('#goal-tree .gt-row').first().waitFor();
    await page.evaluate(() => { window.__sameLoad = true; });
    const panel = page.locator('#goal-panel');
    const openPanel = async owner => {
      if (await panel.evaluate(d => d.open).catch(() => false)) await panel.locator('[data-gp-close]').click();
      await page.locator(`#goal-tree .gt-row[data-owner="${owner}"]:not(.gt-cont) .gt-name`).click();
      await panel.locator('.gp-goal').first().waitFor();
    };
    const goal = id => panel.locator(`[data-gp-goal="${id}"]`);
    const form = id => panel.locator(`[data-gp-update-form="${id}"]`);

    // 1. Ben owns these: each live goal offers "Post update"; the done goal is not listed.
    await openPanel('human:ben');
    assert.deepEqual(await panel.locator('.gp-goal').evaluateAll(els => els.map(el => el.dataset.gpGoal)), ['g-ben', 'g-demos', 'g-hand', 'g-stale']);
    for (const id of ['g-ben', 'g-demos', 'g-hand', 'g-stale']) assert.equal(await goal(id).locator('[data-gp-update]').innerText(), 'Post update');
    // 2. Cara's: only the goal that supports Ben's offers it; Ana's (his manager's) offers nothing.
    await openPanel('human:cara');
    assert.equal(await goal('g-cara').locator('[data-gp-update]').count(), 0, 'not his goal, not under his');
    assert.equal(await goal('g-cara-pilot').locator('[data-gp-update]').count(), 1, 'it supports his goal');
    await openPanel('human:ana');
    assert.equal(await panel.locator('[data-gp-update]').count(), 0, 'his manager\'s goal');

    // 3. Empty words: nothing is sent, and the form says what it needs.
    await openPanel('human:ben');
    await goal('g-ben').locator('[data-gp-update]').click();
    const text = form('g-ben').locator('textarea[name=body]');
    assert.equal(await text.evaluate(el => el === document.activeElement), true, 'the words have the focus');
    assert.equal(await text.getAttribute('maxlength'), '4000');
    assert.deepEqual(await form('g-ben').locator('.gp-signal label').allInnerTexts(), ['On track', 'At risk', 'Off track', 'No answer']);
    assert.equal(await form('g-ben').locator('input[name=signal]:checked').getAttribute('value'), '', 'no answer to start');
    await text.fill('   ');
    await form('g-ben').locator('[type=submit]').click();
    await form('g-ben').locator('[data-gp-update-err]:not([hidden])').waitFor();
    assert.equal(await form('g-ben').locator('[data-gp-update-err]').innerText(), 'Say in a sentence what is going on');
    assert.equal(posted.length, 0, 'nothing sent');

    // 6 (first half). No measure judges this goal: an answer shows no note.
    await form('g-ben').locator('input[name=signal][value=at_risk]').check();
    assert.equal(await form('g-ben').locator('[data-gp-update-note]').isHidden(), true, 'no measure: the answer colours it');

    // 7. The server refuses: the words stay and the reason shows.
    const words = 'Two landlords signed; the third wants a lawyer to read the contract first.';
    await text.fill(words);
    refuse = 'forbidden';
    await form('g-ben').locator('[type=submit]').click();
    await until(() => form('g-ben').locator('[data-gp-update-err]').innerText().then(s => /records a check-in/.test(s)), 'the refusal shows');
    assert.equal(await text.inputValue(), words);
    assert.equal(await form('g-ben').locator('input[name=signal]:checked').getAttribute('value'), 'at_risk');
    assert.equal(await form('g-ben').locator('[type=submit]').isEnabled(), true, 'Post can be pressed again');
    assert.equal(await goal('g-ben').locator('.kpi-check').count(), 0, 'nothing shows as posted');
    // The line drops: the same, after the client's retries (one request id).
    refuse = 'drop';
    const before = posted.length;
    await form('g-ben').locator('[type=submit]').click();
    await until(() => form('g-ben').locator('[data-gp-update-err]').innerText().then(s => /Could not confirm whether this saved/.test(s)), 'the dropped line shows');
    assert.equal(await text.inputValue(), words);
    assert.equal(new Set(posted.slice(before).map(r => r.key)).size, 1, 'retries reuse one request id');

    // A slow reply: the form is frozen while it is out, so nothing typed after Post is dropped as if saved, and a panel
    // redraw meanwhile neither re-arms Post nor loses the refusal that follows.
    let release;
    hold = new Promise(r => { release = r; });
    refuse = 'forbidden';
    const sent = posted.length;
    await form('g-ben').locator('[type=submit]').click();
    await until(() => posted.length > sent, 'the post is out');
    const frozen = async why => {
      assert.equal(await text.evaluate(el => el.readOnly), true, why + ': the words are frozen');
      assert.equal(await form('g-ben').locator('input[name=signal][value=on_track]').isDisabled(), true, why + ': the answer is frozen');
      assert.equal(await form('g-ben').locator('[type=submit]').isDisabled(), true, why + ': Post waits');
      assert.equal(await form('g-ben').locator('[data-gp-update-cancel]').isDisabled(), true, why + ': no Cancel while it is out');
      assert.equal(await goal('g-demos').locator('[data-gp-update]').isDisabled(), true, why + ': no second form while it is out');
    };
    await frozen('posting');
    await text.press('Escape');
    assert.equal(await form('g-ben').count(), 1, 'Escape does not drop a post that is out');
    await goal('g-demos').locator('[data-gp-kpi-add]').click();   // redraws the panel
    await panel.locator('[data-kpi-add]').waitFor();
    await frozen('after a redraw');
    release();
    await until(() => form('g-ben').locator('[data-gp-update-err]').innerText().then(s => /records a check-in/.test(s)), 'the refusal shows after a redraw');
    assert.equal(await text.inputValue(), words);
    assert.equal(await text.evaluate(el => el.readOnly), false, 'the words can be edited again');
    assert.equal(await form('g-ben').locator('[type=submit]').isEnabled(), true, 'Post can be pressed again after a redraw');
    await panel.locator('[data-kpi-add] [data-cancel]').click();
    // The panel closes while a post is out: its refusal is still said, in a toast.
    hold = new Promise(r => { release = r; });
    await form('g-ben').locator('[type=submit]').click();
    await until(() => posted.length > sent + 1, 'the second post is out');
    await panel.locator('[data-gp-close]').click();
    release();
    await page.locator('.toast.err', {hasText: 'records a check-in'}).waitFor();
    hold = null;
    await openPanel('human:ben');
    await goal('g-ben').locator('[data-gp-update]').click();
    await form('g-ben').locator('textarea[name=body]').fill(words);
    await form('g-ben').locator('input[name=signal][value=at_risk]').check();

    // 4. Words and "At risk" on a goal with no measure and no colour set by hand: the latest, and yellow, in this page.
    refuse = '';
    assert.equal(await goal('g-ben').locator('.gp-gline .gdot').getAttribute('class'), 'gdot gray');
    await form('g-ben').locator('[type=submit]').click();
    await until(() => goal('g-ben').locator('.kpi-check').count().then(n => n === 1), 'the update shows');
    assert.deepEqual(posted.at(-1).body, {body: words, signal: 'at_risk'});
    assert.equal(await form('g-ben').count(), 0, 'the form closes');
    assert.match(await goal('g-ben').locator('.kpi-check').innerText(), new RegExp(words.replace(/[.;]/g, '\\$&') + '[\\s\\S]*Ben Park · at risk'));
    await until(() => goal('g-ben').locator('.gp-gline .gdot').getAttribute('class').then(c => c === 'gdot yellow'), 'the goal turns yellow');
    await until(() => page.locator('#goal-tree .gt-row[data-owner="human:ben"] .gdot.yellow').count().then(n => n > 0), 'the tree turns yellow');
    assert.equal(await page.evaluate(() => window.__sameLoad), true, 'no page reload');

    // 5. No answer: saved with no signal, and the colour stays.
    await goal('g-ben').locator('[data-gp-update]').click();
    await form('g-ben').locator('textarea[name=body]').fill('Lawyer meeting is on Thursday.');
    await form('g-ben').locator('[type=submit]').click();
    await until(() => goal('g-ben').locator('.kpi-check').innerText().then(s => /Lawyer meeting/.test(s)), 'the newest shows');
    assert.deepEqual(posted.at(-1).body, {body: 'Lawyer meeting is on Thursday.'}, 'no signal sent');
    assert.doesNotMatch(await goal('g-ben').locator('.kpi-check footer').innerText(), /track|risk/);
    assert.equal(await goal('g-ben').locator('.gp-gline .gdot').getAttribute('class'), 'gdot yellow', 'colour unchanged');

    // 6. A goal its measure judges: an answer says the measures colour it; no answer hides that again.
    await goal('g-demos').locator('[data-gp-update]').click();
    const note = form('g-demos').locator('[data-gp-update-note]');
    assert.equal(await note.isHidden(), true);
    await form('g-demos').locator('input[name=signal][value=on_track]').check();
    assert.equal(await note.isVisible(), true, 'its measures colour it');
    assert.equal(await note.innerText(), 'Its measures set its colour; this update is kept as your words');
    await form('g-demos').locator('textarea[name=body]').fill('Five booked; the webinar should bring the rest.');
    if (screenshotDir) {
      await panel.screenshot({path: path.join(screenshotDir, 'goals-update.png')});
      await page.setViewportSize({width: 390, height: 844});
      await panel.screenshot({path: path.join(screenshotDir, 'goals-update-phone.png')});
      await page.setViewportSize({width: 1440, height: 900});
    }
    await form('g-demos').locator('input[name=signal][value=""]').check();
    assert.equal(await note.isHidden(), true);
    // A colour a person set outranks both the answer and the measure: the note says so instead.
    await goal('g-hand').locator('[data-gp-update]').click();
    await form('g-hand').locator('input[name=signal][value=at_risk]').check();
    assert.equal(await form('g-hand').locator('[data-gp-update-note]').innerText(), 'A person set its colour; this update is kept as your words');
    await form('g-hand').locator('[data-gp-update-cancel]').click();
    // A targeted measure with no recent reading colours nothing, so the answer will: no note.
    await goal('g-stale').locator('[data-gp-update]').click();
    await form('g-stale').locator('input[name=signal][value=at_risk]').check();
    assert.equal(await form('g-stale').locator('[data-gp-update-note]').isHidden(), true, 'a stale measure does not set the colour');
    await form('g-stale').locator('[data-gp-update-cancel]').click();
    await goal('g-demos').locator('[data-gp-update]').click();
    await form('g-demos').locator('textarea[name=body]').fill('Five booked; the webinar should bring the rest.');
    // Escape closes the form and keeps nothing.
    await form('g-demos').locator('textarea[name=body]').press('Escape');
    assert.equal(await form('g-demos').count(), 0);
    assert.equal(await goal('g-demos').locator('[data-gp-update]').count(), 1);

    assert.deepEqual(errors, []);
    console.log('goals-update: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
