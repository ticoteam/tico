// One-click pause, refusal/retry, refresh, permissions, billing and phone layout. Synthetic fixtures only.
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
  await page.route('**/*', route => {
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
    if (p === '/api/v2/bots/ops/pause-state') return json({state: state.paused ? 'paused' : 'active', revision: state.revision,
      can_control: manage, operator: 'Ana', paused_by: state.record || null, open_tasks: state.tasks || 0, company_paused: !!state.billing});
    if (p === '/api/v2/bots/ops/control') {
      const body = req.postDataJSON(); posts.push(body);
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

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const {page, errors, posts, state} = await open(browser, 'owner', '#/bot/ops/info');
    const strip = page.locator('#bot-control-host');
    await strip.getByRole('button', {name: 'Pause', exact: true}).waitFor();
    assert.equal(await page.locator('#main > :first-child').getAttribute('id'), 'bot-control-host');
    let dialogs = 0; page.on('dialog', async d => {dialogs++; await d.dismiss();});
    await strip.getByRole('button', {name: 'Pause', exact: true}).focus();
    await page.keyboard.press('Enter');
    await strip.getByRole('button', {name: 'Unpause', exact: true}).waitFor();
    await page.waitForFunction(() => document.querySelector('#bot-control-host').textContent.includes('Paused by Ana'));
    assert.match(await strip.innerText(), /2026-10-28 12:30 UTC/);
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
    state.paused = true; state.record = null; state.tasks = 3;
    await page.evaluate(() => botAlertDraw());
    await strip.getByRole('button', {name: 'Unpause', exact: true}).waitFor();
    assert.match(await strip.innerText(), /3 open tasks are waiting on it/);
    assert.equal(await strip.locator('a').getAttribute('href'), '#/bot/ops/tasks');
    assert.equal(await strip.locator('.bot-pause-record').innerText(), 'Paused');
    await page.setViewportSize({width: 375, height: 812});
    await page.waitForFunction(() => document.querySelector('#side').getBoundingClientRect().right <= 1);
    const box = await strip.boundingBox(), button = await strip.getByRole('button', {name: 'Unpause', exact: true}).boundingBox();
    assert.ok(button.x >= box.x && button.x + button.width <= box.x + box.width + 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.SHOTS) await page.screenshot({path: process.env.SHOTS + '/bot-paused-phone.png'});
    state.billing = true;
    await page.evaluate(() => botAlertDraw());
    await strip.getByText('Company is paused for billing').waitFor();
    assert.equal(await strip.getByRole('button', {name: 'Unpause', exact: true}).count(), 0);
    assert.match(await strip.innerText(), /Ops is paused/);
    assert.equal(await strip.getByRole('link', {name: 'Add credit'}).getAttribute('href'), '#/settings/billing');
    assert.deepEqual(errors, []);
    await page.close();
    const member = await open(browser, 'member', '#/bot/ops/info');
    member.state.paused = true;
    await member.page.evaluate(() => botAlertDraw());
    await member.page.getByText('Ask Ana to unpause', {exact: true}).waitFor();
    assert.equal(await member.page.locator('#bot-control-host button').count(), 0);
    assert.deepEqual(member.errors, []);
    await member.page.close();
    console.log('bot pause strip: 1 passed, 0 failures');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
