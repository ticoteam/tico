import type {Page, Route} from 'playwright';
// Offline browser check of a page that comes back on the ask screen (spec #263, backend/page_rounds.py). The OK of a
// page at version 3, which Ana sent back at versions 1 and 2:
// - the right side is in parts, not one paragraph: one meta line (who asked · Spec · v3 · date · the Decision model's word · #80 ↗; the
//   line opens a menu with it all in full and the Decision model's reason, never a p-value), the question ("OK the spec?"), one line of
//   what changed in v3 (the bot's lines) and Your points (✓ done / ✗ not done, with why); Your note on v2 is Ana's
//   message above the ask, in full;
// - the version in the meta line is a menu: each version on one line (date, the Decision model's mark, the answer), which opens that
//   version in the page viewer, and "Changes since v2";
// - the page opens clean, even though Ana sent v2 back; Changes since v2 marks each block not in v2 in its margin;
// - a page the Decision model has not judged yet, at version 1: "Decision model checking", no versions in the menu;
// - at 390 px the parts stack, nothing runs off the side and Approve is reachable.
// TICO_UI_SHOTS=<dir> saves a screenshot of each (light and dark at 1040, and 390).
declare function actorLabel(actor: string): string;
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const RID = 'spec-ok-ana';
const brief = (id: string, title: string) => ({id, title, number: 80, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-13T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 7});
const need = (task: string, title: string, file: string, version: number, hour: number, line: string) => ({
  kind: 'ask', id: `v-${task}:${RID}`, task_id: task, requirement_id: RID, line, since: `2026-11-13T${String(hour).padStart(2, '0')}:00:00Z`,
  task: brief(task, title),
  ask: {requirement_id: RID, kind: 'page', line, who: 'human:ana', asked_by: 'bot:pm', choices: null, may: ['ok', 'no'], met: false,
    shows: [`page:${file}@${version}`], standing: 'waiting', answer: null, page: {file_id: file, version, ref: `page:${file}@${version}`}}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const NOTE = 'The evidence table has no sources, so nobody can check the counts. The second section repeats the first. '
  + 'The phone picture is cropped at the bottom and the buttons are cut off, which is the one thing I needed to see. '
  + 'Please also say what the decision is in the first two lines.';
const body = (extra: string) => `<!doctype html><html><body><h1>Decision view</h1><p>Right side in parts.</p>${extra}`
  + '<ul><li>Jev as a chip</li><li>Review history</li></ul><table><tr><td>Tasks</td><td>216</td></tr></table></body></html>';
const pages: Record<string, string> = {
  'pa@1': body('<p>First draft.</p>'),
  'pa@2': body('<p>The evidence has counts.</p>'),
  'pa@3': body('<p>The evidence has counts and sources.</p><p>Decide: OK this page.</p>'),
  'pb@1': '<h1>Paste images spec</h1><p>Paste.</p>',
};
// What the ask says besides the send back and Jev: kept on the face when the parts replace the line.
const CONFLICT = 'The PR has a merge conflict: Builder fixes it after your OK.';
const round = (version: number, day: number, verdict: string | null, reason: string, answers: unknown[], more: Record<string, unknown> = {}) => ({
  file_id: 'pa', version, at: `2026-11-${day}T09:00:00Z`, by: 'bot:pm', replaced: version < 3, note: '', changed: [], answered: [],
  jev: {verdict, reason}, answers, ...more});
const rounds = [
  round(1, 11, 'unsure', 'Cannot tell who uses it', [{by: 'human:ana', answer: 'no', note: 'Too long.', at: '2026-11-11T12:00:00Z'}]),
  round(2, 12, 'not_met', 'No sources on the counts', [{by: 'human:ana', answer: 'no', note: NOTE, at: '2026-11-12T15:00:00Z'}]),
  round(3, 13, 'met', 'Clear and complete; the counts cite their queries', [], {
    note: 'Version 3', changed: ['Sources on every evidence row', 'Cut the repeated second section'],
    answered: [{point: 'Sources on the evidence table', done: true, why: 'each row links its query'},
      {point: 'Phone picture', done: false, why: "it needs a person's phone; the 390 px render is attached"}]}),
];

const fetched: string[] = [];
async function open(page: Page, errors: string[]) {
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', async (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname;
    const json = (b: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(b)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, '../vendor/marked.min.js'), 'utf8')});
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: 2, version: '1', next: null, items: [
      need('t-a', 'Decision view: a page that comes back', 'pa', 3, 10,
        'New commits since Ana\'s OK. Version 3, after the send back: "' + NOTE + '". Jev is unsure: p 0.73, between 0.2 and 0.75. '
        + 'OK Ana (version 3) ' + CONFLICT),
      need('t-b', 'Paste images', 'pb', 1, 11, 'OK Ana (version 1)')]});
    if (p === `/api/v2/oks/${RID}`) return json({error: 'not_found'}, 404);
    if (p === `/api/v2/tasks/t-a/asks/${RID}/rounds`) return json({rounds, notices: ["New commits since Ana's OK.", CONFLICT]});
    if (p === `/api/v2/tasks/t-b/asks/${RID}/rounds`) return json({rounds: [{...round(1, 13, null, '', []), file_id: 'pb', replaced: false}]});
    if (p === '/api/v2/pages/pa') return json({page: {file_id: 'pa', title: 'Decision view', version: 3, kind: 'page', created_at: '2026-11-11T09:00:00Z',
      updated_at: '2026-11-13T09:00:00Z', owner: 'bot:pm'}, pinned: [], can_manage: false, can_delete: false});
    if (p === '/api/v2/files/pa/versions') return json({versions: [1, 2, 3].map(version => ({version, created_at: `2026-11-1${version}T09:00:00Z`}))});
    const file = /^\/api\/v2\/files\/(p[ab])$/.exec(p);
    if (file) fetched.push(`${file[1]}@${url.searchParams.get('v')}`);
    const text = file ? pages[`${file[1]}@${url.searchParams.get('v')}`] : undefined;
    if (text) return route.fulfill({contentType: 'text/html', headers: {'content-disposition': 'inline; filename="page.html"'}, body: text});
    // The task's read names its thread: the ask is answered in its chat (decide.ts decideChatMount).
    const read = /^\/api\/v2\/tasks\/(t-[ab])$/.exec(p);
    if (read && r.method() === 'GET') return json({task: {id: read[1], title: '', conversation_id: 'c-' + read[1]}});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/tasks');
  await page.locator('#task-play').click();
  const dialog = page.locator('#need-card');
  // The rounds are read: the ask in the chat is drawn in parts (on a phone in the Chat tab; an OK opens on its Spec).
  await dialog.locator('[data-dc-vmenu]').waitFor({state: 'attached'});
  return dialog;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const shots = process.env.TICO_UI_SHOTS;
  try {
    // ---- 1040 px: every part, in order.
    const page = await browser.newPage({viewport: {width: 1100, height: 900}, serviceWorkers: 'block'});
    const dialog = await open(page, errors);
    const side = dialog.locator('.dc-side');
    assert.equal(await side.locator('.dc-said').count(), 1, "the paragraph of today is gone: one line");
    assert.equal(await side.locator('.dc-msg-ask .dc-q').innerText(), 'OK the spec?', 'the ask asks its question');
    assert.equal(await side.locator('.dc-msg-ask .dc-one').innerText(), 'Sources on every evidence row; Cut the repeated second section', 'then one line of what changed');
    // Why Ana is asked again, and the conflict her OK hands to the Builder, stay on the face, before the history.
    assert.deepEqual(await side.locator('.dc-notice').allInnerTexts(), ["New commits since Ana's OK.", CONFLICT]);
    assert.ok(await side.locator('.dc-meta ~ .dc-notice + .dc-msg-b').count() > 0, 'the notices come after the meta line, before the question');
    assert.doesNotMatch(await side.innerText(), /p 0\.73|between 0\.2/, 'no p-value on the face');
    assert.doesNotMatch(await side.innerText(), /Version 3, after the send back/, "their note is not quoted back in the heading");
    const asker = await page.evaluate(() => actorLabel('bot:pm'));
    assert.equal((await side.locator('.dc-meta').innerText()).replace(/\s+/g, ' ').trim(), `${asker} · Spec · v3 · Nov 13 · ✓ Meets #80 ↗`);
    // The ask is the asker's last message in the task's chat; the chat's box and Approve under it.
    // Your note on v2 is your message above it, not in it.
    const order = await side.locator('.dc-msg-said, .dc-msg-ask > .dc-meta, .dc-msg-ask .dc-q, .dc-one, .dc-points, .dc-open-task, .dc-part, .p-text, [data-dc="approve"]').evaluateAll(
      els => els.map(e => ['dc-msg-said', 'dc-meta', 'dc-q', 'dc-one', 'dc-points', 'dc-open-task', 'dc-part', 'p-text'].find(c => e.classList.contains(c)) || e.getAttribute('data-dc')));
    assert.deepEqual(order, ['dc-msg-said', 'dc-meta', 'dc-q', 'dc-one', 'dc-points', 'p-text', 'approve']);

    // The line is one control; its menu says it in full: who, stage, version and date, then the Decision model's word and reason.
    const ctl = side.locator('[data-dc-vmenu]');
    await ctl.click();
    assert.deepEqual(await side.locator('.dc-vmenu .dc-vinfo').allInnerTexts(), [`${asker} · Spec · v3 · Nov 13`,
      'Decision model: Meets · Clear and complete; the counts cite their queries']);
    await side.locator('.dc-msg-said .th-body').click();   // a click off the menu (above it: the menu opens down over the question)

    // The version menu: one line per version, its date, the Decision model's mark and Ana's answer, then Changes since v2.
    await ctl.click();
    const steps = side.locator('.dc-vmenu [data-dc-ver]');
    assert.deepEqual((await steps.allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()), [
      'v1 · Nov 11 · ? · Sent back', 'v2 · Nov 12 · ✗ · Sent back', 'v3 · Nov 13 · ✓']);
    assert.deepEqual(await steps.locator('.dc-vjev').evaluateAll(els => els.map(e => e.getAttribute('title'))), [
      'Decision model: Unsure · Cannot tell who uses it', "Decision model: Doesn't · No sources on the counts", 'Decision model: Meets · Clear and complete; the counts cite their queries']);
    assert.equal(await steps.nth(2).getAttribute('aria-current'), 'true');
    assert.equal(await side.locator('.dc-vmenu [data-dc-since]').innerText(), 'Changes since v2');
    await side.locator('.dc-msg-said .th-body').click();   // a click off the menu (above it: the menu opens down over the question)
    assert.equal(await side.locator('.dc-vmenu:not([hidden])').count(), 0, 'a click elsewhere closes the menu');

    // Your note on v2, in full, as your message above the ask; Your points in the ask.
    const note = side.locator('.dc-msg-said');
    assert.equal(await note.locator('.th-body').innerText(), NOTE);
    assert.match((await note.locator('.th-head').innerText()).replace(/\s+/g, ' '), /^You Nov 12 on v2$/);
    assert.deepEqual(await side.locator('.dc-points li').evaluateAll(els => els.map(e => [e.getAttribute('data-done'), (e as HTMLElement).innerText.replace(/\s+/g, ' ').trim()])), [
      ['true', '✓ Sources on the evidence table each row links its query'],
      ['false', "✗ Phone picture it needs a person's phone; the 390 px render is attached"]]);

    // The page opens clean, though Ana sent v2 back: no marks, no diff.
    const thing = dialog.locator('.dc-thing');
    const frame = thing.locator('iframe.dc-page');
    await frame.waitFor();
    assert.doesNotMatch(await frame.getAttribute('srcdoc') ?? '', /data-tico-changed/);
    assert.equal(await thing.locator('.dc-diff, .dc-since').count(), 0);
    if (shots) await page.screenshot({path: path.join(shots, 'page-rounds-1040-light.png')});
    await page.emulateMedia({colorScheme: 'dark'});
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    if (shots) await page.screenshot({path: path.join(shots, 'page-rounds-1040-dark.png')});
    await page.emulateMedia({colorScheme: 'light'});
    await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));

    // Changes since v2 on: the page, with each block not in v2 marked in its margin, and a dot on the control.
    await ctl.click();
    await side.locator('.dc-vmenu [data-dc-since]').click();
    await page.waitForFunction(() => /data-tico-changed/.test(document.querySelector('#need-card iframe.dc-page')?.getAttribute('srcdoc') || ''));
    const doc = await frame.getAttribute('srcdoc') ?? '';
    const marked = [...doc.matchAll(/<(\w+) data-tico-changed="">([^<]*)/g)].map(m => `${m[1]}:${m[2]}`);
    assert.deepEqual(marked, ['p:The evidence has counts and sources.', 'p:Decide: OK this page.']);
    const bar = await page.frameLocator('#need-card iframe.dc-page').locator('p[data-tico-changed]').first().evaluate(e => {
      const s = getComputedStyle(e, '::before');
      return [s.width, s.backgroundColor, s.position];
    });
    assert.deepEqual(bar, ['3px', 'rgb(217, 119, 6)', 'absolute']);
    assert.equal(await side.locator('[data-dc-vmenu].on').count(), 1, 'the control shows changes are on');
    assert.equal(await side.locator('.dc-vmenu [data-dc-since]').getAttribute('aria-checked'), 'true');
    if (shots) await page.screenshot({path: path.join(shots, 'page-rounds-1040-marks.png')});

    // A step opens that version in the page viewer.
    fetched.length = 0;
    await ctl.click();
    await steps.nth(0).click();
    await page.waitForFunction(() => location.hash === '#/page/pa?v=1');
    await page.locator('#pg-view').waitFor();
    for (let i = 0; i < 50 && !fetched.includes('pa@1'); i++) await page.waitForTimeout(100);
    assert.ok(fetched.includes('pa@1') && !fetched.includes('pa@3'), `the viewer opens version 1 (${fetched})`);

    // ---- A page the Decision model has not judged, at version 1: Decision model checking, no history, no switch, no parts with nothing to say.
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('#task-play').click();
    await dialog.locator('.dc-meta').waitFor();
    await dialog.locator('[data-dc="next"]').click();
    await dialog.locator('.dc-tabs-t', {hasText: 'Paste images'}).waitFor({state: 'attached'});
    await dialog.locator('.dc-jev-wait').waitFor();
    assert.equal(await dialog.locator('.dc-jev-wait').innerText(), 'Decision model checking');
    assert.equal(await dialog.locator('.dc-vmenu [data-dc-ver]').count(), 0);
    assert.equal(await dialog.locator('.dc-part').count(), 0);
    assert.equal(await dialog.locator('[data-dc-since]').count(), 0);
    await page.close();

    // ---- 390 px: stacked, nothing off the side, Approve reachable.
    const phone = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    const small = await open(phone, errors);
    const sideBox = small.locator('.dc-side');
    const widths = await sideBox.evaluate(e => [e.scrollWidth, e.clientWidth]);
    assert.ok(widths[0]! <= widths[1]!, `nothing runs off the right side (${widths})`);
    const approve = small.locator('[data-dc="approve"]');
    await approve.scrollIntoViewIfNeeded();
    const box = (await approve.boundingBox())!;
    assert.ok(box.y >= 0 && box.y + box.height <= 844, `Approve is reachable (${box.y})`);
    if (shots) await phone.screenshot({path: path.join(shots, 'page-rounds-390.png')});
    await phone.close();

    assert.deepEqual(errors, []);
    console.log('PASS: a page that comes back shows one meta line with its version menu and the Decision model, what changed, the note sent back and each point answered; the page opens clean, Changes since vK marks it; a version in the menu opens that version.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
