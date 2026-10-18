// An out-of-date desktop app says so: the bar offers Update, the shell's failure reason is shown with a
// download link, and Settings shows both versions.
const {webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await webkit.launch({headless:true});
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width:1240,height:820},
    userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.EventSource = undefined;
    Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
    window.__posted = [];
    window.ticoNative = name => ({postMessage: body => window.__posted.push([name, body])});
    window.ticoNativeFeatures = ['openWindow', 'checkForUpdates'];
    window.ticoNativeVersion = '0.3.40';
  });
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html',body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana',email:'ana@acme.example',role:'owner',cloud:true,config:{company_name:'Acme',version:'0.3.46'}});
    if (p === '/api/status') return json({cloud:true,keeper_alive:true,health_issues:[]});
    if (p === '/api/download/mac') return json({available:true,version:'0.3.46',url:'https://github.com/ticoteam/tico/releases/download/v0.3.46/Tico.dmg'});
    if (p === '/api/employees' || p === '/api/issues') return json([]);
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings', {waitUntil:'domcontentloaded'});
  const bar = page.locator('#app-update');
  await bar.waitFor({state:'visible'});
  assert.match(await page.locator('#app-update-text').textContent(), /App v0\.3\.46 is ready/);

  await page.locator('#app-update-go').click();
  assert.deepEqual(await page.evaluate(() => window.__posted.filter(([n]) => n === 'checkForUpdates').length), 1);
  await page.evaluate(() => window.ticoAppUpdate({state:'installing', detail:'0.3.46'}));
  assert.match(await page.locator('#app-update-text').textContent(), /Installing v0\.3\.46/);
  await page.evaluate(() => window.ticoAppUpdate({state:'failed', detail:'could not read the update feed: 403'}));
  assert.match(await page.locator('#app-update-text').textContent(), /did not install \(could not read the update feed: 403\)/);
  assert.equal(await page.locator('#app-update-dl').getAttribute('href'), 'https://github.com/ticoteam/tico/releases/download/v0.3.46/Tico.dmg');
  assert.ok(await page.locator('#app-update-dl').isVisible());

  await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
  assert.equal(await page.locator('#settings-version').textContent(), 'Version v0.3.46 · App v0.3.40');
  await page.locator('#app-update-later').click();
  assert.equal(await bar.isHidden(), true);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('app update: bar, shell result and settings versions');
})().catch(error => { console.error(error); process.exit(1); });
