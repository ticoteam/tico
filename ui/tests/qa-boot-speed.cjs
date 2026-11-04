// QA only (not for main): time a cold and a returning open of #/tasks with a hub that answers each API read in
// API_MS and each file in FILE_MS. TREE (default: this checkout) picks whose UI is served, so main and a branch can be
// compared on one runner. Prints medians: content = first of the kept screen or the fresh heading.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const tree = process.env.TREE || path.join(__dirname, '../..');
const {html, uiFile} = require(path.join(tree, 'ui/tests/support/page.cjs'));
const {unrouted} = require(path.join(tree, 'ui/tests/support/routes.cjs'));
const API_MS = Number(process.env.API_MS || 200), FILE_MS = Number(process.env.FILE_MS || 40), RUNS = Number(process.env.RUNS || 5);
const wait = ms => new Promise(r => setTimeout(r, ms));
const DATA = {
  '/api/me': {id: 'ana', email: 'ana@acme.example', role: 'owner', cloud: true, owner_id: 'ana', config: {company_name: 'Acme'}},
  '/api/employees': [{name: 'ops', display_name: 'Ops', status: 'active', host: 'keeper', can_chat: true}],
  '/api/humans': {people: [{id: 'ana', name: 'Ana'}], groups: []},
  '/api/status': {cloud: true, keeper_alive: true, health_issues: []},
  '/api/v2/status': {bots: [{bot: 'ops', state: 'idle', open_tasks: 0}]},
  '/api/v2/needs-you': {items: []},
  '/api/v2/task-types': {types: []},
};
const answer = p => p in DATA ? DATA[p] : unrouted(p);

async function context(browser) {
  const ctx = await browser.newContext({serviceWorkers: 'block', viewport: {width: 1280, height: 820}});
  await ctx.addInitScript(() => {
    window.EventSource = undefined; window.ticoBootSnap = true;
    const seen = window.__qa = {};
    const check = () => {
      const now = performance.now();
      if (!seen.snap && document.querySelector('[data-boot-snap]')) seen.snap = now;
      if (!seen.fresh && [...document.querySelectorAll('#main :is(h1,h2,h3,[role=heading])')].some(h => h.textContent.trim() === 'Tasks')) seen.fresh = now;
    };
    new MutationObserver(check).observe(document, {subtree: true, childList: true, attributes: true});
  });
  await ctx.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/') { await wait(FILE_MS); return route.fulfill({contentType: 'text/html', body: html}); }
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) { await wait(FILE_MS); return route.fulfill({path: file}); }
    }
    await wait(API_MS);
    if (p === '/api/v2/boot') {
      const reads = {};
      for (const r of ['/api/employees', '/api/me', '/api/humans', '/api/status', '/api/v2/status', '/api/v2/needs-you']) reads[r] = {status: 200, body: answer(r)};
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({reads})});
    }
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(answer(p))});
  });
  return ctx;
}
const shown = page => page.waitForFunction(() => window.__qa.fresh, null, {timeout: 30000}).then(() => page.evaluate(() => ({...window.__qa})));
const median = xs => { const s = [...xs].sort((a, b) => a - b); return Math.round(s[Math.floor(s.length / 2)]); };

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  const cold = [], warm = [], warmFresh = [];
  try {
    for (let i = 0; i < RUNS; i++) {
      const ctx = await context(browser);
      const page = await ctx.newPage();
      await page.goto('http://acme.test/#/tasks');
      const a = await shown(page);
      cold.push(Math.min(a.fresh, a.snap ?? Infinity));
      await wait(2600);   // the page settles and keeps its screen
      await page.goto('about:blank');
      await page.goto('http://acme.test/#/tasks');
      const b = await shown(page);
      warm.push(Math.min(b.fresh, b.snap ?? Infinity)); warmFresh.push(b.fresh);
      await ctx.close();
    }
    console.log(JSON.stringify({tree: path.basename(tree), api_ms: API_MS, file_ms: FILE_MS, runs: RUNS,
      cold_content_ms: median(cold), returning_content_ms: median(warm), returning_fresh_ms: median(warmFresh)}));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
