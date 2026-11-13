import type {Page, Route} from 'playwright';
// Offline browser check of a page that comes back on the ask screen (spec #263, backend/page_rounds.py). The OK of a
// page at version 3, which Ana sent back at versions 1 and 2:
// - the right side is in parts, not one paragraph: "Version 3 · date" with Jev as a chip (✓ Meets, its reason on a
//   click, never a p-value), the review history (v1 · v2 · v3, each with its date, chip and answer), What changed in v3
//   (the bot's lines), Your note on v2 (cut to 2 lines, with more) and Your points (✓ done / ✗ not done, with why);
// - a step of the history opens that version in the page viewer;
// - "Changes since v2" sits over the page, on because Ana sent v2 back: the page viewer's line diff; off, the page with
//   each block not in v2 marked in its margin;
// - a page Jev has not judged yet, at version 1: "Jev checking", no history, no switch;
// - at 390 px the parts stack, nothing runs off the side, the history scrolls sideways and Approve is reachable.
// TICO_UI_SHOTS=<dir> saves a screenshot of each (light and dark at 1040, and 390).
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
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/tasks');
  await page.locator('#task-play').click();
  const dialog = page.locator('#need-card');
  await dialog.locator('.dc-hist').waitFor();
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
    assert.equal(await side.locator('.dc-said').count(), 0, "the paragraph of today is gone");
    // Why Ana is asked again, and the conflict her OK hands to the Builder, stay on the face, before the history.
    assert.deepEqual(await side.locator('.dc-notice').allInnerTexts(), ["New commits since Ana's OK.", CONFLICT]);
    assert.ok(await side.locator('.dc-notice + * .dc-step, .dc-notice ~ .dc-hist').count() > 0, 'the notices come before the history');
    assert.doesNotMatch(await side.innerText(), /p 0\.73|between 0\.2/, 'no p-value on the face');
    assert.doesNotMatch(await side.innerText(), /Version 3, after the send back/, "their note is not quoted back in the heading");
    assert.match(await side.locator('.dc-ver .dc-sub').innerText(), /^Spec · Version 3 · Nov 13$/);
    const order = await side.locator('.dc-q, .dc-ver, .dc-open-task, .dc-hist, .dc-part h4, .dc-notes, [data-dc="approve"]').evaluateAll(
      els => els.map(e => e.matches('.dc-part h4') ? (e.textContent || '').replace(/\s+\S+ \d+$/, '').trim() : e.className.split(' ')[0] || e.getAttribute('data-dc')));
    assert.deepEqual(order, ['dc-q', 'dc-ver', 'linkish', 'dc-hist', 'What changed in v3', 'Your note on v2', 'Your points', 'dc-notes', 'primary']);

    // Jev's chip: the verdict on its face, the reason on a click, no "(p …)".
    const chip = side.locator('.dc-ver [data-dc-jev]');
    assert.equal((await chip.textContent())?.replace(/\s+/g, ' ').trim(), '✓ Meets');
    assert.equal(await side.locator('.dc-jev-why').count(), 0);
    await chip.click();
    assert.equal(await side.locator('.dc-jev-why').innerText(), 'Clear and complete; the counts cite their queries');
    assert.equal(await side.locator('.dc-ver [data-dc-jev]').getAttribute('aria-expanded'), 'true');

    // The review history: one step per version, its date, Jev's chip and Ana's answer.
    const steps = side.locator('.dc-hist .dc-step');
    assert.deepEqual(await steps.evaluateAll(els => els.map(b => [...b.children].map(c => (c.textContent || '').trim()).join(' '))), [
      'v1 Nov 11 ? Sent back', 'v2 Nov 12 ✗ Sent back', 'v3 Nov 13 ✓']);
    assert.deepEqual(await steps.locator('.dc-jev').evaluateAll(els => els.map(e => e.getAttribute('title'))), [
      'Jev: Unsure · Cannot tell who uses it', "Jev: Doesn't · No sources on the counts", 'Jev: Meets · Clear and complete; the counts cite their queries']);
    const fits = await steps.evaluateAll(els => els.every(b => [...b.children].every(c => c.getBoundingClientRect().right <= b.getBoundingClientRect().right)));
    assert.ok(fits, 'each step holds its date, chip and answer');
    assert.equal(await steps.nth(2).getAttribute('aria-current'), 'step');

    // What changed, Your note (2 lines, then more), Your points.
    assert.deepEqual(await side.locator('.dc-changed-list li').allInnerTexts(), ['Sources on every evidence row', 'Cut the repeated second section']);
    const note = side.locator('.dc-clamp');
    const cut = await note.evaluate(e => [e.clientHeight, parseFloat(getComputedStyle(e).lineHeight), e.scrollHeight]);
    assert.ok(cut[0]! <= cut[1]! * 2 + 1 && cut[2]! > cut[0]!, `the note is cut to 2 lines (${cut})`);
    assert.match(await side.locator('.dc-part h4', {hasText: 'Your note on v2'}).innerText(), /Nov 12/);
    await side.locator('[data-dc-more="note"]').click();
    assert.equal(await side.locator('.dc-clamp.open').innerText(), NOTE);
    assert.equal(await side.locator('[data-dc-more]').count(), 0);
    assert.deepEqual(await side.locator('.dc-points li').evaluateAll(els => els.map(e => [e.getAttribute('data-done'), (e as HTMLElement).innerText.replace(/\s+/g, ' ').trim()])), [
      ['true', '✓ Sources on the evidence table each row links its query'],
      ['false', "✗ Phone picture it needs a person's phone; the 390 px render is attached"]]);

    // Changes since v2: on, because Ana sent v2 back; the line diff of the page viewer.
    const thing = dialog.locator('.dc-thing');
    const sw = thing.locator('[data-dc-since]');
    assert.equal(await thing.locator('.dc-switch').innerText(), 'Changes since v2');
    assert.equal(await sw.isChecked(), true);
    assert.deepEqual(await thing.locator('.dc-diff .tf-diff-lines .add').allInnerTexts(), ['+ The evidence has counts and sources.', '+ Decide: OK this page.']);
    assert.deepEqual(await thing.locator('.dc-diff .tf-diff-lines .del').allInnerTexts(), ['- The evidence has counts.']);
    assert.equal(await thing.locator('iframe.dc-page').count(), 0);
    if (shots) await page.screenshot({path: path.join(shots, 'page-rounds-1040-light.png')});
    await page.emulateMedia({colorScheme: 'dark'});
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    if (shots) await page.screenshot({path: path.join(shots, 'page-rounds-1040-dark.png')});
    await page.emulateMedia({colorScheme: 'light'});
    await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));

    // Off: the page, with each block not in v2 marked in its margin, and nothing else.
    await sw.click();
    const frame = thing.locator('iframe.dc-page');
    await frame.waitFor();
    const doc = await frame.getAttribute('srcdoc') ?? '';
    const marked = [...doc.matchAll(/<(\w+) data-tico-changed="">([^<]*)/g)].map(m => `${m[1]}:${m[2]}`);
    assert.deepEqual(marked, ['p:The evidence has counts and sources.', 'p:Decide: OK this page.']);
    const bar = await page.frameLocator('#need-card iframe.dc-page').locator('p[data-tico-changed]').first().evaluate(e => {
      const s = getComputedStyle(e, '::before');
      return [s.width, s.backgroundColor, s.position];
    });
    assert.deepEqual(bar, ['3px', 'rgb(217, 119, 6)', 'absolute']);
    if (shots) await page.screenshot({path: path.join(shots, 'page-rounds-1040-marks.png')});

    // A step opens that version in the page viewer.
    fetched.length = 0;
    await steps.nth(0).click();
    await page.waitForFunction(() => location.hash === '#/page/pa?v=1');
    await page.locator('#pg-view').waitFor();
    for (let i = 0; i < 50 && !fetched.includes('pa@1'); i++) await page.waitForTimeout(100);
    assert.ok(fetched.includes('pa@1') && !fetched.includes('pa@3'), `the viewer opens version 1 (${fetched})`);

    // ---- A page Jev has not judged, at version 1: Jev checking, no history, no switch, no parts with nothing to say.
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('#task-play').click();
    await dialog.locator('.dc-hist').waitFor();
    await dialog.locator('[data-dc="next"]').click();
    await dialog.locator('.dc-q', {hasText: 'Paste images'}).waitFor();
    await dialog.locator('.dc-jev-wait').waitFor();
    assert.equal(await dialog.locator('.dc-jev-wait').innerText(), 'Jev checking');
    assert.equal(await dialog.locator('.dc-hist').count(), 0);
    assert.equal(await dialog.locator('.dc-part').count(), 0);
    assert.equal(await dialog.locator('.dc-since').count(), 0);
    await page.close();

    // ---- 390 px: stacked, nothing off the side, the history scrolls sideways, Approve reachable.
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
    console.log('PASS: a page that comes back shows its version and Jev chip, the review history, what changed, the note sent back and each point answered; Changes since vK shows the diff or marks the page; a step opens that version.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
