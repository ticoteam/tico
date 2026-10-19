// A bot's Screen row in More: always there, with its status, and when the screen is not ready, why and what to do.
// One bot per state (ready, off, not offered on Linux, a Mac, offline, no computer, someone who may not open it).
// The header's Screen button shows only for the ready one. Turn on (Control) is offered to the operator or an admin
// only; it saves control, as a new computer starts, and the row (and the header button) become ready. A normal refresh redraws both, even with
// Settings' older copy of the computer loaded. Someone who may only see a bot is told its computer isn't shown.
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
      const errors = [], writes = [];
      page.on('pageerror', error => errors.push(error.message));
      const base = {host: 'keeper', status: 'active', state: 'active', can_chat: true, can_manage: true, operator: 'sam',
        my_access: {see: true, read: true, write: true}, schedules: [], revision: 1, thread_mode: 'personal', users: []};
      const computer = (id, more) => ({runner_id: id, generation: 1, label: id, operator: 'sam', last_seen: new Date().toISOString(),
        platform: 'linux', version: '0.3.52', screen: 'off', screen_available: false, ...more});
      const bot = (name, online, machine) => ({...base, name, slug: name, display_name: name, online, machine});
      const bots = [
        bot('ready', true, computer('c-ready', {screen: 'control', screen_available: true})),
        bot('off', true, computer('c-off', {screen_available: true})),
        bot('linux', true, computer('c-linux')),
        bot('mac', true, computer('c-mac', {platform: 'darwin'})),
        bot('offline', false, computer('c-offline', {screen: 'view', screen_available: true})),
        bot('none', false, null),
        bot('unowned', true, computer('c-unowned', {operator: null, screen: 'view', screen_available: true})),
        bot('flip', true, computer('c-flip', {operator: 'ana', screen: 'view'})),
        // Seen, not read: the server leaves out the computer, online and agent (backend/views.py).
        {name: 'seen', display_name: 'seen', state: 'active', status: 'active', description: '', reports_to: '', operator: 'sam',
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
          operator: b.machine.operator, online: b.online, platform: b.machine.platform, screen: b.machine.screen, screen_available: b.machine.screen_available})), services: []});
        if (p === '/api/v2/conversations') return json({conversations: []});
        if (p === '/api/v2/computers/c-off/screen') {
          const body = JSON.parse(request.postData()); writes.push(body);
          bots[1].machine.screen = body.mode;
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

    // Ana is an admin (the owner): every state, and the header button only for the ready one.
    const admin = await visit({id: 'ana', name: 'Ana', role: 'owner'});
    let row = await admin.open('ready');
    assert.equal(row.state, 'ready');
    assert.match(row.text, /^Open · Control$/);
    assert.equal(row.header, 1);
    assert.equal(await admin.page.locator('#bot-screen-row a.bot-screen-open').getAttribute('href'), '#/computers/c-ready/screen');
    assert.equal(await admin.page.locator('#bot-screen-row').evaluate(el => el.previousElementSibling.textContent), 'Screen');

    row = await admin.open('linux');
    assert.deepEqual([row.state, row.header], ['not_offered', 0]);
    // Docker's main step is live (no restart); the .env fallback says it cuts off a running turn and when it is needed.
    assert.match(row.text, /^This computer's screen isn't turned on\. Docker: .* turn it on live\. Nothing restarts, so no running turn is cut off:/);
    assert.match(row.text, /If the runner is too old for that command, or its \.env says TICO_SCREEN=0 \(which wins over it\), .*once the computer is idle: this restarts the runner and cuts off a running turn\./);
    assert.deepEqual(await admin.page.locator('#bot-screen-row [data-screen-copy]').evaluateAll(els => els.map(el => el.dataset.screenCopy)),
      ['sudo docker compose -f runner.compose.yaml $([ -f runner.override.yaml ] && echo -f runner.override.yaml) exec runner tico-runner --config /home/runner/runner.json screen on',
       "sudo sed -i '/^TICO_SCREEN=/d' .env && echo TICO_SCREEN=1 | sudo tee -a .env >/dev/null && sudo docker compose -f runner.compose.yaml $([ -f runner.override.yaml ] && echo -f runner.override.yaml) up -d runner",
       'scripts/tico screen on']);
    assert.equal(await admin.page.locator('#bot-screen-row a[href$="docs/install.md#screen"]').count(), 1);
    await admin.page.evaluate(() => { window.__copied = []; navigator.clipboard.writeText = async value => { window.__copied.push(value); }; });
    await admin.page.locator('#bot-screen-row [data-screen-copy="scripts/tico screen on"]').click();
    await admin.page.waitForFunction(() => window.__copied?.[0] === 'scripts/tico screen on');

    row = await admin.open('mac');
    assert.deepEqual([row.state, row.text, row.header], ['linux_only', 'Screen is Linux only for now.', 0]);
    row = await admin.open('offline');
    assert.deepEqual([row.state, row.text, row.header], ['offline', "This computer is offline. Screen works when it's back.", 0]);
    row = await admin.open('none');
    assert.deepEqual([row.state, row.text, row.header], ['none', 'This bot has no computer yet.', 0]);

    // Off: the operator or an admin turns it on (Control) right here; the row and the header button become ready.
    row = await admin.open('off');
    assert.deepEqual([row.state, row.header], ['off', 0]);
    assert.match(row.text, /^Screen is off for this computer\. Turn on \(Control\) View is a choice in Settings > Computers\.$/);
    await admin.page.locator('#bot-screen-row [data-screen-on]').click();
    await admin.page.locator('#bot-screen-row[data-screen-state=ready]').waitFor();
    assert.deepEqual(admin.writes, [{mode: 'control'}]);
    assert.equal(await admin.rowText(), 'Open · Control');
    assert.equal(await admin.page.locator('#bot-top #bot-screen').getAttribute('href'), '#/computers/c-off/screen');
    assert.equal(await admin.page.locator('#bot-screen-row').evaluate(el => el.previousElementSibling.textContent), 'Screen');

    // Settings was read while c-flip's screen was not offered. Then, with no navigation, normal refreshes: it is
    // offered (ready, header button), then the computer goes offline (no header button).
    await admin.page.goto('https://tico-ui.test/#/settings');
    await admin.page.waitForFunction(() => SETTINGS_DATA.machines?.some(m => m.id === 'c-flip' && m.screen_available === false));
    row = await admin.open('flip');
    assert.deepEqual([row.state, row.header], ['not_offered', 0]);
    const flip = admin.bots.find(b => b.name === 'flip');
    flip.machine.screen_available = true;
    await admin.page.evaluate(() => refresh());
    await admin.page.locator('#bot-screen-row[data-screen-state=ready]').waitFor();
    assert.equal(await admin.rowText(), 'Open · View');
    assert.equal(await admin.page.locator('#bot-top #bot-screen').getAttribute('href'), '#/computers/c-flip/screen');
    flip.online = false;
    await admin.page.evaluate(() => refresh());
    await admin.page.locator('#bot-screen-row[data-screen-state=offline]').waitFor();
    assert.equal(await admin.rowText(), "This computer is offline. Screen works when it's back.");
    assert.equal(await admin.page.locator('#bot-top #bot-screen').count(), 0);
    assert.deepEqual(admin.errors, []);
    await admin.page.close();

    // Sam operates these computers and is not an admin: Turn on (Control) is theirs too.
    const operator = await visit({id: 'sam', name: 'Sam', role: 'member'});
    row = await operator.open('off');
    assert.match(row.text, /^Screen is off for this computer\. Turn on \(Control\)/);
    await operator.page.locator('#bot-screen-row [data-screen-on]').click();
    await operator.page.locator('#bot-screen-row[data-screen-state=ready]').waitFor();
    assert.deepEqual(operator.writes, [{mode: 'control'}]);
    assert.equal(await operator.rowText(), 'Open · Control');
    assert.equal(await operator.page.locator('#bot-top #bot-screen').getAttribute('href'), '#/computers/c-off/screen');
    assert.deepEqual(operator.errors, []);
    await operator.page.close();

    // Bo is a member, not the operator: told who may, never offered Turn on, never shown the computer's steps.
    const member = await visit({id: 'bo', name: 'Bo', role: 'member'});
    row = await member.open('ready');
    assert.deepEqual([row.state, row.text, row.header], ['not_allowed', 'Only Sam and admins can open this computer\'s screen.', 0]);
    row = await member.open('off');
    assert.deepEqual([row.state, row.text, row.header], ['off', 'Screen is off for this computer. Ask Sam or an admin to turn it on in Settings > Computers.', 0]);
    assert.equal(await member.page.locator('[data-screen-on]').count(), 0);
    row = await member.open('linux');
    assert.deepEqual([row.state, row.text], ['not_offered', "This computer's screen isn't turned on. Ask Sam or an admin to turn it on on the computer."]);
    assert.equal(await member.page.locator('[data-screen-copy]').count(), 0);
    // A computer with no operator names admins alone.
    row = await member.open('unowned');
    assert.deepEqual([row.state, row.text], ['not_allowed', "Only admins can open this computer's screen."]);
    // Bo may only see 'seen': no claim it has no computer, nothing to open or turn on, no computer named.
    row = await member.open('seen');
    assert.deepEqual([row.state, row.text, row.header], ['hidden', "You can see this bot but not its activity, so its computer and screen aren't shown to you.", 0]);
    assert.equal(await member.page.locator('#bot-screen-row a, #bot-screen-row button').count(), 0);
    assert.doesNotMatch(row.text, /no computer|offline|Sam/);
    assert.deepEqual(member.writes, []);
    assert.deepEqual(member.errors, []);
    await member.page.close();
    console.log('PASS: Screen row in More for each state; header button only when ready; Turn on (Control) for operator/admin only; refresh redraws; see-only told it is not shown');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
