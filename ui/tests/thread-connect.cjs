// Links outside this Tico, per role, at desktop and phone width (ui/app/thread-connect.js, ui/app/connections.js),
// against fakes of /api/v2/slack/links (backend/slack_threads.py) and /api/v2/federation (backend/federation_routes.py):
// - an admin links a thread to a Slack channel from Library › Connections, pages the links, checks a suspended one
//   again, unlinks one, and reads the API's refusal inline;
// - a person who is neither admin nor owner sees neither section, never asks for either, and gets no Slack or
//   Shared with in a thread's Who can read sheet;
// - an admin who is not the owner sees Slack and Other companies, as the server allows;
// - the owner sees this Tico's ID and key, approves a requested company, pauses another, and asks a new one;
// - in a thread's sheet the owner links it to Slack and adds and removes a company in Shared with;
// - a message from another company's Tico shows its author as "name · company" with a guest tag.
// TICO_UI_SHOTS=<dir> saves the screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1300, height: 900};
const ME = 'gi_' + 'a'.repeat(24), GLOBEX = 'gi_' + 'c'.repeat(24), INITECH = 'gi_' + 'b'.repeat(24), UMBRELLA = 'gi_' + 'e'.repeat(24);
const PEOPLE = {
  ana: {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', bot_admin: true, registered: true},
  cara: {id: 'cara', name: 'Cara', email: 'cara@acme.example', role: 'human', bot_admin: true, registered: true},
  bob: {id: 'bob', name: 'Bob', email: 'bob@acme.example', role: 'human', registered: true},
};
const now = Date.now(), at = min => new Date(now - min * 60000).toISOString();
const me = (extra = {}) => ({member: true, following: true, notify: 'live', last_read_seq: 3, unread: 0, activity: false, can_post: true, can_manage: true, ...extra});
const thread = (id, title, audience, extra = {}) => ({id, title, audience, audience_group_id: audience === 'team' ? 'ops' : null, parent_id: null,
  root_id: id, last_seq: 3, last_unread_seq: 0, last_message_at: at(5), me: me(), members: [], ...extra});

function fakes() {
  const link = (id, threadId, name, state, reason = '') => ({id, thread_id: threadId, channel: {id: '', name, private: false}, audience: 'company',
    state, reason, history: {state: 'off', count: 0}, linked_by: 'human:ana', created_at: at(10), updated_at: at(10)});
  return {
    threads: [thread('t-mkt', 'Marketing', 'company'), thread('t-sup', 'Support', 'members'), thread('t-plan', 'Launch plan', 'members'),
      thread('t-team', 'Ops team', 'team')],
    links: [link('sl1', 't-mkt', 'marketing', 'active'), link('sl2', 't-sup', '#support', 'suspended', 'A member does not map to a teammate'),
      link('sl3', 't-ops', 'ops-desk', 'refused', 'Slack Connect channel')],
    federation: {gateway: true, install: ME, key: {key_id: 'fk_' + '1'.repeat(24), registered: true}, outbox: 0},
    peerings: [
      {id: 'fp_1', peer: INITECH, direction: 'incoming', status: 'pending', approved: false, paused: false, paused_by: null},
      {id: 'fp_2', peer: GLOBEX, direction: 'outgoing', status: 'active', approved: true, paused: false, paused_by: null},
      {id: 'fp_4', peer: UMBRELLA, direction: 'outgoing', status: 'active', approved: true, paused: false, paused_by: null},
      {id: 'fp_3', peer: 'gi_' + 'd'.repeat(24), direction: 'outgoing', status: 'revoked', approved: true, paused: false, paused_by: null},
    ],
    shared: {'t-plan': [{install: GLOBEX, status: 'active', joined_at_seq: 1, removed_at_seq: null}]},
  };
}

async function open(browser, who, {viewport = DESKTOP} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', reducedMotion: 'reduce', ...(viewport === PHONE ? {isMobile: true, hasTouch: true} : {})});
  const seen = {errors: [], calls: []}, state = fakes();
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('dialog', d => d.accept());
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const refuse = (status, detail) => json({error: {code: 'refused', detail, retryable: false}}, status);
    const body = () => JSON.parse(req.postData() || '{}');
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p.startsWith('/api/v2/slack') || p.startsWith('/api/v2/federation')) seen.calls.push(`${method} ${p}${url.search}`);
    if (p === '/api/me') return json(PEOPLE[who]);
    if (p === '/api/v2/config') return json({company_name: 'Acme'});
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: Object.values(PEOPLE).map(({id, name, email}) => ({id, name, email}))});
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/connections') return json({connections: [], next: null});
    // Threads: the sidebar list, one thread, its timeline.
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: state.threads, dms: [], next_cursor: null});
    const single = p.match(/^\/api\/v2\/threads\/([^/]+)$/);
    if (single && method === 'GET') {
      const found = [...state.threads, thread('t-ops', 'Ops desk', 'company')].find(t => t.id === single[1]);
      return found ? json(found) : json({error: 'not_found'}, 404);
    }
    if (p === '/api/v2/threads/t-plan/timeline') return json({thread: state.threads[2], breadcrumb: [{id: 't-plan', title: 'Launch plan'}], next_cursor: null, messages: [
      {id: 'm1', seq: 1, thread_id: 't-plan', depth: 0, from_actor: 'human:ana', body: 'Launch is Monday.', created: at(30)},
      {id: 'm2', seq: 2, thread_id: 't-plan', depth: 0, from_actor: 'keeper', body: 'We are ready on our side.', created: at(20),
        refs: {via: 'peer', origin: {origin_id: 'o1@' + GLOBEX, revision: 0, install: GLOBEX, actor: 'human:dana@' + GLOBEX}}},
    ]});
    if (/^\/api\/v2\/threads\/[^/]+\/(read|calls)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null} : {ok: true});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    // Slack links (admins only, as the server has it).
    if (p.startsWith('/api/v2/slack/links') && !(PEOPLE[who].role === 'owner' || PEOPLE[who].bot_admin)) return refuse(403, 'Only an owner or an admin links a Slack channel to a thread');
    if (p === '/api/v2/slack/links' && method === 'GET') {
      const forThread = url.searchParams.get('thread'), limit = Number(url.searchParams.get('limit') || 50);
      let rows = state.links.filter(l => !forThread || l.thread_id === forThread);
      // Two a page here, so the page's More is the server's next_cursor.
      const start = Number(url.searchParams.get('cursor') || 0), size = Math.min(limit, 2);
      const page = rows.slice(start, start + size);
      return json({links: page, next_cursor: start + size < rows.length ? String(start + size) : null, available: !state.slackOff});
    }
    if (p === '/api/v2/slack/links' && method === 'POST') {
      const b = body();
      if (b.channel === '#general') return refuse(409, 'This thread is already linked to a Slack channel');
      const row = {id: 'sl' + (state.links.length + 1), thread_id: b.thread_id, channel: {id: '', name: b.channel, private: false}, audience: 'members',
        state: 'requested', reason: '', history: {state: b.history ? 'pending' : 'off', count: 0}, linked_by: 'human:' + who, created_at: at(0), updated_at: at(0)};
      seen.linked = b;
      state.links.unshift(row);
      return json(row);
    }
    const one = p.match(/^\/api\/v2\/slack\/links\/([^/]+)(\/resume)?$/);
    if (one) {
      const row = state.links.find(l => l.id === one[1]);
      if (method === 'DELETE') { state.links = state.links.filter(l => l !== row); return json({...row, state: 'unlinked'}); }
      row.state = 'requested'; row.reason = '';
      return json(row);
    }
    // Federation (the owner or an admin).
    if (p.startsWith('/api/v2/federation') && !(PEOPLE[who].role === 'owner' || PEOPLE[who].bot_admin)) return refuse(403, 'Only the owner or an admin may manage federation with other installs');
    const status = () => ({federation: state.federation, peerings: state.peerings,
      threads: Object.entries(state.shared).map(([id, members]) => ({thread_id: id, home: true, home_install: ME, applied_through: 0, high_seq: 0, removed_at_seq: null, members}))});
    if (p === '/api/v2/federation') return json(status());
    if (p === '/api/v2/federation/peerings' && method === 'POST') {
      const peer = body().peer;
      if (!/^gi_[0-9a-f]{24}$/.test(peer)) return refuse(422, "The other install's id starts with gi_ and has 24 hex digits");
      const row = {id: 'fp_' + (state.peerings.length + 1), peer, direction: 'outgoing', status: 'pending', approved: false, paused: false, paused_by: null};
      state.peerings.unshift(row);
      return json(row);
    }
    const peering = p.match(/^\/api\/v2\/federation\/peerings\/([^/]+)\/(approve|pause|revoke)$/);
    if (peering) {
      const row = state.peerings.find(x => x.id === peering[1]);
      if (peering[2] === 'approve') Object.assign(row, {approved: true, status: 'active'});
      if (peering[2] === 'pause') { const paused = body().paused; Object.assign(row, {paused, paused_by: paused ? 'you' : null}); }
      if (peering[2] === 'revoke') row.status = 'revoked';
      return json(row);
    }
    const share = p.match(/^\/api\/v2\/federation\/threads\/([^/]+)\/installs(?:\/([^/]+))?$/);
    if (share) {
      const list = state.shared[share[1]] ||= [];
      if (method === 'POST') { const install = body().install; list.push({install, status: 'active', joined_at_seq: 2, removed_at_seq: null}); return json({thread_id: share[1], install, joined_at_seq: 2}); }
      const m = list.find(x => x.install === decodeURIComponent(share[2]));
      m.status = 'removed';
      return json({thread_id: share[1], install: m.install, removed_at_seq: 3});
    }
    return json(unrouted(p));
  });
  return {page, seen, state};
}
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png'), fullPage: true});
}
async function sheet(page) {
  await page.goto('https://tico-ui.test/#/thread/t-plan');
  await page.getByText('We are ready on our side.').waitFor();
  await page.locator('[data-thv-menu]').click();
  await page.locator('.thv-pop').getByText('Who can read').click();
  await page.locator('#thread-share[open]').waitFor();
  return page.locator('#thread-share');
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const viewport of [DESKTOP, PHONE]) {
      const tag = viewport === PHONE ? 'phone' : 'desktop';

      // ---- The owner on Library › Connections: Slack and Other companies.
      {
        const {page, seen} = await open(browser, 'ana', {viewport});
        await page.goto('https://tico-ui.test/#/library/connections');
        const slack = page.locator('#cn-slack'), peers = page.locator('#cn-peers');
        await slack.locator('[data-link="sl2"]').waitFor();
        assert.match(await slack.locator('[data-link="sl1"] .cn-name').textContent(), /Marketing → #marketing/, `${tag}: thread → #channel`);
        assert.match(await slack.locator('[data-link="sl1"] .cn-status').textContent(), /Live/);
        assert.match(await slack.locator('[data-link="sl2"] .cn-status').textContent(), /Suspended/);
        assert.equal(await slack.locator('[data-link="sl1"] [data-slack-resume]').count(), 0, `${tag}: a live link has nothing to resume`);
        // Paged: the third link comes with More, and its thread's title is read from the thread.
        await slack.locator('.cn-xmore button').click();
        await slack.locator('[data-link="sl3"]').waitFor();
        assert.match(await slack.locator('[data-link="sl3"] .cn-name').textContent(), /Ops desk → #ops-desk/);
        assert.equal(await slack.locator('.cn-xmore').isHidden(), true, `${tag}: nothing more`);
        // Resume a suspended link, then unlink a live one.
        await slack.locator('[data-link="sl2"] [data-slack-resume]').click();
        await page.waitForFunction(() => /Waiting/.test(document.querySelector('#cn-slack [data-link="sl2"] .cn-status')?.textContent || ''));
        await slack.locator('[data-link="sl1"] [data-slack-unlink]').click();
        await slack.locator('[data-link="sl1"]').waitFor({state: 'detached'});
        assert.ok(seen.calls.includes('DELETE /api/v2/slack/links/sl1'));
        // Link a thread: only a company or members root that is not linked yet is offered.
        await slack.locator('[data-cn-open]').click();
        await slack.locator('[name=thread] option').first().waitFor({state: 'attached'});
        // Marketing was just unlinked; Support is still linked; a team thread cannot be.
        const offered = await slack.locator('[name=thread] option').allTextContents();
        assert.deepEqual(offered, ['Marketing', 'Launch plan'], `${tag}: linkable threads only: ${offered}`);
        await slack.locator('[name=thread]').selectOption('t-plan');
        await slack.locator('[name=channel]').fill('#general');
        await slack.locator('[data-cn-link]').click();
        await page.waitForFunction(() => /already linked/.test(document.querySelector('#cn-slack .cn-err')?.textContent || ''));
        await slack.locator('[name=channel]').fill('#launch');
        await slack.locator('[name=history]').check();
        await slack.locator('[data-cn-link]').click();
        await page.waitForFunction(() => /Launch plan → #launch/.test(document.querySelector('#cn-slack .cn-list')?.textContent || ''));
        assert.deepEqual(seen.linked, {thread_id: 't-plan', channel: '#launch', history: true});
        assert.equal(await slack.locator('.cn-err').textContent(), '', `${tag}: the refusal clears`);

        // Other companies: this Tico's ID and key, and its peerings.
        await peers.locator('[data-peering="fp_1"]').waitFor();
        assert.equal(await peers.locator('[data-cn-install]').textContent(), ME);
        assert.equal(await peers.locator('[data-cn-fingerprint]').textContent(), 'fk_' + '1'.repeat(24));
        assert.match(await peers.locator('[data-peering="fp_1"] .cn-status').textContent(), /Requested/);
        assert.equal(await peers.locator('[data-peering="fp_3"] button').count(), 0, `${tag}: a revoked company has no actions`);
        await peers.locator('[data-peering="fp_1"] [data-peer="approve"]').click();
        await page.waitForFunction(() => /Approved/.test(document.querySelector('#cn-peers [data-peering="fp_1"] .cn-status')?.textContent || ''));
        assert.ok(seen.calls.includes('POST /api/v2/federation/peerings/fp_1/approve'));
        await peers.locator('[data-peering="fp_2"] [data-peer="pause"]').click();
        await peers.locator('[data-peering="fp_2"] [data-peer="resume"]').waitFor();
        assert.match(await peers.locator('[data-peering="fp_2"] .cn-status').textContent(), /Paused/);
        // Connect a company: a bad ID is refused inline, a good one is listed as requested.
        await peers.locator('[data-cn-open]').click();
        await peers.locator('[name=peer]').fill('globex');
        await peers.locator('[data-cn-request]').click();
        await page.waitForFunction(() => /24 hex digits/.test(document.querySelector('#cn-peers .cn-err')?.textContent || ''));
        await peers.locator('[name=peer]').fill('gi_' + 'f'.repeat(24));
        await peers.locator('[data-cn-request]').click();
        await page.waitForFunction(() => document.querySelectorAll('#cn-peers [data-peering]').length === 5);
        assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
        await shot(page, `${tag}-connections-owner`);
        assert.deepEqual(seen.errors, [], `${tag}: no page errors`);
        await page.close();
      }

      // ---- A person who is neither admin nor owner: no sections, no calls, nothing in a thread's sheet.
      {
        const {page, seen} = await open(browser, 'bob', {viewport});
        await page.goto('https://tico-ui.test/#/library/connections');
        await page.locator('#cn-root .cn-none').waitFor();
        assert.equal(await page.locator('#cn-slack').isHidden(), true, `${tag}: no Slack section`);
        assert.equal(await page.locator('#cn-peers').isHidden(), true, `${tag}: no Other companies section`);
        // Bob started the thread, so he may open its sheet; Slack and sharing are not his to change, and he would see
        // Shared with only for a message to retry (there is none).
        const dlg = await sheet(page);
        assert.equal(await dlg.locator('[data-tcn="slack"]').count(), 0, `${tag}: no Slack`);
        assert.equal(await dlg.locator('[data-tcn="shared"]').isHidden(), true, `${tag}: no Shared with`);
        assert.equal(await dlg.locator('.tcn button, .tcn input').count(), 0);
        assert.deepEqual(seen.calls, [], `${tag}: never asks for links or companies`);
        assert.deepEqual(seen.errors, []);
        await page.close();
      }

      // ---- An admin who is not the owner: Slack and Other companies.
      {
        const {page, seen} = await open(browser, 'cara', {viewport});
        await page.goto('https://tico-ui.test/#/library/connections');
        await page.locator('#cn-slack [data-link="sl1"]').waitFor();
        await page.locator('#cn-peers .cn-key').waitFor({state: 'attached'});
        assert.equal(await page.locator('#cn-peers').isHidden(), false, `${tag}: Other companies is an admin's too`);
        const dlg = await sheet(page);
        await dlg.locator('[data-tcn="slack"] [name=tcn-channel]').waitFor();
        await dlg.locator('[data-tcn="shared"]').waitFor();
        assert.deepEqual(seen.errors, []);
        await page.close();
      }

      // ---- Slack off and no Gateway: nothing on offer that cannot work, and what is already linked still shows.
      {
        const {page, seen, state} = await open(browser, 'ana', {viewport});
        state.slackOff = true; state.federation = {gateway: false}; state.peerings = []; state.shared = {}; state.links = [];
        await page.goto('https://tico-ui.test/#/library/connections');
        await page.locator('#cn-slack .cn-none').waitFor();
        assert.equal(await page.locator('#cn-slack [data-cn-open]').isHidden(), true, `${tag}: no Link a thread`);
        const dlg = await sheet(page);
        await page.waitForLoadState('networkidle');
        assert.equal(await dlg.locator('[name=tcn-channel]').count(), 0, `${tag}: no Slack form with Slack off`);
        assert.equal(await dlg.locator('[data-tcn="slack"]').isHidden(), true, `${tag}: no Slack section`);
        assert.equal(await dlg.locator('[data-tcn="shared"]').isHidden(), true, `${tag}: no Shared with without the Gateway`);
        assert.equal(await dlg.locator('a[href="#/library/connections"]').count(), 0, `${tag}: no Connect a company`);
        assert.equal(await dlg.locator('.tcn').isHidden(), true, `${tag}: no empty section left behind`);
        assert.deepEqual(seen.errors, []);
        await page.close();
      }

      // ---- The owner in a thread's sheet: link it to Slack, share it with a company and stop.
      {
        const {page, seen} = await open(browser, 'ana', {viewport});
        const dlg = await sheet(page);
        // A guest's message: their name, their company, a guest tag; never the keeper, never a person link.
        const who = page.locator('.th-msg[data-mid="m2"] .th-who');
        assert.match(await who.textContent(), /Dana · gi_cccccc…\s*guest/, `${tag}: a guest author`);
        assert.equal(await who.locator('a').count(), 0);
        const shared = dlg.locator('[data-tcn="shared"]');
        await shared.locator(`[data-install="${GLOBEX}"]`).waitFor();
        const options = await shared.locator('[name=tcn-install] option').allTextContents();
        assert.deepEqual(options, [UMBRELLA], `${tag}: only active companies not already in it: ${options}`);
        await shared.locator('[data-tcn-go]').click();
        await shared.locator(`[data-install="${UMBRELLA}"]`).waitFor();
        await shared.locator(`[data-install="${GLOBEX}"] [data-tcn-remove]`).click();
        await shared.locator(`[data-install="${GLOBEX}"]`).waitFor({state: 'detached'});
        assert.ok(seen.calls.includes(`POST /api/v2/federation/threads/t-plan/installs`));
        assert.ok(seen.calls.includes(`DELETE /api/v2/federation/threads/t-plan/installs/${GLOBEX}`));
        // Slack: Enter in the channel field links it and never saves the sheet.
        const slack = dlg.locator('[data-tcn="slack"]');
        await slack.locator('[name=tcn-channel]').fill('#launch');
        await slack.locator('[name=tcn-channel]').press('Enter');
        await slack.locator('[data-link]').waitFor();
        assert.match(await slack.locator('[data-link]').textContent(), /#launch[\s\S]*Waiting/);
        assert.equal(await dlg.evaluate(d => d.open), true, `${tag}: the sheet stays open`);
        assert.deepEqual(seen.linked, {thread_id: 't-plan', channel: '#launch', history: false});
        await shot(page, `${tag}-thread-sheet-owner`);
        assert.deepEqual(seen.errors, []);
        await page.close();
      }
    }
    console.log('Links outside: Slack and Other companies per role, the thread sheet, guest authors, desktop and phone');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
