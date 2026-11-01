// Settings changes a page at a time (ui/app/settings-history.js), on the owner's Settings > History > Changes and on a
// bot's History tab. A page with next_before ends in "Show older", which fetches the page after it (before=) and draws
// its rows below the ones shown; the control goes after the last page. Undo works on a row from a later page, and the
// list then reads its first page again.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [], calls = [], undos = [];
    page.on('pageerror', error => errors.push(error.message));
    const at = minutes => new Date(Date.parse('2026-10-28T12:00:00Z') - minutes * 60000).toISOString();
    // Newest first, as the server sends them. Ana renamed both bots; the owner's list holds both, the card one only.
    const CHANGES = ['h1', 'o1', 'h2', 'h3', 'o2', 'h4', 'h5'].map((id, i) => ({
      id, bot: id.startsWith('h') ? 'helper' : 'ops', field: 'identity',
      before: {display_name: `Name ${i + 1}`, description: ''}, after: {display_name: `Name ${i + 2}`, description: ''},
      actor: 'ana', via: '', transition_id: null, created_at: at(i * 10), undone_at: null, can_undo: true}));
    // A change still in progress: the owner's first page carries it, and no later page does.
    const PENDING = {id: 'tr1', bot: 'ops', kind: 'model', state: 'prepared', progress: {prepared: 0, total: 1},
      checkpoints: []};
    // Two rows a page, so the stub pages the way the server's byte cap does: a next_before on every page but the last.
    const pageOf = (rows, before) => {
      const start = before ? rows.findIndex(row => row.id === before) + 1 : 0;
      const slice = rows.slice(start, start + 2);
      return {changes: slice, next_before: start + 2 < rows.length ? slice.at(-1).id : null};
    };
    const bot = (name, display_name, revision) => ({name, display_name, description: '', status: 'active',
      state: 'active', host: 'keeper', can_chat: true, can_manage: true, operator: 'ana',
      my_access: {see: true, read: true, write: true}, schedules: [], revision, thread_mode: 'personal', users: [],
      online: true, machine: null});
    const bots = [bot('helper', 'Helper', 3), bot('ops', 'Ops', 5)];
    await page.route('**/*', route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) {
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript',
          body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      }
      if (p === '/api/me') {
        return json({id: 'ana', name: 'Ana', role: 'owner', email: 'ana@example.test', cloud: true, registered: true,
          config: {version: '0.3.52', app_name: 'Tico'}});
      }
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') {
        const bot = url.searchParams.get('bot') || '', before = url.searchParams.get('before') || '';
        calls.push({bot, before});
        const rows = CHANGES.filter(row => !bot || row.bot === bot);
        // The first page left out older in-progress changes that would not fit (`transitions_truncated`).
        return json({...pageOf(rows, before), ...(bot || before ? {transitions: []}
          : {transitions: [PENDING], transitions_truncated: true})});
      }
      const undo = p.match(/^\/api\/v2\/settings\/history\/([^/]+)\/undo$/);
      if (undo && request.method() === 'POST') {
        const row = CHANGES.find(change => change.id === undo[1]);
        undos.push({id: undo[1], ...request.postDataJSON()});
        Object.assign(row, {can_undo: false, undone_at: at(0)});
        const revision = bots.find(b => b.name === row.bot).revision + 1;
        return json({undone: true, bot: row.bot, field: row.field, revision});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const owner = '#set-history', card = '#bot-settings-history';
    const changeIds = scope => page.locator(`${scope} [data-settings-undo]`)
      .evaluateAll(els => els.map(el => el.dataset.settingsUndo));
    const showMore = scope => page.locator(`${scope} [data-settings-older]`);
    const waitForChanges = (scope, count) => page.waitForFunction(([sel, n]) =>
      document.querySelectorAll(`${sel} [data-settings-undo]`).length === n, [scope, count]);

    // The owner's Changes: page one carries the change in progress; each Show older appends the next page.
    await page.goto('https://tico-ui.test/#/settings/history');
    await page.locator('[data-history-view=changes]').click();
    await waitForChanges(owner, 2);
    assert.deepEqual(await changeIds(owner), ['h1', 'o1']);
    assert.deepEqual(calls.at(-1), {bot: '', before: ''});
    assert.equal(await showMore(owner).innerText(), 'Show older');
    assert.equal(await page.locator(`${owner} [data-transition-review]`).count(), 1);
    await showMore(owner).click();
    await waitForChanges(owner, 4);
    assert.deepEqual(await changeIds(owner), ['h1', 'o1', 'h2', 'h3']);
    assert.deepEqual(calls.at(-1), {bot: '', before: 'o1'});
    assert.equal(await page.locator(`${owner} [data-transition-review]`).count(), 1,
      'the change in progress is not drawn twice');
    assert.equal(await page.locator(owner, {hasText: 'Some older in-progress changes are not listed here.'}).count(), 1,
      'the list still says older in-progress changes are left out');
    await showMore(owner).click();
    await waitForChanges(owner, 6);
    assert.deepEqual(calls.at(-1), {bot: '', before: 'h3'});
    await showMore(owner).click();
    await waitForChanges(owner, 7);
    assert.deepEqual(calls.at(-1), {bot: '', before: 'h4'});
    assert.equal(await showMore(owner).count(), 0, 'the last page ends the control');
    // Undo on a row from a later page, then the first page again.
    await page.locator(`${owner} [data-settings-undo=o2]`).click();
    await page.waitForFunction(() => document.querySelectorAll('#set-history [data-settings-undo]').length === 2);
    assert.deepEqual(undos, [{id: 'o2', expected_revision: 5}]);
    assert.deepEqual(await changeIds(owner), ['h1', 'o1']);
    assert.equal(await showMore(owner).count(), 1);

    // A bot's card: its own changes only, paged the same way.
    await page.goto('https://tico-ui.test/#/bot/helper/history');
    await waitForChanges(card, 2);
    assert.deepEqual(await changeIds(card), ['h1', 'h2']);
    assert.deepEqual(calls.at(-1), {bot: 'helper', before: ''});
    await showMore(card).click();
    await waitForChanges(card, 4);
    assert.deepEqual(await changeIds(card), ['h1', 'h2', 'h3', 'h4']);
    assert.deepEqual(calls.at(-1), {bot: 'helper', before: 'h2'});
    assert.equal(await showMore(card).count(), 1, 'a page after this one');
    await showMore(card).click();
    await waitForChanges(card, 5);
    assert.deepEqual(await changeIds(card), ['h1', 'h2', 'h3', 'h4', 'h5']);
    assert.deepEqual(calls.at(-1), {bot: 'helper', before: 'h4'});
    assert.equal(await showMore(card).count(), 0, 'the last page ends the control');
    // Undo on a row from the second page reads the card's first page again, and the control returns.
    await page.locator(`${card} [data-settings-undo=h4]`).click();
    await waitForChanges(card, 2);
    assert.deepEqual(undos.at(-1), {id: 'h4', expected_revision: 3});
    assert.deepEqual(await changeIds(card), ['h1', 'h2']);
    assert.equal(await showMore(card).count(), 1);
    // Phone width: the control shows and the page does not scroll sideways.
    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => setDrawer(false));
    assert.equal(await showMore(card).isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS: settings history pages: Show older on the owner Changes and a bot card; Undo on appended rows');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
