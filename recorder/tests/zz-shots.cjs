const {chromium} = require(process.cwd() + '/node_modules/playwright');
const path = require('node:path');
(async () => {
  const b = await chromium.launch();
  for (const scheme of ['light', 'dark']) for (const q of ['scene=update-move', 'scene=library']) {
    const c = await b.newContext({colorScheme: scheme, viewport: {width: 1040, height: 690}});
    const p = await c.newPage();
    const root = path.resolve('recorder/ui');
    await p.route('**/*', r => r.fulfill({path: path.resolve(root, '.' + new URL(r.request().url()).pathname)}));
    await p.goto('http://recorder.example/preview/index.html?' + q);
    await p.waitForTimeout(500);
    if (q.includes('move')) await p.screenshot({path: `.remote-runs/banner-${scheme}.png`});
    await p.locator('.topbar [data-act="settings"]').last().click();
    if (!q.includes('move')) { await p.locator('#updates button').click(); await p.waitForTimeout(250); }
    await p.waitForTimeout(400);
    const n1 = await p.evaluate(() => { window.__n = 0; new MutationObserver(() => window.__n++).observe(document.getElementById('r-page'), {childList: true}); return 0; });
    await p.waitForTimeout(1500);
    console.log('rerenders in 1.5s', q, scheme, await p.evaluate(() => window.__n));
    await p.evaluate(() => document.getElementById('updates').scrollIntoView());
    await p.locator('#updates').screenshot({path: `.remote-runs/row-${scheme}-${q.includes('move') ? 'move' : 'check'}.png`});
    await c.close();
  }
  await b.close();
})();
