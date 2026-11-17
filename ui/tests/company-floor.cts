import type {Page, Route} from 'playwright';
// The Floor (ui/app/company-floor.ts), Company's first card (#/home, ui/app/dashboard.ts), from fixtures: halls, lines and
// stacks whose heights are relative and capped,
// Flow's mark only over a stage holding something that waits on the reader, the rate flip kept per browser, the card a
// hover shows and a click pins with its task rows, one factory with a deck per teammate and one mark by a stage's name,
// a factory the reader may not see never drawn, the old Dashboard, Floor, Campus, Graph and Factories links landing on
// Company, a phone's stacked halls and sheet. Fixtures only.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const SHOTS = process.env.TICO_FLOOR_SHOTS || '';
type Owner = {actor: string; count: number; needs_you: number};
const stage = (id: string, name: string, owners: Owner[], more = {owners: 0, count: 0}) => ({
  id, name, owners, more,
  count: owners.reduce((n, o) => n + o.count, 0) + more.count,
  needs_you: owners.reduce((n, o) => n + o.needs_you, 0),
});
const o = (actor: string, count: number, needs_you = 0): Owner => ({actor, count, needs_you});
const FEATURES = {id: 'features', name: 'Features', manager_id: 'bot:pm', in: {day: 3, week: 19}, out: {day: 2, week: 14}, stages: [
  stage('pitch', 'Pitch', [o('bot:f1', 2), o('bot:f2', 1)]),
  stage('spec', 'Spec', [o('bot:f1', 4, 4), o('bot:f2', 2, 2), o('bot:f3', 1, 1), o('bot:f4', 1), o('bot:f5', 1)]),
  stage('build', 'Build', [o('bot:b1', 400)]),
  stage('validate', 'Validate', []),
  stage('release', 'Release', [o('bot:rm', 2, 1)]),
]};
// Fourteen holders in one stage: the server lists twelve and sums the other two.
const TRIAGE = stage('triage', 'Triage', Array.from({length: 12}, (_, i) => o(`bot:fx${i}`, 12 - i)), {owners: 2, count: 3});
const FIXES = {id: 'fixes', name: 'Fixes', manager_id: 'bot:em', in: {day: 9, week: 52}, out: {day: 7, week: 41}, stages: [
  TRIAGE, stage('fix-build', 'Build', [o('bot:fb', 7)]), stage('fix-qa', 'QA', [o('bot:qa', 11)]),
]};
const FLOOR = {factories: [
  {id: 'f-product', name: 'Product', manager_id: null, pipelines: [FEATURES]},
  {id: 'f-eng', name: 'Engineering', manager_id: null, pipelines: [FIXES]},
]};
// `idle` reports to the Product manager and holds no task: it is on the hall's chart all the same.
const BOTS = ['pm', 'f1', 'f2', 'f3', 'f4', 'f5', 'b1', 'rm', 'em', 'fb', 'qa', 'idle', ...Array.from({length: 12}, (_, i) => `fx${i}`)]
  .map(id => ({id, slug: id, display_name: id.toUpperCase(), state: 'active', ...(id === 'idle' ? {reports_to: 'pm'} : {})}));
const TASKS = Array.from({length: 5}, (_, i) => ({id: `t${i}`, number: 200 + i, title: `Task ${i}`, status: 'open', stage_id: 'spec',
  pipeline_id: 'features', owner_id: 'bot:f1', stage_since: new Date(Date.now() - (i + 1) * 3600e3).toISOString(), turn: null,
  version: 1, snoozed: false, pushed_past: false, on_person: false, requester_id: null, assignee_id: null, due_at: null, late: null}));

type Hub = {floor: unknown; fail: boolean; reads: URL[]};
// Company (person:me) as the server answers it: the Floor first and full width, a card under it.
const COMPANY = {place: 'person:ana', kind: 'person', saved: true, can_edit: true, pins: [
  {id: 'p1', item: 'card:c1', position: 0, size: 'large', made_by: 'human:ana',
    card: {id: 'c1', template: 'floor', title: '', settings: {}, name: 'Floor', heading: 'Floor', team_name: '', hidden: false, data: {}}},
  {id: 'p2', item: 'card:c2', position: 1, size: 'medium', made_by: 'human:ana',
    card: {id: 'c2', template: 'latest_pages', title: '', settings: {team: '', count: 5}, name: 'Latest pages', heading: 'Latest pages', team_name: '', hidden: false, data: {pages: []}}},
]};
const levels = (page: Page, sel: string) => page.locator(sel).evaluateAll(els => els.map(el => el.querySelectorAll('.cf-box:not(.cf-none)').length));

(async () => {
  const browser = await chromium.launch({channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string, headless: true});
  try {
    const open = async (width: number, height: number, hub: Hub, hash = '#/home', touch = false) => {
      const context = await browser.newContext({viewport: {width, height}, serviceWorkers: 'block', hasTouch: touch, isMobile: touch});
      const page = await context.newPage();
      const errors: string[] = [];
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
        if (p === '/api/v2/places/person%3Ame') return json(COMPANY);
        if (p === '/api/v2/company/floor') { hub.reads.push(url); return hub.fail ? json({error: 'boom'}, 500) : json(hub.floor); }
        if (p === '/api/v2/tasks') { hub.reads.push(url); return json({tasks: TASKS, cursor: 'c1', next_offset: null}); }
        if (p === '/api/employees') return json(BOTS);
        if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
        if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, config: {company_name: 'Acme Studio'}});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/');
      await page.waitForFunction(() => location.hash === '#/home');
      await page.evaluate((h: string) => { location.hash = h; }, hash);
      await page.waitForFunction(() => !document.querySelector(':root:active-view-transition'));
      return {page, errors, close: () => context.close()};
    };
    const shot = async (page: Page, name: string) => {
      if (!SHOTS) return;
      await page.waitForFunction(() => !document.querySelector(':root:active-view-transition'));
      await page.screenshot({path: path.join(SHOTS, name + '.png')});
    };
    const fresh = (): Hub => ({floor: FLOOR, fail: false, reads: []});

    // ---- the company, on a computer
    {
      const hub = fresh();
      const {page, errors, close} = await open(1440, 900, hub);
      await page.locator('.cf-hall').first().waitFor();
      // The Floor is Company's first card, full width, with the other cards under it; Company has no tabs.
      assert.deepEqual(await page.locator('#dash-grid .dash-pin-head h2').allInnerTexts(), ['Floor', 'Latest pages']);
      assert.equal(await page.locator('#dash-grid .dash-pin').first().locator('.cf-host .cf-hall').count(), 2);
      assert.equal(await page.locator('#dash-grid .dash-pin').first().getAttribute('data-size'), 'large');
      assert.equal(await page.locator('#main header.ph .ph-tabs').count(), 0);
      assert.deepEqual(await page.locator('.cf-hall').evaluateAll(els => els.map(el => el.getAttribute('data-cf-factory'))), ['f-product', 'f-eng'],
        'a hall for each factory the server lists, and no other');
      assert.equal(await page.locator('.cf-hall[data-cf-factory="f-product"] .cf-line').count(), 1);
      // Heights are relative and capped: 3, 9 and 400 tasks stand at 3, 5 and 8 boxes; an empty stage is an outlined spot.
      assert.deepEqual(await levels(page, '[data-cf-line="features"] .cf-stack'), [3, 5, 8, 0, 2]);
      assert.equal(await page.locator('[data-cf-line="features"] .cf-stack[data-cf-stage="validate"] .cf-box.cf-none').count(), 1);
      assert.deepEqual(await levels(page, '[data-cf-line="fixes"] .cf-stack'), [8, 5, 6]);
      assert.deepEqual(await page.evaluate(() => [1, 3, 10, 50, 400].map(n => (window as unknown as {cfLayers: (n: number) => number}).cfLayers(n))), [1, 3, 6, 8, 8], '1, 3, 10 and 50 stand apart');
      // Flow's mark only over the stages holding something that waits on the reader, and no names on the floor.
      assert.deepEqual(await page.locator('.cf-flow').evaluateAll(els => els.map(el => el.getAttribute('data-cf-flow'))), ['spec', 'release']);
      assert.equal(await page.locator('.cf-sname').count(), 0, 'no stage names on the company floor');
      // A dim org chart per hall: the manager over its reports and the teammates holding its work.
      assert.equal(await page.locator('.cf-hall[data-cf-factory="f-product"] .cf-org-top [data-cf-mate="bot:pm"]').count(), 1);
      assert.equal(await page.locator('.cf-hall[data-cf-factory="f-product"] .cf-org-row .cf-mate').count(), 8);
      assert.equal(await page.locator('.cf-hall[data-cf-factory="f-product"] .cf-org-row [data-cf-mate="bot:idle"]').count(), 1);
      const scroll = await page.evaluate(() => { const m = document.querySelector('#main')!; return m.scrollWidth - m.clientWidth; });
      assert.ok(scroll <= 0, 'no sideways scroll');
      await shot(page, 'floor-desktop');

      // The rates flip together between a day and a week, and the choice is kept.
      const rates = () => page.locator('.cf-rate').allInnerTexts();
      assert.deepEqual(await rates(), ['3/day', '2/day', '9/day', '7/day']);
      await page.locator('.cf-rate').first().click();
      assert.deepEqual(await rates(), ['19/week', '14/week', '52/week', '41/week']);
      await page.reload();
      await page.locator('.cf-hall').first().waitFor();
      assert.deepEqual(await rates(), ['19/week', '14/week', '52/week', '41/week'], 'kept in this browser');
      await page.locator('.cf-rate').last().click();
      assert.deepEqual(await rates(), ['3/day', '2/day', '9/day', '7/day']);

      // Pointing at a teammate lights its stacks.
      await page.locator('.cf-hall[data-cf-factory="f-product"] [data-cf-mate="bot:f1"]').hover();
      assert.equal(await page.locator('.cf-hall[data-cf-factory="f-product"].cf-lit').count(), 1);
      assert.deepEqual(await page.locator('.cf-hall[data-cf-factory="f-product"] .cf-stack.cf-hit').evaluateAll(els => els.map(el => el.getAttribute('data-cf-stage'))),
        ['pitch', 'spec']);
      await page.mouse.move(5, 890);
      await page.waitForFunction(() => !document.querySelector('.cf-hall.cf-lit'));

      // Pointing at a stack shows its card: who, the stage, the oldest rows of its tasks; nothing pinned yet.
      const spec = page.locator('[data-cf-line="features"] .cf-stack[data-cf-stage="spec"]');
      const card = page.locator('#cf-card');
      hub.reads.length = 0;
      await spec.hover();
      await card.locator('.cf-row').first().waitFor();
      assert.equal(await card.isVisible(), true);
      assert.equal(await card.evaluate(el => el.classList.contains('cf-pinned')), false);
      assert.equal(await card.getAttribute('role'), 'dialog');
      assert.ok((await card.getAttribute('aria-label'))!.length > 0, 'the card has an accessible name');
      assert.equal(await card.locator('.cf-row').count(), 5);
      assert.equal(await card.locator('.cf-row-more').count(), 1, 'nine tasks, five rows: the rest counted');
      assert.equal(await card.locator('[data-cf-flow="spec"]').count(), 1);
      const read = hub.reads.find(u => u.pathname === '/api/v2/tasks')!;
      assert.equal(read.searchParams.get('pipeline'), 'features');
      assert.equal(read.searchParams.get('stages'), 'spec');
      assert.equal(read.searchParams.get('owners'), null, 'a company stack lists every holder\'s tasks');
      await shot(page, 'floor-desktop-card');
      // Away from it, it goes.
      await page.mouse.move(5, 890);
      await page.waitForFunction(() => document.querySelector<HTMLElement>('#cf-card')!.hidden);

      // A click pins the same card; Escape closes it and gives the stack its focus back.
      await spec.click();
      await card.locator('.cf-row').first().waitFor();
      assert.equal(await card.evaluate(el => el.classList.contains('cf-pinned')), true);
      assert.equal(await spec.getAttribute('aria-expanded'), 'true');
      await page.mouse.move(5, 890);
      await page.waitForTimeout(250);
      assert.equal(await card.isVisible(), true, 'a pinned card stays');
      await page.keyboard.press('Escape');
      assert.equal(await card.isHidden(), true);
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-cf-stage')), 'spec');
      // The keyboard: Enter on a focused stack pins its card.
      await page.locator('[data-cf-line="features"] .cf-stack[data-cf-stage="build"]').focus();
      await page.keyboard.press('Enter');
      await card.locator('.cf-row').first().waitFor();
      assert.equal(await card.evaluate(el => el.classList.contains('cf-pinned')), true);
      // A row opens its task the normal way.
      await page.evaluate(() => {
        const w = window as unknown as Record<string, unknown>;
        w['__opened'] = [];
        w['taskModalOpen'] = (id: string) => { (w['__opened'] as string[]).push(id); return Promise.resolve(); };
        w['decideStageOpen'] = (s: string, p: string) => { (w['__opened'] as string[]).push(`flow:${p}/${s}`); return Promise.resolve(true); };
      });
      await card.locator('.cf-row').first().click();
      assert.equal(await card.isHidden(), true);
      // Flow's mark opens Flow on its stage.
      await page.locator('[data-cf-line="features"] .cf-slot > [data-cf-flow="spec"]').click();
      assert.deepEqual(await page.evaluate(() => (window as unknown as Record<string, unknown>)['__opened']), ['t0', 'flow:features/spec']);
      assert.deepEqual(errors, []);
      await close();
    }

    // ---- one factory: a deck per teammate, the stage named under the line, one mark beside the name
    {
      const hub = fresh();
      const {page, errors, close} = await open(1440, 900, hub, '#/overview/floor/f-product');
      await page.locator('.cf-stage').first().waitFor();
      assert.equal(await page.locator('.cf-hall').count(), 1);
      assert.equal(await page.locator('#main header.ph .ph-t').innerText(), 'Product');
      assert.equal(await page.locator('#main header.ph .ph-back').getAttribute('href'), '#/home', 'back to Company');
      const decks = (id: string) => page.locator(`.cf-stage[data-cf-stage-id="${id}"] .cf-deck`);
      assert.deepEqual(await Promise.all(['pitch', 'spec', 'build', 'validate', 'release'].map(id => decks(id).count())), [2, 5, 1, 0, 1]);
      assert.equal(await page.locator('.cf-stage[data-cf-stage-id="validate"] .cf-box.cf-none').count(), 1, 'an empty stage keeps its spot');
      // Decks of different heights, capped: 4 tasks and 1 task, and 400 no taller than the cap.
      const plies = (sel: string) => page.locator(sel).evaluate(el => el.querySelectorAll('.cf-ply').length);
      assert.equal(await plies('.cf-stage[data-cf-stage-id="spec"] .cf-deck[data-cf-actor="bot:f1"]'), 3);
      assert.equal(await plies('.cf-stage[data-cf-stage-id="spec"] .cf-deck[data-cf-actor="bot:f5"]'), 0);
      assert.equal(await plies('.cf-stage[data-cf-stage-id="build"] .cf-deck'), 7);
      // One mark per stage that waits on the reader, beside its name; never one per deck.
      assert.equal(await page.locator('.cf-stage[data-cf-stage-id="spec"] .cf-flow').count(), 1);
      assert.equal(await page.locator('.cf-stage[data-cf-stage-id="spec"] .cf-sname .cf-flow').count(), 1);
      assert.equal(await page.locator('.cf-deck .cf-flow').count(), 0);
      assert.equal(await page.locator('.cf-flow').count(), 2);
      // The factory's manager and teammates, by name.
      assert.equal(await page.locator('.cf-roster-top [data-cf-mate="bot:pm"]').count(), 1);
      assert.equal(await page.locator('.cf-roster-list .cf-mate').count(), 8);
      await shot(page, 'floor-factory');
      // A deck's card is that teammate's tasks in that stage.
      hub.reads.length = 0;
      await page.locator('.cf-stage[data-cf-stage-id="spec"] .cf-deck[data-cf-actor="bot:f1"]').click();
      const card = page.locator('#cf-card');
      await card.locator('.cf-row').first().waitFor();
      const read = hub.reads.find(u => u.pathname === '/api/v2/tasks')!;
      assert.equal(read.searchParams.get('owners'), 'bot:f1');
      assert.equal(read.searchParams.get('stages'), 'spec');
      await shot(page, 'floor-factory-card');
      await page.keyboard.press('Escape');
      assert.deepEqual(errors, []);
      await close();
    }
    // Twelve decks and the rest summed in one, in a stage of fourteen holders.
    {
      const hub = fresh();
      const {page, errors, close} = await open(1440, 900, hub, '#/overview/floor/f-eng');
      await page.locator('.cf-stage').first().waitFor();
      assert.equal(await page.locator('.cf-stage[data-cf-stage-id="triage"] .cf-deck').count(), 13);
      assert.equal(await page.locator('.cf-stage[data-cf-stage-id="triage"] .cf-deck[data-cf-actor="+"]').count(), 1);
      assert.deepEqual(errors, []);
      await close();
    }
    // A factory the reader may not see is not drawn: its address goes back to the company.
    {
      const hub = fresh();
      const {page, errors, close} = await open(1440, 900, hub, '#/overview/floor/f-hidden');
      await page.waitForFunction(() => location.hash === '#/home');
      await page.locator('.cf-hall').first().waitFor();
      assert.equal(await page.locator('.cf-hall[data-cf-factory="f-hidden"]').count(), 0);
      assert.deepEqual(errors, []);
      await close();
    }
    // Company was tabs: an old link to the Dashboard, Floor, Campus, Graph or Factories, query and all, lands on Company.
    {
      const hub = fresh();
      const {page, errors, close} = await open(1440, 900, hub);
      await page.locator('.cf-hall').first().waitFor();
      for (const old of ['#/overview', '#/overview/floor', '#/overview/campus', '#/overview/graph', '#/overview/graph?view=goals', '#/overview/factories']) {
        await page.evaluate((h: string) => { location.hash = h; }, old);
        await page.waitForFunction(() => location.hash === '#/home');
        await page.locator('#dash-grid .cf-host .cf-hall').first().waitFor();
        assert.equal(await page.locator('#main header.ph h1').innerText(), 'Company', old);
      }
      assert.deepEqual(errors, []);
      await close();
    }
    // No factories, then a failed read: each says so, and Retry reads again.
    {
      const empty: Hub = {floor: {factories: []}, fail: false, reads: []};
      const first = await open(1440, 900, empty);
      await first.page.locator('.cf-empty').waitFor();
      assert.equal(await first.page.locator('.cf-hall').count(), 0);
      assert.deepEqual(first.errors, []);
      await first.close();
      const hub: Hub = {floor: {factories: []}, fail: true, reads: []};
      const {page, errors, close} = await open(1440, 900, hub);
      await page.locator('[data-cf-retry]').waitFor();
      hub.fail = false; hub.floor = FLOOR;
      await page.locator('[data-cf-retry]').click();
      await page.locator('.cf-hall').first().waitFor();
      assert.deepEqual(errors, []);
      await close();
    }

    // ---- a phone: halls stacked, 44px targets, the card a sheet
    {
      const hub = fresh();
      const {page, errors, close} = await open(390, 844, hub, '#/home', true);
      await page.locator('.cf-hall').first().waitFor();
      const g = await page.evaluate(() => {
        const m = document.querySelector('#main')!;
        const halls = [...document.querySelectorAll('.cf-hall')].map(h => h.getBoundingClientRect());
        const small = [...document.querySelectorAll<HTMLElement>('.cf-stack, .cf-flow, .cf-rate')].filter(el => {
          const r = el.getBoundingClientRect(); return r.width < 44 || r.height < 44;
        }).length;
        return {scroll: m.scrollWidth - m.clientWidth, lefts: halls.map(r => Math.round(r.left)), tops: halls.map(r => r.top), heights: halls.map(r => r.height), small};
      });
      assert.ok(g.scroll <= 0, 'no sideways scroll at 390px');
      assert.equal(new Set(g.lefts).size, 1, 'the halls stack');
      assert.ok(g.tops[1]! >= g.tops[0]! + g.heights[0]!, 'one under the other');
      assert.equal(g.small, 0, 'every stack, mark and rate is a 44px target');
      await shot(page, 'floor-phone');
      await page.locator('[data-cf-line="features"] .cf-stack[data-cf-stage="spec"]').tap();
      const card = page.locator('#cf-card');
      await card.locator('.cf-row').first().waitFor();
      assert.equal(await card.evaluate(el => el.classList.contains('cf-sheet')), true);
      await page.waitForFunction(() => getComputedStyle(document.querySelector('#cf-card')!).transform === 'none');
      const box = (await card.boundingBox())!;
      assert.ok(Math.abs(box.y + box.height - 844) < 2 && box.width >= 389, 'a sheet along the bottom: ' + JSON.stringify(box));
      await shot(page, 'floor-phone-sheet');
      await page.locator('.cf-scrim').tap({position: {x: 20, y: 20}});
      assert.equal(await card.isHidden(), true);
      // One factory on a phone: a stage a row, every deck a 44px target, nothing past the right edge.
      await page.evaluate((h: string) => { location.hash = h; }, '#/overview/floor/f-product');
      await page.locator('.cf-stage').first().waitFor();
      const one = await page.evaluate(() => {
        const m = document.querySelector('#main')!;
        const stages = [...document.querySelectorAll('.cf-stage')].map(el => el.getBoundingClientRect().top);
        const small = [...document.querySelectorAll<HTMLElement>('.cf-deck')].filter(el => el.getBoundingClientRect().height < 44).length;
        return {scroll: m.scrollWidth - m.clientWidth, down: stages.every((t, i) => i === 0 || t > stages[i - 1]!), small};
      });
      assert.ok(one.scroll <= 0 && one.down && one.small === 0, JSON.stringify(one));
      await shot(page, 'floor-phone-factory');
      assert.deepEqual(errors, []);
      await close();
    }
    console.log('company-floor: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
