// Tico's own update restarts every computer at once, so the runs it stops arrive together. Needs you groups the ones
// the server puts in the update's group under one row with "Don't retry" and, when the review card would retry every
// run, "Try all again" (one request per run), with each run's own line under the row's title, one row per cluster of stops
// within ten minutes. A run that recorded an outside action, a run with a choice already saved for one of its
// requests, and a lone later stop keep their own lines. A group's choice and a run's own never save at once. Someone who may not decide the runs sees the same rows with no buttons. Checked at desktop and phone widths.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

// Two bots' runs stopped by the update within ten minutes, plus one run that may have acted, one folded run with a
// choice already saved for one of its requests, and a third bot's two runs stopped by a later update.
const update = (title, at, retry, id, group = retry) => ({title, cause: 'update', why: 'Stopped during Tico’s update',
  routine: true, retry, update_group: group, at, decided: null, jobs: [{id, attempt_id: `a-${id}`, decided: null}]});
const partly = {...update('Draft the release notes', '2026-10-28T14:03:00Z', true, 'u6'), chosen: true,
  jobs: [{id: 'u6', attempt_id: 'a-u6', decided: 'dismiss'}, {id: 'u6b', attempt_id: 'a-u6', decided: null}]};
const issues = canReview => [
  {kind: 'uncertain_work', title: 'ops has 2 stopped runs holding up work', bot: 'ops', severity: 'error',
    detail: 'Tico’s update stopped these runs at 14:02 UTC, not a bug. 1 queued request waits until you decide.',
    needs_person: true, action: 'review', can_review: canReview, more_runs: 0,
    runs: [update('Ship the receipts fix', '2026-10-28T14:02:10Z', true, 'u1'),
      update('Weekly numbers', '2026-10-28T14:03:40Z', true, 'u2'), partly].map(run => canReview ? run : {...run, jobs: undefined})},
  {kind: 'uncertain_work', title: 'builder has 2 stopped runs holding up work', bot: 'builder', severity: 'error',
    detail: 'Tico’s update stopped these runs at 14:04 UTC, not a bug.', needs_person: true, action: 'review',
    can_review: canReview, more_runs: 0,
    runs: [update('Billing 3/5', '2026-10-28T14:04:05Z', true, 'u3'),
      update('Review the migration plan', '2026-10-28T14:04:05Z', false, 'u4')].map(run => canReview ? run : {...run, jobs: undefined})},
  {kind: 'uncertain_work', title: 'writer has 2 stopped runs holding up work', bot: 'writer', severity: 'error',
    detail: 'These runs stopped during Tico’s update at 14:30 UTC, not a bug.', needs_person: true, action: 'review',
    can_review: canReview, more_runs: 0,
    runs: [update('Rotate the keys', '2026-10-28T14:30:00Z', false, 'u5', true),
      update('Answer the support queue', '2026-10-28T14:31:00Z', true, 'u7')].map(run => canReview ? run : {...run, jobs: undefined})},
];

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const shot = async (page, name) => { if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/${name}.png`}); };
  // Opens Needs you on a fresh page for the runs above; `canReview` is what the server says this person may decide.
  async function open({canReview, width, height}) {
    const page = await browser.newPage({viewport: {width, height}, serviceWorkers: 'block'});
    const errors = [], decisions = [];
    let delay = 0;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const p = new URL(route.request().url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.4.17', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/health') return json({audience: 'owner', attention: 0, checks: [], computers: [], waiting: [], slow: [],
        failures: [], stuck: [], checked: new Date().toISOString()});
      if (p === '/api/v2/health-monitor') return json({state: 'active', on: true, findings: []});
      if (p === '/api/v2/system/metrics') return json({});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true,
        health_issues: issues(canReview)});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: [], issues: issues(canReview)});
      const reconcile = p.match(/^\/api\/v2\/jobs\/([^/]+)\/reconcile$/);
      if (reconcile) {
        const body = route.request().postDataJSON();
        if (delay) await new Promise(resolve => setTimeout(resolve, delay));
        decisions.push({id: reconcile[1], ...body});
        return json({job_id: reconcile[1], state: body.decision === 'resume' ? 'queued' : 'cancelled', deferred: false});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    const panel = page.locator('.needs-you');
    await panel.waitFor({timeout: t(5000)});
    return {page, panel, errors, decisions, slow: ms => { delay = ms; }};
  }

  try {
    // A person who may decide: one row for the three safe runs, the unsafe one stays on its own line.
    {
      const {page, panel, errors, decisions, slow} = await open({canReview: true, width: 1280, height: 800});
      const group = panel.locator('[data-update-group="0"]');
      assert.equal(await panel.locator('[data-update-group]').count(), 2);
      assert.match(await group.locator('.needs-title').innerText(), /^3 runs stopped during Tico.s update at 14:02 UTC/);
      assert.match(await panel.locator('[data-update-group="1"] .needs-title').innerText(), /^2 runs stopped during Tico.s update at 14:30 UTC/);
      // A title, at most one line and two buttons: the title opens the runs, so Details is no button of its own.
      assert.deepEqual(await group.locator('.issue-buttons > button').allInnerTexts(), ['Try all again', 'Don’t retry']);
      assert.doesNotMatch(await group.innerText(), /can.t check/);
      // A group with a run that ran commands Tico cannot check says so and offers no Try all again: retrying that run
      // is its own choice on its line.
      const later = panel.locator('[data-update-group="1"]');
      assert.equal(await later.locator('p').first().innerText(), '1 of them ran commands Tico can’t check');
      assert.deepEqual(await later.locator('.issue-buttons > button').allInnerTexts(), ['Don’t retry']);
      // The bots whose every run is in a group have no rows of their own repeating it; writer's ungrouped run keeps one.
      assert.equal(await panel.locator('[data-list] > .settings-issue:not([hidden])').count(), 4);
      assert.equal(await panel.locator('h2').innerText(), '4 things need you');
      assert.equal(await group.locator('[data-group-runs]').isVisible(), false);
      await group.locator('[data-group-details]').click();
      assert.equal(await group.locator('[data-group-details]').getAttribute('aria-expanded'), 'true');
      assert.deepEqual(await group.locator('[data-group-runs] .stopped-name').allInnerTexts(),
        ['Ship the receipts fix', 'Weekly numbers', 'Billing 3/5']);
      await group.locator('[data-group-details]').click();
      assert.equal(await group.locator('[data-group-runs]').isVisible(), false);
      // The unsafe run is not in the group: it keeps its own line and its own choices, Don't retry first.
      const risky = panel.locator('.stopped-line', {hasText: 'Review the migration plan'});
      assert.equal(await group.locator('[data-group-runs]', {hasText: 'Review the migration plan'}).count(), 0);
      assert.deepEqual(await risky.locator('button').allInnerTexts(), ['Don’t retry', 'Try again']);
      // A run with a choice saved for one of its requests stays on its own line, its other choice closed.
      const chosen = panel.locator('.stopped-line', {hasText: 'Draft the release notes'});
      assert.equal(await panel.locator('[data-group-runs]', {hasText: 'Draft the release notes'}).count(), 0);
      assert.deepEqual(await chosen.locator('button:enabled').allInnerTexts(), ['Don’t retry']);
      await shot(page, 'stopped-group-desktop');
      // While one grouped run's own choice saves, the group offers no choice; afterwards it does again.
      await group.locator('[data-group-details]').click();
      const own = group.locator('.stopped-line', {hasText: 'Ship the receipts fix'});
      slow(300);
      await own.locator('[data-run-decision=dismiss]').click();
      await own.locator('[data-result]', {hasText: 'Saving choice'}).waitFor();
      assert.equal(await group.locator('[data-group-decision]:enabled').count(), 0);
      await own.locator('[data-result]', {hasText: /^(?!Saving)./}).waitFor();
      assert.equal(await group.locator('[data-group-decision]:enabled').count(), 2);
      await group.locator('[data-group-details]').click();
      // Try all again posts each other run's own request once, then every grouped line says what was saved. While the
      // choices are saved, no grouped run offers its own choice.
      await group.locator('[data-group-decision=resume]').click();
      await group.locator('[data-group-result]', {hasText: 'Saving choices'}).waitFor();
      assert.equal(await group.locator('[data-group-runs] [data-run-decision]:enabled').count(), 0);
      await group.locator('[data-group-result]', {hasText: 'Queued to try again.'}).waitFor();
      assert.equal(await group.locator('[data-group-runs] [data-run-decision]:enabled').count(), 0);
      slow(0);
      assert.deepEqual(decisions.map(d => [d.id, d.decision]), [['u1', 'dismiss'], ['u2', 'resume'], ['u3', 'resume']]);
      assert.ok(decisions.slice(1).every(d => /Check existing work/.test(d.note)), 'each retry tells the bot to check first');
      await group.locator('[data-group-details]').click();
      assert.deepEqual((await group.locator('[data-group-runs] [data-result]').allInnerTexts()).slice(1),
        ['Queued to try again.', 'Queued to try again.']);
      assert.deepEqual(await risky.locator('button:enabled').allInnerTexts(), ['Don’t retry', 'Try again']);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Phone: the row keeps inside the panel, its buttons as tall as their words, and nothing scrolls sideways.
    {
      const {page, panel, errors} = await open({canReview: true, width: 390, height: 800});
      const fit = await panel.locator('[data-update-group="0"]').evaluate(el => {
        const box = node => node.getBoundingClientRect();
        const host = el.closest('.needs-you');
        return {group: box(el), panel: box(host), buttons: [...el.querySelectorAll('button')].map(b => box(b).height),
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      assert.ok(fit.group.left >= fit.panel.left && fit.group.right <= fit.panel.right, `group fits the panel: ${JSON.stringify(fit)}`);
      assert.ok(fit.buttons.every(h => h < 44), `buttons sized to their words: ${fit.buttons}`);
      assert.ok(fit.overflow <= 0, `no sideways scroll on a phone: ${fit.overflow}`);
      await shot(page, 'stopped-group-phone');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Someone who may not decide: the same group and times, no buttons, and no choice on any line.
    {
      const {page, panel, errors, decisions} = await open({canReview: false, width: 1280, height: 800});
      const group = panel.locator('[data-update-group="0"]');
      assert.match(await group.locator('.needs-title').innerText(), /^3 runs stopped/);
      assert.deepEqual(await group.locator('.issue-buttons > button').allInnerTexts(), []);
      assert.equal(await panel.locator('[data-run-decision], [data-group-decision]').count(), 0);
      assert.equal(await panel.locator('.stopped-picks').count(), 0);
      assert.deepEqual(decisions, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(`PASS: Tico's update stops are grouped in Needs you, at desktop and phone widths (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
