// A bot's stopped runs are reviewed from a control a person can reach (Dev 280). One that holds queued work is
// needs-person (backend/views.py uncertain_work): Settings > System checks lists it with "Review stopped runs" and
// Needs you opens with "Review now". One that holds nothing stays a note under Health and is still reviewed from
// there. In v0.3.63 every stopped run was not urgent, so the button rendered nowhere. Every request is intercepted.
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
    const holding = {kind: 'uncertain_work', title: 'Builder has 1 stopped run holding up work', bot: 'builder',
      detail: '"Billing 3/5" (owner Builder) stopped partway. 3 queued requests wait on "Dev 268" until you decide. Retry it or drop it.',
      severity: 'error', needs_person: true, action: 'review', held_back: 3, held_tasks: ['Dev 268']};
    const later = {kind: 'uncertain_work', title: 'Writer saved 1 stopped run for later', bot: 'writer',
      detail: 'The bot stopped before returning an answer. Other work can continue. Review it whenever useful.',
      severity: 'warning', needs_person: false, action: 'review'};
    const reviewed = [];
    // The card is the decision (Chris, Oct 22): what stopped, one recommendation with its button first, the rest folded.
    const job = (id, title, stopped, acted, tools) => ({id, attempt_id: `a-${id}`, readable: true, stopped, acted,
      task: {id: `t-${id}`, title, status: 'doing', owner: 'bot:builder', owner_name: 'Builder'},
      request: 'Ship Billing 3/5.', attempt: {started: '2026-10-22T08:12:00Z', final_text: 'Merged #25; deploying next.'},
      output: [], tools: tools.map(tool => ({tool, summary: '', status: 'completed'})),
      reason: 'The run stopped after using a tool that can change things (Bash). Tico cannot tell whether it finished.'});
    const jobs = {builder: [job('j1', 'Billing 3/5', 'changed', ['Bash'], ['Read', 'Bash'])],
      writer: [job('j2', 'Weekly digest', 'read', [], ['Read'])]};
    const health = {audience: 'owner', attention: 0, checks: [], computers: [], waiting: [], slow: [], failures: [],
      checked: new Date().toISOString()};
    await page.route('**/*', route => {
      const p = new URL(route.request().url()).pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
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
      const review = p.match(/^\/api\/v2\/bots\/([^/]+)\/execution-review$/);
      if (review) { reviewed.push(review[1]); return json({jobs: jobs[review[1]] || []}); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });

    await page.goto('https://tico-ui.test/#/settings');
    // Needs you opens by itself for the run holding work, with its review button; the note is not in it.
    const panel = page.locator('.needs-you');
    await panel.waitFor({timeout: t(5000)});
    assert.match(await panel.innerText(), /Builder has 1 stopped run holding up work/);
    assert.match(await panel.innerText(), /3 queued requests wait on "Dev 268"/);
    assert.equal(await panel.locator('[data-execution-review="builder"]').innerText(), 'Review now');
    assert.equal(await panel.locator('[data-execution-review="writer"]').count(), 0);
    await panel.locator('[data-close]').click();

    // Settings > System checks lists it with "Review stopped runs".
    await page.locator('#settings-issues summary').click();
    const settings = page.locator('#settings-issues [data-execution-review="builder"]');
    assert.equal(await settings.innerText(), 'Review stopped runs');
    await settings.click();
    await page.locator('dialog.tmodal h2', {hasText: 'Stopped runs'}).waitFor({timeout: t(5000)});
    // One line on what stopped, one recommendation, two buttons with the recommended one first; nothing else open.
    const card = page.locator('dialog.tmodal form[data-job="j1"]');
    await card.waitFor({timeout: t(5000)});
    assert.match(await card.locator('[data-stopped]').innerText(), /^Stopped .+ while using Bash\.$/);
    assert.match(await card.locator('[data-pick]').innerText(), /^Don’t retry unless you know Bash didn’t finish/);
    assert.deepEqual(await card.locator(':scope > .row button').allInnerTexts(), ['Don’t retry', 'Try again']);
    assert.equal(await card.locator('[data-details]').getAttribute('open'), null);
    assert.equal(await card.locator('textarea[name=note]').isVisible(), false);
    assert.doesNotMatch(await card.innerText(), /Why Tico paused|Did an outside action finish|Merged #25/);
    await page.locator('dialog.tmodal [data-close]').click();

    // Health keeps the one that holds nothing as a note, and it is still reviewed from there.
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=health]').click();
    const note = page.locator('#hl-page .hl-notes [data-execution-review="writer"]');
    await note.waitFor({timeout: t(5000)});
    assert.equal(await note.innerText(), 'Review stopped runs');
    assert.equal(await page.locator('#hl-page .hl-notes [data-execution-review="builder"]').count(), 0);
    await note.click();
    await page.locator('dialog.tmodal h2', {hasText: 'Stopped runs'}).waitFor({timeout: t(5000)});
    // A run that only read is safe to try again, so that button leads.
    const read = page.locator('dialog.tmodal form[data-job="j2"]');
    await read.waitFor({timeout: t(5000)});
    assert.match(await read.locator('[data-stopped]').innerText(), /after only reading\.$/);
    assert.deepEqual(await read.locator(':scope > .row button').allInnerTexts(), ['Try again', 'Don’t retry']);
    assert.deepEqual(reviewed, ['builder', 'writer']);
    assert.deepEqual(errors, []);
    console.log(`PASS: stopped runs are reviewed from Needs you, Settings and Health (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
