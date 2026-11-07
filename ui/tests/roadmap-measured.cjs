// The Product roadmap board. Measured is a done step right after Shipped (backend/roadmap_flow.py measured_step), so its
// cards fold into the finished strip with Shipped: the strip starts folded, and unfolded both steps read their cards.
// TICO_UI_SHOTS=<dir> saves measured-board-<light|dark>-<desktop|phone>.png with the finished strip unfolded.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const STEPS = [['suggested', 'Suggested', 'open'], ['exploring', 'Exploring', 'doing'], ['spec', 'Spec', 'review'],
  ['approved', 'Approved', 'review'], ['building', 'Building', 'doing'], ['shipped', 'Shipped', 'done'],
  ['measured', 'Measured', 'done'], ['declined', 'Declined', 'declined']];
const types = [{id: 'product-roadmap', name: 'Product roadmap', role: 'product-roadmap', manager: 'product-lead',
  steps: STEPS.map(([id, name, status], position) => ({id, name, status, position, type_id: 'product-roadmap'}))}];
const stepOf = id => types[0].steps.find(step => step.id === id);
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const mkTask = (id, title, stepId) => ({id, title, body: 'Details.', owner_id: 'bot:ops', requester_id: 'human:ana',
  type_id: 'product-roadmap', type: {id: 'product-roadmap', name: 'Product roadmap'}, step: stepOf(stepId), step_id: stepId,
  status: stepOf(stepId).status, lane: 'company', version: 1, labels: [], links: [], parts: {total: 0, done: 0}, rank: 0,
  created_at: '2026-10-01T12:00:00Z', updated_at: '2026-10-01T12:00:00Z'});
const tasks = [mkTask('r-explore', 'Score the pricing page', 'exploring'), mkTask('r-ship', 'Ship the export flow', 'shipped'),
  mkTask('r-measure', 'Measure the onboarding email', 'measured'), mkTask('r-measure-2', 'Measure the trial length', 'measured')];

async function open(browser, {viewport = DESKTOP, theme = 'dark', unfolded = false}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  // The theme, and (for the screenshots) the finished strip unfolded, are this browser's own remembered choices.
  await page.addInitScript(([theme, unfolded]) => {
    try {
      localStorage.setItem('tico.theme', theme);
      if (unfolded) localStorage.setItem('tico.board.fold', JSON.stringify({'product-roadmap': {finished: false}}));
    } catch {}
  }, [theme, unfolded]);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tags' && req.method() === 'GET') return json({tags: []});
    if (p === '/api/v2/task-types' && req.method() === 'GET') return json({types});
    if (p === '/api/v2/tasks' && req.method() === 'GET') {
      const wanted = url.searchParams.get('status')?.split(',');
      return json({tasks: wanted ? tasks.filter(task => wanted.includes(task.status)) : tasks, next_offset: null});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/models') return json({models: [], harnesses: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/tasks?type=product-roadmap&view=board');
  const measuredShown = unfolded ? '[data-col="measured"] .bcard' : '#task-body .bcard';
  await page.waitForFunction(sel => TASKS_ST && !TASKS_ST.loading && TASKS_ST.type === 'product-roadmap' && document.querySelector('#task-body ' + sel),
    measuredShown);
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {});
    // Folded: only the open work shows, and the Measured cards wait in the finished strip with Shipped.
    assert.equal(await page.locator('#task-body .bfold [data-board-unfold="finished"]').count(), 1, 'the finished strip starts folded');
    assert.deepEqual(await page.locator('#task-body .bcard-title').allTextContents(), ['Score the pricing page']);
    assert.equal(await page.locator('#task-body [data-col="measured"]').count(), 0, 'no Measured column while the strip is folded');
    // Unfolded: Shipped and Measured read their cards, and Measured sits right after Shipped.
    await page.locator('#task-body [data-board-unfold="finished"]').click();
    await page.locator('#task-body [data-col="measured"] .bcard').first().waitFor({timeout: t(5000)});
    assert.deepEqual(await page.locator('#task-body [data-col="measured"] .bcard-title').allTextContents(),
      ['Measure the onboarding email', 'Measure the trial length']);
    assert.deepEqual(await page.locator('#task-body [data-col="shipped"] .bcard-title').allTextContents(), ['Ship the export flow']);
    const heads = await page.locator('#task-body .bcol > header > h2').allTextContents();
    assert.equal(heads[heads.indexOf('Shipped') + 1], 'Measured', 'Measured sits right after Shipped: ' + heads.join(' | '));
    // And folds again by hand.
    await page.locator('#task-body [data-board-fold="finished"]').click();
    await page.locator('#task-body [data-board-unfold="finished"]').waitFor();
    assert.equal(await page.locator('#task-body [data-col="measured"]').count(), 0, 'the strip folds again by hand');
    assert.deepEqual(errors, [], 'no page errors');
    await page.close();

    // Screenshots: the finished strip unfolded, in both themes at both sizes.
    const dir = process.env.TICO_UI_SHOTS;
    if (dir) {
      fs.mkdirSync(dir, {recursive: true});
      for (const [theme, viewport, size] of [['light', DESKTOP, 'desktop'], ['dark', DESKTOP, 'desktop'], ['light', PHONE, 'phone'], ['dark', PHONE, 'phone']]) {
        const shot = await open(browser, {viewport, theme, unfolded: true});
        assert.equal(await shot.page.evaluate(() => document.documentElement.dataset.theme), theme, `the ${theme} theme`);
        await shot.page.locator('#task-body [data-col="measured"] .bcard').first().waitFor({timeout: t(5000)});
        if (size === 'phone') assert.ok(await shot.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
        await shot.page.screenshot({path: path.join(dir, `measured-board-${theme}-${size}.png`), fullPage: true});
        assert.deepEqual(shot.errors, [], `${theme} ${size}: no page errors`);
        await shot.page.close();
      }
    }
    console.log('roadmap-measured: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
