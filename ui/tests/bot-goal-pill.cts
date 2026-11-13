// The goal in a bot's header never draws as a sliver (#302): from a 1200px header down to 360px it is a pill at
// least 140px wide with a readable title and its icon inside it, or it is not in the header and the Goals card
// leads More. Resizing around the switch point toggles it once. "Set goal" follows the same rule. Both themes.
// Fixtures only, no network. TICO_SCREENSHOT_DIR keeps pictures of the header at 1200, 560 and 430px.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
import type {Browser, LaunchOptions, Page} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RoutesSupport from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RoutesSupport.Api;

const path = require('node:path') as typeof import('node:path');
const shots = process.env.TICO_SCREENSHOT_DIR;
const NAME = 'Skills Product Owner', GOAL = 'Skills directory has 20 approved skills by December';
const BOT = {name: 'spo', display_name: NAME, org_parent: '', host: 'keeper', status: 'active', can_chat: true, runtime: 'codex',
  users: [{id: 'ana', name: 'Ana'}], schedules: []};

async function open(browser: Browser, scheme: 'light' | 'dark', goals: unknown[]) {
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block', colorScheme: scheme});
  await context.addInitScript((theme: string) => { try { localStorage.setItem('tico.theme', theme); } catch {} }, scheme);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const vendor = p.match(/^\/vendor\/((?:fonts\/)?[\w.-]+)$/);
    if (vendor && fs.existsSync(uiFile('vendor/' + vendor[1]))) return route.fulfill({path: uiFile('vendor/' + vendor[1])});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/employees') return json([BOT]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'spo', state: 'idle', bot_state: 'active'}]});
    if (p === '/api/v2/bots/spo') return json({display_name: NAME, access: {}, cant_run: null});
    if (p === '/api/v2/goals') return json({goals: q.get('status') ? [] : goals, chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: q.get('chat_with') ? [{id: 'c-spo', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:spo']}] : []});
    if (p === '/api/v2/conversations/c-spo/snapshot') return json({messages: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/spo');
  await page.locator(`#bot-goal[data-status="${goals.length ? 'set' : 'none'}"] .bot-goal-main`).waitFor();
  // Count every time the header drops or brings back the goal.
  await page.evaluate(() => {
    const top = document.querySelector('#bot-top') as HTMLElement, w = window as unknown as {goalFlips: number};
    w.goalFlips = 0;
    let off = top.classList.contains('no-goal');
    new MutationObserver(() => { const now = top.classList.contains('no-goal'); if (now !== off) { off = now; w.goalFlips++; } })
      .observe(top, {attributes: true, attributeFilter: ['class']});
  });
  return {page, errors, context};
}

interface Shot { width: number; shown: boolean; pill: number; title: number; iconIn: boolean; whole: boolean; twenty: boolean; overflow: boolean }
// Set the header's width, let the observer run, and measure the goal.
const at = (page: Page, width: number): Promise<Shot> => page.evaluate(async (width: number) => {
  const top = document.querySelector('#bot-top') as HTMLElement;
  top.style.width = width + 'px'; top.style.boxSizing = 'border-box';
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const btn = document.querySelector('#bot-goal .bot-goal-main') as HTMLElement;
  const title = btn.querySelector('.bot-goal-title') as HTMLElement, icon = btn.querySelector('.bot-goal-i') as HTMLElement;
  const shown = btn.getClientRects().length > 0, b = btn.getBoundingClientRect(), i = icon.getBoundingClientRect();
  const range = document.createRange(), text = title.firstChild as Text;
  range.setStart(text, 0); range.setEnd(text, Math.min(20, text.length));
  return {width: top.clientWidth, shown, pill: b.width, title: title.clientWidth,
    iconIn: i.left >= b.left - 0.5 && i.right <= b.right + 0.5 && i.top >= b.top - 0.5 && i.bottom <= b.bottom + 0.5,
    whole: title.scrollWidth <= title.clientWidth, twenty: range.getBoundingClientRect().width <= title.clientWidth + 0.5,
    overflow: top.scrollWidth > top.clientWidth + 1};
}, width);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    for (const scheme of ['light', 'dark'] as const) {
      const {page, errors, context} = await open(browser, scheme, [{id: 'g-skills', title: GOAL, owner_id: 'bot:spo', status: 'active', rank: 1}]);
      // AC-1: a wide header shows the pill, its icon and at least 20 characters of the title.
      const wide = await at(page, 1200);
      assert(wide.shown && wide.iconIn && wide.twenty, `${scheme}: 1200px header shows the goal ${JSON.stringify(wide)}`);
      // AC-2: every width down to 360px: no goal, or a pill of 140px or more, a title box of 60px or more and the
      // icon inside the pill. The header never overflows.
      const sweep: Shot[] = [];
      for (let w = 1200; w >= 360; w -= 20) sweep.push(await at(page, w));
      for (const s of sweep) {
        assert(!s.shown || (s.pill >= 140 - 0.5 && s.title >= 60 && s.iconIn), `${scheme}: no sliver at ${s.width}px ${JSON.stringify(s)}`);
        assert(!s.overflow, `${scheme}: header fits at ${s.width}px`);
      }
      assert(sweep.some(s => !s.shown) && sweep.some(s => s.shown && s.pill < 300), scheme + ': the sweep reaches both the cut pill and no goal');
      if (shots) for (const w of [1200, 560, 430]) {
        await at(page, w);
        await page.locator('#bot-top').screenshot({path: path.join(shots, `bot-goal-${scheme}-${w}.png`)});
      }
      // AC-3: Chris's 430px header drops the goal, and Goals is More's first card.
      const chris = await at(page, 430);
      assert.equal(chris.shown, false, scheme + ': no goal in a 430px header');
      const firstCard = await page.evaluate(() => [...(document.querySelector('#pane-more') as HTMLElement).children as unknown as HTMLElement[]]
        .find(el => el.matches('section.card'))?.id);
      assert.equal(firstCard, 'bot-goals-card', scheme + ': Goals leads More');
      // AC-4: find the switch point coming down, then jiggle ±10px around it: one flip at most.
      await at(page, 1200);
      let edge = 1200;
      while (edge > 360 && (await at(page, edge - 1)).shown) edge--;
      await at(page, edge + 10);
      await page.evaluate(() => { (window as unknown as {goalFlips: number}).goalFlips = 0; });
      for (const w of [edge + 10, edge, edge - 10, edge + 2, edge + 10, edge - 3, edge + 10, edge]) await at(page, w);
      assert((await page.evaluate(() => (window as unknown as {goalFlips: number}).goalFlips)) <= 1, `${scheme}: one flip around ${edge}px`);
      // Back with room to spare, the goal returns to its pill and the Goals card goes home.
      assert((await at(page, 1200)).shown, scheme + ': the goal returns');
      const home = await page.evaluate(() => (document.querySelector('#bot-goals-card') as HTMLElement).previousElementSibling?.id);
      assert.equal(home, 'bot-quarantine-host', scheme + ': the Goals card back in its place');
      assert.deepEqual(errors, [], scheme + ': page errors');
      await context.close();
    }
    // AC-5: no goal: "Set goal" is whole with its icon inside, or not in the header.
    for (const scheme of ['light', 'dark'] as const) {
      const {page, errors, context} = await open(browser, scheme, []);
      const sweep: Shot[] = [];
      for (let w = 1200; w >= 360; w -= 20) sweep.push(await at(page, w));
      for (const s of sweep) assert(!s.shown || (s.whole && s.iconIn), `${scheme}: Set goal whole at ${s.width}px ${JSON.stringify(s)}`);
      assert(sweep[0]!.shown && !sweep.at(-1)!.shown, scheme + ': Set goal shows wide and leaves narrow');
      assert.deepEqual(errors, [], scheme + ': page errors');
      await context.close();
    }
    console.log('bot goal pill: a readable pill or no goal at every header width; Goals leads More; one flip at the switch; Set goal too; light and dark');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
