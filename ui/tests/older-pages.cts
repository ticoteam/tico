import type {Browser, Page, Route} from 'playwright';
// The older pages on the one page header (style guide, "Top bars: one line"), fixtures only:
// - Roadmap: while the public roadmap is not offered (404) one calm empty state that says so once and leads to the
//   Product pipeline: no red error, no Previous/Next, no filled New task. Any other failure says why and has Retry.
// - Tags: the header's quiet + opens the New tag form (closed until then); Cancel left, Create right; the list is rows.
// - Replay: who to show is the shared filter icon with a searchable list (no wall of faces); a pick asks the server for
//   that bot, puts a badge on the icon and a chip under the header whose ✕ shows everyone again; an empty range says
//   "Nothing yet" once.
// - SQL and Learnings: the shared header's title, and no caption under a night's date.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Opts = {width?: number; roadmap?: number; lateRoster?: number};
async function open(browser: Browser, hash: string, {width = 1440, roadmap = 404, lateRoster = 0}: Opts = {}): Promise<{page: Page; errors: string[]; replays: URLSearchParams[]; closes: string[]}> {
  const page = await browser.newPage({viewport: {width, height: 860}, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const errors: string[] = [], replays: URLSearchParams[] = [], closes: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) {
      const name = p.slice('/tico/ui/'.length), file = uiFile(name);
      if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
        : name.endsWith('.css') ? 'text/css' : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
    }
    if (p === '/api/employees') return json([{name: 'support', display_name: 'Support', status: 'active', state: 'active', schedules: []},
      {name: 'ops', display_name: 'Ops', status: 'active', state: 'active', schedules: []}]);
    if (p === '/api/humans') {
      // The roster can land after the page has drawn (a link opened cold).
      if (lateRoster) await new Promise(r => setTimeout(r, lateRoster));
      return json({people: [{id: 'ana', name: 'Ana Rivera'}], org_groups: [{id: 'ops', name: 'Operations'}]});
    }
    if (p === '/api/v2/seats/s1/recruit') return json({seat: {id: 's1', title: 'Install Crew Coordinator for the North and East Regions', reports_to: 'support', can_edit: true},
      candidates: [{template: 'dispatcher', name: 'Dispatcher', line: 'Builds the crews\' day', match: 'good', department_id: 'ops'}], agents: [], models: []});
    if (p === '/api/v2/seats/s1/close') { closes.push(p); return json({closed: true}); }
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, can_see_sql: true, config: {company_name: 'Acme'}});
    if (p === '/api/v2/roadmap-tasks') return roadmap === 404 ? json({error: {code: 'not_found', detail: 'The public roadmap is not available yet.'}}, 404)
      : json({error: {code: 'unavailable', detail: 'The roadmap could not load.'}}, roadmap);
    if (p === '/api/v2/pipelines') return json({pipelines: [{id: 'product', preset: 'product', name: 'Product'}]});
    if (p === '/api/v2/tags') return json({tags: [{id: 't1', key: 'bug', label: 'bug', is_template: false, metadata: {}, markdown: '', version: 1, bot_writers: []}]});
    if (p === '/api/v2/replay') {
      replays.push(url.searchParams);
      return json({from: url.searchParams.get('from'), to: url.searchParams.get('to'), items: [], names: {}, start: {tasks: [], bots: {support: {state: 'idle'}, ops: {state: 'idle'}}}});
    }
    if (p === '/api/v2/learnings') return json({nights: [{id: 'run1', night: '2026-11-12', status: 'done', updates: 1}]});
    if (p === '/api/v2/learnings/run1') return json({id: 'run1', night: '2026-11-12', status: 'done', sections: {bots: [{id: 'support', name: 'Support', saw: '', entries: [{title: 'Refunds need a lead', sources: [], change: {where: 'Support'}}]}], docs: [], market: []}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  return {page, errors, replays, closes};
}

(async () => {
  const browser = await chromium.launch();
  try {
    // Roadmap, not offered yet: one calm line and its one way on.
    {
      const {page, errors} = await open(browser, '#/roadmap');
      const empty = page.locator('.roadmap-empty');
      await empty.waitFor();
      assert.equal(await page.locator('header.ph.roadmap-head h1').innerText(), 'Roadmap');
      assert.equal((await empty.locator('p').innerText()).trim(), "The public roadmap isn't available yet.");
      assert.equal(await empty.locator('a.ghost').innerText(), 'Open Product', 'its one action');
      assert.match(await empty.locator('a.ghost').getAttribute('href') || '', /pipeline=product/);
      assert.equal(await page.locator('#main .err, #main [data-roadmap-back], #main [data-roadmap-next], #main button.primary').count(), 0,
        'no red error, no Previous/Next, no filled button');
      assert.equal(await page.locator('#main').evaluate(m => (m.textContent!.match(/available yet/g) || []).length), 1, 'said once');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Roadmap, failed: why, and Retry.
    {
      const {page, errors} = await open(browser, '#/roadmap', {roadmap: 503});
      const failed = page.locator('.roadmap-empty[role=alert]');
      await failed.waitFor({timeout: 30000});
      assert.match(await failed.innerText(), /could not load/);
      assert.equal(await failed.locator('[data-roadmap-retry]').innerText(), 'Retry');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Tags: the + opens the form; Cancel closes it and hands the focus back to the +.
    {
      const {page, errors} = await open(browser, '#/tags');
      await page.locator('.tag-list .tag-task').first().waitFor();
      const add = page.locator('header.ph.tags-head .ph-add[data-tag-new]'), card = page.locator('[data-tag-new-card]');
      assert.equal(await add.getAttribute('aria-label'), 'New tag');
      assert.equal(await card.isHidden(), true, 'the form waits behind the +');
      await add.click();
      await card.waitFor();
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLInputElement).name), 'label', 'the cursor in its name');
      assert.deepEqual(await card.locator('.tag-form-btns button').allInnerTexts(), ['Cancel', 'Create'], 'Cancel left, Create right');
      await card.locator('[data-tag-new-cancel]').click();
      assert.equal(await card.isHidden(), true);
      assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-tag-new]')), true, 'focus back on the +');
      assert.equal(await page.locator('.page-tags').evaluate(el => getComputedStyle(el).marginLeft), '0px', 'at the content\'s left edge');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Replay: Who behind the shared filter icon.
    {
      const {page, errors, replays} = await open(browser, '#/replay');
      await page.locator('#rp-player .rp-feed').waitFor();
      assert.equal(await page.locator('.rp-bots, .rp-bot, #rp-scope').count(), 0, 'no wall of faces, no bare select');
      assert.equal(await page.locator('#rp-player').evaluate(p => (p.textContent!.match(/Nothing yet/g) || []).length), 1, 'the empty feed says so once');
      assert.equal(await page.locator('#rp-note').isHidden(), true);
      assert.equal(await page.locator('header.ph.rp-head .ph-tools .rp-seg[role=tablist]').count(), 1, 'the range is a segment in the header');
      const who = page.locator('#rp-filter'), pop = page.locator('#task-filter-pop');
      assert.equal(await who.getAttribute('aria-label'), 'Who');
      await who.click();
      await pop.locator('.rp-who').waitFor();
      assert.deepEqual(await pop.locator('.tl-menu-h').allInnerTexts().then(t => t.map(x => x.toUpperCase())), ['TEAMS', 'BOTS', 'PEOPLE']);
      await pop.locator('.tl-menu-q').fill('sup');
      assert.deepEqual(await pop.locator('.tl-opt:not([hidden])').allInnerTexts(), ['Support'], 'the search narrows the list');
      assert.equal(await pop.locator('.tl-menu-h:not([hidden])').count(), 1, 'a heading with nothing under it goes');
      const asked = replays.length;
      await pop.locator('.tl-opt', {hasText: 'Support'}).click();
      await pop.waitFor({state: 'hidden'});
      for (let i = 0; i < 50 && replays.length === asked; i++) await page.waitForTimeout(50);
      assert.equal(replays.at(-1)!.get('bot'), 'support', 'the replay is asked for that bot');
      assert.equal(await who.getAttribute('aria-label'), 'Who, Support');
      assert.equal(await who.locator('.tl-fbadge').isHidden(), false);
      const chip = page.locator('#rp-chips .tl-chip');
      assert.match(await chip.innerText(), /Who\s*Support/);
      assert.match(page.url(), /bot=support/, 'the link carries it');
      await chip.locator('[data-rp-drop]').click();
      await chip.waitFor({state: 'detached'});
      for (let i = 0; i < 50 && replays.at(-1)!.get('bot'); i++) await page.waitForTimeout(50);
      assert.equal(replays.at(-1)!.get('bot'), null, '✕ shows everyone again');
      assert.equal(await who.locator('.tl-fbadge').isHidden(), true);
      // The icon closes its own menu (the popover closes itself on the press; the click must not open it again).
      await who.click();
      await pop.locator('.rp-who').waitFor();
      await who.click();
      await pop.waitFor({state: 'hidden'});
      assert.equal(await who.getAttribute('aria-expanded'), 'false', 'the button closes it');
      // Copy link is the header's own in ⋯; times are month first and 12-hour.
      assert.equal(await page.locator('header.rp-head .ph-menu .ph-copy').innerText(), 'Copy link');
      assert.match(await page.locator('.rp-clock').innerText(), /^[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d\d [AP]M$/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Replay opened from a link, the roster landing after the page draws: the chip, the badge and the Who list name who
    // it is for, for a bot, a person and a team.
    for (const [q, name, value] of [['bot=support', 'Support', 'bot:support'], ['person=ana', 'Ana Rivera', 'human:ana'], ['team=ops', 'Operations', 'team:ops']] as [string, string, string][]) {
      const {page, errors} = await open(browser, '#/replay?' + q, {lateRoster: 1200});
      await page.locator('#rp-player .rp-feed').waitFor();
      await page.locator('#rp-chips .tl-chip .v', {hasText: name}).waitFor({timeout: 15000});
      assert.equal(await page.locator('#rp-filter').getAttribute('aria-label'), `Who, ${name}`, q + ': the icon names it');
      assert.equal(await page.locator('#rp-filter .tl-fbadge').isHidden(), false, q + ': the badge');
      await page.locator('#rp-filter').click();
      assert.equal(await page.locator(`#task-filter-pop input[name=rp-who][value="${value}"]`).isChecked(), true, q + ': checked in the Who list');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Replay on a phone, nothing in the range: the feed is one muted line under the board, not half the screen.
    {
      const {page, errors} = await open(browser, '#/replay', {width: 390});
      await page.locator('#rp-player .rp-feed .rp-empty').waitFor();
      const feed = (await page.locator('#rp-player .rp-feed').boundingBox())!;
      assert.ok(feed.height < 60, `the empty feed is one line: ${feed.height}px`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Recruit at 390 with a long title: the header folds Close job into ⋯; reached there, it asks once and closes the job.
    {
      const {page, errors, closes} = await open(browser, '#/seat/s1', {width: 390});
      await page.locator('[data-recruit-row="dispatcher"]').waitFor();
      assert.equal(await page.locator('.rc-facts').innerText().then(t => t.replace(/\s+/g, ' ').trim()), 'Reports to Support', 'who it reports to, one fact line');
      assert.equal(await page.locator('header.rc-head > .ph-menu [data-recruit-close]').count(), 1, 'Close job is in ⋯');
      page.once('dialog', d => void d.accept());
      await page.locator('header.rc-head > .ph-more').click();
      await page.locator('[data-recruit-close]').click();
      for (let i = 0; i < 100 && !closes.length; i++) await page.waitForTimeout(50);
      assert.deepEqual(closes, ['/api/v2/seats/s1/close'], 'the job closes');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // SQL and Learnings on the one header; no count caption under a night.
    {
      const {page, errors} = await open(browser, '#/sql');
      await page.locator('#sql-text').waitFor();
      assert.equal(await page.locator('header.ph.sql-head h1.ph-t').innerText(), 'Query the database');
      assert.equal(await page.locator('#sql-run').getAttribute('aria-keyshortcuts'), 'Meta+Enter Control+Enter');
      assert.equal(await page.locator('.sql-bar > :last-child').getAttribute('id'), 'sql-run', 'Run is last');
      const bar = (await page.locator('.sql-bar').boundingBox())!, run = (await page.locator('#sql-run').boundingBox())!;
      assert.ok(Math.abs(run.x + run.width - (bar.x + bar.width)) < 2, 'Run at the right');
      await page.evaluate(() => { location.hash = '#/learnings'; });
      await page.locator('.lrn-grp').waitFor();
      assert.equal(await page.locator('.lrn-head p').count(), 0, 'no "N updates across" caption');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: older pages on the one header: Roadmap\'s calm empty state and Retry, Tags\' + and its form, Replay\'s Who filter (closes itself, named from a link), its one-line empty feed, Recruit\'s Close job through ⋯, SQL\'s Run last, Learnings');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
