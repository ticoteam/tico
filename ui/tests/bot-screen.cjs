// Screen permissions, setup and reverse states in the bot’s Info and Settings tabs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async me => {
      const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
      page.setDefaultTimeout(t(5000));
      await page.emulateMedia({reducedMotion: 'reduce'});
      const errors = [], writes = [];
      page.on('pageerror', error => errors.push(error.message));
      const base = {host: 'keeper', status: 'active', state: 'active', can_chat: true, can_manage: true, owner_id: 'sam',
        my_access: {see: true, read: true, write: true}, schedules: [], revision: 1, thread_mode: 'personal', users: []};
      const computer = (id, more) => ({runner_id: id, generation: 1, label: id, owner_id: 'sam', last_seen_at: new Date().toISOString(),
        platform: 'linux', version: '0.3.52', screen: 'off', screen_available: false, ...more});
      const bot = (name, online, machine) => ({...base, name, slug: name, display_name: name, online, machine});
      const bots = [
        bot('ready', true, computer('c-ready', {screen: 'control', screen_available: true})),
        bot('off', true, computer('c-off', {screen_available: true})),
        bot('linux', true, computer('c-linux')),
        bot('mac', true, computer('c-mac', {platform: 'darwin'})),
        bot('offline', false, computer('c-offline', {screen: 'view', screen_available: true})),
        bot('none', false, null),
        bot('unowned', true, computer('c-unowned', {owner_id: null, screen: 'view', screen_available: true})),
        bot('flip', true, computer('c-flip', {owner_id: 'ana', screen: 'view'})),
        // Seen, not read: the server leaves out the computer, online and agent (backend/views.py).
        {name: 'seen', display_name: 'seen', state: 'active', status: 'active', description: '', reports_to: '', owner_id: 'sam',
         my_access: {see: true, read: false, write: false}, can_manage: false, bot_owners: [], private_tasks_default: false,
         team: '', org_parent: '', is_branch: false, users: [], icon: '', helper: false, can_chat: false}];
      await page.route('**/*', route => {
        const request = route.request(), p = new URL(request.url()).pathname;
        const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/api/me') return json({...me, cloud: true, registered: true, config: {version: '0.3.52', app_name: 'Tico'}});
        if (p === '/api/employees') return json(bots);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}, {id: 'bo', name: 'Bo'}], teams: {}});
        if (p === '/api/issues') return json([]);
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], schedules: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/goals') return json({goals: [], chain: [], company: [], reports: []});
        if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, today: {}});
        if (p === '/api/v2/tasks') return json({tasks: []});
        if (p === '/api/v2/operations') return json({machines: bots.filter(b => b.machine).map(b => ({id: b.machine.runner_id, label: b.machine.label,
          owner_id: b.machine.owner_id, online: b.online, platform: b.machine.platform, screen: b.machine.screen, screen_available: b.machine.screen_available})), services: []});
        if (p === '/api/v2/conversations') return json({conversations: []});
        if (/^\/api\/v2\/computers\/[^/]+\/screen$/.test(p)) {
          const body = JSON.parse(request.postData()); writes.push(body);
          const machine = bots.find(b => b.machine?.runner_id === p.split('/')[4])?.machine;
          if (machine) machine.screen = body.mode;
          return json({screen: body.mode, screen_available: true});
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        if (p.endsWith('/files')) return json({files: []});
        return json(unrouted(p));
      });
      // The row's words, without the icon's ligature name.
      const rowText = () => page.locator('#bot-screen-row').evaluate(el => {
        let text = el.innerText;
        for (const icon of el.querySelectorAll('.nav-icon')) text = text.replace(icon.textContent, '');
        return text.replace(/\s+/g, ' ').trim();
      });
      const open = async slug => {
        await page.goto(`https://tico-ui.test/#/bot/${slug}/more`);
        await page.locator('#bot-screen-row').waitFor();
        return {state: await page.locator('#bot-screen-row').getAttribute('data-screen-state'),
                text: await rowText(),
                header: await page.locator('#bot-top #bot-screen').count()};
      };
      return {page, errors, writes, open, rowText, bots};
    };

    const admin = await visit({id: 'ana', name: 'Ana', role: 'owner'});
    await admin.open('ready');
    assert.equal(await admin.page.locator('#bot-top #bot-screen').count(), 1);
    assert.deepEqual(await admin.page.locator('[data-bot-detail-tab]').allTextContents(), ['Info', 'Rules', 'Instructions', 'History', 'Settings']);
    assert.equal(await admin.page.locator('#sess-card').count(), 0);
    await admin.page.locator('[data-bot-detail-tab=settings]').click();
    await admin.page.locator('#bot-editor form').waitFor();
    assert.equal(await admin.page.locator('dialog#bot-editor').count(), 0);
    assert.equal(await admin.page.locator('[data-screen-toggle]').isChecked(), true);
    await admin.page.locator('[data-screen-toggle]').uncheck();
    await admin.page.waitForFunction(() => document.querySelector('[data-screen-toggle]')?.disabled === false);
    assert.deepEqual(admin.writes, [{mode: 'off'}]);
    assert.equal(await admin.page.locator('[data-screen-toggle]').isChecked(), false);
    await admin.page.locator('[data-screen-toggle]').check();
    await admin.page.locator('[data-screen-mode]').waitFor();
    await admin.page.locator('[data-screen-mode]').selectOption('view');
    await admin.page.waitForFunction(() => document.querySelector('[data-screen-mode]')?.disabled === false);
    assert.deepEqual(admin.writes, [{mode: 'off'}, {mode: 'control'}, {mode: 'view'}]);

    // An unavailable screen reveals one primary command only after the operator asks to enable it.
    await admin.open('linux');
    assert.equal(await admin.page.locator('[data-screen-copy]:visible').count(), 0);
    await admin.page.locator('[data-bot-detail-tab=settings]').click();
    await admin.page.locator('[data-screen-toggle]').check();
    assert.equal(await admin.page.locator('[data-screen-copy]:visible').count(), 1);
    assert.equal(await admin.page.locator('[data-screen-toggle]').isChecked(), true);
    const linux = admin.bots.find(b => b.name === 'linux'); linux.machine.screen_available = true;
    await admin.page.evaluate(() => refresh());
    await admin.page.locator('[data-screen-mode]').waitFor();
    assert.equal(await admin.page.locator('[data-screen-copy]').count(), 0);
    assert.equal(admin.writes.at(-1).mode, 'control');

    // Draft settings survive navigation, and Instructions stays inline.
    await admin.page.locator('#bot-editor [name=description]').fill('Unsaved draft');
    await admin.page.locator('[data-bot-detail-tab=instructions]').click();
    await admin.page.locator('#bot-instructions-changes').fill('Clarify the review checklist.');
    await admin.page.locator('[data-bot-detail-tab=history]').click();
    await admin.page.locator('#bot-runs-card').waitFor();
    assert.equal(await admin.page.locator('#sess-turns').count(), 0);
    await admin.page.locator('[data-bot-detail-tab=settings]').click();
    assert.equal(await admin.page.locator('#bot-editor [name=description]').inputValue(), 'Unsaved draft');
    await admin.page.setViewportSize({width: 390, height: 844});
    await admin.page.evaluate(() => setDrawer(false));
    assert.equal(await admin.page.locator('[data-bot-detail-tab=settings]').isVisible(), true);
    assert.equal(await admin.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.TICO_SCREENSHOTS) {
      await admin.page.screenshot({path: '/tmp/tico-bot-settings-mobile.png'});
      await admin.page.locator('[data-bot-detail-tab=info]').click();
      await admin.page.locator('#bot-setup-card').waitFor();
      await admin.page.screenshot({path: '/tmp/tico-bot-info-mobile.png'});
      await admin.page.setViewportSize({width: 1280, height: 800});
      await admin.page.screenshot({path: '/tmp/tico-bot-info-desktop.png'});
    }
    await admin.page.locator('[data-bot-detail-tab=instructions]').click();
    assert.equal(await admin.page.locator('#bot-instructions-changes').inputValue(), 'Clarify the review checklist.');
    assert.deepEqual(admin.errors, []);
    await admin.page.close();

    // A bot manager cannot enable someone else's computer screen.
    const member = await visit({id: 'bo', name: 'Bo', role: 'member'});
    const row = await member.open('ready');
    assert.equal(row.state, 'not_allowed');
    assert.equal(row.header, 0);
    await member.page.locator('[data-bot-detail-tab=settings]').click();
    await member.page.locator('#bot-editor form').waitFor();
    assert.equal(await member.page.locator('[data-screen-toggle]').count(), 0);
    await member.open('seen');
    assert.deepEqual(await member.page.locator('[data-bot-detail-tab]').allTextContents(), ['Info', 'Rules']);
    assert.equal(await member.page.locator('#bot-editor').count(), 0);
    assert.equal(await member.page.locator('[data-screen-toggle]').count(), 0);
    assert.deepEqual(member.writes, []);
    assert.deepEqual(member.errors, []);
    await member.page.close();
    console.log('PASS: inline bot tabs preserve drafts and privacy; screen setup, off/view/control and operator permissions');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
