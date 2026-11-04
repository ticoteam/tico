// A bot's stopped runs are reviewed from a control a person can reach (Dev 280). A run that holds queued work is a
// BotOps review (backend/views.py uncertain_work, needs_person false), so Settings' System checks card leaves it out and
// Health lists it as a note with "Review stopped runs". That opens the card: the safe pick first, the evidence folded.
// One that holds nothing is a note under Health too. Health's BotOps checks (stopped runs, a quarantined bot) stay off
// the person's status dot. Every request is intercepted.
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
    // The runs the server lists for an issue (backend/views.py): one per stopped run, with its title and why.
    const stoppedRun = (title, retry, jobs) => ({title, cause: 'interrupted', why: 'Interrupted on its computer',
      routine: true, retry,
      at: '2026-10-26T05:39:09Z', jobs: jobs.map(id => ({id, run_id: `a-${jobs[0]}`}))});
    const holding = {kind: 'uncertain_work', title: 'Builder has 2 stopped runs holding up work', bot: 'builder',
      detail: 'Its computer interrupted these runs at 05:39 UTC, usually for a restart or update, not a bug. ' +
        '3 queued requests wait until you decide.',
      severity: 'error', needs_person: false, action: 'review', held_back: 3, held_tasks: ['Dev 268'], can_review: true,
      runs: [stoppedRun('Review move demands a typed test receipt even when GitHub is green', false, ['r1', 'r1b']),
        stoppedRun('Billing 3/5', true, ['r2'])], more_runs: 1};
    const later = {kind: 'uncertain_work', title: 'Writer saved 1 stopped run for later', bot: 'writer',
      detail: 'The bot stopped before returning an answer. Other work can continue. Review it whenever useful.',
      severity: 'warning', needs_person: false, action: 'review'};
    const reviewed = [], decisions = [];
    const shot = async name => { if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/${name}.png`}); };
    // `refusal` is true to refuse every choice, or one job's id to refuse only that one.
    let refusal = false, deferred = false;
    // Keep a safe default first, with supporting evidence folded.
    const job = (id, title, stopped, acted, tools) => ({id, run_id: `a-${id}`, readable: true, stopped, acted,
      task: {id: `t-${id}`, title, status: 'doing', owner_id: 'bot:builder', owner_name: 'Builder'},
      request: 'Ship Billing 3/5.', run: {started_at: '2026-10-22T08:12:00Z', final_text: 'Merged #25; deploying next.'},
      output: [], tools: tools.map(tool => ({tool, summary: '', status: 'completed'})),
      reason: 'The run stopped after using a tool that can change things (Bash). Tico cannot tell whether it finished.'});
    const jobs = {builder: [job('j1', 'Billing 3/5', 'changed', ['Bash'], ['Read', 'Bash'])],
      writer: [job('j2', 'Weekly digest', 'read', [], ['Read'])]};
    jobs.writer[0].run.final_text = '';
    jobs.writer[0].output = ['The ', 'saved ', 'answer ', 'is ', 'one paragraph.\n\nA second paragraph.'].map(text => ({kind: 'message', text}));
    // Health's BotOps checks as the server builds them (backend/health.py): Builder's stopped runs, and a quarantined bot
    // whose queued work waits. Both are warn or bad, yet BotOps reviews them, so neither lights the person's dot.
    const stoppedCheck = {id: 'stopped_runs', label: 'Stopped runs nobody decided', status: 'warn',
      summary: "Held for a check two hours or more; choose Try again or Don't retry. Builder: 2 stopped runs waiting 72 h.",
      fixes: [{label: 'Open bots', href: '#/settings', tab: 'bots'}],
      items: [{bot: 'builder', name: 'Builder', count: 2, hours: 72, text: 'Builder: 2 stopped runs waiting 72 h',
        fixes: [{label: 'Review stopped runs', href: '#/bot/builder/more'}]}]};
    const quarantineCheck = {id: 'quarantine', label: 'Quarantined bots', status: 'bad',
      summary: '1 bot quarantined; queued work waits until the quarantine clears. Vault (vault): 1 job queued, oldest 180 min; '
        + 'Quarantined: queued work waits for the quarantine to clear.',
      fixes: [{label: 'Open Runs', href: '#/settings', tab: 'runs'}],
      items: [{bot: 'vault', name: 'Vault', queued: 1, dead: 0, held: 0, oldest: '2026-10-26T03:39:09Z', oldest_minutes: 180,
        reason: 'quarantined', text: 'Vault (vault): 1 job queued, oldest 180 min; Quarantined: queued work waits for the quarantine to clear.',
        fixes: [{label: 'Open bot settings', href: '#/bot/vault/more'}], fix: 'hub run list vault'}]};
    // Builder's work behind its stopped runs is Health's Stuck row too, its queue split as Pipeline splits it.
    const health = {audience: 'owner', attention: 2, checks: [stoppedCheck, quarantineCheck], computers: [], waiting: [],
      slow: [], failures: [],
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
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [holding, later]});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: [], issues: [holding, later]});
      const reconcile = p.match(/^\/api\/v2\/jobs\/([^/]+)\/reconcile$/);
      if (reconcile) {
        const body = route.request().postDataJSON(); decisions.push({id: reconcile[1], ...body});
        if (refusal === true || refusal === reconcile[1]) return json({error: {code: 'uncertain', detail: 'This run changed. Refresh and review it again.'}}, 409);
        const state = body.decision === 'resume' ? 'queued' : 'canceled';
        return json({job_id: reconcile[1], state: deferred ? 'uncertain' : state, deferred});
      }
      const review = p.match(/^\/api\/v2\/bots\/([^/]+)\/execution-review$/);
      if (review) { reviewed.push(review[1]); return json({jobs: jobs[review[1]] || []}); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });

    await page.goto('https://tico-ui.test/#/settings');
    // Settings' System checks card has none of these, since BotOps reviews them; Health lists each bot's as a note.
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=health]').click();
    const builderNote = page.locator('#hl-page .hl-notes [data-execution-review="builder"]');
    const writerNote = page.locator('#hl-page .hl-notes [data-execution-review="writer"]');
    await builderNote.waitFor({timeout: t(5000)});
    assert.equal(await builderNote.innerText(), 'Review stopped runs');
    assert.equal(await writerNote.innerText(), 'Review stopped runs');
    assert.equal(await page.locator('#settings-issues .settings-issue').count(), 0, 'nothing in System checks needs a person');
    const stuck = await page.locator('#hl-page [data-stuck="builder"] .muted').innerText();
    assert.match(stuck, /3 behind an interrupted run · 1 held on purpose$/);
    assert.doesNotMatch(stuck, /queued/, 'nothing that can start is called queued');
    // Health shows both BotOps checks with their own rows, and the person's dot stays dark: the checks are warn and bad
    // (window.hlAttention), yet BotOps reviews them, so ui/app/heartbeat.js leaves them off the dot.
    assert.deepEqual(await page.evaluate(() => window.hlAttention().map(check => check.id)), ['stopped_runs', 'quarantine']);
    assert.equal(await page.locator('#hl-summary').innerText(), '2 issues to look at');
    const stoppedRow = page.locator('#hl-page [data-hl-check="stopped_runs"]'), quarantineRow = page.locator('#hl-page [data-hl-check="quarantine"]');
    assert.equal(await stoppedRow.getAttribute('data-status'), 'warn');
    assert.match(await stoppedRow.innerText(), /Builder: 2 stopped runs waiting 72 h/);
    assert.equal(await quarantineRow.getAttribute('data-status'), 'bad');
    assert.match(await quarantineRow.innerText(), /Vault \(vault\): 1 job queued/);
    assert.equal(await page.evaluate(() => document.querySelector('#heartbeat').hidden), true, 'BotOps checks never light the dot');
    assert.equal(await page.evaluate(() => document.querySelectorAll('.account-status-line').length), 0, 'no line for them either');

    // A bot's stopped runs open their review card from the note: the decision first, the evidence folded.
    await builderNote.click();
    await page.locator('dialog.stopped-runs-dialog h2', {hasText: 'Stopped runs'}).waitFor({timeout: t(5000)});
    // Open bot is one of the card's Details.
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
    assert.equal(decisions[0].run_id, 'a-j1');
    assert.equal(decisions[0].acknowledge_uncertain_effects, true);
    assert.equal(await card.locator('button:enabled').count(), 0);
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();

    // The run that holds nothing is a note under Health too, and its review opens the same card.
    await writerNote.click();
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
    assert.equal(decisions.at(-1).run_id, 'a-j2');
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
    assert.equal(decisions.at(-1).run_id, 'a-j2');
    assert.equal(decisions.at(-1).acknowledge_uncertain_effects, true);
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();
    assert.deepEqual(errors, []);
    console.log(`PASS: stopped runs are reviewed from Health and its review card (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
