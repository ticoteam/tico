// Offline branch picker, computer ownership and definition editor contracts.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    page.setDefaultTimeout(t(5000));
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = {host: 'keeper', status: 'active', state: 'active', can_chat: true, can_manage: true,
      my_access: {see: true, read: true, write: true}, schedules: [], revision: 1, thread_mode: 'personal', users: []};
    const original = {...base, name: 'architect', slug: 'architect', display_name: 'Architect', owner_id: 'sam', shared: true, repo: 'bot-architect', reports_to: 'bot:lead'};
    const bots = [original];
    const people = [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}];
    let computers = [
      {id: 'ana-mac', label: 'My Mac', owner_id: 'ana'},
      {id: 'sam-mac', label: 'Other Mac', owner_id: 'sam'},
      {id: 'old-mac', label: 'Old Mac', owner_id: 'ana', revoked_at: '2026-01-01'}];
    let collision = true, stale = true;
    await page.route('**/*', route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], company: [], reports: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p === '/api/v2/computers') return json({computers});
      if (p.endsWith('/branches')) return json({original: 'architect', shared: original.shared,
        branches: bots.filter(b => b.shared_from).map(b => ({...b, id: b.id || b.name, slug: b.name}))});
      if (p.endsWith('/copies')) {
        writes.push([p, JSON.parse(request.postData())]);
        if (collision) return route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({error: {detail: 'That computer already runs the original or a branch of it. Choose another computer.'}})});
        const branch = {...base, id: 'architect-ana', name: 'architect-ana', slug: 'architect-ana', display_name: 'Architect', owner_id: 'ana', shared_from: 'architect',
          status: JSON.parse(request.postData()).runner_id ? 'active' : 'planned'};
        bots.push(branch); return json(branch);
      }
      if (p.endsWith('/definition')) {
        writes.push([p, JSON.parse(request.postData())]);
        if (p.includes('architect-ana') && stale) {
          stale = false;
          return route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({error: {code: 'version_conflict', detail: 'Bot configuration changed; refresh before saving'}})});
        }
        return json({revision: 3});
      }
      if (p === '/api/v2/bots/architect-ana') return json({revision: 2});
      if (p === '/api/v2/bots' && request.method() === 'POST') {
        writes.push([p, JSON.parse(request.postData())]);
        return json({slug: 'head-of-marketing', display_name: 'Head of Marketing'});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    // On a phone the top line is only the bot and its tabs; branches wait at the top of More.
    await page.goto('https://tico-ui.test/#/bot/architect/more');
    await page.locator('[data-branch-make]').waitFor();
    assert.equal(await page.locator('#bot-top #bot-branches').count(), 0);
    assert.equal(await page.locator('#bot-more-top > #bot-branches').count(), 1);
    assert.equal(await page.locator('[data-branch-picker]').count(), 0);
    await page.locator('[data-branch-make]').click();
    let dialog = page.locator('#branch-editor');
    await dialog.waitFor({state: 'visible'});
    assert.deepEqual(await dialog.locator('[name=runner_id] option').allTextContents(), ['My Mac']);
    await dialog.locator('[type=submit]').click();
    await dialog.locator('[data-branch-status]').filter({hasText: 'already runs'}).waitFor();
    assert.equal(await dialog.locator('[type=submit]').isEnabled(), true);
    collision = false;
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => BOT?.slug === 'architect-ana');
    await page.locator('[data-branch-picker] option[value=architect-ana]').waitFor({state: 'attached'});
    assert.equal(await page.locator('[data-branch-picker]').inputValue(), 'architect-ana');
    assert.equal(await page.locator('[data-branch-make]').count(), 0);
    assert.equal(await page.locator('#bot-branches a').getAttribute('href'), '#/bot/architect');
    assert.deepEqual(writes.find(([path]) => path.endsWith('/copies'))[1], {runner_id: 'ana-mac'});
    original.shared = false;
    await page.evaluate(() => botBranchesLoad('architect-ana'));
    assert.match(await page.locator('#bot-branches').innerText(), /branches off/);
    await page.evaluate(() => settingsEditBot('architect-ana'));
    dialog = page.locator('#bot-editor');
    await dialog.locator('[name=status]').waitFor();
    assert.equal(await dialog.locator('[name=description]').count(), 0);
    assert.equal(await dialog.locator('[name=model_effort]').count(), 0);
    await dialog.locator('[name=status]').selectOption('paused');
    await dialog.locator('[type=submit]').click();
    await dialog.locator('[data-branch-status]').filter({hasText: 'configuration changed'}).waitFor();
    await page.waitForFunction(() => !document.querySelector('#bot-editor [type=submit]').disabled);
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#bot-editor [type=submit]')?.disabled);
    assert.deepEqual(writes.at(-1)[1], {status: 'paused', expected_revision: 2});
    await page.waitForFunction(() => document.querySelector('#bot-branches select'));
    await page.evaluate(() => { location.hash = '#/bot/architect/more'; });
    await page.waitForFunction(() => BOT?.slug === 'architect' && BOT?.tab === 'info');
    await page.locator('[data-branch-picker]').waitFor();
    assert.equal(await page.locator('[data-branch-picker] option[value=architect-ana]').count(), 1);
    dialog = page.locator('#bot-editor');
    await page.evaluate(() => { S.emps.find(e => e.name === 'architect').shared = false; SETTINGS_DATA.models = []; SETTINGS_DATA.people = S.people; SETTINGS_DATA.machines = []; settingsEditBot('architect'); });
    const allow = dialog.locator('[name=shared]');
    await allow.waitFor();
    assert.equal(await allow.isChecked(), false);
    assert.match(await allow.locator('..').innerText(), /Allow branches/);
    await allow.check();
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved');
    assert.equal(writes.at(-1)[1].shared, true);
    // Rebuilding the inline form leaves only its current settings subscription active.
    const refreshed = await page.evaluate(() => {
      settingsEditBot('architect', true);
      const editor = document.querySelector('#bot-editor');
      const bot = S.emps.find(e => e.name === 'architect'), name = bot.display_name;
      bot.display_name = 'Refreshed Architect';
      document.dispatchEvent(new Event('tico:settings-loaded'));
      const label = editor.querySelector('[data-edit-access]')?.getAttribute('aria-label');
      bot.display_name = name;
      return label;
    });
    assert.equal(refreshed, 'Change access for Refreshed Architect');
    // New bot creation remains in the company Settings flow.
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => !!document.querySelector('dialog#bot-editor'));
    await page.evaluate(() => {
      settingsChoiceFromValue = () => ({harness: 'claude', model: 'm', effort: 'high'});
      settingsEditBot('');
    });
    dialog = page.locator('#bot-editor');
    const slug = dialog.locator('[name=slug]');
    assert.equal(await slug.isVisible(), false);
    await dialog.locator('[name=display_name]').fill('Head of Marketing');
    assert.equal(await slug.inputValue(), 'head-of-marketing');
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#bot-editor').open);
    const added = writes.at(-1);
    assert.equal(added[0], '/api/v2/bots');
    assert.equal(added[1].display_name, 'Head of Marketing');
    assert.ok(!('slug' in added[1]) && !('repo' in added[1]));
    // Eligibility comes from both the current computer assignments and the roster's family.
    original.shared = true;
    computers = [{id: 'ana-mac', label: 'My Mac', owner_id: 'ana', bots: ['architect']},
      {id: 'ana-other', label: 'Second Mac', owner_id: 'ana', bots: []}];
    await page.evaluate(() => botBranchCreate('architect'));
    dialog = page.locator('#branch-editor');
    assert.deepEqual(await dialog.locator('[name=runner_id] option').allTextContents(), ['Second Mac']);
    await dialog.locator('[data-branch-close]').click();
    // No submit is needed to discover the collision, and a planned branch posts the existing empty payload.
    computers.pop();
    await page.evaluate(() => botBranchCreate('architect'));
    await dialog.locator('.bot-editor-body > p').filter({hasText: /Your computers already run the original or a branch/}).waitFor();
    assert.equal(await dialog.locator('[name=runner_id]').count(), 0);
    assert.equal(await dialog.locator('[type=submit]').innerText(), 'Create planned branch');
    await dialog.locator('[type=submit]').click();
    // The submit refreshes the roster (S.emps) and then opens the branch: wait for that, so it cannot replace the row added below.
    await page.waitForFunction(() => !document.querySelector('#branch-editor') && location.hash === '#/bot/architect-ana' && BOT?.slug === 'architect-ana');
    assert.deepEqual(writes.at(-1)[1], {});
    computers = [{id: 'ana-mac', label: 'My Mac', owner_id: 'ana', bots: ['architect-sam']}];
    await page.evaluate(() => {
      S.emps.push({name: 'architect-sam', shared_from: 'architect', owner_id: 'sam', machine: {runner_id: 'ana-mac'}});
      return botBranchCreate('architect');
    });
    await dialog.locator('.bot-editor-body > p').filter({hasText: /Your computers already run/}).waitFor();
    await dialog.locator('[data-branch-close]').click();
    computers = [];
    await page.evaluate(() => botBranchCreate('architect'));
    await dialog.locator('.bot-editor-body > p').filter({hasText: /You have no computer yet/}).waitFor();
    assert.equal(await dialog.locator('[type=submit]').isEnabled(), true);
    await dialog.locator('[data-branch-close]').click();
    assert.deepEqual(errors, []);
    console.log('bot branches: passed');
    await page.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
