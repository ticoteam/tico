// A slow diagnostic cannot hold the app's first page; an unrelated failed read cannot mark it offline.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

async function check(engine, native) {
  const browser = await engine.launch({headless:true});
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width:390,height:844},
    ...(native ? {userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'} : {})});
  const errors = [], waiting = [];
  let hold = true, failPeople = false, failStatus = false;
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => { window.EventSource = undefined; });
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const json = (body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html',body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (hold && ['/api/status','/api/issues','/api/v2/status','/api/v2/needs-you'].includes(p)) {
      await new Promise(resolve => waiting.push(resolve));
    }
    if (p === '/api/me') return json({id:'ana',email:'ana@acme.example',role:'owner',cloud:true,config:{company_name:'Acme'}});
    if (p === '/api/employees') return json([{name:'ops',display_name:'Ops',status:'active',host:'keeper',can_chat:true}]);
    if (p === '/api/humans') return failPeople ? json({error:{detail:'Roster unavailable'}},503)
      : json({people:[{id:'ana',name:'Ana'}],groups:[]});
    if (p === '/api/status') return failStatus ? json({error:{detail:'Status unavailable'}},503)
      : json({cloud:true,keeper_alive:true,health_issues:[]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/status') return json({bots:[{bot:'ops',state:'idle',open_tasks:0}]});
    if (p === '/api/v2/needs-you') return json({items:[]});
    if (p === '/api/v2/task-types') return json({types:[]});
    return json(unrouted(p));
  });
  const started = Date.now();
  await page.goto('https://tico-ui.test/#/tasks', {waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'Tasks',exact:true}).waitFor({timeout:2000});
  console.log(`${native ? 'native WebKit' : 'browser Chromium'}: first page ${Date.now()-started} ms with diagnostics still held`);
  assert.equal(await page.evaluate(() => S.status),null);
  assert.equal(await page.locator('#heartbeat').getAttribute('aria-label'),'Connecting');
  assert.ok(waiting.length >= 3);
  assert.equal(await page.evaluate(() => document.body.classList.contains('native')),native);
  hold = false; waiting.splice(0).forEach(resolve => resolve());
  await page.waitForFunction(() => S.status?.keeper_alive && S.v2.on);
  failPeople = true;
  await page.evaluate(() => refresh(false));
  assert.equal(await page.locator('#heartbeat').getAttribute('aria-label'),'Cloud backend connected');
  assert.equal(await page.evaluate(() => S.people[0].id),'ana','keep the last complete roster');
  failStatus = true;
  await page.evaluate(() => refresh(false));
  assert.equal(await page.locator('#heartbeat').getAttribute('aria-label'),'Tico unreachable');
  assert.deepEqual(errors,[]);
  await browser.close();
}
(async () => { await check(chromium,false); await check(webkit,true); })().catch(error => {
  console.error(error); process.exit(1);
});
