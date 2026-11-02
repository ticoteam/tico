// A bot's stopped runs are reviewed from a control a person can reach (Dev 280). One that holds queued work is
// needs-person (backend/views.py uncertain_work): Settings > System checks lists it with "Review stopped runs" and
// Needs you opens with one line per run, its own Try again / Don't retry, and a title that opens Details. One that holds nothing stays a
// note under Health and is still reviewed from there. In v0.3.63 every stopped run was not urgent, so the button rendered nowhere. Every request is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // Oct 26 (Chris): six stopped runs read as one paragraph of quoted titles beside a button as tall as the card.
    // Now one line per run, each with its own two buttons; a message folded into a run rides on its row.
    const stoppedRun = (title, retry, jobs) => ({title, cause: 'interrupted', why: 'Interrupted on its computer',
      routine: true, retry,
      at: '2026-10-26T05:39:09Z', jobs: jobs.map(id => ({id, attempt_id: `a-${jobs[0]}`}))});
    const holding = {kind: 'uncertain_work', title: 'Builder has 2 stopped runs holding up work', bot: 'builder',
      detail: 'Its computer interrupted these runs at 05:39 UTC, usually for a restart or update, not a bug. ' +
        '3 queued requests wait until you decide.',
      severity: 'error', needs_person: true, action: 'review', held_back: 3, held_tasks: ['Dev 268'], can_review: true,
      runs: [stoppedRun('Review move demands a typed test receipt even when GitHub is green', false, ['r1', 'r1b']),
        stoppedRun('Billing 3/5', true, ['r2'])], more_runs: 1};
    const later = {kind: 'uncertain_work', title: 'Writer saved 1 stopped run for later', bot: 'writer',
      detail: 'The bot stopped before returning an answer. Other work can continue. Review it whenever useful.',
      severity: 'warning', needs_person: false, action: 'review'};
    const reviewed = [], decisions = [];
    const shot = async name => { if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/${name}.png`}); };
    // `refusal` is true to refuse every choice, or one job's id to refuse only that one.
    let refusal = false, deferred = false;
    const extra = [];
    // Keep a safe default first, with supporting evidence folded.
    const job = (id, title, stopped, acted, tools) => ({id, attempt_id: `a-${id}`, readable: true, stopped, acted,
      task: {id: `t-${id}`, title, status: 'doing', owner: 'bot:builder', owner_name: 'Builder'},
      request: 'Ship Billing 3/5.', attempt: {started_at: '2026-10-22T08:12:00Z', final_text: 'Merged #25; deploying next.'},
      output: [], tools: tools.map(tool => ({tool, summary: '', status: 'completed'})),
      reason: 'The run stopped after using a tool that can change things (Bash). Tico cannot tell whether it finished.'});
    const jobs = {builder: [job('j1', 'Billing 3/5', 'changed', ['Bash'], ['Read', 'Bash'])],
      writer: [job('j2', 'Weekly digest', 'read', [], ['Read'])]};
    jobs.writer[0].attempt.final_text = '';
    jobs.writer[0].output = ['The ', 'saved ', 'answer ', 'is ', 'one paragraph.\n\nA second paragraph.'].map(text => ({kind: 'message', text}));
    // Builder's work behind its stopped runs is Health's Stuck row too, its queue split as Pipeline splits it.
    const health = {audience: 'owner', attention: 0, checks: [], computers: [], waiting: [], slow: [], failures: [],
      stuck: [{bot: 'builder', name: 'Builder', reason: 'uncertain', why: 'An interrupted run waits for review in Runs',
        queued: 0, dead: 3, held: 1, oldest: '2026-10-26T05:39:09Z', computer: 'Studio Mac'}],
      checked: new Date().toISOString()};
    await page.route('**/*', route => {
      const p = new URL(route.request().url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/health') return json(health);
      if (p === '/api/v2/health-monitor') return json({state: 'active', on: true, findings: []});
      if (p === '/api/v2/system/metrics') return json({});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [holding, later, ...extra]});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: [], issues: [holding, later]});
      const reconcile = p.match(/^\/api\/v2\/jobs\/([^/]+)\/reconcile$/);
      if (reconcile) {
        const body = route.request().postDataJSON(); decisions.push({id: reconcile[1], ...body});
        if (refusal === true || refusal === reconcile[1]) return json({error: {code: 'uncertain', detail: 'This run changed. Refresh and review it again.'}}, 409);
        const state = body.decision === 'resume' ? 'queued' : 'cancelled';
        return json({job_id: reconcile[1], state: deferred ? 'uncertain' : state, deferred});
      }
      const review = p.match(/^\/api\/v2\/bots\/([^/]+)\/execution-review$/);
      if (review) { reviewed.push(review[1]); return json({jobs: jobs[review[1]] || []}); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });

    await page.goto('https://tico-ui.test/#/settings');
    // Needs you opens by itself for the run holding work, with its review button; the note is not in it.
    const panel = page.locator('.needs-you');
    await panel.waitFor({timeout: t(5000)});
    assert.match(await panel.innerText(), /Builder has 2 stopped runs holding up work/);
    // The runs say why they stopped, so the card's own sentence is only the title's tooltip.
    const title = panel.locator('[data-execution-review="builder"]');
    assert.match(await title.getAttribute('title'), /not a bug\. 3 queued requests wait until you decide\./);
    assert.doesNotMatch(await panel.innerText(), /queued requests wait/);
    assert.doesNotMatch(await panel.innerText(), /"Dev 268"/);
    const lines = panel.locator('.stopped-line');
    assert.equal(await lines.count(), 2);
    assert.equal(await lines.nth(0).locator('.stopped-name').innerText(), 'Review move demands a typed test receipt even when GitHub is green');
    // The card's own line says it is not a bug, so the row does not repeat it.
    assert.equal(await lines.nth(0).locator('.stopped-why').innerText(), 'Interrupted on its computer');
    // The safe pick leads, by the review card's rule: a run that may have acted puts Don't retry first.
    assert.deepEqual(await lines.nth(0).locator('button').allInnerTexts(), ['Don’t retry', 'Try again']);
    assert.deepEqual(await lines.nth(1).locator('button').allInnerTexts(), ['Try again', 'Don’t retry']);
    // A title, its runs, two buttons each: the retry's hint is a tooltip, Open bot is in Details.
    assert.equal(await lines.nth(0).locator('[data-run-decision=resume]').getAttribute('title'), 'The bot checks what already happened first');
    assert.doesNotMatch(await panel.innerText(), /Try again tells the bot|Open bot/);
    assert.match(await panel.innerText(), /And 1 more/);
    assert.match(await title.innerText(), /^Builder has 2 stopped runs holding up work/);
    assert.equal(await panel.locator('[data-execution-review="writer"]').count(), 0);
    // On a phone: each run is two short lines, every button as tall as its words, the header beside Close and still
    // there once the list scrolls under it.
    await page.setViewportSize({width: 390, height: 360});
    const fit = await panel.evaluate(el => {
      const box = node => node.getBoundingClientRect();
      el.scrollTop = el.scrollHeight;
      const header = box(el.querySelector('header')), title = box(el.querySelector('header h2')), close = box(el.querySelector('[data-close]'));
      return {panel: box(el), header, title, close, scrolled: el.scrollTop > 0,
        buttons: [...el.querySelectorAll('.settings-issue button, .settings-issue a')].map(node => box(node).height),
        lines: [...el.querySelectorAll('.stopped-line')].map(node => box(node).height)};
    });
    assert.ok(fit.scrolled, `the fixture is taller than the phone panel: ${JSON.stringify(fit.panel)}`);
    assert.ok(Math.abs(fit.header.top - fit.panel.top) <= 1.5, 'the header stays at the top while the list scrolls');
    assert.ok(fit.title.right <= fit.close.left && fit.close.right <= fit.panel.right, 'the title is not under Close');
    assert.ok(fit.panel.top >= 0 && fit.panel.bottom <= 360 - 64, 'the panel sits between the top edge and the bottom tabs');
    assert.ok(fit.buttons.every(h => h < 44), `buttons sized to their words: ${fit.buttons}`);
    assert.ok(fit.lines.every(h => h < 80), `one short entry per run: ${fit.lines}`);
    await shot('needs-you-stopped-phone');
    await page.setViewportSize({width: 1280, height: 800});
    // Try again on a row settles every request folded into its run; a refusal stays on the row and can be retried.
    refusal = true;
    await lines.nth(0).locator('[data-run-decision=resume]').click();
    await lines.nth(0).locator('[data-result]', {hasText: 'This run changed.'}).waitFor();
    assert.equal(await lines.nth(0).locator('button:enabled').count(), 2);
    // Dev 561: one folded request settles and the next is refused. Only that choice is offered for the rest, and the
    // next click sends only the request still open, not the settled one again.
    refusal = 'r1b';
    decisions.length = 0;
    await lines.nth(0).locator('[data-run-decision=resume]').click();
    await lines.nth(0).locator('[data-result]', {hasText: 'This run changed.'}).waitFor();
    assert.deepEqual(decisions.map(d => [d.id, d.decision]), [['r1', 'resume'], ['r1b', 'resume']]);
    assert.deepEqual(await lines.nth(0).locator('button:enabled').allInnerTexts(), ['Try again']);
    refusal = false;
    decisions.length = 0;
    await lines.nth(0).locator('[data-run-decision=resume]').click();
    await lines.nth(0).locator('[data-result]', {hasText: 'Queued to try again.'}).waitFor();
    assert.deepEqual(decisions.map(d => [d.id, d.attempt_id, d.decision, d.acknowledge_uncertain_effects]),
      [['r1b', 'a-r1', 'resume', true]]);
    assert.equal(await lines.nth(0).locator('button:enabled').count(), 0);
    await lines.nth(1).locator('[data-run-decision=dismiss]').click();
    await lines.nth(1).locator('[data-result]', {hasText: 'Dismissed.'}).waitFor();
    assert.deepEqual(decisions.at(-1).id, 'r2');
    decisions.length = 0;
    await panel.locator('[data-close]').click();

    // Settings > System checks lists it with "Review stopped runs".
    await page.locator('#settings-issues summary').click();
    const settings = page.locator('#settings-issues [data-execution-review="builder"]');
    assert.equal(await settings.innerText(), 'Review stopped runs');
    await settings.click();
    await page.locator('dialog.stopped-runs-dialog h2', {hasText: 'Stopped runs'}).waitFor({timeout: t(5000)});
    // Open bot lives in Details now, not on the Needs you card.
    assert.equal(await page.locator('dialog.stopped-runs-dialog [data-open-bot]').getAttribute('href'), '#/bot/builder/more');
    // The cause and decision fit above the two actions; the evidence stays folded.
    const card = page.locator('dialog.stopped-runs-dialog form[data-job="j1"]');
    await card.waitFor({timeout: t(5000)});
    assert.match(await card.locator('[data-stopped]').innerText(), /^Stopped .+ after using Bash\.$/);
    assert.match(await card.locator('[data-pick]').innerText(), /^Don’t retry unless you know Bash didn’t finish/);
    assert.deepEqual(await card.locator('.stopped-run-actions .row button').allInnerTexts(), ['Don’t retry', 'Try again']);
    assert.equal(await card.locator('[data-details]').getAttribute('open'), null);
    assert.equal(await card.locator('textarea[name=note]').isVisible(), false);
    assert.equal(await card.locator('[data-retry-warning]').isVisible(), true);
    assert.notEqual(await card.locator('[data-result]').evaluate(el => getComputedStyle(el).display), 'none');
    await page.setViewportSize({width: 390, height: 680});
    const warning = await card.locator('[data-retry-warning]').boundingBox();
    assert.ok(warning.y >= 0 && warning.y + warning.height <= 680, 'retry risk is visible with details folded');
    await shot('stopped-phone-warning');
    await page.setViewportSize({width: 1280, height: 800});
    assert.doesNotMatch(await card.innerText(), /Why Tico paused|Did an outside action finish|Merged #25/);
    await shot('stopped-desktop');
    await card.locator('button[value=dismiss]').click();
    await card.locator('[data-result]', {hasText: 'Dismissed.'}).waitFor();
    assert.equal(decisions[0].decision, 'dismiss');
    assert.equal(decisions[0].attempt_id, 'a-j1');
    assert.equal(decisions[0].acknowledge_uncertain_effects, true);
    assert.equal(await card.locator('button:enabled').count(), 0);
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();

    // Health keeps the one that holds nothing as a note, and it is still reviewed from there.
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=health]').click();
    const note = page.locator('#hl-page .hl-notes [data-execution-review="writer"]');
    await note.waitFor({timeout: t(5000)});
    assert.equal(await note.innerText(), 'Review stopped runs');
    assert.equal(await page.locator('#hl-page .hl-notes [data-execution-review="builder"]').count(), 0);
    const stuck = await page.locator('#hl-page [data-stuck="builder"] .muted').innerText();
    assert.match(stuck, /3 behind an interrupted run · 1 held on purpose$/);
    assert.doesNotMatch(stuck, /queued/, 'nothing that can start is called queued');
    await note.click();
    await page.locator('dialog.stopped-runs-dialog h2', {hasText: 'Stopped runs'}).waitFor({timeout: t(5000)});
    // A run that only read is safe to try again, so that button leads.
    const read = page.locator('dialog.stopped-runs-dialog form[data-job="j2"]');
    await read.waitFor({timeout: t(5000)});
    assert.match(await read.locator('[data-stopped]').innerText(), /before changing anything\.$/);
    assert.deepEqual(await read.locator('.stopped-run-actions .row button').allInnerTexts(), ['Try again', 'Don’t retry']);
    await read.locator('[data-details] > summary').click();
    await read.locator('details:has(> [data-saved]) > summary').click();
    assert.equal(await read.locator('[data-saved]').innerText(), 'The saved answer is one paragraph.\n\nA second paragraph.');
    await page.setViewportSize({width: 390, height: 680});
    await shot('stopped-phone-saved');
    const geometry = await page.locator('dialog.stopped-runs-dialog').evaluate(d => {
      const box = el => el.getBoundingClientRect().toJSON();
      return {dialog: box(d), header: box(d.querySelector('header')), close: box(d.querySelector('[data-close]')),
        body: box(d.querySelector('[data-review]')), saved: box(d.querySelector('[data-saved]'))};
    });
    assert.ok(geometry.close.top >= geometry.header.top && geometry.close.bottom <= geometry.header.bottom);
    assert.ok(geometry.saved.left >= geometry.dialog.left + 12, 'content is padded');
    assert.ok(geometry.dialog.right <= 390 && geometry.dialog.bottom <= 680);
    // Outside Settings, its refresh function used to throw after replacing the form with success.
    await page.evaluate(() => document.querySelector('#settings-tabs').remove());
    refusal = true;
    await read.locator('button[value=resume]').click();
    const result = read.locator('[data-result]');
    await result.filter({hasText: 'This run changed.'}).waitFor();
    const rect = await result.boundingBox();
    assert.ok(rect.y >= 0 && rect.y + rect.height <= 680, 'refusal stays in the visible window');
    assert.equal(await read.locator('button:enabled').count(), 2);
    await shot('stopped-phone-refusal');
    refusal = false;
    await read.locator('button[value=resume]').click();
    await result.filter({hasText: 'Queued to try again.'}).waitFor();
    assert.equal(decisions.at(-1).decision, 'resume');
    assert.equal(decisions.at(-1).attempt_id, 'a-j2');
    assert.doesNotMatch(decisions.at(-1).note, /no outside action completed/, 'retry records a choice, not an unasked confirmation');
    assert.equal(await read.locator('button:enabled').count(), 0);
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();
    deferred = true;
    await page.evaluate(() => executionReview('writer'));
    const busy = page.locator('dialog.stopped-runs-dialog form');
    await busy.locator('button[value=dismiss]').click();
    await busy.locator('[data-result]', {hasText: 'Choice saved. The bot is busy'}).waitFor();
    jobs.writer[0].pending_decision = {decision: 'dismiss'};
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();
    await page.evaluate(() => executionReview('writer'));
    await page.locator('dialog.stopped-runs-dialog [data-result]', {hasText: 'Choice saved. The bot is busy'}).waitFor();
    assert.equal(await page.locator('dialog.stopped-runs-dialog form button:enabled').count(), 0);
    assert.deepEqual(reviewed, ['builder', 'writer', 'writer', 'writer']);
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();

    // A first timeout waiting on live session work retains the safe retry choice, without claiming a restart.
    jobs.writer[0].stopped = 'timeout';
    jobs.writer[0].pending_decision = null;
    deferred = false;
    await page.evaluate(() => executionReview('writer'));
    const timed = page.locator('dialog.stopped-runs-dialog form[data-job="j2"]');
    assert.match(await timed.locator('[data-stopped]').innerText(), /at its run time limit/);
    assert.doesNotMatch(await timed.locator('[data-stopped]').innerText(), /restarted/);
    assert.deepEqual(await timed.locator('.stopped-run-actions .row button').allInnerTexts(), ['Try again', 'Don’t retry']);
    await timed.locator('button[value=resume]').click();
    await timed.locator('[data-result]', {hasText: 'Queued to try again.'}).waitFor();
    assert.equal(decisions.at(-1).attempt_id, 'a-j2');
    assert.equal(decisions.at(-1).acknowledge_uncertain_effects, true);
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();

    // Dev 561: after a refresh, a run whose choice was saved while the bot was busy (stopped_rows `decided`) says what
    // Tico will do and offers no second choice; one with a folded request saved offers only that choice, for the rest.
    holding.runs = [{...stoppedRun('Billing 3/5', true, ['r2']), decided: 'resume',
      jobs: [{id: 'r2', attempt_id: 'a-r2', decided: 'resume'}]},
    {...stoppedRun('Review move demands a typed test receipt even when GitHub is green', false, ['r1', 'r1b']),
      decided: null, jobs: [{id: 'r1', attempt_id: 'a-r1', decided: 'dismiss'}, {id: 'r1b', attempt_id: 'a-r1'}]},
    // The Details card decided each folded request on its own, differently: the line is settled all the same.
    {...stoppedRun('Rotate the deploy keys', false, ['r3', 'r3b']), decided: 'mixed',
      jobs: [{id: 'r3', attempt_id: 'a-r3', decided: 'resume'}, {id: 'r3b', attempt_id: 'a-r3', decided: 'dismiss'}]},
    {...stoppedRun('Weekly digest', true, ['r4']), decided: null}];
    // A reader who is not the bot's operator (can_review false) gets the lines without handles: no decision buttons.
    extra.push({...holding, bot: 'legal', title: 'Legal has 2 stopped runs holding up work', can_review: false,
      runs: [{title: null, cause: 'interrupted', why: 'Interrupted on its computer', routine: true, retry: false,
        at: '2026-10-26T05:39:09Z', decided: null},
      {title: 'Counsel memo', cause: 'stopped', why: 'Stopped by a person', routine: false, retry: false,
        at: '2026-10-26T05:41:00Z', decided: 'dismiss'}], more_runs: 0});
    decisions.length = 0;
    await page.evaluate(async () => { await refresh(true); needsYou(); });
    const builderRows = panel.locator('.settings-issue', {hasText: 'Builder has 2 stopped runs'}).locator('.stopped-line');
    const saved = builderRows.nth(0);
    assert.equal(await saved.locator('[data-result]').innerText(),
      'Choice saved. The bot is busy; Tico will try again when its turn ends.');
    assert.equal(await saved.locator('button').count(), 2);
    assert.equal(await saved.locator('button:enabled').count(), 0);
    const partway = builderRows.nth(1), mixed = builderRows.nth(2), open = builderRows.nth(3);
    assert.deepEqual(await partway.locator('button:enabled').allInnerTexts(), ['Don’t retry']);
    assert.equal(await mixed.locator('[data-result]').innerText(),
      'Choices saved. The bot is busy; Tico will apply them when its turn ends.');
    assert.equal(await mixed.locator('button:enabled').count(), 0);
    // The rest goes through at once, but the request saved earlier still waits for the bot's turn, so the line says so.
    deferred = false;
    await partway.locator('[data-run-decision=dismiss]').click();
    await partway.locator('[data-result]', {hasText: 'Tico will dismiss this run when its turn ends.'}).waitFor();
    assert.deepEqual(decisions.map(d => [d.id, d.decision]), [['r1b', 'dismiss']]);
    // Needs you closed and reopened before a refresh (a line is still open) keeps the outcome and offers nothing more.
    await page.evaluate(() => needsYou());
    assert.equal(await partway.locator('[data-result]').innerText(),
      'Choice saved. The bot is busy; Tico will dismiss this run when its turn ends.');
    assert.equal(await partway.locator('button:enabled').count(), 0);
    // Deciding the last open line refreshes the card.
    const reread = page.waitForResponse(r => new URL(r.url()).pathname === '/api/status', {timeout: 5000});
    await open.locator('[data-run-decision=resume]').click();
    await open.locator('[data-result]', {hasText: 'Queued to try again.'}).waitFor();
    await reread;
    assert.deepEqual(decisions.map(d => [d.id, d.decision]), [['r1b', 'dismiss'], ['r4', 'resume']]);
    const legal = panel.locator('.settings-issue', {hasText: 'Legal has 2 stopped runs'});
    assert.equal(await legal.locator('.stopped-line').count(), 2);
    assert.equal(await legal.locator('.stopped-line').nth(0).locator('.stopped-name').innerText(), 'A request you can’t open');
    assert.equal(await legal.locator('[data-run-decision]').count(), 0);
    assert.equal(await legal.locator('.stopped-picks').count(), 0);
    assert.equal(await legal.locator('.stopped-line').nth(1).locator('[data-result]').innerText(),
      'Choice saved. The bot is busy; Tico will dismiss this run when its turn ends.');
    assert.deepEqual(errors, []);
    console.log(`PASS: stopped runs are reviewed from Needs you, Settings and Health (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
