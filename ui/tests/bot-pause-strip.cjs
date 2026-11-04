// The bot's Settings pause row: one-click pause, refusal/retry, refresh, permissions, billing and phone layout. No strip
// on the page itself; the header's Paused badge follows the control. Synthetic fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');


async function open(browser, role, hash) {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  const errors = [], posts = [];
  const manage = role === 'owner';
  const state = {revision: 7};
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: manage ? 'ana' : 'cara', role: manage ? 'owner' : 'human', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', host: 'keeper', can_chat: true, can_manage: manage,
      users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: state.revision}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle'}]});
    if (p === '/api/v2/bots/ops/pause-state') {
      const snapshot = {state: state.paused ? 'paused' : 'active', revision: state.revision,
        can_control: manage, owner_id: 'Ana', paused_by: state.record || null, open_tasks: state.tasks || 0, company_paused: !!state.billing};
      state.reads = (state.reads || 0) + 1;
      const held = state.holdNext;
      if (held) { delete state.holdNext; held.captured(); await held.wait; }
      const result = await json(snapshot);
      held?.finished();
      return result;
    }
    if (p === '/api/v2/bots/ops/control') {
      const body = req.postDataJSON(); posts.push(body);
      if (body.expected_revision !== state.revision) return json({error: {detail: 'Stale revision', code: 'version_conflict'}}, 409);
      if (state.fail) return json({error: {detail: 'Changed elsewhere', code: 'version_conflict'}}, 409);
      state.paused = body.action === 'pause'; state.revision++;
      state.record = state.paused ? {name: 'Ana', at: '2026-10-28T12:30:00Z'} : null;
      return json({revision: state.revision});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  return {page, errors, posts, state};
}

function heldRead(state) {
  let captured, release, finished;
  const hold = {captured: new Promise(resolve => { captured = resolve; }),
    finished: new Promise(resolve => { finished = resolve; }), release: () => release()};
  state.holdNext = {captured, finished, wait: new Promise(resolve => { release = resolve; })};
  return hold;
}
async function staleReadAfterControl(browser, action) {
  const {page, errors, state} = await open(browser, 'owner', '#/bot/ops/settings');
  let held;
  try {
    await page.locator('[data-bot-pause-action]').waitFor();
    await page.waitForFunction(() => !BOT.pauseLoading);
    if (action === 'resume') {
      state.paused = true;
      await page.evaluate(() => botPauseLoad());
    }
    const revision = state.revision, readsBefore = state.reads;
    held = heldRead(state);
    await page.evaluate(() => { void botPauseLoad(); });
    await held.captured;
    await page.locator(`[data-bot-pause-action="${action}"]`).click();
    const next = action === 'pause' ? 'resume' : 'pause';
    await page.locator(`[data-bot-pause-action="${next}"]`).waitFor();
    // The older GET arrives only after the POST has succeeded and pauseBusy is false.
    held.release(); await held.finished;
    await page.waitForFunction(() => !BOT.pauseLoading);
    assert.deepEqual(await page.evaluate(() => [BOT.pauseState.state, BOT.pauseState.revision]),
      [action === 'pause' ? 'paused' : 'active', revision + 1], `${action}: old GET cannot replace the committed state or revision`);
    assert.ok(state.reads >= readsBefore + 2, 'discarding the stale GET starts a new authoritative read');
    // A subsequent action must use the current revision, including the toast's shortcut.
    if (action === 'pause') await page.locator('.toast button').filter({hasText: 'Unpause'}).last().click();
    else await page.locator('[data-bot-pause-action="pause"]').click();
    await page.waitForFunction(() => !BOT.pauseBusy && !BOT.pauseLoading);
    assert.equal(state.revision, revision + 2);
    assert.deepEqual(errors, []);
  } finally { held?.release(); await page.close(); }
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const action of (process.env.RACE_ACTION ? [process.env.RACE_ACTION] : ['pause', 'resume'])) await staleReadAfterControl(browser, action);
    const {page, errors, posts, state} = await open(browser, 'owner', '#/bot/ops/settings');
    const strip = page.locator('#bot-control-host');
    await strip.getByRole('button', {name: 'Pause', exact: true}).waitFor();
    // One row at the bottom of Settings, and nothing above or below the chat.
    assert.equal(await page.locator('#pane-settings > section.card').last().getAttribute('id'), 'bot-pause-card');
    assert.match(await strip.innerText(), /^Pause this bot\n/);
    await page.evaluate(() => { location.hash = '#/bot/ops'; });
    await page.locator('#pane-settings').waitFor({state: 'hidden'});
    assert.equal(await page.locator('#bot-control-host:visible, .bot-control-strip').count(), 0);
    await page.evaluate(() => { location.hash = '#/bot/ops/settings'; });
    await strip.waitFor();
    // Refreshes with the page's view unchanged do not re-read the pause state until it is a minute old.
    await page.waitForFunction(() => !BOT.pauseLoading);
    const readsBefore = state.reads;
    await page.evaluate(() => { botAlertDraw(); botAlertDraw(); botAlertDraw(); });
    await page.waitForFunction(() => !BOT.pauseLoading);
    assert.equal(state.reads, readsBefore, 'repeated refreshes with an unchanged state make no pause-state request');
    await page.evaluate(() => { BOT.pauseLoadedAt = Date.now() - 60_001; botAlertDraw(); });
    await page.waitForFunction(() => !BOT.pauseLoading);
    assert.equal(state.reads, readsBefore + 1, 'a stale view is re-read once on refresh');
    let dialogs = 0; page.on('dialog', async d => {dialogs++; await d.dismiss();});
    await strip.getByRole('button', {name: 'Pause', exact: true}).focus();
    await page.keyboard.press('Enter');
    await strip.getByRole('button', {name: 'Unpause', exact: true}).waitFor();
    await page.waitForFunction(() => document.querySelector('#bot-control-host').textContent.includes('Paused by Ana'));
    assert.match(await strip.innerText(), /2026-10-28 12:30 UTC/);
    assert.equal(await page.locator('#bot-top #bot-paused .bot-paused-badge').innerText(), 'Paused');
    assert.equal(dialogs, 0);
    assert.equal(posts[0].action, 'pause');
    assert.equal(await strip.locator('[aria-live=polite]').count(), 1);
    state.fail = true;
    await strip.getByRole('button', {name: 'Unpause', exact: true}).click();
    await strip.getByRole('button', {name: 'Retry', exact: true}).waitFor();
    assert.match(await strip.innerText(), /Ops is paused[\s\S]*Could not unpause: Changed elsewhere/);
    state.fail = false;
    await strip.getByRole('button', {name: 'Retry', exact: true}).click();
    await strip.getByRole('button', {name: 'Pause', exact: true}).waitFor();
    assert.equal(await page.locator('#bot-paused .bot-paused-badge').count(), 0);
    state.paused = true; state.record = null; state.tasks = 3;
    // A change made elsewhere shows once the page's last read is a minute old.
    await page.evaluate(() => { BOT.pauseLoadedAt = Date.now() - 60_001; botAlertDraw(); });
    await strip.getByRole('button', {name: 'Unpause', exact: true}).waitFor();
    assert.match(await strip.innerText(), /3 open tasks are waiting on it/);
    assert.equal(await strip.locator('a').getAttribute('href'), '#/bot/ops/tasks');
    assert.equal(await strip.locator('.bot-pause-record').innerText(), 'Paused');
    // The roster still says active (its fixture never changes); the fresh read is the authority for the badges.
    assert.equal(await page.locator('#bot-top #bot-paused .bot-paused-badge').innerText(), 'Paused', 'header follows a paused read');
    assert.equal(await page.locator('#tree a[data-org="b:ops"] .bot-paused-badge').innerText(), 'Paused', 'tree follows a paused read');
    await page.setViewportSize({width: 375, height: 812});
    await page.waitForFunction(() => document.querySelector('#side').getBoundingClientRect().right <= 1);
    const box = await strip.boundingBox(), button = await strip.getByRole('button', {name: 'Unpause', exact: true}).boundingBox();
    assert.ok(button.x >= box.x && button.x + button.width <= box.x + box.width + 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.SHOTS) await page.screenshot({path: process.env.SHOTS + '/bot-paused-phone.png'});
    state.billing = true;
    await page.evaluate(() => { BOT.pauseLoadedAt = Date.now() - 60_001; botAlertDraw(); });
    await strip.getByText('Company is paused for billing').waitFor();
    assert.equal(await strip.getByRole('button', {name: 'Unpause', exact: true}).count(), 0);
    assert.match(await strip.innerText(), /Ops is paused/);
    assert.equal(await strip.getByRole('link', {name: 'Add credit'}).getAttribute('href'), '#/settings/billing');
    assert.deepEqual(errors, []);
    await page.close();
    const member = await open(browser, 'member', '#/bot/ops/settings');
    member.state.paused = true;
    await member.page.waitForFunction(() => BOT?.slug === 'ops' && BOT.pauseLoadedAt != null && !BOT.pauseLoading);
    await member.page.evaluate(() => { BOT.pauseLoadedAt = Date.now() - 60_001; botAlertDraw(); });
    await member.page.getByText(/Ask Ana to unpause\./).waitFor();
    assert.equal(await member.page.locator('#bot-control-host button').count(), 0);
    assert.deepEqual(member.errors, []);
    await member.page.close();
    console.log('bot pause settings: 3 passed, 0 failures');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
