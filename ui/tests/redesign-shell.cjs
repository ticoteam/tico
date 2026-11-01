// The redesign's shell (ui/app/redesign.js, ui/app/strategy-budget.js) against fixtures shaped per the redesign CONTRACT
// (fixtures/redesign-*.json, the Acme example): the sidebar's order, helpers off the Team list, old addresses
// redirected, Settings with its System tab and no design switch, the Strategy tabs with no Strategy Bot
// beside them (it is a helper of the Assistant; Ask is the way in), the Budget tab drawing (its tree and edits are
// strategy-budget.cjs), a task cap's 409 read as its message, and no "CEO Bot" anywhere. Phone first (390×844), then desktop.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const roster = fixture('redesign-roster.json');
const CAP_409 = fixture('redesign-task-cap-409.json');

const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page, name) => { if (shots) await page.screenshot({animations: 'disabled', path: path.join(shots, `redesign-${name}.png`)}); };
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const PHONE_BAR = ['Team', 'Search', 'Ask Tico', 'Tasks', 'Updates', 'More'];   // Ask Tico: the Liquid Dot in the centre slot
// The sliding current-row bar (motion.js .nav-ind) is decoration, not a rail entry.
const railKeys = page => page.$$eval('#side .side-scroll > :not(.nav-ind)', els => els.map(el =>
  el.dataset.nav || (el.classList.contains('nav-row') && el.querySelector(':scope > [data-nav]')?.dataset.nav) || el.id || el.className.split(' ')[0]));
const shownRail = page => page.$$eval('#side .side-scroll a.nav-link, #side .side-scroll .rd-lib a.nav-link', els =>
  [...new Set(els)].filter(el => el.checkVisibility()).map(el => {
    const c = el.cloneNode(true); c.querySelectorAll('.nav-icon, .nav-badge, svg').forEach(x => x.remove());
    return c.textContent.replace(/\s+/g, ' ').trim();
  }));

async function open(browser, {route = '#/updates', viewport = PHONE, goalsDelay = 0} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], sent = [], goals = {asked: 0};
  page.on('pageerror', e => errors.push(e.message));
  // Each goals tree the app has read: counted one task after its body is read, so the code awaiting it has run.
  await page.addInitScript(() => {
    window.__treeRead = 0;
    const fetch0 = window.fetch;
    window.fetch = (...args) => fetch0(...args).then(r => {
      if (!String(args[0]).includes('/api/v2/goals/tree')) return r;
      const text = r.text.bind(r);
      r.text = () => text().finally(() => setTimeout(() => { window.__treeRead++; }, 0));
      return r;
    });
  });
  const config = {};
  await page.route('**/*', async r => {
    const req = r.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return r.abort();
    if (p === '/') return r.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return r.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (method !== 'GET') sent.push([method, p + url.search, req.postData() ? req.postDataJSON() : null]);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, config});
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') goals.asked++;
    if (p === '/api/v2/goals/tree') return new Promise(done => setTimeout(done, goalsDelay)).then(() => json({goals: [{id: 'g-1', title: 'Ship the Acme portal', owner: 'company', parent_id: null, status: 'green', rank: 0, kpis: []}], owners: {}, other_kpis: [], proposals: []}));
    if (p === '/api/v2/goals/needs-you') return json({items: []});
    if (p === '/api/v2/strategy') return json({current: null, bets: [], may_edit: true});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/listening/destinations') return json({destinations: [{key: 'leads', category: 'leads', threshold: 0.75, receiver: 'bot:release', source: 'settings'}],
      categories: [{id: 'leads', label: 'Leads'}], receivers: [{actor: 'bot:release', name: 'Release Manager'}]});
    if (p === '/api/v2/usage/limits') return json(fixture('redesign-usage-limits.json'));
    if (p === '/api/v2/task-caps') return method === 'GET' ? json(fixture('redesign-task-caps.json')) : json({ok: true});
    if (p.startsWith('/api/v2/usage/')) return json({ok: true});
    if (p === '/api/v2/tasks' && method === 'POST') return json(CAP_409, 409);
    if (p === '/api/v2/market/graph') return json({entities: [], edges: []});
    if (p === '/api/v2/market/entities') return json({entities: [], total: 0});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + route);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, sent, goals};
}
const hashSoon = (page, want) => page.waitForFunction(w => location.hash === w, want).then(() => true, () => false);
const noSideScroll = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Phone first: the rail, old addresses redirected, the Strategy tabs with no Strategy Bot.
    {
      const tag = 'phone';
      // The goals tree answers slowly: #/goals/g-1 is left before it arrives, as a person tapping on would.
      const {page, errors, goals} = await open(browser, {goalsDelay: 400});
      await page.locator('#tree li').first().waitFor();
      assert.deepEqual(await railKeys(page), ['side-update', 'overview', 'welcome', 'strategy', 'growth', 'operations', 'updates', 'tasks', 'automations',
        'library', 'rd-lib', 'rules', 'rd-rules', 'nav-pins', 'side-section', 'nav-inboxes-section'], tag + ': the order');
      assert.equal(await page.getAttribute('a[data-nav="automations"]', 'href'), '#/automations', tag + ': Automations');
      for (const helper of ['b:mail-sorter', 'b:botops', 'b:librarian'])
        assert.equal(await page.locator(`#tree [data-org="${helper}"]`).count(), 0, `${tag}: ${helper} is off the Team list`);
      assert.equal(await page.locator('#tree [data-org="b:eng-lead"]').count(), 1, tag + ': the Team list keeps the chart');
      assert.equal(await page.getAttribute('a[data-nav="strategy"]', 'href'), '#/strategy/market', tag + ': Strategy opens on Market');
      assert.deepEqual(await page.locator('#mobile-nav .mobile-nav-label').allInnerTexts(), PHONE_BAR, tag + ': the phone bar is the mock\'s');
      for (const [from, to] of [['#/tasks/routines', '#/automations'], ['#/recurring', '#/automations'], ['#/goals', '#/strategy/goals'],
        ['#/goals/g-1', '#/strategy/goals/g-1'], ['#/settings/listening', '#/strategy/market/listening']]) {
        await page.evaluate(h => { location.hash = h; }, from);
        assert.ok(await hashSoon(page, to), `${tag}: ${from} lands on ${to}`);
      }
      await page.locator('[data-rdm-settings]').click();
      await page.locator('[data-listen-dest="leads"]').waitFor();
      // Every slow tree asked for has been read and acted on: the goal left behind must not open over Listening.
      await page.waitForFunction(n => window.__treeRead >= n, goals.asked);
      assert.equal(await page.evaluate(() => location.hash), '#/strategy/market/listening', tag + ': a goal left before it loaded keeps off the address');
      assert.equal(await page.locator('#goal-panel[open]').count(), 0, tag + ': and does not open');
      assert.equal(await page.locator('.mkt-views [data-market-view="listening"][aria-current="page"]').count(), 1, tag + ': Listening is a Market view');
      await page.evaluate(() => { location.hash = '#/automations'; });
      await page.locator('#au-root, #automations-root').first().waitFor({state: 'attached'});
      assert.equal(await page.locator('#side a[data-nav="automations"].cur').count(), 1, tag + ': Automations is current');
      await page.evaluate(() => { location.hash = '#/strategy/org'; });
      await page.locator('.rd-org').waitFor();
      assert.deepEqual(await page.locator('.strat-tabs a').allInnerTexts(), ['Market', 'Strategy', 'Org', 'Goals', 'Budget'], tag + ': the tabs');
      const org = await page.locator('.rd-org').innerText();
      assert.ok(org.includes('Builder'), tag + ': Org lists the chart');
      assert.ok(!org.includes('BotOps'), tag + ': helpers are off the Org chart');
      assert.equal(await page.locator('#sbot, #sbot-fab').count(), 0, tag + ': no Strategy Bot panel or button');
      await page.evaluate(() => { location.hash = '#/strategy/budget'; });
      await page.locator('[data-bt-open="bot:builder"]').waitFor();
      assert.ok(await noSideScroll(page), tag + ': no sideways scroll');
      await shot(page, 'phone-budget');
      await page.evaluate(() => { location.hash = '#/strategy/goals'; });
      await page.locator('#goal-body').waitFor();
      assert.equal(await page.locator('#goal-manager-panel').count(), 0, tag + ': Goals has no rail of its own');
      assert.equal(await page.locator('#goal-tree .gt-sep').count(), 0, tag + ': helpers are off Goals too');
      // The roster fixture stores the old default name, which state.js shows as the Strategy Bot: it must not appear.
      assert.ok(!(await page.locator('body').innerText()).includes('CEO Bot'), tag + ': no "CEO Bot" anywhere');
      await page.locator('#mobile-more').click();
      await shot(page, 'phone-sidebar');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // Desktop: the rail reads as the mock, no Strategy Bot beside any tab, and a task cap's 409 reads as its message.
    {
      const tag = 'desktop';
      const {page, errors} = await open(browser, {viewport: DESKTOP, route: '#/strategy/budget'});
      await page.locator('[data-bt-open="bot:builder"]').waitFor();
      const shown = await shownRail(page);
      assert.deepEqual(shown, ['Overview', 'Strategy', 'Growth', 'Operations', 'Updates', 'Tasks', 'Automations', 'Library', 'Docs', 'Files', 'Meetings', 'Tools', 'Rules'], tag + ': the rail, with no Strategy Bot entry');
      assert.equal(await page.locator('#tree [data-org="b:goal-manager"]').count(), 0, tag + ': the Strategy Bot is off the Team list');
      assert.ok(!shown.includes('Goals'), tag + ': Goals left the rail');
      assert.equal(await page.locator('#nav-inboxes-section').isVisible(), false, tag + ': Message bots hidden');
      assert.equal(await page.locator('#side a[data-nav="strategy"].cur').count(), 1, tag + ': Strategy is current');
      assert.equal(await page.locator('#sbot, #sbot-fab').count(), 0, tag + ': no Strategy Bot rail');
      assert.equal(await page.locator('#main').evaluate(el => el.classList.contains('sbot-open')), false);
      await shot(page, 'desktop-budget');

      // A task cap's 409: the message is what a person reads, in a toast's words and in the bot's chat.
      const said = await page.evaluate(() => post('/v2/tasks', {title: 'Check the Acme release'}).then(() => '', e => e.message));
      assert.equal(said, CAP_409.message, tag + ': the 409 reads as its message');
      const chat = await page.evaluate(m => {
        const div = document.createElement('div');
        div.innerHTML = v2MessageHTML({id: 'm1', from_actor: 'bot:eng-lead', body: 'Sending the review to Ana.', created: new Date().toISOString(),
          run: {id: 'r1', status: 'done', did: [{kind: 'refused', rule: 'task_cap', text: m}]}});
        return div.textContent.replace(/\s+/g, ' ');
      }, CAP_409.message);
      assert.ok(/refused ·\s*Ana takes 10 tasks a day from Engineering Lead and 10 are open\. Nothing was queued\./.test(chat), tag + ': the chat shows the refusal: ' + chat);

      for (const [hash, tab] of [['#/strategy/market/listening', 'Market'], ['#/strategy', 'Strategy'], ['#/strategy/lenses', 'Strategy'], ['#/strategy/goals', 'Goals'], ['#/strategy/org', 'Org']]) {
        await page.evaluate(h => { location.hash = h; }, hash);
        await page.waitForFunction(t => document.querySelector('.strat-tabs a.cur')?.textContent === t, tab);
        assert.equal(await page.locator('#sbot').count(), 0, `${tag}: no Strategy Bot beside ${hash}`);
        if (hash === '#/strategy/goals') await shot(page, 'desktop-goals');
        if (hash === '#/strategy') await shot(page, 'desktop-strategy');
        if (hash === '#/strategy/org') await shot(page, 'desktop-org');
        if (hash === '#/strategy/market/listening') await shot(page, 'desktop-listening');
      }
      await page.evaluate(() => { location.hash = '#/tasks'; });
      // The roster fixture stores the old default name, which state.js shows as the Strategy Bot: it must not appear.
      assert.ok(!(await page.locator('body').innerText()).includes('CEO Bot'), tag + ': no "CEO Bot" anywhere');
      await page.evaluate(() => { location.hash = '#/settings'; });
      await page.locator('#settings-tabs').waitFor();
      assert.equal(await page.locator('#settings-redesign, #redesign-switch').count(), 0, tag + ': no design switch');
      assert.equal(await page.locator('[data-settings-tab="listening"], [data-settings-tab="rules"], [data-settings-tab="recurring"]').count(), 0, tag + ': Listening and Rules left Settings');
      await shot(page, 'desktop-settings');
      for (const old of ['#/settings/rules', '#/settings/routines', '#/settings/recurring']) {
        await page.evaluate(h => { location.hash = h; }, old);
        assert.ok(await hashSoon(page, '#/settings/system'), `${tag}: ${old} lands on System`);
      }
      assert.equal(await page.locator('[data-settings-tab="system"][aria-selected="true"], [data-settings-tab="system"].cur, [data-settings-tab="system"][aria-current]').count(), 1, tag + ': System is the tab shown');
      assert.equal(await page.locator('#settings-system #set-rules-card, #settings-system [data-bot-routines]').count(), 0, tag + ': no wake rules or bot routines');
      await shot(page, 'desktop-settings-system');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

  } finally {
    await browser.close();
  }
  console.log('redesign-shell: ok');
})().catch(e => { console.error(e); process.exit(1); });
