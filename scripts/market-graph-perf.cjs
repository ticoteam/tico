// Companion to market-graph-perf.py. Real Market code, canvas and CSS, with synthetic API bodies.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');
const dir = path.join(root, '.market-perf');
const data = JSON.parse(fs.readFileSync(path.join(dir, 'fixtures.json')));

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined});
  const result = {};
  try {
    for (const mode of process.argv.includes('--after-only') ? ['after'] : ['before', 'after']) {
      const page = await browser.newPage({viewport: {width: 1280, height: 860}});
      page.setDefaultTimeout(120000);
      const errors = [], requests = [];
      page.on('request', request => requests.push(request.url()));
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url()), p = url.pathname;
        if (p === '/') return route.fulfill({contentType: 'text/html', body: '<main id="main"></main>'});
        let key = null, body = {};
        if (p === '/api/v2/market/graph') key = url.searchParams.get('focus') === 'company/self' ? 'graph' : 'focus';
        if (p === '/api/v2/market/edges') key = 'edges';
        if (p === '/api/v2/market/entities') key = url.searchParams.has('limit') ? url.searchParams.get('q') ? 'search' : url.searchParams.get('offset') === '50' ? 'next_list' : 'list' : 'entities';
        if (p.startsWith('/api/v2/market/entities/')) key = 'show';
        if (p === '/api/company-docs') body = {documents: [{id: 'market/overview', title: 'Overview'}]};
        if (p.startsWith('/api/company-docs/')) body = {content: '<h1>Overview</h1>'};
        if (key) {
          body = data[key];
          await new Promise(resolve => setTimeout(resolve, data.api[key].ms));
        }
        return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      });
      await page.goto('http://market.test');
      await page.addStyleTag({content: ['tokens.css', 'hub-v2.css'].map(f => fs.readFileSync(path.join(root, 'ui/styles', f), 'utf8')).join('\n') + '\n#main{height:860px}.market-shell{height:100%}'});
      await page.evaluate(() => {
        window.S = {route: '#/market', status: {}, me: {role: 'owner'}};
        window.$ = selector => document.querySelector(selector);
        window.esc = text => String(text).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
        window.get = async url => { const response = await fetch('/api' + url); if (!response.ok) throw Error('API failed'); return response.json(); };
        window.safeMd = text => text;
        window.toast = text => { throw Error(text); };
        window.metrics = {durations: [], firstDraw: null, draws: 0};
        const raf = window.requestAnimationFrame;
        window.requestAnimationFrame = callback => raf.call(window, ts => {
          const start = performance.now();
          callback(ts);
          if (callback.name === 'draw') {
            metrics.durations.push(performance.now() - start);
            metrics.draws++;
            metrics.firstDraw ??= performance.now() - metrics.start;
          }
        });
      });
      await page.addScriptTag({content: fs.readFileSync(mode === 'before' ? path.join(dir, 'before.js') : path.join(root, 'ui/market-page.js'), 'utf8')});
      await page.evaluate(() => {
        window.addEventListener('hashchange', () => { S.route = location.hash; void pageMarket(); });
        metrics.start = performance.now(); void pageMarket();
      });
      await page.waitForFunction(() => metrics.draws >= 15);
      const measured = await page.evaluate(() => {
        const sorted = metrics.durations.slice(0, 15).sort((a, b) => a - b);
        return {first_draw_ms: +metrics.firstDraw.toFixed(2), frame_median_ms: +sorted[7].toFixed(2),
          frame_max_ms: +Math.max(...sorted).toFixed(2), list_rows: document.querySelectorAll('#market-index [data-market-note]').length};
      });
      result[mode] = measured;
      if (mode === 'after') {
        // Exercise an entity absent from the opening subset, its evidence, citations, and reset.
        await page.locator('[data-market-next]').click();
        await page.locator(`#market-index a[data-market-note="${data.next_list.entities[0].id}"]`).waitFor();
        assert.equal(await page.locator('#market-index [data-market-note]').count(), 50);
        const target = data.search.entities[0];
        await page.locator('#market-filter').fill(target.name);
        await page.locator(`#market-index a[data-market-note="${target.id}"]`).waitFor();
        await page.locator(`#market-index a[data-market-note="${target.id}"]`).click();
        await page.locator('#market-read h1', {hasText: target.name}).waitFor();
        assert.equal(await page.locator('#market-read h1').textContent(), target.name);
        assert.ok(await page.locator('.market-quote').count());
        await page.evaluate(id => MARKET_VIEW.cite([id]), target.id);
        await page.locator('#market-reset').click();
        await page.waitForTimeout(250);
        const canvas = await page.locator('#market-canvas').boundingBox();
        await page.mouse.click(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
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
        await page.waitForFunction(n => metrics.draws > n, hidden);
      }
      if (mode === 'after') await page.screenshot({path: path.join(dir, 'after.png')});
      assert.deepEqual(errors, []);
      await page.close();
    }
    process.stdout.write(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
