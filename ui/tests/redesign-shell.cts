// The redesign's shell (ui/app/redesign.ts, ui/app/strategy-budget.ts) against fixtures shaped per the redesign CONTRACT
// (fixtures/redesign-*.json, the Acme example): the sidebar's order, helpers off the Team list, old addresses
// redirected (System's to Automations' System jobs), Settings with no design switch, the Strategy tabs with no Strategy Owner
// beside them (it is a helper of the Assistant; Ask is the way in), the Budget tab drawing (its tree and edits are
// strategy-budget.cts), a task cap's 409 read as its message, and no "CEO Bot" anywhere. Phone first (390×844), then desktop.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
declare var S: {route: string; emps: {name: string; [key: string]: unknown}[]; me: {role: string; bot_admin?: boolean}; config: {update: {[key: string]: unknown}; [key: string]: unknown}};
const uiDir = path.join(__dirname, '..');
const fixture = (name: string): unknown => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const roster = fixture('redesign-roster.json') as {employees: Record<string, unknown>[]; humans: {people: Record<string, unknown>[]}};
const CAP_409 = fixture('redesign-task-cap-409.json') as {message: string};

const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page: import('playwright').Page, name: string) => { if (shots) await page.screenshot({animations: 'disabled', path: path.join(shots, `redesign-${name}.png`)}); };
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

// u5: browser globals the page's own scripts define (see ui/app); declared once per name, same type everywhere.
declare function recruitPlaces(): [string, unknown][];
declare function cpSourceChoices(): [string, unknown][];
declare function finHelper(id: string): unknown;
declare function budgetTree(data: {bots: Record<string, unknown>}): {nodes: Map<string, {parent: {key: string}}>};
declare function botRow(slug: string): {reports_to: string; org_parent: string; team: string};
declare function renderTree(): void;
declare function post(path: string, body: unknown): Promise<unknown>;
declare function v2MessageHTML(m: {id: string; sender_id: string; body: string; created_at: string; [k: string]: unknown}): string;


const PHONE_BAR = ['Team', 'Search', 'Assistant', 'Tasks', 'Updates', 'More'];   // Assistant: the Liquid Dot in the centre slot
// The sliding current-row bar (motion.js .nav-ind) is decoration, not a rail entry.
const railKeys = (page: import('playwright').Page) => page.$$eval('#side .side-scroll > :not(.nav-ind)', els => (els as HTMLElement[]).map(el =>
  el.dataset.nav || (el.classList.contains('nav-row') && (el.querySelector(':scope > [data-nav]') as HTMLElement | null)?.dataset.nav) || el.id || el.className.split(' ')[0]));
const shownRail = (page: import('playwright').Page) => page.$$eval('#side .side-scroll a.nav-link, #side .side-scroll .rd-lib a.nav-link', els =>
  [...new Set(els as HTMLElement[])].filter(el => el.checkVisibility()).map(el => {
    const c = el.cloneNode(true) as HTMLElement; c.querySelectorAll('.nav-icon, .nav-badge, svg').forEach(x => x.remove());
    return c.textContent!.replace(/\s+/g, ' ').trim();
  }));

async function open(browser: import('playwright').Browser, {route = '#/updates', viewport = PHONE, goalsDelay = 0, messageBot = false} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors: unknown[] = [], sent: unknown[] = [], goals = {asked: 0};
  page.on('pageerror', (e: Error) => errors.push(e.message));
  // Each goals tree the app has read: counted one task after its body is read, so the code awaiting it has run.
  await page.addInitScript(() => {
    (window as unknown as {__treeRead: number}).__treeRead = 0;
    const fetch0 = window.fetch;
    window.fetch = (...args) => fetch0(...args).then(r => {
      if (!String(args[0]).includes('/api/v2/goals/tree')) return r;
      const text = r.text.bind(r);
      r.text = () => text().finally(() => setTimeout(() => { (window as unknown as {__treeRead: number}).__treeRead++; }, 0));
      return r;
    });
  });
  const config = {};
  const employees = [...roster.employees], humans = structuredClone(roster.humans);
  const messaging: {bots: {bot: string; name: string; sources: {kind: string; id: string; name: string}[]}[]} = {bots: []};
  if (messageBot) {
    employees.push(
      {name: 'counsel', display_name: 'Counsel', reports_to: 'eng-lead', org_parent: 'b:eng-lead', team: 'eng', status: 'active', can_chat: true},
      {name: 'rules-owner', display_name: 'Built-in Counsel', org_parent: 'b:eng-lead', status: 'active'},
    );
    humans.people.push({id: 'legal-mailbox', name: 'Legal mailbox', email: 'legal@acme.example', inbox_bot: 'counsel', org_parent: ''});
    messaging.bots.push({bot: 'counsel', name: 'Counsel', sources: [{kind: 'slack', id: 'slack:CLEGAL', name: '#legal'}]},
      {bot: 'builder', name: 'Builder', sources: [{kind: 'slack', id: 'slack:CBUILD', name: '#build'}]});
  }
  await page.route('**/*', async r => {
    const req = r.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return r.abort();
    if (p === '/') return r.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return r.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (method !== 'GET') sent.push([method, p + url.search, req.postData() ? req.postDataJSON() : null]);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json(employees);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, config});
    if (p === '/api/humans') return json(humans);
    if (p === '/api/v2/messaging/bots') return json(messaging);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') goals.asked++;
    if (p === '/api/v2/goals/tree') return new Promise(done => setTimeout(done, goalsDelay)).then(() => json({goals: [{id: 'g-1', title: 'Ship the Acme portal', owner_id: 'company', parent_id: null, status: 'green', rank: 0, kpis: []}], owners: {}, other_kpis: [], proposals: []}));
    if (p === '/api/v2/goals/needs-you') return json({items: []});
    if (p === '/api/v2/strategy') return json({current: null, bets: [], may_edit: true});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/usage/limits') return json(fixture('redesign-usage-limits.json'));
    if (p === '/api/v2/task-caps') return method === 'GET' ? json(fixture('redesign-task-caps.json')) : json({ok: true});
    if (p.startsWith('/api/v2/usage/')) return json({ok: true});
    if (p === '/api/v2/tasks' && method === 'POST') return json(CAP_409, 409);
    if (p === '/api/v2/market/graph') return json({entities: [], edges: []});
    if (p === '/api/v2/market/entities') return json({entities: [{id: 'company/self', type: 'organization', name: 'Acme'}], total: 1});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + route);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, sent, goals};
}
const hashSoon = (page: import('playwright').Page, want: string) => page.waitForFunction(w => location.hash === w, want).then(() => true, () => false);
const noSideScroll = (page: import('playwright').Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

(async () => {
  const browser = await chromium.launch({channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string, headless: true});
  try {
    // A custom mailbox bot is an employee: keep its reporting line and nest its sources beneath it on both widths.
    for (const [tag, viewport] of [['phone', PHONE], ['desktop', DESKTOP]] as const) {
      const {page, errors} = await open(browser, {viewport, messageBot: true});
      if (tag === 'phone') await page.locator('#mobile-more').click();
      await page.locator('#tree [data-toggle="b:eng-lead"]').click();
      const counsel = page.locator('#tree [data-org="b:counsel"]');
      await counsel.waitFor({state: 'visible'});
      const manager = page.locator('#tree [data-org="b:eng-lead"]').locator('xpath=ancestor::li[1]');
      assert.equal(await manager.locator(':scope > ul > li > .noderow > [data-org="b:counsel"]').count(), 1, tag + ': Counsel reports to its manager');
      assert.equal(await counsel.getAttribute('data-helper'), null, tag + ': reading mail does not make Counsel a helper');
      const sources = counsel.locator('xpath=ancestor::li[1]').locator(':scope > .node-sources');
      await sources.getByText('#legal', {exact: true}).waitFor();
      assert.deepEqual(await sources.locator('a').allTextContents().then(rows => rows.map(s => s.replace(/^mail/, '').trim())),
        ['legal@acme.example', '#legal'], tag + ': mailbox and channel nest under Counsel');
      assert.equal(await sources.locator('a').first().getAttribute('href'), '#/messaging?bot=counsel&source=email%3Alegal%40acme.example');
      assert.equal(await page.locator('#nav-inboxes-section').isVisible(), false, tag + ': sources are not duplicated in a separate section');
      assert.equal(await page.locator('#tree a[href*="slack%3ACBUILD"]').count(), 0, tag + ': other chart bots keep Slack in Tools');
      for (const helper of ['botops', 'rules-owner', 'mail-sorter'])
        assert.equal(await page.locator(`#tree [data-org="b:${helper}"]`).count(), 0, tag + ': built-in and explicit helpers stay off Team');
      assert.deepEqual(await page.evaluate(() => ({
        recruiting: recruitPlaces().some(([id]) => id === 'counsel'),
        audience: cpSourceChoices().some(([id]) => id === 'counsel'),
        financeHelper: finHelper('bot:counsel'),
        budgetParent: budgetTree({bots: {}}).nodes.get('bot:counsel')?.parent.key,
      })), {recruiting: true, audience: true, financeHelper: false, budgetParent: 'bot:eng-lead'}, tag + ': chart pickers and spending keep Counsel on the team');
      await page.evaluate(() => { location.hash = '#/strategy/org'; });
      await page.locator('.rd-org [data-rdo-ref="bot:counsel"]').waitFor();
      assert.equal(await page.locator('.rd-org [data-rdo-ref="bot:rules-owner"]').count(), 0);
      await page.evaluate(() => { location.hash = '#/strategy/goals'; });
      await page.locator('#goal-tree [data-owner="bot:counsel"]').waitFor();
      assert.equal(await page.locator('#goal-tree [data-owner="bot:rules-owner"]').count(), 0);
      // Removing the manager leaves the bot at the root, still with its mailbox beneath it.
      await page.evaluate(() => {
        const bot = botRow('counsel'); bot.reports_to = ''; bot.org_parent = ''; bot.team = ''; renderTree();
      });
      assert.equal(await page.locator('#tree > li > .noderow > [data-org="b:counsel"]').count(), 1, tag + ': no manager means top level');
      assert.equal(await counsel.locator('xpath=ancestor::li[1]').locator(':scope > .node-sources a').count(), 2);
      assert.deepEqual(errors, [], tag + ': no page errors with a custom message bot');
      await page.close();
    }

    // Phone first: the rail, old addresses redirected, the Strategy tabs with no Strategy Owner.
    {
      const tag = 'phone';
      // The goals tree answers slowly: #/goals/g-1 is left before it arrives, as a person tapping on would.
      const {page, errors, goals} = await open(browser, {goalsDelay: 400});
      await page.locator('#tree li').first().waitFor();
      assert.deepEqual(await railKeys(page), ['side-update', 'welcome', 'tasks', 'calendar', 'threads', 'updates',
        'overview', 'rd-co', 'library', 'rd-lib', 'nav-pins',
        'side-section', 'nav-inboxes-section'], tag + ': the order');
      assert.equal(await page.getAttribute('a[data-nav="automations"]', 'href'), '#/automations', tag + ': Automations');
      for (const helper of ['b:mail-sorter', 'b:botops', 'b:librarian'])
        assert.equal(await page.locator(`#tree [data-org="${helper}"]`).count(), 0, `${tag}: ${helper} is off the Team list`);
      assert.equal(await page.locator('#tree [data-org="b:eng-lead"]').count(), 1, tag + ': the Team list keeps the chart');
      assert.equal(await page.getAttribute('a[data-nav="strategy"]', 'href'), '#/strategy/market', tag + ': Strategy opens on Market');
      assert.deepEqual(await page.locator('#mobile-nav .mobile-nav-item:not([hidden]) .mobile-nav-label').allInnerTexts(), PHONE_BAR, tag + ': the phone bar is the mock\'s');
      for (const [from, to] of [['#/tasks/routines', '#/automations'], ['#/recurring', '#/automations'], ['#/goals', '#/strategy/goals'],
        ['#/goals/g-1', '#/strategy/goals/g-1'], ['#/settings/listening', '#/listening'], ['#/strategy/market/listening', '#/listening']] as const) {
        await page.evaluate(h => { location.hash = h; }, from);
        assert.ok(await hashSoon(page, to), `${tag}: ${from} lands on ${to}`);
      }
      await page.locator('#au-root.au-lst').waitFor({state: 'attached'});
      // #au-root can still be the one #/automations drew a step before: wait for the row, not just the page.
      await page.locator('#side a[data-nav="listening"].cur').waitFor({state: 'attached', timeout: 5000}).catch(() => {});
      assert.equal(await page.locator('#side a[data-nav="listening"].cur').count(), 1, tag + ': Listening is current');
      assert.equal(await page.locator('#side a[data-nav="automations"].cur').count(), 0, tag + ': and Automations is not');
      await page.evaluate(() => { location.hash = '#/strategy/market'; });
      await page.locator('.mkt-pg #mkt-q-saying a[href="#/listening"]').waitFor();
      // Every slow tree asked for has been read and acted on: the goal left behind must not open over Market.
      await page.waitForFunction(n => (window as unknown as {__treeRead: number}).__treeRead >= n, goals.asked);
      assert.equal(await page.evaluate(() => location.hash), '#/strategy/market', tag + ': a goal left before it loaded keeps off the address');
      assert.equal(await page.locator('#goal-panel[open]').count(), 0, tag + ': and does not open');
      assert.equal(await page.locator('.mkt-pg #mkt-q-saying').count(), 1, tag + ': What are buyers saying? is a question on Market');
      await page.evaluate(() => { location.hash = '#/automations'; });
      await page.locator('#au-root, #automations-root').first().waitFor({state: 'attached'});
      assert.equal(await page.locator('#side a[data-nav="automations"].cur').count(), 1, tag + ': Automations is current');
      await page.evaluate(() => { location.hash = '#/strategy/org'; });
      await page.locator('.rd-org').waitFor();
      assert.deepEqual(await page.locator('.strat-tabs a').allInnerTexts(), ['Market', 'Strategy', 'Org', 'Goals', 'Budget'], tag + ': the tabs');
      const org = await page.locator('.rd-org').innerText();
      assert.ok(org.includes('Builder'), tag + ': Org lists the chart');
      assert.ok(!org.includes('BotOps'), tag + ': helpers are off the Org chart');
      assert.equal(await page.locator('#sbot, #sbot-fab').count(), 0, tag + ': no Strategy Owner panel or button');
      await page.evaluate(() => { location.hash = '#/strategy/budget'; });
      await page.locator('[data-bt-open="bot:builder"]').waitFor();
      assert.ok(await noSideScroll(page), tag + ': no sideways scroll');
      await shot(page, 'phone-budget');
      await page.evaluate(() => { location.hash = '#/strategy/goals'; });
      await page.locator('#goal-body').waitFor();
      assert.equal(await page.locator('#goal-manager-panel').count(), 0, tag + ': Goals has no rail of its own');
      assert.equal(await page.locator('#goal-tree .gt-sep').count(), 0, tag + ': helpers are off Goals too');
      // The roster fixture stores the old default name, which state.js shows as the Strategy Owner: it must not appear.
      assert.ok(!(await page.locator('body').innerText()).includes('CEO Bot'), tag + ': no "CEO Bot" anywhere');
      await page.locator('#mobile-more').click();
      await shot(page, 'phone-sidebar');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // Desktop: the rail reads as the mock, no Strategy Owner beside any tab, and a task cap's 409 reads as its message.
    {
      const tag = 'desktop';
      const {page, errors} = await open(browser, {viewport: DESKTOP, route: '#/strategy/budget'});
      await page.locator('[data-bt-open="bot:builder"]').waitFor();
      const shown = await shownRail(page);
      assert.deepEqual(shown, ['Tasks', 'Calendar', 'Updates', 'Company', 'Strategy', 'Growth', 'Automations', 'Listening', 'Rules', 'Library', 'Docs', 'Files', 'Skills', 'Tools', 'Meetings'], tag + ': the rail, with no Strategy Owner entry');
      assert.equal(await page.locator('#tree [data-org="b:strategy-owner"]').count(), 0, tag + ': the Strategy Owner is off the Team list');
      assert.ok(!shown.includes('Goals'), tag + ': Goals left the rail');
      assert.equal(await page.locator('#nav-inboxes-section').isVisible(), false, tag + ': Message bots hidden');
      assert.equal(await page.locator('#side a[data-nav="strategy"].cur').count(), 1, tag + ': Strategy is current');
      assert.equal(await page.locator('#sbot, #sbot-fab').count(), 0, tag + ': no Strategy Owner rail');
      assert.equal(await page.locator('#main').evaluate(el => el.classList.contains('sbot-open')), false);
      await shot(page, 'desktop-budget');
      if (shots) { await page.evaluate(() => (window as unknown as {ticoTheme: {set(t: string): void}}).ticoTheme.set('light')); await shot(page, 'desktop-budget-light'); await page.evaluate(() => (window as unknown as {ticoTheme: {set(t: string): void}}).ticoTheme.set('dark')); }

      // A task cap's 409: the message is what a person reads, in a toast's words and in the bot's chat.
      const said = await page.evaluate(() => post('/v2/tasks', {title: 'Check the Acme release'}).then(() => '', e => e.message));
      assert.equal(said, CAP_409.message, tag + ': the 409 reads as its message');
      const chat = await page.evaluate(m => {
        const div = document.createElement('div');
        div.innerHTML = v2MessageHTML({id: 'm1', sender_id: 'bot:eng-lead', body: 'Sending the review to Ana.', created_at: new Date().toISOString(),
          run: {id: 'r1', status: 'done', did: [{kind: 'refused', rule: 'task_cap', text: m}]}});
        return div.textContent.replace(/\s+/g, ' ');
      }, CAP_409.message);
      assert.ok(/refused ·\s*Ana takes 10 tasks a day from Engineering Lead and 10 are open\. Nothing was queued\./.test(chat), tag + ': the chat shows the refusal: ' + chat);

      for (const [hash, tab] of [['#/strategy/market', 'Market'], ['#/strategy', 'Strategy'], ['#/strategy/lenses', 'Strategy'], ['#/strategy/goals', 'Goals'], ['#/strategy/org', 'Org']] as const) {
        await page.evaluate(h => { location.hash = h; }, hash);
        await page.waitForFunction(t => document.querySelector('.strat-tabs a.cur')?.textContent === t, tab);
        assert.equal(await page.locator('#sbot').count(), 0, `${tag}: no Strategy Owner beside ${hash}`);
        if (hash === '#/strategy/goals') await shot(page, 'desktop-goals');
        if (hash === '#/strategy') await shot(page, 'desktop-strategy');
        if (hash === '#/strategy/org') await shot(page, 'desktop-org');
        if (hash === '#/strategy/market') await shot(page, 'desktop-market');
      }
      await page.evaluate(() => { location.hash = '#/tasks'; });
      // The roster fixture stores the old default name, which state.js shows as the Strategy Owner: it must not appear.
      assert.ok(!(await page.locator('body').innerText()).includes('CEO Bot'), tag + ': no "CEO Bot" anywhere');
      await page.evaluate(() => { location.hash = '#/settings'; });
      await page.locator('#settings-tabs').waitFor();
      assert.equal(await page.locator('#settings-redesign, #redesign-switch').count(), 0, tag + ': no design switch');
      assert.equal(await page.locator('[data-settings-tab="listening"], [data-settings-tab="rules"], [data-settings-tab="recurring"]').count(), 0, tag + ': Listening and Rules left Settings');
      await shot(page, 'desktop-settings');
      for (const old of ['#/settings/rules', '#/settings/routines', '#/settings/recurring']) {
        await page.evaluate(h => { location.hash = h; }, old);
        assert.ok(await hashSoon(page, '#/automations/system-jobs'), `${tag}: ${old} lands on System jobs`);
      }
      assert.equal(await page.locator('[data-settings-tab="system"]').count(), 0, tag + ': no System tab');
      await page.locator('#set-system-routines').waitFor();
      assert.equal(await page.locator('#set-rules-card, [data-bot-routines]').count(), 0, tag + ': no wake rules or bot routines');
      await shot(page, 'desktop-automations-system-jobs');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

  } finally {
    await browser.close();
  }
  console.log('redesign-shell: ok');
})().catch(e => { console.error(e); process.exit(1); });
