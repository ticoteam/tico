// Companion to market-graph-perf.py. Real Market code, canvas and CSS, with synthetic API bodies.
import type {Route} from 'playwright';
const {chromium}: typeof import('playwright') = require('playwright');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const root = path.join(__dirname, '..');
const dir = path.join(root, '.market-perf');

interface Fixtures {
  api: Record<string, {ms: number}>;
  search: {entities: {id: string; name: string}[]};
  next_list: {entities: {id: string}[]};
  [key: string]: unknown;
}
interface Measured {
  first_draw_ms: number;
  frame_median_ms: number;
  frame_max_ms: number;
  list_rows: number;
}
interface Metrics {
  durations: number[];
  draws: number;
  firstDraw: number | null;
  start: number;
}
// Page-side globals the Market page and this script share.
declare const metrics: Metrics;
declare const S: {route: string};
declare const MARKET_VIEW: {cite(ids: string[]): void};
declare function pageMarket(): Promise<void>;
// The page's window as the evaluate callbacks set it up. File-local, not a global augmentation: every script under
// scripts/ shares one program (scripts/tsconfig.json).
declare const window: Window & typeof globalThis & {
  S: {route: string; status: object; me: {role: string}};
  $: (selector: string) => Element | null;
  esc: (text: unknown) => string;
  get: (url: string) => Promise<unknown>;
  safeMd: (text: string) => string;
  toast: (text: string) => never;
  metrics: Omit<Metrics, 'start'>;
};

const data: Fixtures = JSON.parse(fs.readFileSync(path.join(dir, 'fixtures.json')) as unknown as string);

(async () => {
  // Playwright reads an undefined channel as none (its bundled Chromium); the type has no room for undefined.
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  const result: Record<string, Measured> = {};
  try {
    for (const mode of process.argv.includes('--after-only') ? ['after'] : ['before', 'after']) {
      const page = await browser.newPage({viewport: {width: 1280, height: 860}});
      page.setDefaultTimeout(120000);
      const errors: string[] = [], requests: string[] = [];
      page.on('request', (request: import('playwright').Request) => requests.push(request.url()));
      page.on('pageerror', (e: Error) => errors.push(e.message));
      await page.route('**/*', async (route: Route) => {
        const url = new URL(route.request().url()), p = url.pathname;
        if (p === '/') return route.fulfill({contentType: 'text/html', body: '<main id="main"></main>'});
        let key: string | null = null, body: unknown = {};
        if (p === '/api/v2/market/graph') key = url.searchParams.get('focus') === 'company/self' ? 'graph' : 'focus';
        if (p === '/api/v2/market/edges') key = 'edges';
        if (p === '/api/v2/market/entities') key = url.searchParams.has('limit') ? url.searchParams.get('q') ? 'search' : url.searchParams.get('offset') === '50' ? 'next_list' : 'list' : 'entities';
        if (p.startsWith('/api/v2/market/entities/')) key = 'show';
        if (p === '/api/company-docs') body = {documents: [{id: 'market/overview', title: 'Overview'}]};
        if (p.startsWith('/api/company-docs/')) body = {content: '<h1>Overview</h1>'};
        if (key) {
          body = data[key];
          await new Promise(resolve => setTimeout(resolve, data.api[key]!.ms));
        }
        return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      });
      await page.goto('http://market.test');
      await page.addStyleTag({content: ['tokens.css', 'hub-v2.css'].map((f: string) => fs.readFileSync(path.join(root, 'ui/styles', f), 'utf8')).join('\n') + '\n#main{height:860px}.market-shell{height:100%}'});
      await page.evaluate(() => {
        window.S = {route: '#/strategy/market', status: {}, me: {role: 'owner'}};
        window.$ = (selector: string) => document.querySelector(selector);
        window.esc = (text: unknown) => String(text).replace(/[&<>"']/g, (ch: string) => (({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[ch]!));
        window.get = async (url: string) => { const response = await fetch('/api' + url); if (!response.ok) throw Error('API failed'); return response.json(); };
        window.safeMd = (text: string) => text;
        window.toast = (text: string) => { throw Error(text); };
        window.metrics = {durations: [], firstDraw: null, draws: 0};
        const raf = window.requestAnimationFrame;
        window.requestAnimationFrame = (callback: (ts: number) => void) => raf.call(window, (ts: number) => {
          const start = performance.now();
          callback(ts);
          if (callback.name === 'draw') {
            metrics.durations.push(performance.now() - start);
            metrics.draws++;
            metrics.firstDraw ??= performance.now() - metrics.start;
          }
        });
      });
      // The map's page and its graph (ui/market-page-graph.ts) are two scripts; the baseline's are already one.
      const scripts = mode === 'before' ? [path.join(dir, 'before.js')] : ['market-page.js', 'market-page-graph.js'].map(name => path.join(root, 'build/ui', name));
      for (const file of scripts) await page.addScriptTag({content: fs.readFileSync(file, 'utf8')});
      await page.evaluate(() => {
        window.addEventListener('hashchange', () => { S.route = location.hash; void pageMarket(); });
        metrics.start = performance.now(); void pageMarket();
      });
      await page.waitForFunction(() => metrics.draws >= 15);
      const measured = await page.evaluate<Measured>(() => {
        const sorted = metrics.durations.slice(0, 15).sort((a, b) => a - b);
        return {first_draw_ms: +metrics.firstDraw!.toFixed(2), frame_median_ms: +sorted[7]!.toFixed(2),
          frame_max_ms: +Math.max(...sorted).toFixed(2), list_rows: document.querySelectorAll('#market-index [data-market-note]').length};
      });
      result[mode] = measured;
      if (mode === 'after') {
        // Exercise an entity absent from the opening subset, its evidence, citations, and reset.
        await page.locator('[data-market-next]').click();
        await page.locator(`#market-index a[data-market-note="${data.next_list.entities[0]!.id}"]`).waitFor();
        assert.equal(await page.locator('#market-index [data-market-note]').count(), 50);
        const target = data.search.entities[0]!;
        await page.locator('#market-filter').fill(target.name);
        await page.locator(`#market-index a[data-market-note="${target.id}"]`).waitFor();
        await page.locator(`#market-index a[data-market-note="${target.id}"]`).click();
        await page.locator('#market-read h1', {hasText: target.name}).waitFor();
        assert.equal(await page.locator('#market-read h1').textContent(), target.name);
        assert.ok(await page.locator('.market-quote').count());
        await page.evaluate((id: string) => MARKET_VIEW.cite([id]), target.id);
        await page.locator('#market-reset').click();
        await page.waitForTimeout(250);
        const canvas = await page.locator('#market-canvas').boundingBox();
        await page.mouse.click(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2);
        await page.waitForFunction(() => location.hash.includes('company%2Fself'));
        await page.waitForTimeout(250);
        assert.ok(requests.some(url => url.includes('/market/graph?') && url.includes('focus=company%2Fself') && url.includes('fill=false')));
        await page.locator('#market-reset').click();
        // Settled graphs schedule no animation; hidden tabs schedule no ticks or paint.
        await page.waitForFunction(() => metrics.draws > 30);
        await page.waitForTimeout(3500);
        const idle = await page.evaluate(() => metrics.draws);
        await page.waitForTimeout(150);
        assert.equal(await page.evaluate(() => metrics.draws), idle);
        await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange')); });
        const hidden = await page.evaluate(() => metrics.draws);
        await page.mouse.wheel(0, -50);
        await page.waitForTimeout(100);
        assert.equal(await page.evaluate(() => metrics.draws), hidden);
        await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange')); });
        await page.waitForFunction((n: number) => metrics.draws > n, hidden);
      }
      if (mode === 'after') await page.screenshot({path: path.join(dir, 'after.png')});
      assert.deepEqual(errors, []);
      await page.close();
    }
    process.stdout.write(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch((error: unknown) => { console.error(error); process.exitCode = 1; });
