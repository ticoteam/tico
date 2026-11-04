// Thread settings (ui/app/threads-page.js threadSettingsOpen): the ⋯ in a thread's header opens one panel whose rows
// save as they change, with the calls the thread's routes take: the name, who can read (A team waits for a team when
// there is a choice; the Slack row redraws after), Members, notifications (Off is a lasting mute; not following reads
// as Off), pin for everyone and how long messages are kept, then Summarize, Mark all read and Archive. Saves go one at
// a time, so a slow rename is not undone by a later change. A row the viewer may not use is left out: a reader who
// does not manage the thread, and a one-to-one DM (a group DM keeps its name). At desktop and at a phone's 390px,
// where the panel takes the screen.
// TICO_SCREENSHOT_DIR=<dir> also saves thread-settings-<desktop|phone>-<dark|light>.png.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const shots = process.env.TICO_SCREENSHOT_DIR;
const at = min => new Date(Date.now() - min * 60000).toISOString();
const me = (extra = {}) => ({member: true, following: true, notify: 'live', last_read_seq: 4, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false, ...extra});
const thread = (id, title, extra = {}) => ({id, title, audience: 'team', audience_group_id: 'ops', last_seq: 4, last_unread_seq: 0, last_message_at: at(5),
  me: me(), members: [], pinned_for_everyone: false, ...extra});
// Each page gets fresh copies: a PATCH changes the copy the page reads.
const world = () => ({
  't-plan': thread('t-plan', 'Launch plan', {me: me({can_manage: true, pinned: true})}),
  't-read': thread('t-read', 'Read only', {me: me({member: false, following: false})}),
  't-muted': thread('t-muted', 'Quiet', {me: me({notify: 'muted'})}),
  't-dm': {id: 't-dm', title: 'Chat with Bea', audience: 'personal', last_seq: 4, last_unread_seq: 0, last_message_at: at(3), me: me({can_manage: true}),
    members: [{actor: 'human:ana'}, {actor: 'human:bea'}], dm_with: [{actor: 'human:bea', name: 'Bea'}], dm_with_count: 1},
  't-gdm': {id: 't-gdm', title: 'Bea, Cy', audience: 'personal', last_seq: 4, last_unread_seq: 0, last_message_at: at(3), me: me({can_manage: true}),
    members: [{actor: 'human:ana'}, {actor: 'human:bea'}, {actor: 'human:cy'}], dm_with: [{actor: 'human:bea', name: 'Bea'}, {actor: 'human:cy', name: 'Cy'}],
    dm_with_count: 2},
});

async function open(browser, {phone = false} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const threads = world();
  const seen = {errors: [], patches: [], follows: [], readAll: [], catchUps: [], slack: 0};
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('dialog', d => d.accept());
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const body = () => JSON.parse(req.postData() || '{}');
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'bea', name: 'Bea', email: 'bea@acme.example'}],
      org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}, {id: 'eng', name: 'Engineering', my_access: {see: true, read: true, write: true}}]});
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/slack/links') return seen.slack++, json({links: [], available: false, next_cursor: null});
    const rail = () => Object.values(threads).filter(t => t.audience === 'team' && !t.archived_at);
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: rail(), dms: [threads['t-dm']], bot_dms: {}, next_cursor: null});
    if (p === '/api/v2/threads/rail') return json({pinned: rail().filter(t => t.me.pinned), threads: rail().filter(t => !t.me.pinned), catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads/catch-up') return json({items: [], count: 0, next_cursor: null});
    const m = p.match(/^\/api\/v2\/threads\/([^/]+)(?:\/([a-z-]+))?$/);
    const t = m && threads[m[1]];
    if (t) {
      const what = m[2];
      if (what === 'timeline') return json({thread: t, breadcrumb: [{id: t.id, title: t.title}], next_cursor: null,
        messages: [{id: 'x1-' + t.id, seq: 1, thread_id: t.id, sender_id: 'human:bea', body: 'Morning.', created_at: at(10), depth: 0}]});
      if (what === 'members') return json({members: [{actor: 'human:ana', role: 'owner'}, {actor: 'human:bea'}, {actor: 'bot:ops'}], next_cursor: null});
      if (what === 'read') return json({ok: true});
      if (what === 'calls') return json({calls: [], next_before: null, available: false});
      if (what === 'read-all') { seen.readAll.push(t.id); return json({ok: true}); }
      if (what === 'catch-up') { seen.catchUps.push(t.id); return json({thread_id: t.id, messages: 2, posted: {id: 'cu1'}}); }
      if (what === 'follow') {
        const b = method === 'PUT' ? body() : {};
        seen.follows.push(method === 'PUT' ? 'PUT:' + b.notify : method);
        t.me = method === 'PUT' ? {...t.me, following: true, notify: b.notify} : {...t.me, following: false};
        return json(method === 'PUT' ? {notify: b.notify} : {ok: true});
      }
      if (!what && method === 'PATCH') {
        const b = body(); seen.patches.push(b);
        if (b.title === 'Refused') return json({error: {code: 'invalid', detail: 'Not that name'}}, 422);
        if (b.title === 'Slow plan') await new Promise(r => setTimeout(r, 600));
        const {archived, ...rest} = b;
        Object.assign(t, rest, 'archived' in b ? {archived_at: archived ? at(0) : null} : {});
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
// The panel in both themes: thread-settings-<desktop|phone>-<dark|light>.png.
async function themed(page, phone) {
  if (!shots) return;
  for (const theme of ['dark', 'light']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.evaluate(() => Promise.race([new Promise(r => setTimeout(r, 1000)),
      Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})))]));
    await page.waitForTimeout(80);
    await shot(page, `thread-settings-${phone ? 'phone' : 'desktop'}-${theme}`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}
const panel = page => page.locator('#thread-settings[open]');
async function openPanel(page, id, phone = false) {
  if (!page.url().endsWith('#/thread/' + id)) {
    await page.goto('https://tico-ui.test/#/thread/' + id);
    await page.getByText('Morning.').waitFor();
  }
  const button = page.locator('.thv-head [data-thv-menu]');
  assert.equal(await button.getAttribute('aria-label'), 'Thread settings');
  await (phone ? button.tap() : button.click());
  await panel(page).waitFor();
  return panel(page);
}
const labels = dlg => dlg.locator('.ths-lbl').evaluateAll(els => els.filter(e => e.getClientRects().length).map(e => e.textContent.trim()));
const pressed = dlg => dlg.locator('[data-notify][aria-pressed="true"]').textContent();
const until = async (page, check, what) => {
  for (let i = 0; i < 60; i++) { if (check()) return; await page.waitForTimeout(50); }
  assert.fail('timed out: ' + what);
};

let browser = null;
(async () => {
  browser = await chromium.launch();

  // ---- Ana manages Launch plan, at desktop width
  {
    const {page, seen} = await open(browser);
    let dlg = await openPanel(page, 't-plan');
    assert.equal(await dlg.locator('.tmodal-head h2').textContent(), 'Thread settings');
    assert.deepEqual(await labels(dlg), ['Name', 'Who can read', 'Members', 'Notifications', 'Pin for everyone', 'Keep messages']);
    assert.equal(await dlg.locator('legend').count(), 0, 'no headings inside the rows');
    assert.equal(await pressed(dlg), 'All');
    assert.equal(await dlg.locator('[data-ths="members"]').count(), 1);
    assert.equal(await dlg.locator('[data-ths="archive"]').textContent(), 'Archive');
    assert.equal(await dlg.locator('[type=submit]').count(), 0, 'nothing to Save');
    const box = await dlg.boundingBox();
    assert.ok(box.width <= 540 && box.x > 300, `a dialog in the middle at desktop: ${JSON.stringify(box)}`);
    await themed(page, false);

    // Name saves on Enter; an unchanged or empty name sends nothing; a refused one goes back.
    const name = dlg.locator('#ths-name');
    await name.fill('Launch plan v2');
    await name.press('Enter');
    await until(page, () => seen.patches.length === 1, 'the rename');
    assert.deepEqual(seen.patches, [{title: 'Launch plan v2'}]);
    await page.locator('.thv-head .thv-title', {hasText: 'Launch plan v2'}).waitFor();
    await name.fill('  ');
    await dlg.locator('#ths-keep').focus();
    assert.equal(await name.inputValue(), 'Launch plan v2');
    await name.fill('Refused');
    await name.press('Enter');
    await dlg.locator('[data-status]', {hasText: 'Not that name'}).waitFor();
    assert.equal(await dlg.locator('#ths-name').inputValue(), 'Launch plan v2', 'the refused name goes back');
    seen.patches.length = 0;

    // A slow rename, then a notification change before it answers: both stick.
    await name.fill('Slow plan');
    await name.press('Enter');
    await until(page, () => seen.patches.length === 1, 'the slow rename');
    await dlg.locator('[data-notify="mentions"]').click();
    await dlg.locator('[data-notify="mentions"][aria-pressed="true"]').waitFor();
    await page.locator('.thv-head .thv-title', {hasText: 'Slow plan'}).waitFor();
    assert.equal(await dlg.locator('#ths-name').inputValue(), 'Slow plan');
    assert.deepEqual(seen.follows, ['PUT:mentions']);
    seen.patches.length = 0; seen.follows.length = 0;

    // Who can read: a radio saves at once. A team, with two to choose from, waits for one: nothing is sent until then.
    let slack = seen.slack;
    await dlg.locator('input[name=audience][value=company]').check();
    await until(page, () => seen.patches.length === 1, 'the audience');
    assert.deepEqual(seen.patches[0], {audience: 'company', audience_group_id: null});
    await until(page, () => seen.slack > slack, 'the Slack row redraws after the audience');
    assert.equal(await dlg.locator('input[name=audience]:checked').getAttribute('value'), 'company');
    await dlg.locator('input[name=audience][value=team]').check();
    assert.equal(await dlg.locator('select[name=group]').inputValue(), '', 'Choose a team');
    assert.equal(await dlg.locator('select[name=group] option[value=""]').textContent(), 'Choose a team');
    await page.waitForTimeout(300);
    assert.equal(seen.patches.length, 1, 'no team is sent before one is chosen');
    slack = seen.slack;
    await dlg.locator('select[name=group]').selectOption('eng');
    await until(page, () => seen.patches.length === 2, 'the team');
    assert.deepEqual(seen.patches[1], {audience: 'team', audience_group_id: 'eng'});
    await until(page, () => seen.slack > slack, 'the Slack row redraws after the team');
    assert.equal(await dlg.locator('select[name=group]').inputValue(), 'eng');
    assert.equal(await dlg.locator('select[name=group] option[value=""]').count(), 0, 'no Choose a team once one is set');
    // Another team, from the select, saves at once.
    await dlg.locator('select[name=group]').selectOption('ops');
    await until(page, () => seen.patches.length === 3, 'the other team');
    assert.deepEqual(seen.patches[2], {audience: 'team', audience_group_id: 'ops'});
    assert.ok(seen.patches.every(b => b.audience !== 'team' || b.audience_group_id), 'never a team with no group');
    seen.patches.length = 0;

    // Members opens the Members dialog over the panel; Done there comes back to it.
    await dlg.locator('[data-ths="members"]').click();
    const members = page.locator('#thread-members[open]');
    await members.locator('.thd-name', {hasText: 'Bea'}).waitFor();
    await members.locator('footer [data-close]').click();
    await members.waitFor({state: 'hidden'});
    assert.equal(await panel(page).count(), 1, 'the panel is still open');

    // Notifications: one choice saves; Off mutes, for good; another choice follows again.
    await dlg.locator('[data-notify="digest"]').click();
    await dlg.locator('[data-notify="digest"][aria-pressed="true"]').waitFor();
    await dlg.locator('[data-notify="off"]').click();
    await dlg.locator('[data-notify="off"][aria-pressed="true"]').waitFor();
    await dlg.locator('[data-notify="live"]').click();
    await dlg.locator('[data-notify="live"][aria-pressed="true"]').waitFor();
    assert.deepEqual(seen.follows, ['PUT:digest', 'PUT:muted', 'PUT:live']);

    // Pin for everyone is a switch; Keep messages a select.
    await dlg.locator('#ths-pin').click();
    await until(page, () => seen.patches.length === 1, 'the pin');
    assert.deepEqual(seen.patches[0], {pinned_for_everyone: true});
    assert.equal(await dlg.locator('#ths-pin').isChecked(), true);
    await dlg.locator('#ths-keep').selectOption('90');
    await until(page, () => seen.patches.length === 2, 'keep 90');
    assert.deepEqual(seen.patches[1], {retention_days: 90});
    await dlg.locator('#ths-keep').selectOption('');
    await until(page, () => seen.patches.length === 3, 'keep forever');
    assert.deepEqual(seen.patches[2], {retention_days: null});
    seen.patches.length = 0;

    // Done closes it; open again, it shows what was saved.
    await page.waitForLoadState('networkidle');
    await dlg.locator('footer [data-close]').click();
    await page.waitForFunction(() => !document.querySelector('#thread-settings')?.open);
    dlg = await openPanel(page, 't-plan');
    assert.equal(await dlg.locator('#ths-keep').inputValue(), '');
    assert.equal(await dlg.locator('#ths-pin').isChecked(), true);
    assert.equal(await dlg.locator('input[name=audience]:checked').getAttribute('value'), 'team');

    // The one-off actions run and close the panel.
    await dlg.locator('[data-ths="read"]').click();
    await page.waitForFunction(() => !document.querySelector('#thread-settings')?.open);
    assert.deepEqual(seen.readAll, ['t-plan']);
    dlg = await openPanel(page, 't-plan');
    await dlg.locator('[data-ths="catchup"]').click();
    await page.locator('.toast', {hasText: 'Summary ready'}).waitFor();
    assert.deepEqual(seen.catchUps, ['t-plan']);
    assert.equal(await panel(page).count(), 0);
    dlg = await openPanel(page, 't-plan');
    await dlg.locator('[data-ths="archive"]').click();
    await page.waitForFunction(() => !document.querySelector('#thread-settings')?.open);
    assert.deepEqual(seen.patches, [{archived: true}]);
    dlg = await openPanel(page, 't-plan');
    assert.equal(await dlg.locator('[data-ths="archive"]').textContent(), 'Unarchive');
    assert.ok(!(await labels(dlg)).includes('Pin for everyone'), 'an archived thread is off the rail: no pin for everyone');
    await page.keyboard.press('Escape');

    // A reader who does not manage the thread: Members, Notifications and the two actions; not following reads as Off.
    dlg = await openPanel(page, 't-read');
    assert.deepEqual(await labels(dlg), ['Members', 'Notifications']);
    assert.equal(await pressed(dlg), 'Off');
    assert.equal(await dlg.locator('[data-ths="archive"], #ths-name, #ths-pin, #ths-keep, .thd-aud').count(), 0);
    assert.deepEqual(await dlg.locator('.ths-acts button').allTextContents(), ['Summarize', 'Mark all read']);
    seen.follows.length = 0;
    await dlg.locator('[data-notify="mentions"]').click();
    await until(page, () => seen.follows.length === 1, 'mentions');
    assert.deepEqual(seen.follows, ['PUT:mentions']);
    await page.keyboard.press('Escape');

    // Muted shows as Off; another choice follows again.
    dlg = await openPanel(page, 't-muted');
    assert.equal(await pressed(dlg), 'Off');
    seen.follows.length = 0;
    await dlg.locator('[data-notify="digest"]').click();
    await dlg.locator('[data-notify="digest"][aria-pressed="true"]').waitFor();
    assert.deepEqual(seen.follows, ['PUT:digest']);
    await page.keyboard.press('Escape');

    // A one-to-one DM, managed: no name, no Who can read and no pin for everyone.
    dlg = await openPanel(page, 't-dm');
    const dm = await labels(dlg);
    for (const gone of ['Name', 'Who can read', 'Pin for everyone']) assert.ok(!dm.includes(gone), `no ${gone} in a DM: ${dm}`);
    assert.ok(dm.includes('Notifications'));
    await page.keyboard.press('Escape');

    // A group DM keeps its name: Rename, but still no Who can read.
    dlg = await openPanel(page, 't-gdm');
    const gdm = await labels(dlg);
    assert.ok(gdm.includes('Name') && !gdm.includes('Who can read'), `a group DM: ${gdm}`);
    seen.patches.length = 0;
    await dlg.locator('#ths-name').fill('Weekend crew');
    await dlg.locator('#ths-name').press('Enter');
    await until(page, () => seen.patches.length === 1, 'the group DM rename');
    assert.deepEqual(seen.patches, [{title: 'Weekend crew'}]);
    await page.keyboard.press('Escape');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // ---- The same panel on a phone: the whole screen, a finger's room, no sideways scroll
  {
    const {page, seen} = await open(browser, {phone: true});
    const dlg = await openPanel(page, 't-plan', true);
    const box = await dlg.boundingBox();
    assert.ok([box.x, box.y, box.width - 390, box.height - 844].every(v => Math.abs(v) <= 1), `full screen: ${JSON.stringify(box)}`);
    const wide = await dlg.evaluate(d => [d.scrollWidth, d.clientWidth, d.querySelector('.ths').scrollWidth, d.querySelector('.ths').clientWidth]);
    assert.ok(wide[0] <= wide[1] && wide[2] <= wide[3], `no sideways scroll: ${wide}`);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390);
    const small = await dlg.locator('.tmodal-x, [data-notify], .ths-acts button, footer button, #ths-name, #ths-keep').evaluateAll(els =>
      els.map(e => [e.textContent.trim() || e.id || e.className, Math.round(e.getBoundingClientRect().height)]).filter(([, h]) => h < 44));
    assert.deepEqual(small, [], 'every control is at least 44px tall');
    await themed(page, true);
    await dlg.locator('[data-notify="mentions"]').tap();
    await until(page, () => seen.follows.length === 1, 'mentions on a phone');
    assert.deepEqual(seen.follows, ['PUT:mentions']);
    await dlg.locator('.tmodal-x').tap();
    await page.waitForFunction(() => !document.querySelector('#thread-settings')?.open);
    assert.deepEqual(seen.errors, []);
    await page.close();
  }
  console.log('PASS: Thread settings: the ⋯ opens one panel; name, who can read, members, notifications (Off mutes, muted reads as Off), a slow rename kept, pin for everyone and keep messages save as they change; Summarize, Mark all read and Archive close it; A team waits for a chosen team; a reader and a one-to-one DM get only their rows, a group DM its name; full screen on a phone');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
