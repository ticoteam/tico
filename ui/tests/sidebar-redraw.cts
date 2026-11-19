// The sidebar keeps its rows (ui/app/sidebar.ts, ui/app/refresh.ts). A page change moves the selected mark and writes
// nothing else in the Team list; a live burst that changes nothing the list draws writes nothing; one bot's status or
// count changes that row in place (the count's own text), and no other row is replaced. So the rows stay the same DOM
// nodes: a fold, the rail's scroll, the focused row, a group name being typed and an open plus menu all survive a page
// change and a burst. Draws asked for in one frame are one pass, and the list after any of it reads as one drawn from
// nothing would. Fixtures only. Prints what each case cost: times the list's markup was built and parsed, innerHTML
// writes in the rail, and elements added and removed, attributes and text changed (counted by a MutationObserver).
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

// What one case cost: times the list's markup was built (`builds`) and parsed (`parsed`), innerHTML writes in the rail,
// and what changed in the Team list (#tree) and in the whole rail (#side), the list's attribute changes by name.
interface Wrote { builds: number; parsed: number; inner: number; treeAdded: number; treeRemoved: number; treeAttrs: string[]; treeText: number; sideAdded: number; sideRemoved: number; sideAttrs: number; own: string[]; others: string[] }
declare const S: {route: string; v2: {on: boolean; status: Record<string, unknown>}};
declare const LIVE_SOON: Map<string, unknown>;
declare const THREADS: {on: unknown; dms: Record<string, number>};
declare var INBOX_NAV: unknown;
declare var TREE_DRAWN: {html: string} | null;
declare var sidebarTree: () => unknown;
declare function liveEmit(topic: string, data: unknown): void;
declare function refresh(force?: boolean): Promise<void>;
declare function renderTree(): void;
declare function renderTreeSoon(): void;
declare function treeKids(old: Element, fresh: ParentNode): void;
declare function v2Refresh(): Promise<void>;
declare const window: Window & typeof globalThis & {
  sbrStart(): void; sbrStop(): Wrote; sbrKeep(): void; sbrSame(): string[]; sbrFaithful(): boolean; sbrPasses: number;
};

const t = (ms: number) => ms * (Number(process.env.TICO_TEST_TIMEOUT_SCALE) || 1);
const FULL = {see: true, read: true, write: true};
const bot = (id: string, display_name: string, team: string, org_parent: string, reports_to: string | null = null, helper = false) =>
  ({id, name: id, slug: id, display_name, team, org_parent, reports_to, helper, host: 'cloud', status: 'active', state: 'active', can_chat: true,
    my_access: FULL, users: [{id: 'chris', name: 'Chris'}], owner_id: 'chris', revision: 1});
const ENGINEERS = [['builder', 'Builder'], ['tester', 'Tester'], ['reviewer', 'Reviewer'], ['releaser', 'Releaser'], ['designer', 'Designer'], ['planner', 'Planner']] as const;
const BOTS = [
  bot('assistant', 'Assistant', '', 'p:chris', 'human:chris'),
  bot('general-manager', 'General Manager', 'built-in', 'p:chris', 'human:chris', true),
  bot('botops', 'BotOps', 'built-in', 'b:general-manager', 'general-manager'),
  bot('hop', 'Head of Product', 'leadership', 'p:chris', 'human:chris'),
  ...ENGINEERS.map(([id, name]) => bot(id, name, 'engineering', 'b:hop', 'hop')),
  bot('writer', 'Writer', 'growth', 'b:hop', 'hop'),
  bot('seo', 'Search Owner', 'growth', 'b:hop', 'hop'),
];
const GROUPS = [{id: 'built-in', name: 'General', parent: '', order: 0}, {id: 'leadership', name: 'Leadership', parent: '', order: 1},
  {id: 'engineering', name: 'Engineering', parent: '', order: 2}, {id: 'growth', name: 'Growth', parent: '', order: 3}];
const PEOPLE = [{id: 'chris', name: 'Chris', email: 'chris@example.test', title: 'Owner', team: 'leadership', org_parent: '', reports_to: ''}];
// One thing the Builder asks of Chris (GET /v2/needs-you, backend/views.py).
const need = (n: number) => ({kind: 'ask', id: 'r' + n, task_id: 't' + n, requirement_id: 'r' + n, line: 'OK the plan ' + n, since: new Date().toISOString(),
  task: {id: 't' + n, title: 'Plan ' + n, status: 'open', owner_id: 'bot:builder', requester_id: 'bot:builder'},
  ask: {requirement_id: 'r' + n, kind: 'ok', line: 'OK the plan ' + n, met: false, who: 'human:chris', asked_by: 'bot:builder', shows: [], answer: null, may: ['ok', 'no']}});

// Counts what is written under #side between sbrStart() and sbrStop(), and remembers rows to compare by identity.
function counters(): void {
  const inner = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML')!;
  let writes = 0, parsed = 0, built = 0, records: MutationRecord[] = [], observer: MutationObserver | null = null;
  // A write into the rail, or the list's markup parsed apart from the page before it is put in (it alone has data-org).
  Object.defineProperty(Element.prototype, 'innerHTML', {configurable: true, enumerable: true, get: inner.get!,
    set(this: Element, value: string) {
      if (this.closest('#side')) writes++; else if (this.localName === 'template' && String(value).includes(' data-org="')) parsed++;
      inner.set!.call(this, value);
    }});
  window.sbrPasses = 0;
  const elements = (nodes: NodeList) => [...nodes].reduce((n, node) => n + (node instanceof Element ? 1 + node.querySelectorAll('*').length : 0), 0);
  window.sbrStart = () => {
    writes = 0; parsed = 0; built = window.sbrPasses; records = [];
    observer = new MutationObserver(list => { records.push(...list); });
    observer.observe(document.querySelector('#side')!, {subtree: true, childList: true, attributes: true, attributeOldValue: true, characterData: true});
  };
  window.sbrStop = () => {
    records.push(...observer!.takeRecords()); observer!.disconnect();
    const tree = document.querySelector('#tree')!, out: Wrote = {builds: window.sbrPasses - built, parsed, inner: writes, treeAdded: 0, treeRemoved: 0, treeAttrs: [], treeText: 0, sideAdded: 0, sideRemoved: 0, sideAttrs: 0, own: [], others: []};
    const name = (el: Element) => el.id ? '#' + el.id : (el as HTMLElement).dataset.org || (el as HTMLElement).dataset.toggle || el.className;
    for (const r of records) {
      const el = r.target instanceof Element ? r.target : r.target.parentElement!, inTree = tree.contains(r.target);
      // The parts sidebar.ts draws outside the list: a pass that changes nothing writes none of them. (Their menus say
      // they are shut again on every page change: ui/app/team-add.ts, not the list's draw.)
      if (el.closest('#nav-inboxes-section, #org-more, #org-add-group') && r.attributeName !== 'aria-expanded') out.own.push(`${name(el)}:${r.type}:${r.attributeName || ''}`);
      if (r.type === 'childList') {
        out.sideAdded += elements(r.addedNodes); out.sideRemoved += elements(r.removedNodes);
        if (inTree) { out.treeAdded += elements(r.addedNodes); out.treeRemoved += elements(r.removedNodes); }
      } else if (r.type === 'attributes') {
        out.sideAttrs++;
        if (inTree) out.treeAttrs.push(`${name(el)}:${r.attributeName}`);
        else out.others.push(`${name(el)}:${r.attributeName}${el.getAttribute(r.attributeName!) === r.oldValue ? '(same)' : ''}`);
      } else if (inTree) out.treeText++;
    }
    out.treeAttrs.sort();
    return out;
  };
  // Every row and list of the Team list, to find again by identity.
  let kept: Element[] = [];
  const label = (el: Element) => el.tagName.toLowerCase() + ':' + ((el as HTMLElement).dataset.org || (el as HTMLElement).dataset.toggle
    || el.querySelector<HTMLElement>(':scope > .noderow [data-org], :scope > .noderow[data-org]')?.dataset.org || el.className);
  window.sbrKeep = () => { kept = [...document.querySelectorAll('#tree li, #tree ul, #tree .noderow, #tree a.node, #tree a.dept-label, #tree .av, #tree .chev')]; };
  // The remembered ones that are no longer in the page.
  window.sbrSame = () => kept.filter(el => !el.isConnected).map(label);
  // The list as it stands reads as its markup drawn from nothing would: the same nodes, attributes and text, whatever
  // order the attributes and classes were written in, and but for the mark of the page you are on.
  const read = (n: Node): string => n.nodeType === 3 ? n.nodeValue || '' : !(n instanceof Element) ? '' : `<${n.localName} ${[...n.attributes]
    .map(a => a.name === 'class' ? [...n.classList].filter(c => c !== 'cur').sort().join(' ') && 'class=' + [...n.classList].filter(c => c !== 'cur').sort().join(' ') : a.name === 'aria-current' ? '' : a.name + '=' + a.value)
    .filter(Boolean).sort().join(' ')}>${[...n.childNodes].map(read).join('')}</${n.localName}>`;
  window.sbrFaithful = () => {
    const fresh = document.createElement('ul');
    inner.set!.call(fresh, TREE_DRAWN!.html);
    return [...document.querySelector('#tree')!.childNodes].map(read).join('') === [...fresh.childNodes].map(read).join('');
  };
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  try {
    // A short window, so the rail scrolls.
    const page = await browser.newPage({viewport: {width: 1280, height: 420}, serviceWorkers: 'block', reducedMotion: 'reduce'});
    const errors: string[] = [];
    let needs = 0;
    const states: Record<string, string> = {};        // what /v2/status says, kept in step with the status lines sent
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(counters);
    await page.route('**/*', route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body: unknown) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'chris', owner_id: 'chris', role: 'owner', name: 'Chris', email: 'chris@example.test', registered: true, config: {version: 'dev', company_name: 'Acme'}});
      if (p === '/api/v2/config') return json({version: 'dev', company_name: 'Acme'});
      if (p === '/api/employees') return json(BOTS);
      if (p === '/api/humans') return json({people: PEOPLE, org_groups: GROUPS, teams: {}});
      if (p === '/api/status') return json({keeper_alive: true, active: [], queued: [], recent_runs: [], health_issues: []});
      if (p === '/api/v2/status') return json({bots: BOTS.map(b => ({bot: b.id, state: states[b.id] || 'idle', open_tasks: 0, needs_human: 0}))});
      if (p === '/api/v2/needs-you') return json({actor: 'human:chris', items: Array.from({length: needs}, (_, i) => need(i + 1)), count: needs, version: 'v' + needs, next: null});
      if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [], catch_up: 0, catch_up_ids: [], next_cursor: null, dms: {}});
      if (p === '/api/v2/threads') return json({attention: [], threads: [], dms: [], bot_dms: {}, next_cursor: null});
      if (p === '/api/v2/events') return;                                   // held open: events come by liveEmit
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/updates');
    await page.waitForFunction(() => document.querySelector('#tree a.node[data-org="b:builder"]') && S.v2.on && INBOX_NAV && THREADS.on !== null, null, {timeout: t(15000)});
    // Each build of the list's markup reads the Team tree once.
    await page.evaluate(() => { const tree = sidebarTree; sidebarTree = () => { window.sbrPasses++; return tree(); }; });

    const frames = () => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    const row = (id: string) => page.locator(`#tree a.node[data-org="b:${id}"]`);
    // What `act` wrote in the rail, from its start until `done` holds and the frame after it is drawn.
    const wrote = async (act: () => Promise<unknown>, done: () => Promise<unknown>): Promise<Wrote> => {
      await frames();
      await page.evaluate(() => window.sbrStart());
      await act(); await done(); await frames();
      return page.evaluate(() => window.sbrStop());
    };
    const burst = (...events: {bot: string; status: {state: string}}[]) => {
      for (const e of events) states[e.bot] = e.status.state;
      return page.evaluate(list => { for (const e of list) liveEmit('bots', e); }, events);
    };
    // A burst is done when its 200 ms are up and the row it was for (if any) shows it.
    const shows = (id: string, state: string) => async () => { await burstDone(); await row(id).and(page.locator('.' + state)).waitFor(); };
    const burstDone = () => page.waitForFunction(() => !LIVE_SOON.has('bots'), null, {timeout: t(5000)});
    const status = (id: string, state: string, focus = '') => ({bot: id, status: {bot: id, state, focus, open_tasks: 0, needs_human: 0, changed_at: new Date().toISOString()}});
    const goto = (hash: string) => page.evaluate(h => { location.hash = h; }, hash);
    const at = (hash: string, id: string) => page.waitForFunction(([h, k]) => S.route === h && document.querySelector(`#tree a.node[data-org="b:${k}"]`)?.classList.contains('cur'), [hash, id] as const, {timeout: t(15000)});
    const report: Record<string, Wrote> = {}, failed: string[] = [];
    const check = (ok: unknown, what: string) => { if (!ok) failed.push(what); };
    const gone = async (what: string) => {
      const lost = await page.evaluate(() => window.sbrSame());
      check(!lost.length, `${what}: rows replaced: ${lost.join(', ')}`);
      check(await page.evaluate(() => window.sbrFaithful()), `${what}: the list reads as one drawn from nothing`);
    };

    // General starts folded and Growth open: the reader opens one and folds the other, and both stay as put.
    const folded = (key: string) => page.evaluate(k => !!document.querySelector(`#tree [data-toggle="${k}"]`)?.closest('li')?.querySelector(':scope > ul')?.hasAttribute('hidden'), key);
    const fold = async (key: string, want: boolean) => {
      await page.locator(`#tree [data-toggle="${key}"]`).click();
      await page.waitForFunction(([k, w]) => !!document.querySelector(`#tree [data-toggle="${k}"]`)?.closest('li')?.querySelector(':scope > ul')?.hasAttribute('hidden') === w, [key, want] as const);
    };
    await fold('g:built-in', false); await fold('g:growth', true);
    await page.evaluate(() => { window.sbrKeep(); });
    const scroller = page.locator('#side .side-scroll');
    assert.ok(await scroller.evaluate(el => el.scrollHeight > el.clientHeight + 40), 'the rail scrolls in this window');
    await scroller.evaluate(el => { el.scrollTop = 40; });

    // 1. A page change: the mark moves, nothing else in the list is written.
    report['page change (to a bot)'] = await wrote(() => goto('#/bot/builder'), () => at('#/bot/builder', 'builder'));
    report['page change (bot to bot)'] = await wrote(() => goto('#/bot/tester'), () => at('#/bot/tester', 'tester'));
    for (const [name, marks] of [['page change (to a bot)', ['b:builder:aria-current', 'b:builder:class']],
      ['page change (bot to bot)', ['b:builder:aria-current', 'b:builder:class', 'b:tester:aria-current', 'b:tester:class']]] as const) {
      const w = report[name]!;
      check(w.inner === 0 && w.parsed === 0 && w.builds <= 1 && w.treeAdded === 0 && w.treeRemoved === 0 && w.treeText === 0, `${name}: the list is not rebuilt (${JSON.stringify(w)})`);
      check(JSON.stringify(w.treeAttrs) === JSON.stringify(marks), `${name}: only the selected mark is written, got ${w.treeAttrs.join(' ')}`);
      check(!w.own.length, `${name}: nothing else of the sidebar's is written, got ${w.own.join(' ')}`);
    }
    check(await page.locator('#tree .cur').count() === 1 && await row('tester').getAttribute('aria-current') === 'page' && await row('builder').getAttribute('aria-current') === null,
      'the selected mark follows the page');
    await gone('a page change');
    check(await folded('g:built-in') === false && await folded('g:growth') === true, 'a page change keeps the folds');
    check(await scroller.evaluate(el => el.scrollTop) === 40, 'a page change keeps the rail where it was scrolled');

    // 2. A live burst that changes nothing the list draws (what the bot is doing is not shown there): nothing is written.
    report['burst: nothing the list shows'] = await wrote(() => burst(status('builder', 'idle', 'Reading the plan'), status('writer', 'idle', 'Drafting')), burstDone);
    {
      const w = report['burst: nothing the list shows']!;
      check(w.builds === 0 && w.parsed === 0, `an unrelated burst does not build the list's markup (${JSON.stringify(w)})`);
      check(w.inner === 0 && w.treeAdded === 0 && w.treeRemoved === 0 && w.treeText === 0 && !w.treeAttrs.length, `an unrelated burst writes nothing in the list (${JSON.stringify(w)})`);
      check(!w.own.length, `an unrelated burst writes nothing else of the sidebar's, got ${w.own.join(' ')}`);
    }
    await gone('an unrelated burst');

    // 3. One bot starts working: its row changes in place, and the rows beside it are not touched.
    report['burst: one bot starts working'] = await wrote(() => burst(status('builder', 'running')), shows('builder', 'running'));
    {
      const w = report['burst: one bot starts working']!;
      check(w.inner === 0 && w.builds === 1 && w.treeRemoved === 0 && w.treeAdded === 1, `one bot's status changes its row in place: one ring added (${JSON.stringify(w)})`);
      check(w.treeAttrs.every(a => a.startsWith('b:builder:') || a.startsWith('av ')), `only that bot's row is written, got ${w.treeAttrs.join(' ')}`);
    }
    await gone('a status change');
    check(await row('builder').locator('.tree-spin').count() === 1, 'the working row shows its ring');

    // 4. A count: it appears on its row, then its number changes as text, on the same badge.
    needs = 2;
    report['count appears'] = await wrote(() => page.evaluate(() => refresh(true)), () => row('builder').locator('.cnt.needs', {hasText: /^2$/}).waitFor());
    await page.evaluate(() => { window.sbrKeep(); (window as unknown as {sbrBadge: Element | null}).sbrBadge = document.querySelector('#tree a.node[data-org="b:builder"] .cnt.needs'); });
    needs = 3;
    report['count changes'] = await wrote(() => page.evaluate(() => refresh(true)), () => row('builder').locator('.cnt.needs', {hasText: /^3$/}).waitFor());
    {
      const w = report['count changes']!;
      check(w.inner === 0 && w.treeAdded === 0 && w.treeRemoved === 0 && w.treeText === 1 && w.treeAttrs.join() === 'cnt needs working:aria-label,cnt needs working:title',
        `a count changes as text, with its words (${JSON.stringify(w)})`);
      check(await page.evaluate(() => (window as unknown as {sbrBadge: Element}).sbrBadge === document.querySelector('#tree a.node[data-org="b:builder"] .cnt.needs')), 'the badge is the same node');
      check(await row('builder').locator('.cnt.needs').getAttribute('aria-label') === '3 things Builder needs from you', 'the badge says its new count');
    }
    await gone('a count change');

    // 4b. An unread DM arrives on the row that has a count: a dot is added beside the count, which keeps its node (the
    // two share their first class). The dot goes again the same way.
    const badgeKept = () => page.evaluate(() => (window as unknown as {sbrBadge: Element}).sbrBadge === document.querySelector('#tree a.node[data-org="b:builder"] .cnt.needs'));
    report['a DM dot beside a count'] = await wrote(() => page.evaluate(() => { THREADS.dms = {...THREADS.dms, 'bot:builder': 2}; renderTree(); }),
      () => row('builder').locator('.cnt.tree-dm.dot').waitFor({state: 'attached'}));
    {
      const w = report['a DM dot beside a count']!;
      check(await badgeKept(), 'a DM dot arriving beside a count leaves the count its node');
      check(w.treeAdded === 1 && w.treeRemoved === 0 && w.treeText === 0, `a DM dot is one node added (${JSON.stringify(w)})`);
    }
    await gone('a DM dot arriving');
    await page.evaluate(() => { const {'bot:builder': _gone, ...rest} = THREADS.dms; THREADS.dms = rest; renderTree(); });
    check(await row('builder').locator('.cnt.tree-dm').count() === 0 && await badgeKept(), 'the DM dot going leaves the count its node');
    await gone('a DM dot going');

    // 4c. Needs you read again with no draw of the list (a snoozed ask coming back is read by a timer, hub-v2.ts
    // needsWakeAt): the next burst draws the count, though no bot in it changed what its row shows.
    needs = 4;
    await page.evaluate(() => v2Refresh());
    await burst(status('builder', 'running', 'Still at it'));
    await row('builder').locator('.cnt.needs', {hasText: /^4$/}).waitFor({timeout: t(5000)}).catch(() => {});
    check(await row('builder').locator('.cnt.needs').innerText() === '4' && await badgeKept(), 'a burst draws a count that was read without a draw');
    await burstDone();
    await gone('a count read without a draw');

    // 4d. A draw asked for after the address changed without a page change (the Tasks page rewrites it when its layout
    // switches) is still made before renderTree() returns: its caller reads the list right after.
    check(await page.evaluate(() => {
      const route = S.route, had = S.v2.status.reviewer, shown = () => !!document.querySelector('#tree a.node[data-org="b:reviewer"] .tree-spin');
      S.route = route + '?layout=list'; S.v2.status.reviewer = {bot: 'reviewer', state: 'running'};
      renderTree();
      const drawn = shown();
      S.route = route;
      if (had) S.v2.status.reviewer = had; else delete S.v2.status.reviewer;
      renderTree();
      return drawn && !shown();
    }), 'renderTree() draws before it returns, whatever the address');
    await gone('a draw after the address changed');

    // 5. Focus on a row, where the rail is scrolled to and the folds survive a burst that changes the row beside it.
    await row('reviewer').focus();
    await scroller.evaluate(el => { el.scrollTop = 60; });
    report['burst: one bot stops'] = await wrote(() => burst(status('builder', 'idle')), shows('builder', 'idle'));
    check(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.org) === 'b:reviewer', 'a burst keeps the focused row');
    check(await scroller.evaluate(el => el.scrollTop) === 60, 'a burst keeps the rail where it was scrolled');
    check(await folded('g:built-in') === false && await folded('g:growth') === true, 'a burst keeps the folds');
    await gone('a burst beside the focused row');

    // 6. A group name being typed keeps its field, its text and the cursor through a burst in that group.
    const group = page.locator('#tree .noderow[data-org="g:engineering"]');
    await group.hover();
    await group.locator('[data-group-rename]').click();
    const field = page.locator('#tree .org-name');
    await field.fill('Engineering and QA');
    await page.evaluate(() => { (window as unknown as {sbrField: Element | null}).sbrField = document.querySelector('#tree .org-name'); });
    report['burst while renaming'] = await wrote(() => burst(status('tester', 'running')), shows('tester', 'running'));
    check(await page.evaluate(() => {
      const now = document.querySelector<HTMLInputElement>('#tree .org-name');
      return now === (window as unknown as {sbrField: Element}).sbrField && document.activeElement === now && now?.value === 'Engineering and QA';
    }), 'a burst keeps the name being typed, in the same field, with the cursor in it');
    await field.press('Escape');
    await page.locator('#tree .noderow[data-org="g:engineering"] a.dept-label').waitFor();

    // 7. An open plus menu stays open on its button through a burst.
    await page.evaluate(() => { window.sbrKeep(); (window as unknown as {sbrPlus: Element | null}).sbrPlus = document.querySelector('#tree [data-group-plus="engineering"]'); });
    await group.hover();
    await group.locator('[data-group-plus]').click();
    await page.locator('#team-add-menu:not([hidden])').waitFor();
    report['burst with a menu open'] = await wrote(() => burst(status('tester', 'idle')), shows('tester', 'idle'));
    check(await page.evaluate(() => {
      const plus = document.querySelector('#tree [data-group-plus="engineering"]');
      return plus === (window as unknown as {sbrPlus: Element}).sbrPlus && plus?.getAttribute('aria-expanded') === 'true' && !document.querySelector<HTMLElement>('#team-add-menu')!.hidden;
    }), 'a burst keeps the plus menu open on the same button');
    await gone('a burst with a menu open');
    await page.keyboard.press('Escape');

    console.log('sidebar writes per case:');
    for (const [name, w] of Object.entries(report))
      console.log(`  ${name}: markup built ${w.builds}, parsed ${w.parsed}, innerHTML writes ${w.inner}; list: +${w.treeAdded} -${w.treeRemoved} elements, ${w.treeAttrs.length} attributes, ${w.treeText} text; rail: +${w.sideAdded} -${w.sideRemoved} elements, ${w.sideAttrs} attributes${w.others.length ? ' (' + w.others.join(' ') + ')' : ''}`);

    // 8. Draws asked for in one frame are one pass, and a draw made at once leaves none waiting.
    await frames();
    await page.evaluate(() => { window.sbrPasses = 0; renderTreeSoon(); renderTreeSoon(); renderTreeSoon(); });
    await frames();
    check(await page.evaluate(() => window.sbrPasses) === 1, 'three draws asked for in one frame are one pass');
    await page.evaluate(() => { window.sbrPasses = 0; renderTreeSoon(); renderTree(); });
    await frames();
    check(await page.evaluate(() => window.sbrPasses) === 1, 'a draw made at once leaves none waiting for the frame');
    // Three bots' status lines in one burst: one pass.
    await page.evaluate(() => { window.sbrPasses = 0; });
    await burst(status('builder', 'running'), status('tester', 'running'), status('writer', 'running'));
    await shows('tester', 'running')();
    await frames();
    check(await page.evaluate(() => window.sbrPasses) === 1, 'a burst of three status lines is one pass');
    await gone('a burst of three');

    // 9. A deep chart: a leaf that changes under forty rows reads each row's markup once, not once for every row above.
    {
      const read = await page.evaluate(() => {
        const chain = (leaf: string): string => {
          let html = `<li><div class="noderow"><a class="node" data-org="b:leaf">${leaf}</a></div></li>`;
          for (let i = 40; i > 0; i--) html = `<li class="dept"><div class="noderow"><a class="node" data-org="b:up${i}">Manager ${i}</a></div><ul>${html}</ul></li>`;
          return html;
        };
        const parsed = (html: string): DocumentFragment => { const fresh = document.createElement('template'); fresh.innerHTML = html; return fresh.content; };
        const host = document.createElement('ul');
        treeKids(host, parsed(chain('one'))); treeKids(host, parsed(chain('two')));
        const leaf = host.querySelector('[data-org="b:leaf"]'), top = host.querySelector('[data-org="b:up1"]');
        const outer = Object.getOwnPropertyDescriptor(Element.prototype, 'outerHTML')!;
        let chars = 0;
        Object.defineProperty(Element.prototype, 'outerHTML', {...outer, get(this: Element): string { const text = String(outer.get!.call(this)); chars += text.length; return text; }});
        try { treeKids(host, parsed(chain('three'))); } finally { Object.defineProperty(Element.prototype, 'outerHTML', outer); }
        return {chars, whole: chain('three').length, text: leaf?.textContent,
          same: leaf === host.querySelector('[data-org="b:leaf"]') && top === host.querySelector('[data-org="b:up1"]')};
      });
      check(read.same && read.text === 'three', `a leaf under forty rows changes in place (${JSON.stringify(read)})`);
      check(read.chars < read.whole * 2, `a deep chart's markup is read once: ${read.chars} characters read for ${read.whole} of markup`);
      console.log(`  deep chart (40 rows above one leaf): ${read.chars} characters of markup read for ${read.whole} drawn`);
    }

    assert.deepEqual(failed, []);
    assert.deepEqual(errors, []);
    console.log('sidebar redraw: a page change moves the mark only, a burst writes what changed, rows keep their nodes: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
