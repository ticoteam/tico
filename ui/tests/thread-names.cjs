// A thread with no title is named by the people in it (the server's `name`, backend threads._thread_name): the rail row,
// the thread's header, its Catch up card, a Browse row and the Name field's placeholder all show that name, and the
// server's name is the one shown even when the members list would order the people differently. A DM is still named
// by the other person. Ana is the viewer; the people are Bea, Cy and Dan (fictional).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const at = min => new Date(Date.now() - min * 60000).toISOString();
const me = (extra = {}) => ({member: true, following: true, notify: 'live', last_read_seq: 4, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false, ...extra});
const thread = (id, extra = {}) => ({id, title: '', audience: 'members', last_seq: 4, last_unread_seq: 0, last_message_at: at(5), me: me(), members: [],
  pinned_for_everyone: false, ...extra});
// Each page gets fresh copies: a PATCH changes the copy the page reads.
const world = () => ({
  // Untitled, with Ana, Cy and Bea in its members list (Cy first): the name the server gives is Bea, Cy.
  't-group': thread('t-group', {name: 'Bea, Cy', me: me({can_manage: true}),
    members: [{actor: 'human:cy'}, {actor: 'human:bea'}, {actor: 'human:ana'}]}),
  't-dm': {id: 't-dm', title: '', audience: 'personal', name: 'Bea', last_seq: 4, last_unread_seq: 0, last_message_at: at(3), me: me({can_manage: true}),
    members: [{actor: 'human:ana'}, {actor: 'human:bea'}], dm_with: [{actor: 'human:bea', name: 'Bea'}], dm_with_count: 1},
});
// A thread in the Browse list, not joined: the viewer is not in it, so the server names it from what it may read.
const browsed = () => thread('t-open', {name: 'Bea, Dan +1', me: me({member: false, following: false})});

async function open(browser) {
  const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const threads = world();
  const seen = {errors: []};
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('dialog', d => d.accept());
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'bea', name: 'Bea', email: 'bea@acme.example'},
      {id: 'cy', name: 'Cy', email: 'cy@acme.example'}, {id: 'dan', name: 'Dan', email: 'dan@acme.example'}],
      org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}]});
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/slack/links') return json({links: [], available: false, next_cursor: null});
    const rail = () => Object.values(threads).filter(t => t.audience === 'members');
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: rail(), dms: [threads['t-dm']], bot_dms: {}, next_cursor: null});
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: rail(), catch_up: 1, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads/catch-up') return json({items: [{thread: threads['t-group'], messages: [{id: 'm1', seq: 4, thread_id: 't-group', sender_id: 'human:bea',
      body: 'Ready?', created_at: at(2), depth: 0}], new_count: 1, more: 0}], count: 1, next_cursor: null});
    if (p === '/api/v2/threads/browse') return json({threads: [browsed()], next_cursor: null});
    const m = p.match(/^\/api\/v2\/threads\/([^/]+)(?:\/([a-z-]+))?$/);
    const t = m && threads[m[1]];
    if (t) {
      const what = m[2];
      if (what === 'timeline') return json({thread: t, breadcrumb: [{id: t.id, title: t.name || ''}], next_cursor: null,
        messages: [{id: 'x1-' + t.id, seq: 1, thread_id: t.id, sender_id: 'human:bea', body: 'Morning.', created_at: at(10), depth: 0}]});
      if (what === 'members') return json({members: t.members, next_cursor: null});
      if (what === 'read') return json({ok: true});
      if (what === 'calls') return json({calls: [], next_before: null, available: false});
      if (!what && method === 'GET') return json(t);
    }
    return json(unrouted(p));
  });
  return {page, seen};
}

const until = async (page, check, what) => {
  for (let i = 0; i < 60; i++) { if (await check()) return; await page.waitForTimeout(50); }
  assert.fail('timed out: ' + what);
};
const title = page => page.locator('.thv-head .thv-title').textContent().then(t => t.trim());

let browser = null;
(async () => {
  browser = await chromium.launch();
  const {page, seen} = await open(browser);

  // The header of an untitled group names the people in it, Ana (the viewer) left out, in the server's order.
  await page.goto('https://tico-ui.test/#/thread/t-group');
  await page.getByText('Morning.').waitFor();
  assert.equal(await title(page), 'Bea, Cy', 'the header names the group by its people');

  // The rail row for the same thread, and the Catch up card that asks about it.
  const row = page.locator('#thread-rail .tr-title', {hasText: 'Bea, Cy'});
  await row.first().waitFor();
  assert.equal(await page.locator('#thread-rail .tr-title', {hasText: 'Bea, Cy'}).count(), 1, 'one rail row for the group');
  await page.locator('#thread-rail [data-tr-catch]').click();
  await page.locator('#catch-up .cu-card h3 > span:last-child', {hasText: 'Bea, Cy'}).waitFor();
  assert.equal(await page.locator('#catch-up .cu-card h3 > span:last-child').first().textContent(), 'Bea, Cy', 'the Catch up card names the group');
  await page.keyboard.press('Escape');
  await until(page, async () => !(await page.locator('#catch-up[open]').count()), 'Catch up closes');

  // A Browse row for a thread the viewer is not in: named by the server.
  await page.locator('#thread-rail [data-tr-browse]').click();
  await page.locator('#thread-browse .tr-browse-name > span:last-child', {hasText: 'Bea, Dan +1'}).waitFor();
  assert.equal(await page.locator('#thread-browse .tr-browse-name > span:last-child').first().textContent(), 'Bea, Dan +1', 'the Browse row names the thread');
  await page.keyboard.press('Escape');

  // The Name field stays empty (a title is what it sets) and its placeholder is the name the thread shows.
  await page.goto('https://tico-ui.test/#/thread/t-group');
  await page.getByText('Morning.').waitFor();
  await page.locator('.thv-head [data-thv-menu]').click();
  const dlg = page.locator('#thread-settings[open]');
  await dlg.waitFor();
  const name = dlg.locator('#ths-name');
  assert.equal(await name.inputValue(), '', 'an untitled thread has no stored name to edit');
  assert.equal(await name.getAttribute('placeholder'), 'Bea, Cy', 'the placeholder is the name the thread shows');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('#thread-settings')?.open);

  // A DM is named by the other person, as before.
  await page.goto('https://tico-ui.test/#/thread/t-dm');
  await page.getByText('Morning.').waitFor();
  assert.equal(await title(page), 'Bea', 'a DM is named by the person in it');

  assert.deepEqual(seen.errors, []);
  await page.close();
  console.log('PASS: Thread names: an untitled thread shows the people in it (viewer left out) in the header, rail, Catch up, Browse and the Name placeholder, as the server names it; a DM is still the other person.');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
