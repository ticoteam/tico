// The bot page keeps Active, Updates and Files paths, with empty sections hidden.
// Tools open from the bot header or More on desktop and phone; one theme exercises each path.
// Fixtures only, no network. TICO_SCREENSHOT_DIR keeps screenshots of the page.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
import type {Browser, BrowserContextOptions, LaunchOptions, Page} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RoutesSupport from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RoutesSupport.Api;
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
interface PageWin { opened: string[]; open: (url: string) => null }
interface OpenData { updates?: unknown[]; files?: unknown[]; bots?: unknown[]; goals?: unknown[]; status?: Record<string, unknown>; cantRun?: {cause: string; fix: string | null} | null }
// Page-defined function (the bot page script), called inside page.evaluate.
declare function botLatestUpdate(bot: string): void;
const shots = process.env.TICO_SCREENSHOT_DIR;

const now = Date.now(), iso = (ms: number) => new Date(now + ms).toISOString(), hour = 3600e3;
const bots = [{name: 'cmo', display_name: 'AI CMO', org_parent: '', host: 'keeper', status: 'active', can_chat: true, runtime: 'codex',
  users: [{id: 'ana', name: 'Ana'}],
  schedules: [{id: 'r1', title: 'Morning metrics digest', cron: '0 8 * * 1-5', active: true, next: iso(14 * hour)},
    {id: 'r2', title: 'Friday launch review', cron: '0 15 * * 5', active: true, next: iso(50 * hour)}]}];
const tool = (id: string, service: string, name: string, extra: Record<string, unknown>) => ({id, service, name, logo_key: null, identity: '', can: [], scope: {}, note: '',
  status: 'ready', ...extra});
const TOOLS = [
  tool('model', 'codex', 'Codex', {logo_key: 'openai', identity: 'openai/gpt-6-luna', can: ['use'], scope: {effort: 'high'}}),
  tool('repo', 'github', 'GitHub', {logo_key: 'github', identity: 'acme-co/emp-cmo', scope: {repo: 'acme-co/emp-cmo'}, url: 'https://github.com/acme-co/emp-cmo'}),
  tool('posthog', 'posthog', 'PostHog', {logo_key: 'posthog', identity: 'PostHog project 12345 (US), personal key', can: ['read'],
    scope: {project: '12345'}, env: 'POSTHOG_KEY', note: 'funnels only', status: 'problem', problem: 'Credential missing on Test Mac'}),
  tool('slack', 'slack', 'Slack', {logo_key: 'slack', identity: 'Acme workspace', can: ['read', 'post'], scope: {channels: ['#ops', '#launch']}, env: 'SLACK_TOKEN'}),
  tool('github', 'github', 'GitHub', {logo_key: 'github', identity: 'acme-co/website', can: ['read', 'write'], scope: {repo: 'acme-co/website'},
    env: 'GH_TOKEN', status: 'problem', problem: 'Credential missing on Test Mac'}),
  tool('meeting-notes', 'meeting-notes', 'Meeting notes', {can: ['use'], status: 'unknown', detail: 'No credential is declared, so there is nothing to check'}),
  ...Array.from({length: 4}, (_, n) => tool('extra' + n, 'extra' + n, 'Extra ' + n, {identity: 'account ' + n})),
  ...['acme-co/docs', 'acme-co/pricing-api'].map((name, n) => tool('github-extra-' + n, 'github', 'GitHub', {logo_key: 'github', identity: name,
    can: n ? ['read', 'write'] : ['read'], scope: {repo: name}, url: 'https://github.com/' + name, status: 'unknown', detail: 'Granted repository'})),
];
const task = (id: string, title: string, extra?: Record<string, unknown>) => ({id, title, status: 'open', pipeline_id: 'general', stage_id: 'general-work', turn: {who: 'bot:cmo', state: 'bot'}, owner_id: 'bot:cmo', requester_id: 'human:ana', created_at: iso(-30 * hour), updated_at: iso(-hour), ...extra});
const TASKS = [task('t1', 'Draft the October pricing page copy'), task('t2', 'Pull last week\'s funnel numbers from PostHog', {status: 'open'}),
  task('t3', 'Write the launch email for the new plan', {turn: {who: 'human:ana', state: 'person'}}),
  task('t4', 'Weekly report', {status: 'done', done_at: iso(-20 * hour)}), task('t5', 'Clean up UTM tags', {status: 'done', done_at: iso(-40 * hour)})];
const ASKED = [task('a1', 'Approve the pricing page before Friday', {owner_id: 'human:ana', requester_id: 'bot:cmo'})];
const UPDATE = {id: 'u1', bot: 'cmo', kind: 'daily', day: '2026-10-01', read: false, created_at: iso(-10 * hour), updated_at: iso(-10 * hour),
  body: '- Drafted the pricing page copy; two headline options are on the task.\n- Funnel: sign-ups up 8% week over week.'};
const said = (n: number, who: string, body: string) => ({id: 'm' + n, sender_id: who, body, created_at: iso((n - 9) * hour)});
const CHAT = [said(1, 'human:ana', 'Where are we on the pricing page?'),
  said(2, 'bot:cmo', 'The copy is drafted, with two headline options on the task. I am pulling last week\'s funnel numbers to pick between them.'),
  said(3, 'human:ana', 'Go with the shorter headline if the numbers are close.'),
  said(4, 'bot:cmo', 'Will do. The launch email waits on your approval of the page.')];
const file = (n: number, title: string) => ({id: 'file-' + n, bot: 'cmo', title, kind: 'document', locator: 'tico_blob', name: title + '.md',
  open: {type: 'tico', url: '/api/v2/files/file-' + n}, last_activity_at: iso(-n * hour)});
const FILES = ['Q4 launch plan', 'Pricing page draft', 'Funnel export', 'Old brief', 'Brand voice notes'].map((t, n) => file(n + 1, t));

async function open(browser: Browser, viewport: {width: number; height: number}, options: BrowserContextOptions = {}, data: OpenData = {}) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760, ...options});
  await context.addInitScript((theme: string) => { try { localStorage.setItem('tico.theme', theme); } catch {} }, options.colorScheme || 'dark');
  const page = await context.newPage();
  const errors: string[] = [], read: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const {updates = [UPDATE], files = FILES} = data;
  const cleared: string[] = [];
  let status = data.status || {bot: 'cmo', state: 'running', task_id: 't1'};
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    // The vendored Markdown and icon font, so the page reads as it does for real.
    const vendor = p.match(/^\/vendor\/((?:fonts\/)?[\w.-]+)$/);
    if (vendor && fs.existsSync(uiFile('vendor/' + vendor[1]))) return route.fulfill({path: uiFile('vendor/' + vendor[1])});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    // A list, as the server sends it: the catch-all {} broke the sidebar tree whenever it landed ahead of
    // /api/v2/status, and the bot page never opened (the bot-needs.cts hang from the v0.3.54 release check).
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(data.bots || bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: bots[0]!.schedules.map(s => ({...s, employee: 'cmo'}))});
    if (p === '/api/v2/status') return json({bots: [status]});
    if (p === '/api/v2/bots/cmo/limit/retry') { cleared.push(p); status = {bot: 'cmo', state: 'idle', bot_state: 'active'}; return json({cleared: 1}); }
    if (p === '/api/v2/bots/cmo/execution-review') return json({jobs: []});
    if (p === '/api/v2/bots/cmo') return json({display_name: 'AI CMO', access: {}, cant_run: data.cantRun ?? null});
    if (p === '/api/v2/bots/cmo/quarantine/clear') { cleared.push(p); status = {bot: 'cmo', state: 'idle', bot_state: 'active'}; return json({}); }
    if (p === '/api/v2/goals') return json({goals: data.goals || [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates/read') { read.push(...JSON.parse(route.request().postData()!).ids); return json({}); }
    if (p === '/api/v2/updates') return json({updates: q.get('bot') ? updates : [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: q.get('owner') === 'cmo' ? TASKS : q.get('requester') === 'cmo' ? ASKED : []});
    if (p === '/api/v2/conversations') return json({conversations: q.get('chat_with') ? [{id: 'c-cmo', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:cmo']}] : []});
    if (p === '/api/v2/conversations/c-cmo/snapshot') return json({messages: CHAT});
    if (p === '/api/v2/bots/cmo/tools') return json({bot: 'cmo', tools: TOOLS, computer: 'Test Mac', online: true, reported_at: null});
    if (p === '/api/v2/bots/cmo/files') {
      const limit = Number(q.get('limit'));
      return json({bot: 'cmo', files: files.slice(0, limit), total: files.length, next_cursor: files.length > limit ? 'next' : null, has_more: files.length > limit, can_manage: true, actors: {}});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors, read, context, cleared};
}

const railOrder = (page: Page) => page.evaluate(() => [...(document.querySelectorAll('#pane-tasks>.bot-w>section.rail-sec') as unknown as HTMLElement[])]
  .filter(el => !el.hidden).map(el => el.querySelector('.rail-h')!.firstChild!.textContent!.trim()));

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    // A long, real bot goal must not take the last fractional pixel from the name. Keep the production
    // botGoalLoad/render path, the desktop Tasks rail, and the precise 1380px Chromium repro in both themes.
    for (const scheme of ['light', 'dark'] as const) {
      const name = 'Head of Engineering';
      const longGoal = 'Deliver dependable releases and keep every customer informed about important improvements across the whole team';
      const {page, errors, context} = await open(browser, {width: 1380, height: 900}, {colorScheme: scheme}, {
        bots: [{...bots[0], display_name: name}],
        goals: [{id: 'g-engineering', title: longGoal, owner_id: 'bot:cmo', status: 'active', rank: 1},
                {id: 'g-docs', title: 'Keep the docs current', owner_id: 'bot:cmo', status: 'active', rank: 2}],
      });
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#bot-goal[data-status="set"] .bot-goal-title').waitFor();
      await page.locator('#bot-tasks-h').waitFor();
      assert.equal(await page.locator('#pane-tasks').isVisible(), true, scheme + ': Tasks rail is open');
      assert.equal((await page.locator('#bot-goal .bot-goal-title').innerText()).trim(), longGoal, scheme + ': unchanged goal');
      // The goal: a target, the first goal cut to fit and "+1"; a tap lists both and Add goal. All of it on one line,
      // right of the bot's name.
      assert.equal(await page.locator('#bot-goal .bot-goal-n').innerText(), '+1', scheme + ': the other goal is a count');
      const line = await page.evaluate(() => {
        const mid = (s: string) => { const r = (document.querySelector(s) as HTMLElement).getBoundingClientRect(); return {y: r.top + r.height / 2, left: r.left, right: r.right}; };
        return {name: mid('.bot-ident h1'), goal: mid('#bot-goal-btn'), more: mid('#bot-more-btn')};
      });
      assert(Math.abs(line.name.y - line.goal.y) < 4 && Math.abs(line.goal.y - line.more.y) < 4, scheme + ': one line ' + JSON.stringify(line));
      assert(line.goal.left > line.name.right && line.goal.right <= line.more.left, scheme + ': the goal sits right, by the actions');
      await page.locator('#bot-goal-btn').click();
      const items = await page.locator('#bot-goal-pop .tl-mi').evaluateAll(els => els.map(el =>
        [...(el.childNodes as unknown as Element[])].filter(n => !n.classList?.contains('nav-icon')).map(n => n.textContent).join('').trim()));
      assert.deepEqual(items, [longGoal, 'Keep the docs current', 'Add goal'], scheme + ': the goal menu');
      await page.keyboard.press('Escape');
      const header = await page.locator('.bot-ident h1').evaluate((el: Element) => {
        // Measure the whole heading: the runtime mark sits inside it, and a fractional overflow ellipsizes the line.
        const range = document.createRange();
        range.selectNodeContents(el);
        const text = el.firstChild!.textContent;
        return {text, contentWidth: range.getBoundingClientRect().width, boxWidth: el.getBoundingClientRect().width,
                scroll: el.scrollWidth, client: el.clientWidth};
      });
      assert.equal(header.text, name, scheme + ': full bot name text');
      assert(header.contentWidth <= header.boxWidth + 0.01 && header.scroll <= header.client,
             `${scheme}: name and runtime mark fit without ellipsis (${header.contentWidth}px in ${header.boxWidth}px)`);
      const layout = await page.evaluate(() => {
        const top = (document.querySelector('#bot-top') as HTMLElement).getBoundingClientRect();
        const rail = (document.querySelector('#pane-tasks') as HTMLElement).getBoundingClientRect();
        return {topRight: top.right, railLeft: rail.left, railRight: rail.right, viewport: innerWidth};
      });
      assert(layout.topRight <= layout.railLeft + 1, scheme + ': toolbar stays left of the Tasks rail');
      assert(layout.railRight <= layout.viewport + 1, scheme + ': Tasks rail stays within the viewport');
      assert.deepEqual(errors, [], scheme + ': page errors');
      await context.close();
    }

    {
      const scheme = 'dark';
      const {page, errors, read, context} = await open(browser, {width: 1440, height: 900}, {colorScheme: scheme});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#t-open .tk-row').first().waitFor();
      await page.locator('#bot-files .bf-row').first().waitFor();
      await page.locator('#bot-latest:not([hidden])').waitFor();

      assert.deepEqual(await railOrder(page), ['Tasks', 'Updates', 'Files'], scheme + ': Tasks leads');
      assert.equal(await page.locator('#pane-tasks .card').count(), 0, 'no cards in the rail');
      assert.equal(await page.locator('#main >> text=Latest update').count(), 0, 'no Latest update banner');
      assert.equal(await page.locator('#bot-history-btn').count(), 0, 'all updates is in the rail now');
      // Updates: its age, and a quiet way to all of them; showing it there reads it.
      assert.match(await page.locator('#bot-latest .rail-age').innerText(), /10h ago/);
      assert.equal(await page.locator('#bot-latest a.rail-ico').getAttribute('href'), '#/bot/cmo/history');
      assert.match(await page.locator('#bot-latest .upd-body').innerText(), /two headline options/);
      for (const end = Date.now() + t(10000); !read.includes('u1') && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
      assert.deepEqual(read, ['u1'], 'the latest update counts as read once the rail shows it');
      // Files: names only, three of them, and a small "+2" for the rest.
      assert.equal(await page.locator('#bot-files .bf-row').count(), 3);
      assert.equal(await page.locator('#bot-files .nav-icon').count(), 0, 'no icon before a name');
      assert.equal((await page.locator('#bot-files [data-bf-all]').innerText()).trim(), '+2');
      // No routines on the rail: they are Automations now.
      assert.equal(await page.locator('#bot-recurring').count(), 0);
      // Assigned to others is a tab now, not a folded section; Done recently is a tab that stays closed.
      assert.equal(await page.locator('#bot-assigned').count(), 0);
      assert.equal(await page.locator('#pane-tasks .bot-done').isHidden(), true);
      // The top line keeps no tool icons; the Tools card under More lists them.
      // The title is the name alone: no harness or provider mark beside it (#299).
      assert.equal(await page.locator('.rt').count(), 0, 'no harness mark anywhere on the page');
      assert.equal((await page.locator('.bot-nameline h1').innerText()).trim(), 'AI CMO');
      // A bot with a harness still leaves the model out of the tool strip.
      await page.locator('#bot-tool-strip .bts-icon').first().waitFor({state: 'attached'});
      assert.equal(await page.locator('#bot-tool-strip [data-tool="model"]').count(), 0, 'the strip leaves the model out');
      assert.equal(await page.locator('#bot-tool-strip').isVisible(), false, 'no tool icons in the top line');
      await page.goto('https://tico-ui.test/#/bot/cmo/tools');
      const list = page.locator('#bot-tools');
      await list.locator('.bt-item').first().waitFor();
      assert.equal(await page.locator('#pane-more').isVisible(), true);
      // Three tools, then a small "Show more" for the rest.
      const moreAll = page.locator('#bot-tools + .more-all');
      await moreAll.waitFor();
      assert.equal(await list.locator('.bt-item:visible').count(), 3);
      assert.equal(await moreAll.innerText(), 'Show more');
      await moreAll.click();
      assert.equal(await list.locator('.bt-item:visible').count(), 9);
      assert.equal(await moreAll.innerText(), 'Show less');
      assert.equal(await list.locator('details[open]').count(), 0);
      const repos = list.locator('.bt-item[data-tool=github-repos]');
      assert.match(await repos.locator('summary').first().innerText(), /4 repositories · 2 read and write, 1 read only[\s\S]*Needs attention/);
      // Opened, every detail reads as before.
      await list.locator('details').evaluateAll(els => (els as HTMLDetailsElement[]).forEach(el => { el.open = true; }));
      assert.equal(await repos.locator('.bt-repo').count(), 4);
      const text = await list.innerText();
      for (const expected of ['GPT 6 Luna', 'acme-co/emp-cmo', 'acme-co/website', 'GH_TOKEN', 'acme-co/pricing-api', 'PostHog project 12345 (US), personal key', '12345', 'POSTHOG_KEY',
        'funnels only', 'Credential missing on Test Mac', '#ops, #launch', 'SLACK_TOKEN', 'nothing to check', 'account 3'])
        assert(text.includes(expected), scheme + ': the list says ' + expected + '\n' + text);
      assert.equal(await list.locator('[data-tool=repo] summary a').getAttribute('href'), 'https://github.com/acme-co/emp-cmo');
      // Who else has a tool, and its keys, are one link away: the tool's page under Tools.
      assert.equal(await list.locator('[data-tool=repo] .bt-open').getAttribute('href'), '#/tools/github');
      // The repository's name opens GitHub (window.open is stubbed: nothing leaves the test) and leaves its line as it was.
      await page.evaluate(() => { (window as unknown as PageWin).opened = []; (window as unknown as PageWin).open = url => { (window as unknown as PageWin).opened.push(url); return null; }; });
      await list.locator('[data-tool=repo]').evaluate(d => { (d as HTMLDetailsElement).open = false; });
      await list.locator('[data-tool=repo] summary a').click();
      assert.deepEqual(await page.evaluate(() => (window as unknown as PageWin).opened), ['https://github.com/acme-co/emp-cmo']);
      assert.equal(await list.locator('[data-tool=repo]').evaluate(d => (d as HTMLDetailsElement).open), false, 'following the link does not open the line');
      assert.equal(await list.locator('[data-tool=meeting-notes] .tool-initials').innerText(), 'Me');
      if (shots && scheme === 'dark') await page.screenshot({path: path.join(shots, 'bot-page-tools-desktop-dark.png')});
      assert.deepEqual(errors, [], scheme + ': page errors');
      await context.close();
    }

    // Nothing to show: no Updates or Files section; Active still says so in one line.
    {
      const saved = bots[0]!.schedules; bots[0]!.schedules = [];
      const {page, errors, context} = await open(browser, {width: 1280, height: 800}, {}, {updates: [], files: []});
      await page.route('**/api/v2/tasks*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({tasks: []})}));
      await page.route('**/api/status', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []})}));
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#t-open .rail-empty:text("None")').waitFor();
      // Files has drawn its (empty) answer, and the latest update has been read back empty.
      await page.locator('#bot-files .rail-h').waitFor({state: 'attached'});
      await page.evaluate(() => botLatestUpdate('cmo'));
      assert.deepEqual(await railOrder(page), ['Tasks']);
      assert.deepEqual(errors, []);
      bots[0]!.schedules = saved;
      await context.close();
    }

    // A phone: one column; Tasks leads and the same sections; the tools are under More.
    {
      const scheme = 'dark';
      const {page, errors, context} = await open(browser, {width: 390, height: 844}, {colorScheme: scheme});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#bot-thread').waitFor();
      assert.equal(await page.locator('#pane-tasks').isVisible(), false, 'chat alone');
      assert.equal(await page.locator('#bot-tool-strip').isVisible(), false, 'no room for the tools beside the name');
      if (shots) await page.screenshot({path: path.join(shots, `bot-page-phone-chat-${scheme}.png`)});
      await page.evaluate(() => { location.hash = '#/bot/cmo/tasks'; });
      await page.locator('#t-open .tk-row').first().waitFor();
      await page.locator('#bot-files .bf-row').first().waitFor();
      await page.locator('#bot-latest:not([hidden])').waitFor();
      assert.deepEqual(await railOrder(page), ['Tasks', 'Updates', 'Files']);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no sideways scroll');
      if (shots) await page.screenshot({path: path.join(shots, `bot-page-phone-tasks-${scheme}.png`), fullPage: true});
      await page.evaluate(() => { location.hash = '#/bot/cmo/more'; });
      await page.locator('#bot-tools .bt-item').first().waitFor();
      assert.equal(await page.locator('#bot-tools .bt-item').count(), 9);
      // Open, the repositories and a tool's details still fit: every line stays one line and nothing scrolls sideways.
      await page.locator('#bot-tools [data-tool=github-repos] > summary').click();
      await page.locator('#bot-tools [data-tool=posthog] > summary').click();
      await page.locator('#bot-tools [data-tool=posthog][open] dl').waitFor();
      const heights = await page.locator('#bot-tools summary:visible').evaluateAll(els => els.map(el => el.getBoundingClientRect().height));
      assert(heights.length > 4 && heights.every(h => h < 40), 'one line a tool: ' + heights);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'More: no sideways scroll');
      if (shots) await page.screenshot({path: path.join(shots, `bot-page-phone-tools-${scheme}.png`), fullPage: true});
      assert.deepEqual(errors, [], scheme + ': phone page errors');
      await context.close();
    }
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
      // A quarantine from repeated refusals says when it lifts and offers Resume now to a manager; the message box stays.
      const since = iso(-10 * 60e3), resumes = iso(50 * 60e3);
      const why = `Quarantined for repeated refused writes (rule 8; 10 repeated refusals today): resumes by itself at ${new Date(resumes).toISOString().slice(0, 16).replace('T', ' ')} UTC; a person who manages it can clear it sooner. Refused write (identity): Only a person changes a task number. <script>throw 1</script>`;
      const held = {bot: 'cmo', state: 'quarantined', bot_state: 'quarantined', changed_at: since, focus: '10 repeated refusals today',
        quarantine: {since, auto: true, resumes_at: resumes, why}};
      const {page, errors, context, cleared} = await open(browser, viewport, {timezoneId: 'America/Los_Angeles'}, {status: held});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const banner = page.locator('#conv-paused:not([hidden])');
      await banner.waitFor();
      assert((await banner.innerText()).includes(why), 'shows Health explanation verbatim, escaped and in UTC');
      assert.equal(await page.locator('#bot-alert .bot-alert').getAttribute('title'), why);
      assert.doesNotMatch(await page.locator('#bot-alert').innerText(), /review/);
      assert((await page.locator('#bot-quarantine').innerText()).includes(why));
      assert.doesNotMatch(await page.locator('#bot-quarantine h2').innerText(), /review/);
      assert.match(await banner.innerText(), /Your messages are saved and run when it's back/);
      assert.doesNotMatch(await banner.innerText(), /under More/);
      if (shots) await page.screenshot({path: path.join(shots, `bot-quarantine-banner-${viewport.width}.png`)});
      await banner.locator('[data-quarantine-resume]').click();
      await page.locator('#conv-paused').waitFor({state: 'hidden'});
      assert.deepEqual(cleared, ['/api/v2/bots/cmo/quarantine/clear']);
      assert.deepEqual(errors, [], 'quarantine banner errors');
      await context.close();
    }
    for (const scheme of ['dark', 'light'] as const) {
      // A usage limit offers Try now beside the warning and in the chat banner; one click clears it.
      const {page, errors, context, cleared} = await open(browser, {width: 1440, height: 900}, {colorScheme: scheme},
        {status: {bot: 'cmo', state: 'limited', bot_state: 'active', changed_at: iso(-10 * 60e3), focus: 'claude usage limit at 01:43; retrying after 02:13 UTC'}});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const banner = page.locator('#conv-paused:not([hidden])');
      await banner.waitFor();
      assert.match(await banner.innerText(), /usage limit; your messages will run when it's back\s*Try now$/);
      // The header warning is drawn at the end of the refresh, after the issues list, so it can follow the chat banner.
      await page.locator('#bot-alert', {hasText: /Try now/}).waitFor();
      assert.match(await page.locator('#bot-alert').innerText(), /Try now/);
      if (shots) await page.screenshot({path: path.join(shots, `bot-limit-try-now-${scheme}.png`)});
      await banner.locator('[data-limit-retry]').click();
      await page.locator('#conv-paused').waitFor({state: 'hidden'});
      assert.deepEqual(cleared, ['/api/v2/bots/cmo/limit/retry']);
      assert.deepEqual(errors, [], 'limit banner errors');
      await context.close();
    }
    {
      // An escape waits for a person, on a bot placed on a computer too: it says what the check matched, shows the
      // refused words and task under More, and Resume bot sits beside the warning.
      const since = iso(-10 * 60e3);
      const review = {what: 'task note', found: 'bot-assistant/', preview: 'Paste this into bot-assistant/tools.yaml: token=[hidden]', task: {id: 't9', title: 'Set up the tool'}};
      const {page, errors, context, cleared} = await open(browser, {width: 1440, height: 900}, {}, {bots: bots.map(b => ({...b, host: 'runner'})),
        status: {bot: 'cmo', state: 'quarantined', bot_state: 'quarantined', changed_at: since, focus: 'escape: the task note reaches outside the hub',
          quarantine: {since, auto: false, resumes_at: null, review}}});
      await page.goto('https://tico-ui.test/#/bot/cmo/more');
      const card = page.locator('#bot-quarantine');
      await card.waitFor();
      assert.match(await card.innerText(), /Paused for review[\s\S]*a task note that named another bot's folder \(bot-assistant\/\)[\s\S]*the checks stay on/);
      assert.match(await card.locator('.bot-quarantine-text').innerText(), /token=\[hidden\]/);
      assert.equal(await card.locator('a[href="#/task/t9"]').innerText(), 'Set up the tool');
      assert.doesNotMatch(await page.locator('#main').innerText(), /outside the hub|escape:/);
      if (shots) await page.screenshot({path: path.join(shots, 'bot-quarantine-review.png'), fullPage: true});
      await page.locator('#bot-alert', {hasText: /Paused for review\s*Resume bot/}).waitFor();
      assert.match(await page.locator('#bot-alert').innerText(), /Paused for review\s*Resume bot/);
      await page.locator('#bot-alert [data-quarantine-resume]').click();
      await page.locator('#bot-alert [data-quarantine-resume]').waitFor({state: 'detached'});
      assert.deepEqual(cleared, ['/api/v2/bots/cmo/quarantine/clear']);
      assert.deepEqual(errors, [], 'escape banner errors');
      await context.close();
    }
    {
      // Never a bare "Crashed" (Oct 22): a run its computer never started is a grey line that says nothing is needed;
      // a run that stopped partway is red, offers the one action that exists, and a click says what happened.
      const NOT_STARTED = "Last run didn't start in time. Nothing was lost; nothing is needed.";
      const soft = {bot: 'cmo', state: 'idle', bot_state: 'active', changed_at: iso(-60e3), last_stop: {kind: 'not_started', tone: 'soft',
        needs_person: false, action: null, at: iso(-60e3), run_id: 'a1', text: NOT_STARTED,
        reason: "Its computer took the run but didn't start it before its lease ran out. It is back in the queue and runs again shortly."}};
      let {page, errors, context} = await open(browser, {width: 1440, height: 900}, {}, {status: soft});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const line = page.locator('#bot-alert .bot-stop.soft');
      await line.waitFor();
      assert.equal(await line.innerText(), NOT_STARTED);
      assert.doesNotMatch(await page.locator('#bot-alert').innerText(), /Crashed|Retry/);
      assert.equal(await page.locator('#conv-paused').isHidden(), true);
      if (shots) await page.screenshot({path: path.join(shots, 'bot-last-run-not-started.png')});
      await line.click();
      const dialog = page.locator('dialog#last-stop');
      assert.match(await dialog.innerText(), /What happened\s*Its computer took the run but didn't start it before its lease ran out\.[\s\S]*Is anything needed\?\s*No\. Nothing was lost\./);
      assert.equal(await dialog.locator('[data-stop-review]').count(), 0);
      await dialog.locator('[data-close]').click();
      await dialog.waitFor({state: 'detached'});
      assert.deepEqual(errors, [], 'soft last-run errors');
      await context.close();
      const stopped = {bot: 'cmo', state: 'crashed', bot_state: 'active', changed_at: iso(-60e3), focus: 'One run stopped; saved for later',
        last_stop: {kind: 'stopped', tone: 'alert', needs_person: true, action: 'review', at: iso(-60e3), run_id: 'a2',
          text: 'Last run stopped partway. Retry it or dismiss it.',
          reason: 'Its computer stopped answering while the run was going, so the run\'s lease ran out.'}};
      ({page, errors, context} = await open(browser, {width: 1440, height: 900}, {}, {status: stopped}));
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const red = page.locator('#bot-alert .bot-stop:not(.soft)');
      await red.waitFor();
      assert.match(await page.locator('#bot-alert').innerText(), /^⚠\s*Last run stopped partway\. Retry it or dismiss it\.\s*Retry or dismiss$/);
      assert.match(await page.locator('#conv-paused').innerText(), /AI CMO: Last run stopped partway/);
      if (shots) await page.screenshot({path: path.join(shots, 'bot-last-run-stopped.png')});
      await red.click();
      assert.match(await page.locator('dialog#last-stop').innerText(), /lease ran out[\s\S]*Yes: say whether to try it again or let it go\./);
      await page.locator('dialog#last-stop [data-stop-review]').click();
      await page.locator('dialog.tmodal', {hasText: 'No stopped runs remain.'}).waitFor();
      assert.deepEqual(errors, [], 'stopped last-run errors');
      await context.close();
    }
    {
      // S14: while it can't run, the top line is a red dot, the server's cause as it is, and the one fix; no fix, no button.
      let {page, errors, context} = await open(browser, {width: 1440, height: 900}, {}, {status: {bot: 'cmo', state: 'idle', bot_state: 'active'},
        cantRun: {cause: 'AI CMO has no computer.', fix: 'assign_computer'}});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const line = page.locator('#bot-alert .bot-cant-run');
      await line.waitFor();
      assert.equal((await line.innerText()).trim(), 'AI CMO has no computer.');
      assert.equal(await line.locator('.tk-mark-cant').count(), 1);
      assert.equal(await page.locator('#bot-alert a').getAttribute('href'), '#/bot/cmo/info');
      assert.equal(await page.locator('#bot-alert a').innerText(), 'Assign a computer');
      assert.deepEqual(errors, [], "can't run errors");
      await context.close();
      ({page, errors, context} = await open(browser, {width: 1440, height: 900}, {}, {status: {bot: 'cmo', state: 'idle', bot_state: 'active'},
        cantRun: {cause: 'AI CMO was removed.', fix: null}}));
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#bot-alert .bot-cant-run').waitFor();
      assert.equal(await page.locator('#bot-alert button, #bot-alert a').count(), 0);
      assert.deepEqual(errors, [], "can't run, no fix errors");
      await context.close();
    }
    console.log('bot page: Tasks, Updates and Files; tools open under More; desktop and phone navigation; quarantine says why and when it resumes; Resume bot beside the warning and under More; Try now on a usage limit; a last run that ended unfinished says why');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
