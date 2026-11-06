// Slack links per role, at desktop and phone width (ui/app/thread-connect.js, Slack's page under Tools in
// ui/app/redesign-library.js), against fakes of
// /api/v2/slack/links (backend/slack_threads.py):
// - an admin links a thread to a Slack channel from Tools › Slack, pages the links, checks a suspended one
//   again, unlinks one, and reads the API's refusal inline;
// - a person who is not an admin sees no Slack section, never asks for links, and gets no Slack in a thread's sheet;
// - an admin who is not the owner sees the Slack section and the thread's Slack form;
// - in a thread's sheet the owner links it to Slack, and Enter in the channel field never saves the sheet;
// - with Slack off, nothing is on offer that cannot work.
// TICO_UI_SHOTS=<dir> saves the screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1300, height: 900};
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
    if (p.startsWith('/api/v2/slack')) seen.calls.push(`${method} ${p}${url.search}`);
    if (p === '/api/me') return json(PEOPLE[who]);
    if (p === '/api/v2/config') return json({company_name: 'Acme'});
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: Object.values(PEOPLE).map(({id, name, email}) => ({id, name, email}))});
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/tools') return json({integrations: []});
    if (p.startsWith('/api/v2/tools/')) return json({service: decodeURIComponent(p.split('/').pop()), title: 'Slack', access: '', writes: 'read', owner: 'tico', declared_as: '', body: '', queries: [], learnings: [], read_only: true});
    // Threads: the sidebar list, one thread, its timeline.
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: state.threads, dms: [], next_cursor: null});
    const single = p.match(/^\/api\/v2\/threads\/([^/]+)$/);
    if (single && method === 'GET') {
      const found = [...state.threads, thread('t-ops', 'Ops desk', 'company')].find(t => t.id === single[1]);
      return found ? json(found) : json({error: 'not_found'}, 404);
    }
    if (p === '/api/v2/threads/t-plan/timeline') return json({thread: state.threads[2], breadcrumb: [{id: 't-plan', title: 'Launch plan'}], next_cursor: null, messages: [
      {id: 'm1', seq: 1, thread_id: 't-plan', depth: 0, sender_id: 'human:ana', body: 'Launch is Monday.', created_at: at(30)},
      {id: 'm2', seq: 2, thread_id: 't-plan', depth: 0, sender_id: 'human:cara', body: 'We are ready on our side.', created_at: at(20)},
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
  await page.locator('#thread-settings[open]').waitFor();
  return page.locator('#thread-settings');
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const viewport of [DESKTOP, PHONE]) {
      const tag = viewport === PHONE ? 'phone' : 'desktop';

      // ---- The owner on Tools › Slack: its linked threads.
      {
        const {page, seen} = await open(browser, 'ana', {viewport});
        await page.goto('https://tico-ui.test/#/tools/slack');
        const slack = page.locator('#tool-slack-links');
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
        await page.waitForFunction(() => /Waiting/.test(document.querySelector('#tool-slack-links [data-link="sl2"] .cn-status')?.textContent || ''));
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
        await page.waitForFunction(() => /already linked/.test(document.querySelector('#tool-slack-links .cn-err')?.textContent || ''));
        await slack.locator('[name=channel]').fill('#launch');
        await slack.locator('[name=history]').check();
        await slack.locator('[data-cn-link]').click();
        await page.waitForFunction(() => /Launch plan → #launch/.test(document.querySelector('#tool-slack-links .cn-list')?.textContent || ''));
        assert.deepEqual(seen.linked, {thread_id: 't-plan', channel: '#launch', history: true});
        assert.equal(await slack.locator('.cn-err').textContent(), '', `${tag}: the refusal clears`);

        assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
        await shot(page, `${tag}-connections-owner`);
        assert.deepEqual(seen.errors, [], `${tag}: no page errors`);
        await page.close();
      }

      // ---- A person who is neither admin nor owner: no sections, no calls, nothing in a thread's sheet.
      {
        const {page, seen} = await open(browser, 'bob', {viewport});
        await page.goto('https://tico-ui.test/#/tools/slack');
        await page.locator('#int-detail h1').waitFor();
        assert.equal(await page.locator('#tool-slack-links').isHidden(), true, `${tag}: no Slack section`);
        // Bob started the thread, so he may open its sheet; Slack is not his to change.
        const dlg = await sheet(page);
        assert.equal(await dlg.locator('[data-tcn="slack"]').count(), 0, `${tag}: no Slack`);
        assert.equal(await dlg.locator('.tcn button, .tcn input').count(), 0);
        assert.deepEqual(seen.calls, [], `${tag}: never asks for links or companies`);
        assert.deepEqual(seen.errors, []);
        await page.close();
      }

      // ---- An admin who is not the owner: Slack.
      {
        const {page, seen} = await open(browser, 'cara', {viewport});
        await page.goto('https://tico-ui.test/#/tools/slack');
        await page.locator('#tool-slack-links [data-link="sl1"]').waitFor();
        const dlg = await sheet(page);
        await dlg.locator('[data-tcn="slack"] [name=tcn-channel]').waitFor();
        assert.deepEqual(seen.errors, []);
        await page.close();
      }

      // ---- Slack off and no Gateway: nothing on offer that cannot work, and what is already linked still shows.
      {
        const {page, seen, state} = await open(browser, 'ana', {viewport});
        state.slackOff = true; state.links = [];
        await page.goto('https://tico-ui.test/#/tools/slack');
        await page.locator('#tool-slack-links .cn-none').waitFor();
        assert.equal(await page.locator('#tool-slack-links [data-cn-open]').isHidden(), true, `${tag}: no Link a thread`);
        const dlg = await sheet(page);
        await page.waitForLoadState('networkidle');
        assert.equal(await dlg.locator('[name=tcn-channel]').count(), 0, `${tag}: no Slack form with Slack off`);
        assert.equal(await dlg.locator('[data-tcn="slack"]').isHidden(), true, `${tag}: no Slack section`);
        assert.equal(await dlg.locator('.tcn').isHidden(), true, `${tag}: no empty section left behind`);
        assert.deepEqual(seen.errors, []);
        await page.close();
      }

      // ---- The owner in a thread's sheet: link it to Slack.
      {
        const {page, seen} = await open(browser, 'ana', {viewport});
        const dlg = await sheet(page);
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
    console.log('Links outside: Slack per role, the thread sheet, desktop and phone');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
