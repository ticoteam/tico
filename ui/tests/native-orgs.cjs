// Several organizations in the Mac app: the page tells the app only its own company name and email, and offers
// Switch organization (the app's native menu) and Add organization only when the app says it can. Outside the app,
// or in an app too old to know them, neither appears and nothing is posted.
const {webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

async function open(browser, {features, many}) {
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width:1240,height:820},
    userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(({features, many}) => {
    window.EventSource = undefined;
    Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
    window.__posted = [];
    if (!features) return;
    window.ticoNative = name => ({postMessage: body => {
      window.__posted.push([name, body]);
      return Promise.resolve(name === 'multiOrg' ? many : null);
    }});
    window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', ...features];
  }, {features, many});
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({contentType:'application/json', body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html', body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana', email:'ana@acme.example', role:'owner', cloud:true, config:{company_name:'Acme Lights'}});
    if (p === '/api/status') return json({cloud:true, keeper_alive:true, health_issues:[]});
    if (p === '/api/employees' || p === '/api/issues') return json([]);
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings', {waitUntil:'domcontentloaded'});
  await page.locator('#account .account-email', {hasText:'ana@acme.example'}).waitFor();
  return {page, errors};
}
const posted = (page, name) => page.evaluate(n => window.__posted.filter(([k]) => k === n).map(([, body]) => body), name);

(async () => {
  const browser = await webkit.launch({headless:true});
  const ALL = ['orgInfo', 'multiOrg', 'showOrgMenu', 'addOrg'];

  // Two orgs in the app: its own name and email go to the app once; both items show and post to the app.
  {
    const {page, errors} = await open(browser, {features:ALL, many:true});
    await page.waitForFunction(() => window.__posted.some(([n]) => n === 'orgInfo'));
    assert.deepEqual(await posted(page, 'orgInfo'), [{name:'Acme Lights', email:'ana@acme.example'}]);
    await page.locator('#account').click();
    await page.locator('#org-switch').waitFor({state:'visible'});
    assert.ok(await page.locator('#org-add').isVisible());
    await page.locator('#org-switch').click();
    const [where] = await posted(page, 'showOrgMenu');
    assert.ok(Number.isInteger(where.x) && Number.isInteger(where.y) && where.x >= 0 && where.y > 0, JSON.stringify(where));
    await page.locator('#account').click();
    await page.locator('#org-add').click();
    assert.equal((await posted(page, 'addOrg')).length, 1);
    assert.equal((await posted(page, 'orgInfo')).length, 1, 'the same name and email are sent once');
    assert.deepEqual(errors, []);
    await page.close();
  }

  // One org: Add organization only.
  {
    const {page, errors} = await open(browser, {features:ALL, many:false});
    await page.locator('#account').click();
    await page.locator('#org-add').waitFor({state:'visible'});
    await page.waitForFunction(() => window.__posted.some(([n]) => n === 'multiOrg'));
    assert.equal(await page.locator('#org-switch').isHidden(), true);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // An older app, and a browser: neither item, and nothing about organizations is posted.
  for (const features of [[], null]) {
    const {page, errors} = await open(browser, {features, many:true});
    await page.locator('#account').click();
    await page.locator('#account-menu').waitFor({state:'visible'});
    assert.equal(await page.locator('#org-switch').isHidden(), true);
    assert.equal(await page.locator('#org-add').isHidden(), true);
    assert.deepEqual(await page.evaluate(() => window.__posted.map(([n]) => n).filter(n => /org/i.test(n))), []);
    assert.deepEqual(errors, []);
    await page.close();
  }

  await browser.close();
  console.log('native orgs: own name and email only; Switch and Add only when the app offers them');
})().catch(error => { console.error(error); process.exit(1); });
