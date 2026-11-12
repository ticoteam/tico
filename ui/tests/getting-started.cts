// Offline regression for the pieces around the wizard: there is no checklist and no card above any page; an
// empty Market page is where the owner is asked four questions (kept as answered, so they can skip and
// resume; the last one asks for research, and a notice shows until the market has content), and a quiet empty
// state for everyone else; one line under the org list
// points at BotOps while there are no bots of your own; the tour is replayable from Help, traps focus, closes
// on Escape and works in the phone drawer; the owner of a team from the company file gets the welcome instead. Fixtures only - no server.
// TICO_SCREENSHOT_DIR=<dir> saves the review screenshots (the market's empty states and the sidebar line in both
// themes, and a phone).
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
import type {Page} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RoutesSupport from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RoutesSupport.Api;
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
interface PageWin { gsStartTour: () => void }
type Answers = {sell: string; buyers: string; competitors: string; watch: string};
interface Body { action?: string; answers?: Answers; step?: number; tour?: boolean; checklist?: boolean; skip?: string }
interface Setup { can_answer: boolean; answers: Answers; step: number; skipped: boolean; task: {id: string; status: string; created_at?: string} | null }
interface MarketEntity { id: string; name: string; type: string; tier: string; aliases: string[]; summary: string; status: string }
// Page-defined global of the getting-started script, read inside page callbacks.
declare const GS: {tour_seen?: boolean} | undefined;
const shots = process.env.TICO_SCREENSHOT_DIR;
// Before a screenshot: the new viewport is laid out and no finite transition is still running.
const settled = (page: Page, width: number) => page.waitForFunction(w => innerWidth === w && document.getAnimations()
  .every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity), width, {timeout: t(5000)});
const shot = (page: Page, name: string) => shots ? page.screenshot({path: path.join(shots, `onboarding-v2-${name}.png`)}) : null;
// One state of the Market page, light and dark, on a desktop and a phone.
const shotAll = async (page: Page, name: string) => {
  if (!shots) return;
  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({colorScheme});
    for (const [size, viewport] of [['desktop', {width: 1440, height: 900}], ['phone', {width: 390, height: 844}]] as [string, {width: number; height: number}][]) {
      await page.setViewportSize(viewport);
      await settled(page, viewport.width);
      await page.screenshot({path: path.join(shots, `market-${name}-${colorScheme}-${size}.png`)});
    }
  }
  await page.emulateMedia({colorScheme: null});
  await page.setViewportSize({width: 1200, height: 800});
};

const item = (id: string, label: string, done: boolean, extra: Record<string, unknown> = {}) => ({id, label, done, optional: false, skipped: false, why: '', href: '', tab: '', action: '', ...extra});
// What the server computed for a company with a computer online but no model signed in yet.
const checklist = () => [
  item('signed_in', 'Signed in', true),
  item('computer', 'A computer is online', true),
  item('model', 'A model is signed in on it', false, {why: 'Sign in to a model on the computer (run `codex login`).', href: '#/settings', tab: 'devices'}),
  item('github', 'GitHub is connected', false, {optional: true, why: 'Lets bots keep their work in your GitHub.', href: '#/settings', tab: 'cloud'}),
  item('botops', 'BotOps is active', true),
  item('first_bot', 'Create your first bot', false, {why: 'Say what it should do and BotOps builds it.', action: 'create-bot'}),
  item('next_bot', 'Set up Support Triage', false, {why: 'Press Start onboarding on its page and answer its questions; it drafts a first result for you to approve.', href: '#/bot/support'}),
  item('first_output', 'First approved output', false, {why: 'Set up a starter bot and approve the first thing it drafts. That is the point of the team.', href: '#/bot/support'}),
  item('first_update', 'Your first update arrived', false, {why: 'Each active bot posts a short update every day.', href: '#/updates'}),
];
const bots = [['assistant', 'Ace'], ['botops', 'BotOps']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', can_chat: true, team: '', owner_id: 'ana'}));
// A team that came in from the company file: its lead listed after a report, so the welcome's order is its own. The
// built-ins' group is ordered first and led by no bot; the welcome still shows it last, under the server's name, from
// the role most of it reports to.
// A company file's bot has an id apart from its slug, and lines name it either way: Triage's line names the lead's id,
// the group and the seat name its slug.
const importedBots = [['support-triage', 'Support Triage', '01a1-lead', 'support'], ['support-lead', 'Support Lead', 'human:ana', 'support'],
  ['finance-owner', 'Finance Owner', 'human:ana', 'built-in'], ['librarian', 'Librarian', 'general-manager', 'built-in'],
  ['general-manager', 'General Manager', 'human:ana', 'built-in']].map(([name, display_name, reports_to, team]) =>
  ({id: name === 'support-lead' ? '01a1-lead' : name, name, slug: name, display_name, status: 'active', can_chat: true, team, reports_to, owner_id: 'ana'}));
const importedChart = {org_groups: [{id: 'built-in', name: 'General', order: 0}, {id: 'support', name: 'Support', lead: 'bot:support-lead', order: 1}],
  seats: [{id: 's1', kind: 'seat', title: 'Support Writer', team: 'support', reports_to: 'bot:support-lead', stage: 'recruiting'}]};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const calls: {p: string; body: Body}[] = [];
    let items = checklist(), role = 'owner', canBuild = true, imported = false;
    let dismissed = {tour: true, checklist: false, skipped: [] as string[]};
    let librarianOff = false, polls: number[] = [], held: {gate: Promise<unknown>; body?: Body} | null = null;
    let setup: Setup = {can_answer: true, answers: {sell: '', buyers: '', competitors: '', watch: ''}, step: 0, skipped: false, task: null};
    let market: {entities: MarketEntity[]; docs: {id: string; title: string; fetched: string; seeded?: boolean}[]} = {entities: [], docs: [{id: 'market/overview', title: 'Overview', seeded: true, fetched: '2026-01-01T00:00:00Z'}]};
    const view = () => {
      const rows = items.map(row => ({...row, skipped: row.optional && !row.done && dismissed.skipped.includes(row.id)}));
      const done = rows.filter(row => row.done || row.skipped).length;
      return {items: rows, done, total: rows.length, complete: done === rows.length, dismissed: dismissed.checklist,
              tour_seen: dismissed.tour, can_build: canBuild, owner_id: role === 'owner'};
    };
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui) { const file = uiFile(ui[1]!); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p.startsWith('/vendor/fonts/') && p.endsWith('.woff2')) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(path.join(__dirname, '..', p))});
      if (p === '/api/employees') return json(imported ? [...bots, ...importedBots] : bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role, cloud: true});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], ...(imported ? importedChart : {})});
      if (p === '/api/v2/setup' && imported) return json({completed: '2026-11-11T08:00:00Z', company_import: {seats: 3}, names: {company_name: 'Acme'},
        machine: {runners: [], enrolled: false, cloud: true}, bots: [], default_chart: []});
      if (p === '/api/v2/updates/unread') return json({unread: 0});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/docs') return json({docs: [], next_cursor: null});
      if (p === '/api/v2/linked-docs') return json({linked: []});
      if (p === '/api/v2/setup/getting-started' && req.method() === 'GET') return json(view());
      if (p === '/api/v2/setup/getting-started/state') {
        const body: Body = req.postDataJSON(); calls.push({p, body});
        if (body.tour !== undefined) dismissed.tour = body.tour;
        if (body.checklist !== undefined) dismissed.checklist = body.checklist;
        if (body.skip) dismissed.skipped = [...new Set([...dismissed.skipped, body.skip])];
        return json({tour: dismissed.tour, checklist: dismissed.checklist, skipped: dismissed.skipped});
      }
      if (p === '/api/v2/librarian/setup') return json({available: true, docs: {show: false, empty: true, skipped: false, task: null}, market: setup});
      if (p === '/api/v2/librarian/setup/market') {
        const body: Body = req.postDataJSON(); calls.push({p, body});
        if (held && !held.body) { held.body = body; await held.gate; }   // this write is slow to arrive
        if (body.action === 'finish' && librarianOff) return json({error: {code: 'librarian', detail: 'The Librarian is not running yet.'}}, 409);
        setup = {...setup, answers: {...setup.answers, ...body.answers}, step: body.step!, skipped: body.action === 'skip',
                 task: body.action === 'finish' ? {id: 't-market', status: 'open'} : setup.task};
        return json(setup);
      }
      if (p === '/api/v2/market/entities') { polls.push(Date.now()); return json({entities: market.entities, total: market.entities.length}); }
      if (p === '/api/v2/market/graph') return json({entities: market.entities, edges: []});
      if (p === '/api/company-docs') return json({documents: market.docs});
      if (p.startsWith('/api/company-docs/')) return json({content: '# Overview\n\nNorthwind sells office cleaning. [Source](https://northwind.example/about)'});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const errors: string[] = [];
    const ready = (page: Page) => page.waitForFunction(() => typeof GS !== 'undefined' && !!GS);
    const open = async (hash: string, viewport?: {width: number; height: number}) => {
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(hash + ': ' + e.message));
      if (viewport) await page.setViewportSize(viewport);
      await page.goto('http://tico-ui.test/' + hash);
      await ready(page);
      return page;
    };

    // ---- no checklist and no card slot above any page
    let page = await open('#/updates');
    assert.equal(await page.locator('#nav-getting-started').count(), 0);
    assert.equal(await page.locator('#gs-card, [data-gs-card]').count(), 0);
    await page.close();
    // The old address lands on Tasks.
    page = await open('#/getting-started');
    await page.waitForFunction(() => location.hash === '#/tasks');
    await page.close();

    // ---- Market (its Players view): an empty market is the page's own empty state: it asks four questions, one at a
    // time, kept as answered so the owner can skip and pick up again; the last asks for research, and a notice
    // shows until the market has content
    page = await context.newPage();
    await page.clock.install();
    page.on('pageerror', e => errors.push('market: ' + e.message));
    await page.goto('http://tico-ui.test/#/strategy/market/players');
    await ready(page);
    const box = page.locator('#market-shell [data-market-setup]');
    const asked = () => box.locator('h1').textContent();
    const posts = () => calls.filter(c => c.p === '/api/v2/librarian/setup/market');
    await box.waitFor();
    assert.equal(await asked(), 'What do you sell?');
    assert.equal(await box.locator('.market-who').count(), 0, 'no one is named as asking');
    assert.equal(await box.locator('textarea').count(), 1);                 // one question at a time
    assert.equal(await box.locator('[type=submit]').textContent(), 'Next');
    assert.equal(await box.locator('a[href="#/docs?import=1"]').textContent(), 'Attach files');
    assert.equal(await box.locator('[data-market-back]').count(), 0);
    // It fills the page: no empty graph, index or ask box, and never the seed's text.
    assert.equal(await page.locator('#market-canvas, #market-index, #market-ask').count(), 0);
    assert.doesNotMatch((await page.locator('#market-shell').textContent())!, /None in the seed|Northwind/);
    await shotAll(page, 'empty');
    await box.locator('[type=submit]').click();                          // the first answer is the one it needs
    await box.locator('[data-market-error]:not([hidden])').waitFor();
    assert.equal(posts().length, 0);
    await box.locator('textarea').fill('https://northwind.example');
    await box.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#market-shell h1')?.textContent === 'Who buys it?');
    assert.deepEqual(posts().at(-1)!.body, {answers: {sell: 'https://northwind.example', buyers: '', competitors: '', watch: ''}, step: 1, action: 'save'});
    await box.locator('textarea').fill('Property managers in Ohio');
    // Skip for now keeps the answer and the place; the page then offers to continue.
    await box.locator('[data-market-skip]').click();
    const resume = page.locator('#market-shell [data-market-resume]');
    await resume.waitFor();
    assert.deepEqual(posts().at(-1)!.body, {answers: {sell: 'https://northwind.example', buyers: 'Property managers in Ohio', competitors: '', watch: ''}, step: 1, action: 'skip'});
    assert.match((await resume.textContent())!, /You've answered 2\./);
    await shotAll(page, 'skipped');
    // A reload (or another browser) resumes from the server at the same question, with the answer in it.
    await page.reload();
    await resume.waitFor();
    await resume.locator('[data-market-continue]').click();
    await page.waitForFunction(() => document.querySelector('#market-shell h1')?.textContent === 'Who buys it?');
    assert.equal(await box.locator('textarea').inputValue(), 'Property managers in Ohio');
    await box.locator('[data-market-back]').click();                       // back keeps what was typed
    await page.waitForFunction(() => document.querySelector('#market-shell h1')?.textContent === 'What do you sell?');
    assert.equal(await box.locator('textarea').inputValue(), 'https://northwind.example');
    await box.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#market-shell h1')?.textContent === 'Who buys it?');
    await box.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#market-shell h1')?.textContent === 'Who do you compete with?');
    await box.locator('textarea').fill('CleanCo');
    await box.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#market-shell h1')?.textContent === 'What should Market watch?');
    assert.equal(await box.locator('[type=submit]').textContent(), 'Start research');
    await shotAll(page, 'question');
    // A Librarian that is not running is said plainly, and the question keeps what was typed.
    await box.locator('textarea').fill('r/PropertyManagement');
    librarianOff = true;
    await box.locator('[type=submit]').click();
    await box.locator('[data-market-error]:not([hidden])').waitFor();
    assert.match((await box.locator('[data-market-error]').textContent())!, /Librarian is not running/);
    assert.equal(await box.locator('textarea').inputValue(), 'r/PropertyManagement');
    librarianOff = false;
    await box.locator('[type=submit]').click();
    const notice = page.locator('#market-shell [data-market-researching]');
    await notice.waitFor();
    assert.deepEqual(posts().at(-1)!.body, {answers: {sell: 'https://northwind.example', buyers: 'Property managers in Ohio',
      competitors: 'CleanCo', watch: 'r/PropertyManagement'}, step: 3, action: 'finish'});
    assert.equal(posts().filter(c => c.body.action === 'finish').length, 2);
    assert.match((await notice.textContent())!, /^\s*Researching your market\.\s+This usually takes 5–10 minutes\./);
    assert.equal(await page.locator('[data-market-setup]').count(), 0);
    assert.equal(await notice.locator('a[href="#/task/t-market"]').count(), 1);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.market.researching')!).task), 't-market');
    await shotAll(page, 'researching');
    // The notice is kept in this browser: a reload still shows it.
    await page.reload();
    await notice.waitFor();
    // It polls the market every 30 seconds while it shows: two small GETs, no server change.
    const beforePolls = polls.length;
    const pollsReach = async (n: number) => { for (const end = Date.now() + t(10000); polls.length < n && Date.now() < end;) await new Promise(r => setTimeout(r, 50)); };
    await page.clock.fastForward(31000);
    await pollsReach(beforePolls + 1);
    assert.equal(polls.length, beforePolls + 1);
    // The market gets its first content a minute in: the notice stays out its two minutes.
    market = {entities: [
      {id: 'company/cleanco', name: 'CleanCo', type: 'organization', tier: 'core', aliases: [], summary: '', status: 'active'}],
      docs: [{id: 'market/overview', title: 'Overview', fetched: '2026-09-29T10:00:00Z'}]};
    await page.clock.fastForward(30000);
    await pollsReach(beforePolls + 2);
    assert.equal(polls.length, beforePolls + 2);
    assert.equal(await notice.count(), 1);
    // ...and goes once two minutes have passed and there is content: the normal Market page is drawn.
    await page.clock.fastForward(60000);
    await page.locator('#market-index a', {hasText: 'CleanCo'}).waitFor();
    // The index renders before the separate overview request finishes.
    await page.locator('#market-read', {hasText: 'Northwind sells office cleaning'}).waitFor();
    assert.match((await page.locator('#market-read').textContent())!, /Northwind sells office cleaning/);
    assert.equal(await notice.count(), 0);
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.market.researching')), null);
    market = {entities: [], docs: []};
    await page.close();

    // The notice is only kept for thirty minutes: an old record is dropped and the questions are back.
    setup = {...setup, task: {id: 't-market', status: 'done'}};
    for (const [minutes, shows] of [[10, '[data-market-researching]'], [31, '[data-market-setup]']]) {
      page = await context.newPage();
      await page.addInitScript(([ago]: [number]) => localStorage.setItem('tico.market.researching', JSON.stringify({at: Date.now() - ago * 60000, task: 't-market', base: '0|'})), [minutes] as [number]);
      page.on('pageerror', e => errors.push('market-old: ' + e.message));
      await page.goto('http://tico-ui.test/#/strategy/market/players');
      await page.locator('#market-shell ' + shows).waitFor();
      await page.evaluate(() => localStorage.removeItem('tico.market.researching'));
      await page.close();
    }
    // An open task restores the notice in another browser, but its thirty minutes count from when the task was
    // filed: a reload never starts them again, and the clock running out brings the questions back.
    const filedAgo = (minutes: number) => ({id: 't-market', status: 'open', created_at: new Date(Date.now() - minutes * 60000).toISOString()});
    setup = {...setup, task: filedAgo(31)};
    page = await context.newPage();
    page.on('pageerror', e => errors.push('market-open: ' + e.message));
    await page.addInitScript(() => { if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); localStorage.setItem('tico.market.researching', JSON.stringify({at: Date.now() - 31 * 60000, task: 't-market', base: null})); } });
    await page.goto('http://tico-ui.test/#/strategy/market/players');
    await page.locator('#market-shell [data-market-setup]').waitFor();
    await page.reload();
    await page.locator('#market-shell [data-market-setup]').waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.market.researching')), null);
    await page.close();
    setup = {...setup, task: filedAgo(29)};
    page = await context.newPage();
    page.on('pageerror', e => errors.push('market-open: ' + e.message));
    await page.clock.install();
    await page.goto('http://tico-ui.test/#/strategy/market/players');
    await page.locator('#market-shell [data-market-researching]').waitFor();
    await page.clock.fastForward(2 * 60000);
    await page.locator('#market-shell [data-market-setup]').waitFor();
    await page.clock.fastForward(31000);
    assert.equal(await page.locator('#market-shell [data-market-researching]').count(), 0);
    await page.close();

    // Back saves without waiting, but the writes go out in order: a slow Back save never lands after the Next
    // that followed it, so a reload resumes at the newer answer and place.
    setup = {...setup, answers: {sell: 'https://northwind.example', buyers: '', competitors: '', watch: ''}, step: 1, skipped: false, task: null};
    page = await open('#/strategy/market/players');
    const shows = (text: string) => page.waitForFunction(text => document.querySelector('#market-shell h1')?.textContent === text, text);
    const form = page.locator('#market-shell [data-market-setup]');
    await shows('Who buys it?');
    let release!: (value?: unknown) => void;
    held = {gate: new Promise(resolve => { release = resolve; })};
    await form.locator('[data-market-back]').click();
    await shows('What do you sell?');
    for (const end = Date.now() + t(5000); !held.body && Date.now() < end;) await new Promise(r => setTimeout(r, 20));
    assert.equal(held.body!.step, 0);
    await form.locator('textarea').fill('https://northwind.example/cleaning');
    await form.locator('[type=submit]').click();
    await new Promise(r => setTimeout(r, 300));
    release();
    await shows('Who buys it?');
    held = null;
    assert.deepEqual({sell: setup.answers.sell, step: setup.step}, {sell: 'https://northwind.example/cleaning', step: 1});
    await page.reload();
    await shows('Who buys it?');
    await page.close();

    // Someone who is not the owner cannot ask: the empty market just says so.
    role = 'human';
    page = await open('#/strategy/market/players');
    await page.locator('#market-shell .market-none').waitFor();
    assert.equal(await page.locator('#market-shell').textContent(), 'Nothing here yet.');
    assert.equal(await page.locator('[data-market-setup], [data-market-resume], #market-canvas').count(), 0);
    await shotAll(page, 'none');
    await page.close();
    role = 'owner';

    // ---- Meetings has no intro card: the page itself offers Add notes and the sources
    page = await open('#/meetings');
    assert.equal(await page.locator('[data-gs-card]').count(), 0);
    await page.close();

    // ---- Bots: one quiet line under the org list while there are no bots of your own, for whoever may add them
    page = await open('#/updates');
    const hint = page.locator('#gs-org-hint');
    await hint.waitFor();
    assert.equal(await hint.textContent(), 'Ask to add or edit your bots');
    assert.equal(await hint.locator('a').textContent(), 'Ask');
    assert.equal(await hint.locator('button').count(), 0);                 // no buttons, no X
    assert.equal(await page.locator('#gs-org-card, [data-gs-build], [data-gs-connect], [data-gs-dismiss]').count(), 0);
    if (shots) for (const colorScheme of ['light', 'dark'] as const) {
      await page.setViewportSize({width: 1440, height: 900});
      await page.emulateMedia({colorScheme});
      await settled(page, 1440);
      await page.screenshot({path: path.join(shots, `sidebar-botops-line-${colorScheme}.png`)});
    }
    await hint.locator('a').click();
    await page.locator('#ask-panel').waitFor();          // it opens Ask, staying on the page
    assert.equal(await page.evaluate(() => location.hash), '#/updates');
    await page.close();
    // ...and it is gone once there is a bot of your own, and never shown to someone who cannot add bots.
    items = items.map(row => row.id === 'first_bot' ? {...row, done: true, why: ''} : row);
    page = await open('#/updates');
    assert.equal(await page.locator('#gs-org-hint').isHidden(), true);
    await page.close();
    items = checklist();
    canBuild = false;
    page = await open('#/updates');
    assert.equal(await page.locator('#gs-org-hint').isHidden(), true);
    await page.close();
    canBuild = true;

    // ---- the tour: replay from the account menu, focus stays inside, Escape closes and is remembered
    page = await open('#/updates');
    await page.locator('#account').click();
    await page.locator('#account-tour').click();
    const tour = page.locator('.gs-tour[role=dialog][aria-modal=true]');
    await tour.waitFor();
    assert.equal(await tour.locator('.gs-tour-count').textContent(), '1 of 6');
    assert.equal(await tour.locator('#gs-tour-title').textContent(), 'Updates');
    await shot(page, 'tour');
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => !!(document.activeElement as Element).closest('.gs-tour')), true);
    }
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => !!(document.activeElement as Element).closest('.gs-tour')), true);
    const titles = [];
    for (let i = 0; i < 5; i++) {
      await tour.locator('[data-tour-next]').click();
      titles.push(await tour.locator('#gs-tour-title').textContent());
    }
    assert.deepEqual(titles, ['Tasks', 'Team', 'Docs', 'Strategy', 'Meetings']);
    assert.equal(await tour.locator('[data-tour-next]').textContent(), 'Done');
    // Removing the dialog is synchronous; its remembered-state write finishes separately.
    const tourSaved = page.waitForResponse(r => r.url().endsWith('/api/v2/setup/getting-started/state')
      && r.request().method() === 'POST' && r.request().postDataJSON()?.tour === true);
    await page.keyboard.press('Escape');
    assert.equal((await tourSaved).ok(), true);
    await tour.waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => document.body.classList.contains('drawer')), false);
    assert.deepEqual(calls.filter(c => c.body.tour).length >= 1, true);
    await page.close();

    // From the keyboard too: the menu closes as the tour starts, and focus comes back to the account button.
    page = await open('#/updates');
    const menuTour = page.locator('.gs-tour[role=dialog]');
    await page.locator('#account').click();
    assert.deepEqual(await page.locator('#account-menu :is(a, button):not([hidden])').evaluateAll(els => els.map(e => e.textContent.trim()).slice(-1)), ['tourTake the tour']);
    await page.locator('#account').press('ArrowUp');
    assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement).id), 'account-tour', 'the arrow keys reach it');
    await page.keyboard.press('Enter');
    await menuTour.waitFor();
    assert.equal(await page.locator('#account-menu').isHidden(), true);
    assert.equal(await page.locator('.gs-tour-count').textContent(), '1 of 6');
    await page.keyboard.press('Escape');
    await menuTour.waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement).id), 'account');
    await page.locator('#account').click();
    await page.locator('#account-tour').click();
    await menuTour.waitFor();
    assert.equal(await page.locator('#account-menu').isHidden(), true, 'a click closes the menu as well');
    await page.locator('[data-tour-skip]').click();
    await menuTour.waitFor({state: 'detached'});
    await page.close();

    // The owner of a team that came in from the company file gets one welcome screen in place of the tour: the team as
    // it is, what the bots run on, Start. It stays on the page it opened over, and closing it is the tour seen.
    dismissed.tour = false; imported = true;
    page = await open('#/updates');
    const welcome = page.locator('#onb[open]');
    await welcome.waitFor();
    assert.equal(await page.locator('.gs-tour').count(), 0);
    assert.equal(await welcome.locator('#onb-count').textContent(), '');
    assert.deepEqual(await welcome.locator('[data-onb-team-row] strong').allTextContents(), ['Support', 'General']);
    assert.deepEqual(await welcome.locator('.onb-team-roles > span').allTextContents(),
      ['Support Lead', 'Support Triage', 'Support Writer', 'General Manager', 'Librarian', 'Finance Owner']);
    assert.equal(await welcome.locator('.onb-team-roles .onb-team-open').textContent(), 'Support Writer');
    assert.equal(await welcome.locator('input, [data-onb-team]').count(), 0, 'nothing to switch: the team is set up');
    assert.equal(await welcome.locator('#onb-machine-status').getAttribute('data-onb-machine'), 'cloud');
    assert.equal(await welcome.locator('#onb-subscription a').count(), 0, 'the sign-in sheet opens here, not Settings');
    assert.equal(await welcome.locator('#onb-subscription [data-onb-subscription]').isVisible(), true);
    const welcomeSaved = page.waitForResponse(r => r.url().endsWith('/api/v2/setup/getting-started/state')
      && r.request().method() === 'POST' && r.request().postDataJSON()?.tour === true);
    await welcome.locator('#onb-next').click();
    assert.equal((await welcomeSaved).ok(), true);
    await page.locator('#onb').waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => location.hash), '#/updates');
    assert.equal(await page.locator('.gs-tour').count(), 0);
    imported = false;
    await page.close();

    // Skip is the same as closing, and the tour works on a phone, through the drawer.
    page = await open('#/updates', {width: 390, height: 844});
    await page.evaluate(() => (window as unknown as PageWin).gsStartTour());
    const phoneTour = page.locator('.gs-tour');
    await phoneTour.waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('drawer')), true);
    const fits = async () => page.evaluate(() => {
      const card = (document.querySelector('.gs-tour-card') as HTMLElement).getBoundingClientRect(), hole = (document.querySelector('.gs-tour-hole') as HTMLElement).getBoundingClientRect();
      return card.left >= 0 && card.right <= innerWidth && card.top >= 0 && card.bottom <= innerHeight && hole.width > 0
        && document.documentElement.scrollWidth <= innerWidth;
    });
    for (let i = 0; i < 6; i++) {
      assert.equal(await fits(), true);
      if (i < 5) await phoneTour.locator('[data-tour-next]').click();
    }
    await shot(page, 'tour-phone');
    await page.locator('[data-tour-skip]').click();
    await phoneTour.waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => document.body.classList.contains('drawer')), false);
    await page.close();

    assert.deepEqual(errors, []);
    console.log('PASS: no checklist or card slot; the empty Market page files its ask and shows the notice until there is content; the BotOps line shows until there is a bot of your own; the tour traps focus.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
