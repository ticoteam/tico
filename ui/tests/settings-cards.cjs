// Every Settings card starts with one plain line and has an Ask BotOps link (settings stage 2, criteria 22 and 23).
// Fixtures only.
//  - in every group, for the owner and for a member, each card shown has its first line right under its heading and
//    an Ask BotOps link; a card Settings draws that has neither is listed, so a new card can't miss them;
//  - Task settings is the Tasks Owner's card: it has the link, and its content stays its own;
//  - Ask BotOps opens the person's BotOps chat with a draft that names the card's setting and where it is, and sends
//    nothing: no write leaves the page.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const GROUPS = ['you', 'people', 'bots', 'models', 'computers', 'limits', 'automation', 'tasks', 'connections', 'policies', 'health'];
const OWN_LINE = ['set-task-settings-card'];   // the Tasks Owner's card: no line from Settings

async function open(browser, {role = 'owner', hash = '#/settings', phone = false} = {}) {
  const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 900}, serviceWorkers: 'block'});
  const page = await context.newPage();
  const errors = [], dialogs = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => { dialogs.push(d.message()); void d.dismiss(); });
  page.on('request', req => { if (req.method() !== 'GET' && /\/api\//.test(req.url())) writes.push(new URL(req.url()).pathname); });
  const admin = role === 'owner';
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: admin ? 'ana' : 'cara', role: admin ? 'owner' : 'human', name: admin ? 'Ana' : 'Cara',
      email: admin ? 'ana@acme.example' : 'cara@acme.example', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'cara', name: 'Cara'}], teams: {}});
    if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', status: 'active', host: 'keeper', can_chat: true,
      can_manage: admin, operator: 'ana', users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: 1}, {name: 'ops', display_name: 'Ops', status: 'active', host: 'keeper', can_chat: true,
      can_manage: admin, operator: 'ana', users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: 1}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [],
      schedules: [{id: 'r1', employee: 'ops', title: 'Morning brief', cron: '30 7 * * 1-5', timezone: 'America/Los_Angeles',
        enabled: true, active: true, next: new Date(Date.now() + 3600e3).toISOString()}]});
    if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle'}]});
    // What the tabs the links open read first, empty.
    if (p === '/api/v2/listening/destinations') return json({destinations: [], categories: [], receivers: []});
    if (p === '/api/v2/billing') return json({state: 'no_gateway'});
    if (p === '/api/v2/ceo-bot') return json(null, 404);
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator('#settings-tabs').waitFor();
  return {page, context, errors, dialogs, writes};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const role of ['owner', 'member']) {
      const {page, context, errors, writes} = await open(browser, {role});
      const groups = await page.locator('#settings-tabs [data-settings-tab]').evaluateAll(els => els.map(el => el.dataset.settingsTab));
      assert.deepEqual(groups.filter(g => !GROUPS.includes(g)), []);
      let seen = 0;
      for (const group of groups) {
        await page.locator(`#settings-tabs [data-settings-tab="${group}"]`).click();
        await page.waitForTimeout(500);   // the group's cards draw as their data loads
        const cards = await page.evaluate(g => {
          const pane = document.querySelector(`[data-settings-group="${g}"]`);
          const shown = el => el.getClientRects().length > 0;
          return [...pane.querySelectorAll('section.card, #hl-page')].filter(shown).filter(el => !el.closest('dialog')).map(el => {
            const line = el.querySelector(':scope > .settings-card-line');
            return {id: el.id || el.className, decorated: el.hasAttribute('data-settings-card'),
              ask: !!el.querySelector(':scope > header > [data-settings-ask], :scope > .settings-card-line > [data-settings-ask]'),
              line: line ? line.textContent.replace('Ask BotOps', '').trim() : '',
              first: line ? (line.previousElementSibling === null || line.previousElementSibling.tagName === 'HEADER') : false};
          });
        }, group);
        assert.deepEqual(cards.filter(c => !c.decorated).map(c => c.id), [], `${role} ${group}: every card is listed`);
        for (const card of cards) {
          assert.ok(card.ask, `${role} ${group} ${card.id}: Ask BotOps`);
          if (OWN_LINE.includes(card.id)) continue;
          assert.ok(card.line.length > 20 && /\.$/.test(card.line.replace(/Open Tools & Credentials$/, '.')), `${role} ${group} ${card.id}: a plain first line (${card.line})`);
          assert.ok(card.first, `${role} ${group} ${card.id}: the line comes first, under the heading`);
        }
        seen += cards.length;
      }
      assert.ok(seen >= (role === 'owner' ? 25 : 12), `${role}: ${seen} cards checked`);
      // A card redrawn by its module gets its line and link back.
      await page.locator('#settings-tabs [data-settings-tab="models"]').click();
      await page.evaluate(() => { const card = document.querySelector('#settings-models'); card.querySelector('.settings-card-line').remove(); card.querySelector('[data-settings-ask]').remove(); });
      await page.locator('#settings-models > .settings-card-line').waitFor();
      await page.locator('#settings-models > header > [data-settings-ask]').waitFor();
      // Ask BotOps: the chat opens with the draft in the box, and nothing is sent.
      const before = writes.length;
      await page.locator('#settings-models > header > [data-settings-ask]').click();
      await page.waitForFunction(() => location.hash === '#/bot/botops/chat');
      const box = page.locator('.p-text').first();
      await page.waitForFunction(() => /company models/.test(document.querySelector('.p-text')?.value || ''));
      assert.equal(await box.inputValue(), 'Help me change the company models in Settings > AI models: ');
      await page.waitForTimeout(300);
      assert.deepEqual(writes.slice(before), [], 'nothing is sent');
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('settings-cards: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
