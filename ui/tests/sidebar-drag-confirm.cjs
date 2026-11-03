// Offline regression for moving teammates in the sidebar's team list (ui/app/sidebar.js orgDropAsk). Fixtures only.
//  - a drop saves nothing at once: the tree shows the new place and a strip under the moved row asks "Move Doc Updater
//    under Design?" with Move and Cancel, focus on Move; Cancel, Escape and a press elsewhere put the chart back with
//    no request and focus back on the row (not for a press elsewhere);
//  - Move saves (POST the bot's definition; PUT /v2/org/order for a drop above or below a fellow report) and the new
//    place holds through a reload; a failed save says why and puts the chart back;
//  - the strip spans the rail, comes in by transform and opacity in at most 150ms, and not at all with reduced motion;
//  - on touch, a long press picks the row up and the same drop asks the same question; a quick swipe only scrolls.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, beta} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const FULL = {see: true, read: true, write: true};
const ME = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true, owner_id: 'ana'};

async function open(browser, options = {}) {
  const context = await browser.newContext({viewport: {width: 1280, height: 800}, serviceWorkers: 'block', ...options});
  const page = await context.newPage();
  const errors = [], calls = [], fail = new Set();
  const people = [{id: 'ana', name: 'Ana Rivera', email: 'ana@example.test', org_parent: '', team: ''}];
  const bot = (name, display_name, extra = {}) => ({name, display_name, team: '', org_parent: 'p:ana', reports_to: 'human:ana',
    host: 'keeper', status: 'active', state: 'active', can_chat: true, my_access: FULL, users: [{id: 'ana', name: 'Ana'}],
    owner_id: 'ana', revision: 1, ...extra});
  const bots = [bot('design', 'Design'), bot('doc-updater', 'Doc Updater'), bot('reviewer', 'Reviewer')];
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/')) {
      const file = path.join(__dirname, '..', p);
      if (fs.existsSync(file)) return route.fulfill({contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript', body: fs.readFileSync(file)});
    }
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({...ME, config: {beta}});
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/messaging/bots') return json({bots: []});
    if (p === '/api/v2/seats') return json({seats: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/assistant') return json({available: true, state: 'active', bot: 'coo', name: 'Assistant', can_turn_on: false, room_id: 'asst', messages: [], earlier: null});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    let m;
    if (method !== 'GET' && fail.has(p)) {
      calls.push([method, p, 'failed']);
      return route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({error: {detail: 'The chart changed'}})});
    }
    if ((m = p.match(/^\/api\/v2\/bots\/([^/]+)\/definition$/)) && method === 'POST') {
      const body = request.postDataJSON(), one = bots.find(b => b.name === m[1]);
      calls.push(['POST', 'definition', m[1], body]);
      one.reports_to = body.reports_to; one.revision++;
      one.org_parent = body.reports_to.startsWith('human:') ? 'p:' + body.reports_to.slice(6) : 'b:' + body.reports_to;
      delete one.report_position;
      return json({});
    }
    if ((m = p.match(/^\/api\/v2\/org\/order\/([^/]+)$/)) && method === 'PUT') {
      const body = request.postDataJSON(); calls.push(['PUT', decodeURIComponent(m[1]), body]);
      body.reports.forEach((id, i) => { const [kind, key] = id.split(':'); (kind === 'human' ? people.find(r => r.id === key) : bots.find(r => r.name === key)).report_position = i; });
      return json({});
    }
    if (method !== 'GET') calls.push([method, p]);
    return json(unrouted(p));
  });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://tico-ui.test/#/updates');
  await page.locator('#tree a.node[data-org="b:reviewer"]').waitFor();
  return {page, context, errors, calls, fail, bots};
}

// The chart as nested text, a row's name or {name: [its reports]}; the strip is no row.
const shape = page => page.evaluate(() => {
  const walk = ul => [...ul.children].filter(li => li.tagName === 'LI' && !li.classList.contains('org-no-group')).map(li => {
    const kids = li.querySelector(':scope > ul:not(.node-sources)'), name = li.querySelector(':scope > .noderow .nm')?.textContent.trim();
    return kids ? {[name]: walk(kids)} : name;
  });
  return walk(document.querySelector('#tree'));
});
const node = (page, key) => page.locator(`#tree [data-org="${key}"]`);
const strip = page => page.locator('#tree .org-confirm');
const focused = page => page.evaluate(() => document.activeElement?.dataset.orgConfirm || document.activeElement?.dataset.org || document.activeElement?.tagName);
// A mouse drag; `edge` drops on the top or bottom of a fellow report (above or below it), else on its middle.
async function drag(page, from, to, edge) {
  await node(page, from).hover();
  await page.mouse.down();
  await page.mouse.move(40, 40);
  await page.mouse.move(45, 45);
  const box = await node(page, to).boundingBox();
  await node(page, to).hover({position: {x: 20, y: edge === 'top' ? 2 : edge === 'bottom' ? box.height - 2 : box.height / 2}});
  await page.mouse.up();
}
async function asks(page, text) {
  await strip(page).waitFor();
  assert.equal(await strip(page).locator('.org-confirm-q').textContent(), text);
  assert.deepEqual(await strip(page).getByRole('button').allTextContents(), ['Move', 'Cancel']);
}
async function shot(page, name) {
  if (!process.env.TICO_SCREENSHOT_DIR) return;
  await page.waitForTimeout(250);
  await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, name + '.png')});
}

async function mouse(browser) {
  const {page, context, errors, calls, fail} = await open(browser);
  const flat = ['Ana', 'Design', 'Doc Updater', 'Reviewer'];
  assert.deepEqual(await shape(page), flat);
  assert.equal(await page.locator('#tree [data-org-menu], #tree [aria-keyshortcuts]').count(), 0, 'no Move menu');
  await shot(page, 'sidebar-drag-before');

  // Onto another manager: the tree shows it there (under Design, whose reports are folded at first, so the strip is
  // under Design's row), and Cancel puts it back with no request.
  await drag(page, 'b:doc-updater', 'b:design');
  await asks(page, 'Move Doc Updater under Design?');
  assert.deepEqual(await shape(page), ['Ana', {Design: ['Doc Updater']}, 'Reviewer'], 'the proposed place while it waits');
  const under = () => strip(page).evaluate(el => el.closest('li').querySelector(':scope > .noderow > a.node').dataset.org);
  assert.equal(await under(), 'b:design', 'under the row it goes under');
  assert.equal(await node(page, 'b:doc-updater').isVisible(), false, 'gone from its old place');
  assert.equal(await focused(page), 'go', 'focus on Move');
  const [tree, box] = await Promise.all([page.locator('#tree').boundingBox(), strip(page).boundingBox()]);
  assert.ok(Math.abs(box.x - tree.x) <= 1 && Math.abs(box.x + box.width - tree.x - tree.width) <= 1, 'the strip spans the rail');
  const motion = await strip(page).evaluate(el => { const s = getComputedStyle(el); return [s.animationName, parseFloat(s.animationDuration)]; });
  assert.equal(motion[0], 'org-confirm-in'); assert.ok(motion[1] > 0 && motion[1] <= 0.15, 'in at most 150ms');
  await shot(page, 'sidebar-drag-confirm');
  await strip(page).getByRole('button', {name: 'Cancel'}).click();
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(await shape(page), flat);
  assert.equal(await focused(page), 'b:doc-updater', 'focus back on the row');
  assert.deepEqual(calls, []);

  // Again, and Move: the bot's definition is saved, and the place holds through a reload.
  await drag(page, 'b:doc-updater', 'b:design');
  await asks(page, 'Move Doc Updater under Design?');
  await strip(page).getByRole('button', {name: 'Move'}).click();
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(calls.shift(), ['POST', 'definition', 'doc-updater', {reports_to: 'design', expected_revision: 1}]);
  assert.equal(await focused(page), 'b:design', 'focus on the row it went under, as that one is folded');
  await page.reload();
  await node(page, 'b:reviewer').waitFor();
  const moved = ['Ana', {Design: ['Doc Updater']}, 'Reviewer'];
  assert.deepEqual(await shape(page), moved);

  // Above a fellow report: Escape puts it back, and so does a press elsewhere, with no request.
  await drag(page, 'b:reviewer', 'b:design', 'top');
  await asks(page, 'Move Reviewer above Design?');
  assert.deepEqual(await shape(page), ['Ana', 'Reviewer', {Design: ['Doc Updater']}]);
  assert.equal(await under(), 'b:reviewer', 'under the moved row');
  assert.equal(await node(page, 'b:reviewer').evaluate(el => el.classList.contains('org-moved')), true, 'which is marked');
  await page.keyboard.press('Escape');
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(await shape(page), moved);
  assert.equal(await focused(page), 'b:reviewer');
  await drag(page, 'b:reviewer', 'b:design', 'top');
  await asks(page, 'Move Reviewer above Design?');
  await page.mouse.click(900, 400);
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(await shape(page), moved);
  assert.deepEqual(calls, []);

  // A second drag while one waits replaces it; the first is put back.
  await drag(page, 'b:reviewer', 'b:design', 'top');
  await asks(page, 'Move Reviewer above Design?');
  await drag(page, 'b:reviewer', 'b:design');
  await asks(page, 'Move Reviewer under Design?');
  assert.deepEqual(await shape(page), ['Ana', {Design: ['Doc Updater', 'Reviewer']}]);
  await page.keyboard.press('Escape');
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(await shape(page), moved);
  assert.deepEqual(calls, []);

  // A failed save says why and puts it back.
  fail.add('/api/v2/org/order/human%3Aana');
  await drag(page, 'b:reviewer', 'b:design', 'top');
  await asks(page, 'Move Reviewer above Design?');
  await page.keyboard.press('Enter');
  await page.locator('.toast.err', {hasText: 'The chart changed'}).waitFor();
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(await shape(page), moved);
  assert.equal(calls.shift()[2], 'failed');
  fail.clear();

  // Enter on Move saves the order (PUT /v2/org/order), which holds through a reload.
  await drag(page, 'b:reviewer', 'b:design', 'top');
  await asks(page, 'Move Reviewer above Design?');
  await page.keyboard.press('Enter');
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(calls.shift(), ['PUT', 'human:ana', {reports: ['bot:reviewer', 'bot:design']}]);
  await page.reload();
  await node(page, 'b:reviewer').waitFor();
  assert.deepEqual(await shape(page), ['Ana', 'Reviewer', {Design: ['Doc Updater']}]);

  // With reduced motion the strip just appears.
  await page.emulateMedia({reducedMotion: 'reduce'});
  await drag(page, 'b:design', 'b:reviewer', 'top');
  await asks(page, 'Move Design above Reviewer?');
  assert.equal(await strip(page).evaluate(el => getComputedStyle(el).animationName), 'none');
  await page.keyboard.press('Escape');
  assert.deepEqual(calls, []);
  assert.deepEqual(errors, []);
  await context.close();
}

// Touch through the DevTools protocol: Chromium starts no HTML drag from it, as a phone does not.
async function touch(browser) {
  const {page, context, errors, calls} = await open(browser, {hasTouch: true});
  const cdp = await context.newCDPSession(page);
  const centre = async key => { const b = await node(page, key).boundingBox(); return {x: b.x + 30, y: b.y + b.height / 2}; };
  const send = (type, points) => cdp.send('Input.dispatchTouchEvent', {type, touchPoints: points});
  const route = () => page.evaluate(() => location.hash);
  const at = await route();
  // A quick swipe scrolls: nothing is picked up.
  let a = await centre('b:doc-updater');
  await send('touchStart', [a]);
  await send('touchMove', [{x: a.x, y: a.y + 30}]);
  await page.waitForTimeout(500);
  assert.equal(await page.locator('#tree .dragging').count(), 0, 'a swipe picks nothing up');
  await send('touchEnd', []);
  // A long press picks it up; carried onto Design and lifted, it asks the same question, and stays on this page.
  a = await centre('b:doc-updater');
  const b = await centre('b:design');
  await send('touchStart', [a]);
  await page.waitForTimeout(500);
  await page.locator('#tree .dragging[data-org="b:doc-updater"]').waitFor();
  for (let i = 1; i <= 5; i++) await send('touchMove', [{x: a.x + (b.x - a.x) * i / 5, y: a.y + (b.y - a.y) * i / 5}]);
  assert.equal(await page.locator('#tree .drop-target[data-org="b:design"]').count(), 1, 'the row under the finger is marked');
  await send('touchEnd', []);
  await asks(page, 'Move Doc Updater under Design?');
  assert.equal(await route(), at, 'lifting the finger opens no row');
  assert.deepEqual(await shape(page), ['Ana', {Design: ['Doc Updater']}, 'Reviewer']);
  await strip(page).getByRole('button', {name: 'Cancel'}).tap();
  await strip(page).waitFor({state: 'detached'});
  assert.deepEqual(await shape(page), ['Ana', 'Design', 'Doc Updater', 'Reviewer']);
  assert.deepEqual(calls, []);
  assert.deepEqual(errors, []);
  await context.close();
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    await mouse(browser);
    await touch(browser);
    console.log('PASS: a drop in the team list waits for Move; Cancel, Escape and a press elsewhere undo it; Move saves; touch long-press drags.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
