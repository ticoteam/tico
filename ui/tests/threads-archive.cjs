// Archive where a thread is seen (ui/app/thread-rail.ts, ui/app/thread-view.ts, ui/app/threads.ts): on a phone at 390x844
// a Threads row has an Archive button for a thread the viewer manages. Tapping it takes the thread off the list and its
// Catch up count with it, at once, and an Undo toast brings it back. The open thread's header has the same Archive,
// which goes back to the list; on an archived thread it says Unarchive. A thread the viewer does not manage has neither.
// Screenshots go to TICO_SCREENSHOT_DIR (default: the gd-shots folder in the scratchpad).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const shots = process.env.TICO_SCREENSHOT_DIR || '/private/tmp/claude-501/-Volumes-PNYRP60PSSD-orca-workspaces-emp-tico-open-source-AWS-Spend/2d002655-83b9-402a-ad61-b8f812cf5eb3/scratchpad/gd-shots';
const at = min => new Date(Date.now() - min * 60000).toISOString();
const me = (extra = {}) => ({member: true, following: true, notify: 'live', last_read_seq: 2, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false, ...extra});
const thread = (id, title, extra = {}) => ({id, title, audience: 'members', root_id: id, path: [id], parent_id: null, last_seq: 4, last_unread_seq: 4,
  last_message_at: at(5), me: me(), members: [{actor: 'human:ana'}, {actor: 'human:ben'}], pinned_for_everyone: false, ...extra});
// Each page gets fresh copies: a PATCH changes the copy the page reads.
const world = () => ({
  't-plan': thread('t-plan', 'Launch plan', {me: me({can_manage: true, unread: 2, activity: true})}),
  't-read': thread('t-read', 'Team lunch', {me: me({can_manage: false})}),
});

async function open(browser) {
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const threads = world();
  const seen = {errors: [], patches: []};
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('dialog', d => d.accept());
  const rail = () => Object.values(threads).filter(t => !t.archived_at);
  const catchUp = () => rail().filter(t => t.me.unread > 0);
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const body = () => JSON.parse(req.postData() || '{}');
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben', email: 'ben@acme.example'}], org_groups: []});
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/slack/links') return json({links: [], available: false, next_cursor: null});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: rail(), dms: [], bot_dms: {}, next_cursor: null});
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: rail(), catch_up: catchUp().length, catch_up_ids: catchUp().map(t => t.id), dms: {}, next_cursor: null});
    if (p === '/api/v2/threads/catch-up') return json({items: catchUp().map(t => ({thread: t, new_count: t.me.unread, mention: false, messages: [], more: 0})), count: catchUp().length, next_cursor: null});
    const m = p.match(/^\/api\/v2\/threads\/([^/]+)(?:\/([a-z-]+))?$/);
    const t = m && threads[m[1]];
    if (t) {
      const what = m[2];
      if (what === 'timeline') return json({thread: t, breadcrumb: [{id: t.id, title: t.title}], next_cursor: null,
        messages: [{id: 'x1-' + t.id, seq: 1, thread_id: t.id, sender_id: 'human:ben', body: 'Morning.', created_at: at(10), depth: 0}]});
      if (what === 'members') return json({members: [{actor: 'human:ana', role: 'owner'}, {actor: 'human:ben'}], next_cursor: null});
      if (what === 'read') return json({ok: true});
      if (what === 'calls') return json({calls: [], next_before: null, available: false});
      if (!what && method === 'PATCH') {
        const b = body(); seen.patches.push({id: t.id, ...b});
        if ('archived' in b) t.archived_at = b.archived ? at(0) : null;
        return json(t);
      }
      if (!what && method === 'GET') return json(t);
    }
    return json(unrouted(p));
  });
  return {page, seen};
}
async function shot(page, name) {
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `${name}.png`)});
}
const until = async (check, what) => {
  for (let i = 0; i < 80; i++) { if (await check()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail('timed out: ' + what);
};
const noSideways = page => page.evaluate(() => document.documentElement.scrollWidth <= 390);

let browser = null;
(async () => {
  browser = await chromium.launch();

  // The Threads list on a phone: the row's Archive is shown (a touch screen always shows it), is 44px, and only for the
  // thread the viewer manages. Tapping it takes the row off the list and the Catch up count with it; Undo brings it back.
  {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/threads');
    const plan = page.locator('.tr-row[data-thread="t-plan"]');
    await plan.waitFor();
    const archive = page.locator('[data-tr-arch="t-plan"]');
    assert.equal(await archive.getAttribute('aria-label'), 'Archive');
    assert.equal(await archive.evaluate(el => getComputedStyle(el).opacity), '1', 'the phone always shows the row action');
    const size = await archive.boundingBox();
    assert.ok(size.width >= 44 && size.height >= 44, `a 44px target: ${JSON.stringify(size)}`);
    assert.equal(await page.locator('[data-tr-arch="t-read"]').count(), 0, 'a thread the viewer does not manage has no Archive');
    assert.equal(await page.locator('[data-tr-catch] .tr-badge').textContent(), '1');
    assert.ok(await noSideways(page), 'no sideways scroll at 390px');
    await shot(page, 'threads-archive-phone-list');

    await archive.tap();
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor({state: 'detached'});
    await page.locator('[data-tr-catch] .tr-badge').waitFor({state: 'hidden'});
    assert.deepEqual(seen.patches, [{id: 't-plan', archived: true}]);
    const toast = page.locator('.toast', {hasText: 'Archived'});
    await toast.waitFor();
    await shot(page, 'threads-archive-phone-undo');

    await toast.getByRole('button', {name: 'Undo'}).tap();
    await plan.waitFor();
    await until(() => seen.patches.length === 2, 'the undo is sent');
    assert.deepEqual(seen.patches[1], {id: 't-plan', archived: false});
    await page.locator('[data-tr-catch] .tr-badge').waitFor({state: 'visible'});
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // The open thread's header: Archive on a thread the viewer manages goes back to the list; on an archived thread it
  // says Unarchive and brings the thread back in place. A thread the viewer does not manage has no Archive.
  {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/thread/t-read');
    await page.getByText('Morning.').waitFor();
    assert.equal(await page.locator('.thv-head [data-thv-archive]').count(), 0);

    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('Morning.').waitFor();
    const headArchive = page.locator('.thv-head [data-thv-archive]');
    assert.equal(await headArchive.getAttribute('aria-label'), 'Archive');
    const size = await headArchive.boundingBox();
    assert.ok(size.width >= 44 && size.height >= 44, `a 44px header target: ${JSON.stringify(size)}`);
    await shot(page, 'threads-archive-phone-thread');

    await headArchive.tap();
    await page.waitForFunction(() => location.hash === '#/threads');
    assert.deepEqual(seen.patches, [{id: 't-plan', archived: true}]);
    await page.locator('.toast', {hasText: 'Archived'}).waitFor();
    assert.equal(await page.locator('.tr-row[data-thread="t-plan"]').count(), 0);

    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('Morning.').waitFor();
    const back = page.locator('.thv-head [data-thv-archive]');
    assert.equal(await back.getAttribute('aria-label'), 'Unarchive');
    await back.tap();
    await until(async () => (await page.locator('.thv-head [data-thv-archive]').getAttribute('aria-label')) === 'Archive', 'Unarchive brings it back in place');
    assert.deepEqual(seen.patches[1], {id: 't-plan', archived: false});
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  console.log('PASS: Threads archive: a phone row shows Archive for a managed thread (44px, always shown), taking it and its Catch up count off at once with an Undo that brings it back; the open thread\'s header archives it back to the list, and Unarchive restores it in place; a thread the viewer does not manage has neither; no sideways scroll at 390px');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
