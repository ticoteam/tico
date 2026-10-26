// A bot Tico stopped (backend/bot_stops.py: five fast failed runs, or over its run budget). Guards: the server's words
// show to everyone, Release only to those who manage the bot; one click posts the release and the button goes once the
// status says the bot runs again; Health's stuck row for the stop carries the same Release. The run budget fields in the
// bot's settings are held until Save bot, then save with the bot's revision; Follow team clears both. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const WORDS = 'Tico stopped Ops: 340 runs in the last hour. This looks like a runaway loop, not normal work. Check its last runs, then release it or raise its budget.';

async function open(browser, role, hash) {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  const errors = [], posts = [];
  const manage = role === 'owner';
  const state = {held: true, own: {turns_per_hour: null, turns_per_day: null}, revision: 7};
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
    if (p === '/api/v2/status') return json({bots: [state.held ? {bot: 'ops', state: 'paused', stopped: 'budget', focus: WORDS} : {bot: 'ops', state: 'idle'}]});
    if (p === '/api/v2/health') return json({audience: manage ? 'owner' : 'human', attention: 1, checks: [], computers: [], waiting: [], slow: [], failures: [],
      checked: new Date().toISOString(), stuck: state.held ? [{bot: 'ops', name: 'Ops', reason: 'budget', queued: 1, oldest: new Date().toISOString(),
        computer: '', why: 'Tico stopped it for too many runs in a short time', fix: null}] : []});
    if (p === '/api/v2/bots/ops/definition') { posts.push({path: p, body: req.postDataJSON()}); state.revision += 1; return json({name: 'ops', revision: state.revision}); }
    if (p === '/api/v2/bots/ops/release') { posts.push({path: p, body: req.postDataJSON()}); state.held = false; return json({run_hold: false, budget: true}); }
    if (p === '/api/v2/bots/ops/budget') {
      if (!manage) return json({error: {detail: 'Only those who manage the bot', code: 'forbidden'}}, 403);
      if (req.method() === 'POST') {
        const body = req.postDataJSON();
        posts.push({path: p, body});
        if (body.expected_revision !== state.revision) return json({error: {detail: 'Changed elsewhere', code: 'conflict'}}, 409);
        for (const key of ['turns_per_hour', 'turns_per_day']) if (key in body) state.own[key] = body[key];
        state.revision += 1;
        return json({released: false});
      }
      return json({turns_per_hour: state.own.turns_per_hour ?? 300, turns_per_day: state.own.turns_per_day ?? 2500, own: {...state.own},
        team: {turns_per_hour: 300, turns_per_day: 2500}, runs: {hour: 340, day: 400}, held: state.held ? {words: WORDS} : null, revision: state.revision});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  return {page, errors, posts};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // Someone who cannot manage the bot reads why it stopped, with no Release.
      const {page, errors} = await open(browser, 'member', '#/bot/ops');
      await page.waitForFunction(text => document.body.innerText.includes(text), WORDS);
      assert.equal(await page.locator('[data-bot-release]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A manager releases it in one click; the buttons go when the status says it runs again.
      const {page, errors, posts} = await open(browser, 'owner', '#/bot/ops');
      await page.locator('[data-bot-release]').first().waitFor();
      await page.locator('[data-bot-release]').first().click();
      await page.waitForFunction(() => !document.querySelector('[data-bot-release]'));
      assert.deepEqual(posts, [{path: '/api/v2/bots/ops/release', body: {}}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Health's stuck row for the stop has the same Release.
      const {page, errors, posts} = await open(browser, 'owner', '#/health');
      const release = page.locator('#hl-page [data-stuck="ops"] [data-bot-release]');
      await release.waitFor();
      await release.click();
      await page.waitForFunction(() => !document.querySelector('#hl-page [data-stuck="ops"]'));
      assert.equal(posts.length, 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // The bot's settings: a number is held until Save bot, then saves with the revision the definition made;
      // Follow team clears both with the next Save bot.
      const {page, errors, posts} = await open(browser, 'owner', '#/bot/ops/settings');
      const hour = page.locator('[data-budget-key="turns_per_hour"]');
      const budgetPosts = () => posts.filter(post => post.path.endsWith('/budget'));
      await hour.waitFor();
      assert.equal(await page.locator('[data-budget-team]').count(), 0, 'nothing of its own to drop');
      assert.equal(await page.locator('[data-bot-budget-row] + [data-bot-budget-sub] [data-bot-release]').count(), 1, 'held: Release under the fields');
      await hour.fill('50');
      await hour.press('Tab');
      await page.locator('[data-bot-pending]:not([hidden])').waitFor();
      assert.deepEqual(posts, [], 'nothing saves before Save bot');
      await page.locator('#bot-editor button[type=submit]').click();
      await page.locator('[data-budget-team]').waitFor();
      assert.deepEqual(budgetPosts()[0], {path: '/api/v2/bots/ops/budget', body: {turns_per_hour: 50, expected_revision: 8}});
      assert.equal(await page.locator('[data-budget-key="turns_per_hour"]').inputValue(), '50');
      await page.locator('[data-budget-team]').click();
      await page.locator('#bot-editor button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('[data-budget-key="turns_per_hour"]')?.value === '' && !document.querySelector('[data-budget-team]'));
      assert.deepEqual(budgetPosts()[1].body, {turns_per_hour: null, turns_per_day: null, expected_revision: 10});
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // No budget row for someone the server would refuse.
      const {page, errors} = await open(browser, 'member', '#/bot/ops/settings');
      await page.waitForTimeout(800);
      assert.equal(await page.locator('[data-budget-key]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('bot-release ok');
})().catch(error => { console.error(error); process.exit(1); });
