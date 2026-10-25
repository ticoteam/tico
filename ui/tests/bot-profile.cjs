// A bot's profile picker in its settings (ui/app/bot-profile.js, backend/profiles.py). Guards: only someone who manages
// the bot can change it; each save carries the bot's revision; Custom's own fields save as Custom; leaving Custom asks
// first, naming how many rules go, and sends nothing when declined; a Lead's digest hour saves; at phone width the
// row stays inside the screen. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const CHOICES = ['doer', 'reviewer', 'lead', 'responder', 'monitor', 'custom'].map(name => ({name, label: name[0].toUpperCase() + name.slice(1),
  description: `The ${name} profile.`, pulls: 'all', sweeps: true, takes: 'all', digest_hour: name === 'lead' ? 9 : null, rules: []}));
const effective = (name, own = {}) => ({name, label: name[0].toUpperCase() + name.slice(1), description: `The ${name} profile.`, chosen: true,
  batch_seconds: 900, batch_scope: 'conversation', sweep_seconds: 900, digest_hour: name === 'lead' ? own.digest_hour ?? 9 : null,
  pulls: own.pulls || 'all', sweeps: own.sweeps ?? true, takes: own.takes || 'all', options: own, summary: `Runs as ${name}.`});

async function open(browser, role, viewport = {width: 1280, height: 900}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], posts = [], dialogs = [];
  const manage = role === 'owner';
  const state = {name: 'custom', own: {}, revision: 5, rules: 3};
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
      users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: state.revision, profile: effective(state.name, state.own)}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle'}]});
    if (p === '/api/v2/profiles') return json({profiles: CHOICES});
    if (p === '/api/v2/bots/ops/profile' && req.method() === 'GET') return json({bot: 'ops', profile: effective(state.name, state.own), rules: state.rules, revision: state.revision});
    if (p === '/api/v2/bots/ops/profile') {
      const body = req.postDataJSON();
      posts.push(body);
      if (body.expected_revision !== state.revision) return json({error: {detail: 'Bot configuration changed; refresh before saving', code: 'version_conflict'}}, 409);
      if (state.name === 'custom' && body.profile !== 'custom' && state.rules && !body.confirm_drop_rules)
        return json({error: {detail: `ops has ${state.rules} wake rules of its own. Send confirm_drop_rules to go on`, code: 'conflict'}}, 409);
      const {profile, expected_revision, confirm_drop_rules, ...own} = body;
      if (profile !== 'custom') state.rules = 0;
      state.own = profile === state.name ? {...state.own, ...own} : own;
      state.name = profile; state.revision += 1;
      return json({bot: 'ops', profile: effective(state.name, state.own), rules: state.rules, revision: state.revision});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  page.on('dialog', dialog => { dialogs.push(dialog.message()); return dialogs.length === 1 ? dialog.dismiss() : dialog.accept(); });
  await page.goto('https://tico-ui.test/#/bot/ops/settings');
  await page.locator(manage ? '[data-bot-profile]' : '#bot-editor:has-text("read-only")').waitFor();
  return {page, errors, posts, dialogs};
}
const picker = page => page.locator('[data-bot-profile]');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // Someone who cannot manage the bot gets no picker.
      const {page, errors} = await open(browser, 'member');
      assert.equal(await page.locator('[data-bot-profile], [data-profile-key]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors, posts, dialogs} = await open(browser, 'owner');
      // The choices come from the server, with the bot's own profile picked.
      await page.waitForFunction(() => document.querySelectorAll('[data-bot-profile] option').length === 6);
      assert.equal(await picker(page).inputValue(), 'custom');
      assert.equal(await page.locator('[data-profile-rules]').getAttribute('href'), '#/settings/rules?bot=ops');
      // Custom's own field saves as Custom, with the revision read.
      await page.locator('[data-profile-key="takes"]').selectOption('review');
      await page.waitForFunction(() => document.querySelector('[data-profile-key="takes"]')?.value === 'review' && !document.querySelector('[data-profile-key="takes"]').disabled);
      assert.deepEqual(posts[0], {profile: 'custom', takes: 'review', expected_revision: 5});
      // Leaving Custom: declined, nothing more is sent and the picker shows Custom again.
      await picker(page).selectOption('doer');
      await page.waitForFunction(() => document.querySelector('[data-bot-profile]')?.value === 'custom' && !document.querySelector('[data-bot-profile]').disabled);
      assert.equal(posts.length, 2, 'only the refused try');
      assert.match(dialogs[0], /3 wake rules/);
      // Accepted: sent again with the confirm.
      await picker(page).selectOption('lead');
      await page.locator('[data-profile-key="digest_hour"]').waitFor();
      assert.deepEqual(posts.at(-1), {profile: 'lead', expected_revision: 6, confirm_drop_rules: true});
      assert.equal(await page.locator('[data-profile-custom]').count(), 0);
      // A Lead's digest hour.
      await page.locator('[data-profile-key="digest_hour"]').selectOption('7');
      await page.waitForFunction(() => document.querySelector('[data-profile-key="digest_hour"]')?.value === '7' && !document.querySelector('[data-profile-key="digest_hour"]').disabled);
      assert.deepEqual(posts.at(-1), {profile: 'lead', digest_hour: 7, expected_revision: 7});
      assert.equal(await page.locator('[data-profile-summary]').innerText(), 'Runs as lead.');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Phone width: the picker and Custom's fields stay inside the screen.
      const {page, errors} = await open(browser, 'owner', {width: 390, height: 844});
      await page.locator('[data-profile-custom]').waitFor();
      const out = await page.evaluate(() => [...document.querySelectorAll('[data-bot-profile-box], [data-bot-profile-box] select, [data-bot-profile-box] a')]
        .filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => el.outerHTML.slice(0, 60)));
      assert.deepEqual(out, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('bot-profile ok');
})().catch(error => { console.error(error); process.exit(1); });
