// Offline regression for per-bot permissions and roles (docs/permissions.md). Fixtures only.
//  - access: Settings > Bots shows an Access column and its editor holds the presets and a custom mix until Save
//    bot, which saves them with the revision it read; the org panel's person icon hides the bots the caller may only see, keeps that
//    with the account, and combines with the Recent sort; a bot page for someone who may only see or write
//    to it shows no activity.
//  - roles: Settings > Bots lists who owns each bot and saves co-owners as a diff with Save bot; Settings > Computers has an
//    admin-only "Accepts members' bots" toggle; a Confirm card BotOps left in a person's chat renders, and
//    only their click confirms it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

async function access(browser) {
  const FULL = {see: true, read: true, write: true};
  const EVERYONE = {everyone: true, people: [], teams: [], bots: []};
  const ME = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true};
  const bot = (name, display_name, extra = {}) => ({name, display_name, org_parent: '', host: 'keeper', status: 'active',
    state: 'active', can_chat: true, my_access: FULL, users: [{id: 'ana', name: 'Ana'}], owner_id: 'ana', revision: 1, ...extra});
  const page = await browser.newPage({viewport: {width: 1300, height: 900}, serviceWorkers: 'block'});
  const errors = [], puts = [], saved = [];
  let serverMine = null;
  const policies = {
    ops: {see: EVERYONE, read: EVERYONE, write: EVERYONE},
    legal: {see: EVERYONE, read: {everyone: false, people: [], teams: ['legal'], bots: []}, write: EVERYONE},
  };
  const revisions = {ops: 1, legal: 4};
  const employees = () => [
    bot('ops', 'Ops', {can_manage: true, access_policy: policies.ops, revision: revisions.ops}),
    bot('legal', 'Legal', {can_manage: true, access_policy: policies.legal, revision: revisions.legal}),
    // What someone who may only send requests, and someone who may only look, is given.
    bot('intake', 'Intake', {my_access: {see: true, read: false, write: true}, description: 'Takes requests.', reports_to: 'human:ben'}),
    bot('sales', 'Sales', {my_access: {see: true, read: false, write: false}, description: 'Runs the pipeline.', can_chat: false}),
  ];
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(ME);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', org_parent: ''}, {id: 'ben', name: 'Ben Cole', org_parent: ''}],
      org_groups: [{id: 'legal', name: 'Legal', org_parent: 'p:ana'}, {id: 'sales', name: 'Sales', org_parent: 'p:ana'}]});
    if (p === '/api/employees') return json(employees());
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/operations') return json({machines: [], services: [], agents: []});
    if (p === '/api/v2/models') return json({models: []});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    let m;
    if ((m = p.match(/^\/api\/v2\/bots\/([^/]+)\/access$/))) {
      const slug = m[1];
      if (method === 'PUT') {
        const body = request.postDataJSON(); puts.push([slug, body]);
        policies[slug] = {see: body.see, read: body.read, write: body.write}; revisions[slug] = body.revision + 1;
        return json({bot: slug, ...policies[slug], revision: revisions[slug]});
      }
      return json({bot: slug, ...policies[slug], revision: revisions[slug], you: FULL,
        teams: [{id: 'legal', name: 'Legal'}, {id: 'sales', name: 'Sales'}]});
    }
    if (p === '/api/v2/preferences/org.mine') {
      if (method === 'POST') { saved.push(request.postDataJSON().value); return json({}); }
      return json({key: 'org.mine', value: serverMine});
    }
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (process.env.DEBUG_UI) console.log('console:', m.text()); });

  // ---- Settings > Bots: the Access column and its editor
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('[data-settings-tab="bots"]').click();
  await page.locator('tr[data-settings-bot=legal]').waitFor();
  assert.equal(await page.locator('[data-settings-tab="bots"]').getAttribute('aria-selected'), 'true');
  assert.match(await page.locator('#set-bots thead').innerText(), /access/i);
  assert.doesNotMatch(await page.locator('#set-bots thead').innerText(), /can use/i);
  const summary = slug => page.locator(`tr[data-settings-bot=${slug}] [data-access-summary]`).innerText();
  assert.equal(await summary('ops'), 'Open');
  assert.equal(await summary('legal'), 'Requests only');
  assert.equal(await summary('intake'), 'You: See · Write', 'someone who may only use a bot sees what they can do');
  assert.equal(await page.locator('tr[data-settings-bot=intake] [data-edit-access]').count(), 0, 'only managers edit access');
  // One Edit button per row opens the bot editor, which holds access, who it works for and who owns it.
  const editor = page.locator('#bot-editor');
  const openEditor = async slug => { await page.evaluate(slug => settingsEditBot(slug), slug); await editor.locator(`[data-edit-access=${slug}]`).waitFor(); };
  // Every row in the bot editor is held until Save bot: nothing is written before it, then it all saves.
  const saveBot = async () => {
    assert.equal(puts.length, 0, 'nothing saves before Save bot');
    await editor.locator('button[type=submit]').click();
    for (let i = 0; i < 100 && !puts.length; i++) await page.waitForTimeout(50);
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-pending]')?.hidden !== false);
  };
  await openEditor('legal');
  assert.equal(await editor.locator('[data-access-summary]').innerText(), 'See: Everyone · Read: Legal · Write: Everyone');
  assert.match(await editor.locator('.sb-row').filter({has: page.locator('[data-edit-owners]')}).innerText(), /Works for/);

  const dialog = page.locator('#access-editor');
  const preset = () => dialog.locator('[name=preset]:checked').getAttribute('value');
  await page.locator('[data-edit-access=legal]').click();
  await dialog.locator('form').waitFor();
  assert.equal(await preset(), 'requests', 'See and Write Everyone with Read chosen is Visible, requests only');
  assert.equal(await dialog.locator('.access-level').count(), 1, 'one picker: who can read its work');
  assert.equal(await dialog.locator('[data-access-pick=teams][value=legal]').isChecked(), true);

  // Private: one audience for all three, carried over from what was chosen.
  await dialog.locator('[name=preset][value=private]').check();
  assert.equal(await dialog.locator('.access-level').count(), 1);
  assert.match(await dialog.locator('.access-level legend').innerText(), /Who can use it/);
  assert.equal(await dialog.locator('[data-access-pick=teams][value=legal]').isChecked(), true, 'the chosen audience follows');
  await dialog.locator('[data-access-pick=people][value=ben]').check();
  await dialog.locator('[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('#access-editor form'));
  await saveBot();
  const chosen = {everyone: false, people: ['ben'], teams: ['legal'], bots: []};
  assert.deepEqual(puts.shift(), ['legal', {see: chosen, read: chosen, write: chosen, revision: 4}]);
  await page.waitForFunction(() => /Legal, Ben · Read: Legal, Ben · Write: Legal, Ben/.test(document.querySelector('#bot-editor [data-access-summary]')?.textContent || ''));


  // Reopened, it is Private; Open puts everyone back.
  await page.locator('[data-edit-access=legal]').click();
  await dialog.locator('form').waitFor();
  assert.equal(await preset(), 'private');
  await dialog.locator('[name=preset][value=open]').check();
  assert.equal(await dialog.locator('.access-level').count(), 0, 'Open has nothing to choose');
  await dialog.locator('[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('#access-editor form'));
  await saveBot();
  assert.deepEqual(puts.shift(), ['legal', {see: EVERYONE, read: EVERYONE, write: EVERYONE, revision: 5}]);

  await page.waitForFunction(() => document.querySelector('#bot-editor [data-access-summary]')?.textContent === 'Open');

  // Visible, requests only: See and Write stay Everyone, Read is what is picked.
  await page.locator('[data-edit-access=legal]').click();
  await dialog.locator('form').waitFor();
  await dialog.locator('[name=preset][value=requests]').check();
  await dialog.locator('[data-access-pick=teams][value=sales]').check();
  await dialog.locator('[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('#access-editor form'));
  await saveBot();
  assert.deepEqual(puts.shift(), ['legal', {see: EVERYONE, read: {everyone: false, people: [], teams: ['sales'], bots: []},
    write: EVERYONE, revision: 6}]);

  // Custom: each level on its own, Everyone or chosen.
  await editor.locator('[data-bot-close]').first().click();
  await openEditor('ops');
  await page.locator('[data-edit-access=ops]').click();
  await dialog.locator('form').waitFor();
  await dialog.locator('[name=preset][value=custom]').check();
  assert.equal(await dialog.locator('.access-level').count(), 3, 'See, Read and Write each get a picker');
  assert.equal(await dialog.locator('.access-level [data-access-everyone]:checked').count(), 3);
  await dialog.locator('.access-level[data-access-levels=write] [data-access-everyone]').uncheck();
  await dialog.locator('.access-level[data-access-levels=write] [data-access-pick=people][value=ben]').check();
  await dialog.locator('[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('#access-editor form'));
  await saveBot();
  assert.deepEqual(puts.shift(), ['ops', {see: EVERYONE, read: EVERYONE,
    write: {everyone: false, people: ['ben'], teams: [], bots: []}, revision: 1}]);

  // ---- The org panel: only bots I can read or write, with the Recent sort
  await page.goto('https://tico-ui.test/');
  await page.locator('#tree .node').first().waitFor();
  const order = () => page.locator('#tree .node[data-org^="b:"]').evaluateAll(ns => ns.map(n => n.dataset.org));
  const toggle = page.locator('#org-mine');
  assert.equal(await toggle.getAttribute('aria-label'), 'Only bots I can read or write');
  assert.equal(await toggle.getAttribute('title'), 'Only bots I can read or write');
  assert.equal(await toggle.getAttribute('aria-pressed'), 'false');
  assert.deepEqual((await order()).sort(), ['b:intake', 'b:legal', 'b:ops', 'b:sales']);
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-pressed'), 'true');
  assert.deepEqual((await order()).sort(), ['b:intake', 'b:legal', 'b:ops'], 'a bot I may only see leaves the chart');
  await page.evaluate(() => { location.hash = '#/bot/legal'; });
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('tico.org.history') || '[]')[0] === 'b:legal');
  await page.locator('#org-history').click();
  assert.equal((await order())[0], 'b:legal', 'the Recent sort still orders what is left');
  assert.ok(!(await order()).includes('b:sales'), 'and the filter still applies');
  await page.locator('#org-history').click();
  const savedBy = Date.now() + t(10000);                     // the account copy is written after a short pause
  while (saved.at(-1)?.on !== true && Date.now() < savedBy) await new Promise(r => setTimeout(r, 50));
  assert.equal(saved.at(-1)?.on, true, 'kept with the account');
  // A newer copy from another device wins.
  serverMine = {on: false, at: Date.now() + 60000};
  await page.reload();
  await page.locator('#tree .node').first().waitFor();
  await page.waitForFunction(() => document.querySelector('#org-mine')?.getAttribute('aria-pressed') === 'false');
  assert.ok((await order()).includes('b:sales'));

  // ---- A bot page for someone who may only write to it, and one who may only see it
  // The settings hook is present so only the access check can hide the Tasks header icons.
  await page.evaluate(() => { window.botTaskSettingsOpen = () => {}; });
  await page.goto('https://tico-ui.test/#/bot/intake');
  await page.waitForFunction(() => S.route === '#/bot/intake');   // a page change draws a frame later (its fade)
  await page.locator('#btabs').waitFor({state: 'attached'});
  assert.deepEqual(await page.locator('#btabs [data-bt]').evaluateAll(ns => ns.map(n => n.dataset.bt)), ['chat', 'tasks', 'info']);
  await page.locator('#bot-tasks .rail-head').waitFor({state: 'attached'});
  assert.equal(await page.locator('#bot-tasks-board, #bot-sidebar-edit').count(), 0, 'no board or edit icon without read access');
  assert.equal(await page.locator('#bot-goal').count(), 0, 'no goal, no activity');
  assert.equal(await page.locator('#bot-files, #bot-history, #sess-card, #bot-updates-card').count(), 0);
  assert.match(await page.locator('.bot-request-head').innerText(), /Send a request/);
  await page.locator('#chat-composer .p-text').waitFor();
  await page.evaluate(() => { location.hash = '#/bot/intake/more'; });
  await page.locator('#pane-more').waitFor();
  const about = page.locator('#pane-more');
  assert.match(await about.innerText(), /About/);
  assert.match(await about.innerText(), /Takes requests\./);
  assert.match(await about.innerText(), /Owner\s*Ana/);
  assert.match(await about.innerText(), /See it and send requests/);
  await page.goto('https://tico-ui.test/#/bot/sales');
  await page.waitForFunction(() => S.route === '#/bot/sales');
  await page.locator('#btabs').waitFor({state: 'attached'});
  assert.deepEqual(await page.locator('#btabs [data-bt]').evaluateAll(ns => ns.map(n => n.dataset.bt)), ['info'], 'see only: no chat, no tasks');
  assert.match(await page.locator('#pane-more').innerText(), /See it only/);
  assert.deepEqual(errors, []);
  console.log('PASS: Access column and editor presets, the org filter beside Recent, and bot pages that show no activity to someone who cannot read.');
}

async function roles(browser) {
  const FULL = {see: true, read: true, write: true};
  const ME = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true, company_role: 'owner',
  bot_admin: true, can_create_bots: true, can_add_people: true};
  const page = await browser.newPage({viewport: {width: 1300, height: 900}, serviceWorkers: 'block'});
  const errors = [], posts = [];
  let action = {id: 'act1', owner_id: 'human:ana', status: 'pending', proposer: 'botops', proposed_via: 'botops',
    description: 'Add Sean (sean@example.test) to the roster, and let them sign in', summary: 'Add Sean (sean@example.test) to the roster',
    method: 'POST', path: '/api/v2/access/humans', body: {name: 'Sean', email: 'sean@example.test'}, diff: []};
  let accepts = false;
  // The runner keeps a limit of 2, as one that cannot take a raised Turns at once setting does.
  let turnsAtOnce = null, holdTurns = false, heldTurns = null;
  const bot = (name, display_name, extra = {}) => ({name, display_name, org_parent: '', host: 'keeper', status: 'active',
    state: 'active', can_chat: true, my_access: FULL, users: [{id: 'ana', name: 'Ana'}], owner_id: 'ana', revision: 1,
    can_manage: true, ...extra});
  const conv = {id: 'botops-chat', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:botops']};
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(ME);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', org_parent: ''}, {id: 'ben', name: 'Ben Cole', org_parent: ''},
      {id: 'cara', name: 'Cara Diaz', org_parent: ''}], org_groups: []});
    if (p === '/api/employees') return json([
      bot('jira-manager', 'Jira Manager', {owner_id: 'cara', bot_owners: [{id: 'cara', name: 'Cara Diaz'}], access_policy: null}),
      bot('botops', 'BotOps'),
      // Synced Grok Bots: Ana's own, and Cara's, already delivered through the app on Cara's Mac.
      bot('groky', 'Grok Groky', {harness: 'grokbot', app_delivery_app: 'grok', agent: {harness: 'grokbot', synced: true, credential: true, profile: 'Groky'}}),
      bot('cara-grok', 'Grok Scout', {owner_id: 'cara', harness: 'grokbot', app_delivery_app: 'grok', agent: {harness: 'grokbot', synced: true, credential: true},
        app_delivery: {computer: 'r9', app: 'grok', app_label: 'Grok', computer_label: 'Cara Mac', active: true}})]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Shared Mac', owner_id: 'ana', last_seen_at: new Date().toISOString(),
      platform: 'darwin', bots: [], readiness: {}, accepts_member_bots: accepts, turns_at_once: turnsAtOnce,
      turn_limit: 2, capacity: 2},
      {id: 'r2', label: 'Acme Mac', owner_id: 'ana', last_seen_at: new Date().toISOString(), platform: 'darwin', bots: [],
       readiness: {appdriver: {trusted: true, apps: {grok: {installed: true, signed_in: true, background_ok: true, ready: true, state: 'ready'}}}}}],
      agents: [], services: []});
    if (p.match(/^\/api\/v2\/bots\/[^/]+\/definition$/) && method === 'POST') { posts.push(['definition', request.postDataJSON()]); return json({}); }
    if (p === '/api/v2/models') return json({models: []});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    if (p === '/api/v2/bots/jira-manager/co-owners' && method === 'POST') { posts.push(['co-owners', request.postDataJSON()]); return json({}); }
    if (p === '/api/v2/computers/r1/member-bots' && method === 'POST') {
      posts.push(['member-bots', request.postDataJSON()]); accepts = request.postDataJSON().accepts; return json({});
    }
    if (p === '/api/v2/computers/r1/turns-at-once' && method === 'POST') {
      posts.push(['turns-at-once', request.postDataJSON()]);
      // A held save is answered by the test, so a change can land in the field while it is in flight.
      if (holdTurns) { heldTurns = route; return; }
      turnsAtOnce = request.postDataJSON().turns_at_once;
      return json({turns_at_once: turnsAtOnce, turn_limit: 2});
    }
    if (p === '/api/v2/assistant/actions/act1' && method === 'GET') return json({action});
    if (p === '/api/v2/assistant/actions/act1/confirm' && method === 'POST') {
      posts.push(['confirm', request.postDataJSON()]); action = {...action, status: 'done'}; return json({action});
    }
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: [conv]});
    if (p.endsWith('/snapshot') || p.endsWith('/messages')) return json({conversation: conv, messages: [
      {id: 'm1', conversation_id: conv.id, sender_id: 'human:ana', recipient_id: 'bot:botops', kind: 'say', body: 'Add sean@example.test please',
       created_at: new Date().toISOString(), refs: {}},
      {id: 'm2', conversation_id: conv.id, sender_id: 'bot:botops', recipient_id: 'human:ana', kind: 'say', body: 'Needs your OK: Add Sean (sean@example.test) to the roster',
       created_at: new Date().toISOString(), refs: {assistant: true, action: 'act1'}}], execution: null, has_more: false, next_before: null});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  page.on('pageerror', e => errors.push(e.message));

  // ---- Settings > Bots: who owns each bot
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('[data-settings-tab="bots"]').click();
  await page.locator('tr[data-settings-bot=jira-manager]').waitFor();
  await page.locator('tr[data-settings-bot=jira-manager] [data-edit-bot]').click();
  const owned = page.locator('#bot-editor [data-bot-owners]');
  assert.match(await owned.innerText(), /Cara/);
  await page.locator('#bot-editor [data-edit-bot-owners=jira-manager]').click();
  const dialog = page.locator('#bot-owners-editor');
  await dialog.locator('form').waitFor();
  assert.equal(await dialog.locator('input[value=cara]').isChecked(), true);
  assert.equal(await dialog.locator('input[value=cara]').isDisabled(), true, 'its operator is always an owner');
  await dialog.locator('input[value=ben]').check();
  await dialog.locator('[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('#bot-owners-editor form'));
  const saveBot = async what => {
    assert.deepEqual(posts, [], 'nothing saves before Save bot');
    await page.locator('#bot-editor button[type=submit]').click();
    for (let i = 0; i < 100 && !posts.some(post => post[0] === what); i++) await page.waitForTimeout(50);
    assert.equal(posts.shift()[0], 'definition', 'Save bot saves the definition first');
  };
  await saveBot('co-owners');
  assert.deepEqual(posts.shift(), ['co-owners', {add: ['ben'], remove: []}]);
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('[data-settings-tab="bots"]').click();

  // ---- A Grok Bot's "Deliver through the app on": its person picks one of their own computers where Grok is ready
  // (the others are listed, disabled); anyone else, an admin included, can only turn it off.
  await page.locator('tr[data-settings-bot=groky] [data-edit-bot]').click();
  const delivery = page.locator('#bot-editor [data-bot-app-delivery=groky]');
  await delivery.waitFor();
  assert.deepEqual(await delivery.locator('option').evaluateAll(os => os.map(o => [o.value, o.disabled])), [['', false], ['r1', true], ['r2', false]]);
  await delivery.selectOption('r2');
  await saveBot('definition');
  for (let i = 0; i < 100 && !posts.length; i++) await page.waitForTimeout(50);
  assert.deepEqual(posts.shift(), ['definition', {app_delivery: {computer: 'r2', app: 'grok'}, expected_revision: 1}]);
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('[data-settings-tab="bots"]').click();
  await page.locator('tr[data-settings-bot=cara-grok] [data-edit-bot]').click();
  const theirs = page.locator('#bot-editor [data-bot-app-delivery=cara-grok]');
  await theirs.waitFor();
  assert.deepEqual(await theirs.locator('option').evaluateAll(os => os.map(o => [o.value, o.selected])), [['', false], ['r9', true]]);
  await theirs.selectOption('');
  await saveBot('definition');
  for (let i = 0; i < 100 && !posts.length; i++) await page.waitForTimeout(50);
  assert.deepEqual(posts.shift(), ['definition', {app_delivery: null, expected_revision: 1}]);
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('[data-settings-tab="bots"]').click();

  // ---- Settings > Computers: an admin says which computers take members' bots
  await page.locator('[data-settings-tab="devices"]').click();
  await page.locator('#set-machines .machine-name[data-computer-open=r1]').click();   // under Settings in its pop-up
  const box = page.locator('[data-member-bots=r1]');
  assert.equal(await box.isChecked(), false);
  await box.check();
  await page.waitForFunction(() => document.querySelector('[data-member-bots=r1]')?.checked === true);
  // The box is checked before its POST reaches the route handler: wait for the POST, as the saves above do.
  for (let i = 0; i < 100 && !posts.length; i++) await page.waitForTimeout(50);
  assert.deepEqual(posts.shift(), ['member-bots', {accepts: true}]);

  // ---- Turns at once on that computer: empty is automatic, the runner's own count; a raised setting says the limit
  // the runner keeps; a value that is not a whole number from 1 to 256 is named under the row and not sent.
  const turns = page.locator('[data-turns-at-once=r1]'), turnsNote = page.locator('[data-turns-note]');
  const pop = page.locator('#set-machines dialog.computer-detail [data-field-err]');
  await turns.waitFor();
  assert.equal((await turnsNote.innerText()).trim(), 'Automatic (2)');
  await turns.fill('4');
  await turns.dispatchEvent('change');
  await page.waitForFunction(
    () => document.querySelector('[data-turns-note]')?.textContent.includes('This computer runs at most 2'));
  assert.deepEqual(posts.shift(), ['turns-at-once', {turns_at_once: 4}]);
  await turns.fill('2');
  await turns.dispatchEvent('change');
  await page.waitForFunction(() => document.querySelector('[data-turns-note]')?.textContent.trim() === '');
  assert.deepEqual(posts.shift(), ['turns-at-once', {turns_at_once: 2}],
    'a setting equal to the limit is not named as a limit');
  await turns.fill('');
  await turns.dispatchEvent('change');
  await page.waitForFunction(() => document.querySelector('[data-turns-note]')?.textContent.trim() === 'Automatic (2)');
  assert.deepEqual(posts.shift(), ['turns-at-once', {turns_at_once: null}], 'empty posts null');
  for (const value of ['300', '2.5']) {
    await turns.fill(value);
    await turns.dispatchEvent('change');
    await pop.first().waitFor();
    await page.waitForTimeout(100);
    assert.deepEqual(posts, [], `${value} is not posted`);
  }
  await turns.fill('');
  await turns.dispatchEvent('change');
  assert.equal(await pop.count(), 0, 'the line clears once the value is valid');

  // A change that lands while a save is in flight is not dropped: the field's value once that save
  // answers is sent next.
  holdTurns = true;
  await turns.fill('3');
  await turns.dispatchEvent('change');
  await page.waitForFunction(() => document.querySelector('[data-turns-at-once=r1]')?.disabled === true);
  await turns.evaluate(el => { el.value = '5'; el.dispatchEvent(new Event('change', {bubbles: true})); });
  holdTurns = false;
  heldTurns.fulfill({contentType: 'application/json', body: JSON.stringify({turns_at_once: 3, turn_limit: 2})});
  for (let i = 0; i < 100 && posts.length < 2; i++) await page.waitForTimeout(50);
  assert.deepEqual(posts.splice(0), [['turns-at-once', {turns_at_once: 3}], ['turns-at-once', {turns_at_once: 5}]],
    'the change made during the save is sent after it, and not with it');
  await page.waitForFunction(() => document.querySelector('[data-turns-at-once=r1]')?.value === '5');

  // ---- BotOps' chat: a Confirm card, and nothing happens until the person clicks it
  await page.goto('https://tico-ui.test/#/bot/botops');
  const card = page.locator('.asst-card[data-action=act1]');
  await card.waitFor();
  assert.match(await card.innerText(), /Add Sean \(sean@example\.test\) to the roster/);
  assert.equal(await card.locator('.asst-who').innerText(), 'BotOps asks');
  // BotOps' own words sit behind the title; Confirm says how it runs on hover.
  await card.locator('details.asst-raw > summary').click();
  assert.match(await card.innerText(), /BotOps says/);
  assert.equal(await card.locator('[data-confirm]').getAttribute('title'), 'Runs as you, only when you confirm');
  assert.deepEqual(posts, []);
  await card.locator('[data-confirm]').click();
  await page.waitForFunction(() => /Done\./.test(document.querySelector('.asst-card[data-action=act1]')?.textContent || ''));
  assert.deepEqual(posts.shift(), ['confirm', {}]);
  assert.deepEqual(errors, []);
  console.log('PASS: bot owners, a Grok Bot\'s app delivery, the members\' bots toggle and BotOps\' Confirm cards.');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    await access(browser);
    await roles(browser);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
