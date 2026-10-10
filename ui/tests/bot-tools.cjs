// The bot page keeps Active, Updates, Files and Recurring paths, with empty sections hidden.
// Tools open from the bot header or More on desktop and phone; one theme exercises each path.
// Fixtures only, no network. TICO_SCREENSHOT_DIR keeps screenshots of the page.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SCREENSHOT_DIR;

const now = Date.now(), iso = ms => new Date(now + ms).toISOString(), hour = 3600e3;
const bots = [{name: 'cmo', display_name: 'AI CMO', org_parent: '', host: 'keeper', status: 'active', can_chat: true, runtime: 'codex',
  users: [{id: 'ana', name: 'Ana'}],
  schedules: [{id: 'r1', title: 'Morning metrics digest', cron: '0 8 * * 1-5', active: true, next: iso(14 * hour)},
    {id: 'r2', title: 'Friday launch review', cron: '0 15 * * 5', active: true, next: iso(50 * hour)}]}];
const tool = (id, service, name, extra) => ({id, service, name, logo_key: null, identity: '', can: [], scope: {}, note: '',
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
const task = (id, title, extra) => ({id, title, status: 'doing', owner: 'bot:cmo', requester: 'human:ana', created: iso(-30 * hour), updated: iso(-hour), ...extra});
const TASKS = [task('t1', 'Draft the October pricing page copy'), task('t2', 'Pull last week\'s funnel numbers from PostHog', {status: 'open'}),
  task('t3', 'Write the launch email for the new plan', {status: 'waiting'}),
  task('t4', 'Weekly report', {status: 'done', done_at: iso(-20 * hour)}), task('t5', 'Clean up UTM tags', {status: 'done', done_at: iso(-40 * hour)})];
const ASKED = [task('a1', 'Approve the pricing page before Friday', {owner: 'human:ana', requester: 'bot:cmo'})];
const UPDATE = {id: 'u1', bot: 'cmo', kind: 'daily', day: '2026-10-01', read: false, created: iso(-10 * hour), updated: iso(-10 * hour),
  body: '- Drafted the pricing page copy; two headline options are on the task.\n- Funnel: sign-ups up 8% week over week.'};
const said = (n, who, body) => ({id: 'm' + n, from_actor: who, body, created: iso((n - 9) * hour)});
const CHAT = [said(1, 'human:ana', 'Where are we on the pricing page?'),
  said(2, 'bot:cmo', 'The copy is drafted, with two headline options on the task. I am pulling last week\'s funnel numbers to pick between them.'),
  said(3, 'human:ana', 'Go with the shorter headline if the numbers are close.'),
  said(4, 'bot:cmo', 'Will do. The launch email waits on your approval of the page.')];
const file = (n, title) => ({id: 'file-' + n, bot: 'cmo', title, kind: 'document', locator: 'tico_blob', name: title + '.md',
  open: {type: 'tico', url: '/api/v2/files/file-' + n}, last_activity_at: iso(-n * hour)});
const FILES = ['Q4 launch plan', 'Pricing page draft', 'Funnel export', 'Old brief', 'Brand voice notes'].map((t, n) => file(n + 1, t));

async function open(browser, viewport, options = {}, data = {}) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760, ...options});
  await context.addInitScript(theme => { try { localStorage.setItem('tico.theme', theme); } catch {} }, options.colorScheme || 'dark');
  const page = await context.newPage();
  const errors = [], read = [];
  page.on('pageerror', error => errors.push(error.message));
  const {updates = [UPDATE], files = FILES} = data;
  const cleared = [];
  let status = data.status || {bot: 'cmo', state: 'running', task_id: 't1'};
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    // The vendored Markdown and icon font, so the page reads as it does for real.
    const vendor = p.match(/^\/vendor\/((?:fonts\/)?[\w.-]+)$/);
    if (vendor && fs.existsSync(uiFile('vendor/' + vendor[1]))) return route.fulfill({path: uiFile('vendor/' + vendor[1])});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/employees') return json(data.bots || bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: bots[0].schedules.map(s => ({...s, employee: 'cmo'}))});
    if (p === '/api/v2/status') return json({bots: [status]});
    if (p === '/api/v2/bots/cmo/limit/retry') { cleared.push(p); status = {bot: 'cmo', state: 'idle', bot_state: 'active'}; return json({cleared: 1}); }
    if (p === '/api/v2/bots/cmo/quarantine/clear') { cleared.push(p); status = {bot: 'cmo', state: 'idle', bot_state: 'active'}; return json({}); }
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates/read') { read.push(...JSON.parse(route.request().postData()).ids); return json({}); }
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
    return json({});
  });
  return {page, errors, read, context, cleared};
}

const railOrder = page => page.evaluate(() => [...document.querySelectorAll('#pane-tasks>section.rail-sec')]
  .filter(el => !el.hidden).map(el => el.querySelector('.rail-h').firstChild.textContent.trim()));

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      const scheme = 'dark';
      const {page, errors, read, context} = await open(browser, {width: 1440, height: 900}, {colorScheme: scheme});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#t-open .trow').first().waitFor();
      await page.locator('#bot-files .bf-row').first().waitFor();
      await page.locator('#bot-latest:not([hidden])').waitFor();
      await page.locator('#bot-tool-strip .bts-stack .bts-icon').first().waitFor();

      assert.deepEqual(await railOrder(page), ['Active', 'Updates', 'Files', 'Recurring'], scheme + ': Active leads');
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
      // Recurring: the bot's routines, one line each.
      assert.deepEqual(await page.locator('#bot-recurring .ttl').allInnerTexts(), ['Morning metrics digest', 'Friday launch review']);
      // Assigned to others opens while it waits on a person; Done stays folded.
      assert.equal(await page.locator('#bot-assigned').evaluate(el => el.open), true);
      assert.equal(await page.locator('#pane-tasks .bot-done').evaluate(el => el.open), false);
      // Tools beside the name: the runtime mark, then one stack of three icons (not the model again) and "+8".
      const strip = page.locator('#bot-tool-strip');
      const stack = strip.locator('.bts-stack');
      assert.equal(await page.locator('.bot-nameline .rt').count(), 1);
      assert.equal(await strip.locator('button').count(), 1, 'one button, not an icon each');
      assert.deepEqual(await stack.locator('.bts-icon').evaluateAll(els => els.map(el => el.dataset.tool)), ['repo', 'posthog', 'slack']);
      assert.equal((await stack.locator('.bts-more').innerText()).trim(), '+8');
      assert.equal(await stack.getAttribute('aria-label'), 'Tools: 11');
      assert.equal(await stack.locator('[data-tool=posthog] .bt-dot').count(), 1, 'a problem shows a dot');
      if (shots) await page.screenshot({path: path.join(shots, `bot-page-desktop-${scheme}.png`)});
      // It opens a list, one line a tool; Escape closes it and gives the button back its focus.
      await stack.click();
      const pop = page.locator('#bts-pop');
      assert.equal(await stack.getAttribute('aria-expanded'), 'true');
      assert.equal(await pop.locator('.bts-row').count(), 11);
      assert.match(await pop.locator('.bts-row[data-tool=slack]').getAttribute('title'), /^Slack, Acme workspace, can read, post, channels #ops, #launch, Ready$/);
      assert.match(await pop.locator('.bts-row[data-tool=posthog]').innerText(), /PostHog[\s\S]*Credential missing on Test Mac/);
      await page.keyboard.press('Escape');
      assert.equal(await pop.count(), 0);
      assert.equal(await stack.evaluate(el => el === document.activeElement), true);
      // From the keyboard, "Manage" opens the Tools card under More: a closed line a tool, the repositories sharing one.
      await page.keyboard.press('Enter');
      await page.locator('#bts-pop .bts-manage').waitFor();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => location.hash === '#/bot/cmo/tools');
      assert.equal(await pop.count(), 0, 'leaving closes the list');
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
      await list.locator('details').evaluateAll(els => els.forEach(el => { el.open = true; }));
      assert.equal(await repos.locator('.bt-repo').count(), 4);
      const text = await list.innerText();
      for (const expected of ['GPT-6-luna', 'acme-co/emp-cmo', 'acme-co/website', 'GH_TOKEN', 'acme-co/pricing-api', 'PostHog project 12345 (US), personal key', '12345', 'POSTHOG_KEY',
        'funnels only', 'Credential missing on Test Mac', '#ops, #launch', 'SLACK_TOKEN', 'nothing to check', 'account 3'])
        assert(text.includes(expected), scheme + ': the list says ' + expected + '\n' + text);
      assert.equal(await list.locator('[data-tool=repo] a').getAttribute('href'), 'https://github.com/acme-co/emp-cmo');
      // The repository's name opens GitHub (window.open is stubbed: nothing leaves the test) and leaves its line as it was.
      await page.evaluate(() => { window.opened = []; window.open = url => { window.opened.push(url); return null; }; });
      await list.locator('[data-tool=repo]').evaluate(d => { d.open = false; });
      await list.locator('[data-tool=repo] summary a').click();
      assert.deepEqual(await page.evaluate(() => window.opened), ['https://github.com/acme-co/emp-cmo']);
      assert.equal(await list.locator('[data-tool=repo]').evaluate(d => d.open), false, 'following the link does not open the line');
      assert.equal(await list.locator('[data-tool=meeting-notes] .tool-initials').innerText(), 'Me');
      if (shots && scheme === 'dark') await page.screenshot({path: path.join(shots, 'bot-page-tools-desktop-dark.png')});
      assert.deepEqual(errors, [], scheme + ': page errors');
      await context.close();
    }

    // Nothing to show: no Updates, Files or Recurring section; Active still says so in one line.
    {
      const saved = bots[0].schedules; bots[0].schedules = [];
      const {page, errors, context} = await open(browser, {width: 1280, height: 800}, {}, {updates: [], files: []});
      await page.route('**/api/v2/tasks*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({tasks: []})}));
      await page.route('**/api/status', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []})}));
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#t-open .rail-empty:text("None")').waitFor();
      // Files has drawn its (empty) answer, and the latest update has been read back empty.
      await page.locator('#bot-files .rail-h').waitFor({state: 'attached'});
      await page.evaluate(() => botLatestUpdate('cmo'));
      assert.deepEqual(await railOrder(page), ['Active']);
      assert.deepEqual(errors, []);
      bots[0].schedules = saved;
      await context.close();
    }

    // A phone: one column; Tasks has Active first and the same sections; the tools are under More.
    {
      const scheme = 'dark';
      const {page, errors, context} = await open(browser, {width: 390, height: 844}, {colorScheme: scheme});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      await page.locator('#conv').waitFor();
      assert.equal(await page.locator('#pane-tasks').isVisible(), false, 'chat alone');
      assert.equal(await page.locator('#bot-tool-strip').isVisible(), false, 'no room for the tools beside the name');
      if (shots) await page.screenshot({path: path.join(shots, `bot-page-phone-chat-${scheme}.png`)});
      await page.evaluate(() => { location.hash = '#/bot/cmo/tasks'; });
      await page.locator('#t-open .trow').first().waitFor();
      await page.locator('#bot-files .bf-row').first().waitFor();
      await page.locator('#bot-latest:not([hidden])').waitFor();
      assert.deepEqual(await railOrder(page), ['Active', 'Updates', 'Files', 'Recurring']);
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
      const held = {bot: 'cmo', state: 'quarantined', bot_state: 'quarantined', since, focus: '10 repeated refusals today',
        quarantine: {since, auto: true, resumes_at: resumes}};
      const {page, errors, context, cleared} = await open(browser, viewport, {}, {status: held});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const banner = page.locator('#conv-paused:not([hidden])');
      await banner.waitFor();
      const at = await page.evaluate(r => new Date(r).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}), resumes);
      assert.match(await banner.innerText(), new RegExp(`resumes by itself at ${at.replace(/\s/g, '\\s')}`));
      assert.match(await banner.innerText(), /Your messages are saved and run when it's back/);
      assert.doesNotMatch(await banner.innerText(), /under More/);
      if (shots) await page.screenshot({path: path.join(shots, `bot-quarantine-banner-${viewport.width}.png`)});
      await banner.locator('[data-quarantine-resume]').click();
      await page.locator('#conv-paused').waitFor({state: 'hidden'});
      assert.deepEqual(cleared, ['/api/v2/bots/cmo/quarantine/clear']);
      assert.deepEqual(errors, [], 'quarantine banner errors');
      await context.close();
    }
    for (const scheme of ['dark', 'light']) {
      // A usage limit offers Try now beside the warning and in the chat banner; one click clears it.
      const {page, errors, context, cleared} = await open(browser, {width: 1440, height: 900}, {colorScheme: scheme},
        {status: {bot: 'cmo', state: 'limited', bot_state: 'active', since: iso(-10 * 60e3), focus: 'claude usage limit at 01:43; retrying after 02:13 UTC'}});
      await page.goto('https://tico-ui.test/#/bot/cmo');
      const banner = page.locator('#conv-paused:not([hidden])');
      await banner.waitFor();
      assert.match(await banner.innerText(), /usage limit; your messages will run when it's back\s*Try now$/);
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
      const review = {what: 'task note', found: 'bot-coo/', preview: 'Paste this into bot-coo/tools.yaml: token=[hidden]', task: {id: 't9', title: 'Set up the tool'}};
      const {page, errors, context, cleared} = await open(browser, {width: 1440, height: 900}, {}, {bots: bots.map(b => ({...b, host: 'runner'})),
        status: {bot: 'cmo', state: 'quarantined', bot_state: 'quarantined', since, focus: 'escape: the task note reaches outside the hub',
          quarantine: {since, auto: false, resumes_at: null, review}}});
      await page.goto('https://tico-ui.test/#/bot/cmo/more');
      const card = page.locator('#bot-quarantine');
      await card.waitFor();
      assert.match(await card.innerText(), /Paused for review[\s\S]*a task note that named another bot's folder \(bot-coo\/\)[\s\S]*the checks stay on/);
      assert.match(await card.locator('.bot-quarantine-text').innerText(), /token=\[hidden\]/);
      assert.equal(await card.locator('a[href="#/task/t9"]').innerText(), 'Set up the tool');
      assert.doesNotMatch(await page.locator('#main').innerText(), /outside the hub|escape:/);
      if (shots) await page.screenshot({path: path.join(shots, 'bot-quarantine-review.png'), fullPage: true});
      assert.match(await page.locator('#bot-alert').innerText(), /Paused for review\s*Resume bot/);
      await page.locator('#bot-alert [data-quarantine-resume]').click();
      await page.locator('#bot-alert [data-quarantine-resume]').waitFor({state: 'detached'});
      assert.deepEqual(cleared, ['/api/v2/bots/cmo/quarantine/clear']);
      assert.deepEqual(errors, [], 'escape banner errors');
      await context.close();
    }
    console.log('bot page: Active, Updates, Files and Recurring; tools open under More; desktop and phone navigation; quarantine says why and when it resumes; Resume bot beside the warning and under More; Try now on a usage limit');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
