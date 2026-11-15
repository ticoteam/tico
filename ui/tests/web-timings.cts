// People's browser timings (ui/observability.ts, `vitals`; docs/observability.md "People's browser timings"): a visible
// page sends its first screen's LCP once and each screen visit's slowest interaction (INP) to this server only, when
// the screen changes or the page is hidden. A page loaded in the background sends no LCP; a browser without the API
// sends nothing and keeps working. Nothing goes to another host, with no vendor settings at all.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {chromium}: typeof import('playwright') = require('playwright');

interface Reading {
  metric: string;
  route: string;
  ms: number;
}
declare const document: Document;
declare const window: Window & typeof globalThis;

const SCRIPT = fs.readFileSync(path.join(__dirname, '..', '..', 'build', 'ui', 'observability.js'), 'utf8');
// A fictional page: a large heading to paint, and a button whose handler is busy for 120 ms (a slow interaction).
const PAGE = `<!doctype html><html><body><h1 style="font-size:64px">Acme tasks</h1>
<button id="slow" style="font-size:32px">Slow</button><button id="go">Go</button>
<script>const busy = () => { const end = performance.now() + 120; while (performance.now() < end); };
document.getElementById('slow').addEventListener('click', busy);
document.getElementById('go').addEventListener('click', () => { busy(); location.hash = '#/thread/t9'; });</script>
<script src="/observability.ts"></script>
<script>if (location.hash === '#/chat') location.replace('#/tasks'); // the app's router moves old links on after load</script></body></html>`;

async function open(browser: import('playwright').Browser, init?: () => void, landing = '#/tasks') {
  const page = await browser.newPage();
  if (init) await page.addInitScript(init);
  const sent: Reading[][] = [], hosts = new Set<string>(), errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    hosts.add(url.hostname);
    if (url.pathname === '/api/v2/web-timings') {
      assert.equal(route.request().method(), 'POST');
      sent.push((JSON.parse(route.request().postData() || '{}') as {timings: Reading[]}).timings);
      return route.fulfill({status: 202, contentType: 'application/json', body: '{"kept":1,"dropped":0}'});
    }
    if (url.pathname === '/observability.ts') return route.fulfill({contentType: 'text/javascript', body: SCRIPT});
    if (url.pathname.startsWith('/api/')) return route.fulfill({status: 401, contentType: 'application/json', body: '{}'});
    return route.fulfill({contentType: 'text/html', body: PAGE});
  });
  await page.goto('http://acme.test/' + landing);
  await page.waitForTimeout(300); // the heading's paint is the page's LCP
  return {page, sent, hosts, errors};
}
// Hides the page as a switch of tab does: the visibility state reads hidden, then visibilitychange fires.
const hide = (page: import('playwright').Page) => page.evaluate(() => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  document.dispatchEvent(new Event('visibilitychange'));
});
async function until(test: () => boolean, what: string) {
  const end = Date.now() + 5000;
  while (!test()) {
    if (Date.now() > end) throw new Error('timed out waiting for ' + what);
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    {
      const {page, sent, hosts, errors} = await open(browser);
      await page.click('#slow');
      await page.waitForTimeout(200);
      await page.evaluate(() => { window.location.hash = '#/thread/t1'; });
      await until(() => sent.length === 1, 'the /tasks visit');
      const first = sent[0]!;
      const lcp = first.filter(r => r.metric === 'lcp'), inp = first.filter(r => r.metric === 'inp');
      assert.equal(lcp.length, 1, 'one LCP for the landing screen');
      assert.equal(lcp[0]!.route, '/tasks');
      assert.ok(lcp[0]!.ms > 0 && lcp[0]!.ms <= 60000 && Number.isInteger(lcp[0]!.ms), 'LCP in whole ms within 0-60000');
      assert.equal(inp.length, 1, 'one INP for the visit');
      assert.equal(inp[0]!.route, '/tasks');
      assert.ok(inp[0]!.ms >= 100, `the visit's slowest interaction (120 ms busy) is its INP, got ${inp[0]!.ms}`);
      assert.deepEqual(Object.keys(first[0]!).sort(), ['metric', 'ms', 'route'], 'a reading is its metric, screen and time only');
      // The next screen: a template, never its address or id; hiding the page ends the visit; no second LCP.
      await page.click('#slow');
      await page.waitForTimeout(200);
      await hide(page);
      await until(() => sent.length === 2, 'the thread visit');
      assert.deepEqual(sent[1]!.map(r => [r.metric, r.route]), [['inp', '/thread/:thread']]);
      // A visit with no interaction sends nothing.
      await page.evaluate(() => { window.location.hash = '#/goals'; });
      await page.evaluate(() => { window.location.hash = '#/runs'; });
      await page.waitForTimeout(300);
      assert.equal(sent.length, 2, 'no interaction, no reading');
      assert.ok(!JSON.stringify(sent).includes('t1'), 'no id or address leaves');
      assert.deepEqual([...hosts], ['acme.test'], 'nothing goes to another host');
      assert.deepEqual(errors, []);
      await page.close();
      console.log('web timings: LCP once and INP per screen visit, to this server only: ok');
    }
    for (const [landing, screen] of [['#/task/abc-123', '/task/:task'], ['#/home', '/home'], ['#/chat', '/tasks']] as const) {
      // The busiest screens, by their templates; an old link the app moves on at load (#/chat to Tasks) is named by
      // where the person lands. A click whose handler changes the screen counts in the visit it ended.
      const {page, sent, errors} = await open(browser, undefined, landing);
      await page.click('#go');
      await until(() => sent.length === 1, 'the first visit of ' + landing);
      assert.deepEqual(sent[0]!.map(r => [r.metric, r.route]), [['lcp', screen], ['inp', screen]], landing);
      assert.ok(sent[0]![1]!.ms >= 100, `the click that changed the screen is the visit's INP, got ${sent[0]![1]!.ms}`);
      assert.ok(!JSON.stringify(sent).includes('abc-123'), 'no id leaves');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('web timings: task view, Home and a redirected old link send under their templates: ok');
    {
      // Loaded in a background tab: hidden from the start, so its LCP would be wrong and none is sent.
      const {page, sent, errors} = await open(browser, () => {
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
      });
      await hide(page);
      await page.evaluate(() => { window.location.hash = '#/chat'; });
      await page.waitForTimeout(300);
      assert.ok(sent.flat().every(r => r.metric !== 'lcp'), 'no LCP from a page loaded hidden');
      assert.deepEqual(errors, []);
      await page.close();
      console.log('web timings: a page loaded in the background sends no LCP: ok');
    }
    {
      // A browser without the API: nothing is sent and the page keeps working.
      const {page, sent, errors} = await open(browser, () => {
        Object.defineProperty(window, 'PerformanceObserver', {configurable: true, value: undefined});
      });
      await page.click('#slow');
      await page.evaluate(() => { window.location.hash = '#/chat'; });
      await hide(page);
      await page.waitForTimeout(300);
      assert.equal(sent.length, 0, 'no API, no reading');
      assert.deepEqual(errors, []);
      assert.equal(await page.evaluate(() => typeof (window as unknown as {TicoObservability?: unknown}).TicoObservability), 'object', 'the page still runs');
      await page.close();
      console.log('web timings: a browser without the API sends nothing and keeps working: ok');
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
