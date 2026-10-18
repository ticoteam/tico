// The desktop app is one ordinary window: no right-rail button or mode, and the page stays usable as the person
// drags it narrow (the compact layout up to 760px, as the rail used to give) or wide.
const {webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await webkit.launch({headless:true});
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width:1240,height:820},
    userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [], posted = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.EventSource = undefined;
    // What the shell's bridge script provides; every message the page sends it is recorded.
    window.__posted = [];
    window.ticoNative = name => ({postMessage: body => window.__posted.push([name, body])});
    window.ticoNativeFeatures = ['openWindow'];
  });
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html',body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana',email:'ana@acme.example',role:'owner',cloud:true,config:{company_name:'Acme'}});
    if (p === '/api/employees') return json([{name:'ops',display_name:'Ops',status:'active',host:'keeper',can_chat:true}]);
    if (p === '/api/humans') return json({people:[{id:'ana',name:'Ana'}],groups:[]});
    if (p === '/api/status') return json({cloud:true,keeper_alive:true,health_issues:[]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/status') return json({bots:[{bot:'ops',state:'idle',open_tasks:0}]});
    if (p === '/api/v2/needs-you') return json({items:[]});
    if (p === '/api/v2/task-types') return json({types:[]});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks', {waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'Tasks',exact:true}).waitFor();
  await page.waitForFunction(() => S.status?.keeper_alive);
  assert.equal(await page.evaluate(() => document.body.classList.contains('native')), true);
  assert.equal(await page.locator('#rail').count(), 0, 'no right-rail button');
  assert.equal(await page.evaluate(() => typeof window.ticoWindowModeChanged), 'undefined');

  // 360 is the window's minimum width, 420 what the rail was; 760 is the widest compact layout.
  for (const [width, compact] of [[360, true], [420, true], [760, true], [761, false], [1240, false]]) {
    await page.setViewportSize({width, height:820});
    await page.waitForFunction(w => window.innerWidth === w, width);
    const seen = await page.evaluate(() => {
      const shown = el => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
      const main = document.querySelector('#main').getBoundingClientRect();
      return {overflow: document.scrollingElement.scrollWidth - window.innerWidth,
              mobileNav: shown(document.querySelector('#mobile-nav')),
              sidebar: shown(document.querySelector('#side')) && document.querySelector('#side').getBoundingClientRect().left >= 0,
              mainLeft: main.left, mainRight: main.right,
              heading: shown([...document.querySelectorAll('h1,h2')].find(h => h.textContent.trim() === 'Tasks'))};
    });
    assert.ok(seen.overflow <= 0, `${width}px: page scrolls sideways by ${seen.overflow}px`);
    assert.ok(seen.heading, `${width}px: the page heading is visible`);
    assert.ok(seen.mainLeft >= 0 && seen.mainRight <= width + 0.5, `${width}px: the page fits the window`);
    assert.equal(seen.mobileNav, compact, `${width}px: compact navigation ${compact ? 'shown' : 'hidden'}`);
    if (!compact) assert.ok(seen.sidebar, `${width}px: the sidebar is shown`);
  }
  posted.push(...await page.evaluate(() => window.__posted));
  assert.deepEqual(posted.filter(([name]) => name === 'windowMode'), [], 'the page no longer asks the shell for a window mode');
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('native window: no rail mode; usable from 360px to 1240px');
})().catch(error => { console.error(error); process.exit(1); });
