// Offline owner Settings upload: type/size checks, stable retries, refresh preservation and roles.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const logo = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6XcAAAAASUVORK5CYII=', 'base64');
(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:800}, serviceWorkers:'block'});
    const errors = [], writes = [];
    let role = 'owner', cloud = true, failures = 0, refusal = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const request = route.request(), p = new URL(request.url()).pathname;
      const json = (body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType:ui[1].endsWith('.css') ? 'text/css' : 'application/javascript',body:fs.readFileSync(uiFile(ui[1]),'utf8')});
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType:'font/woff2',body:fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p.endsWith('.js')) return route.fulfill({contentType:'application/javascript',body:''});
      if (p === '/') return route.fulfill({contentType:'text/html',body:html});
      if (p === '/api/v2/team/icon') {
        if (request.method() === 'GET') return route.fulfill({contentType:'image/png',body:logo});
        writes.push({key:request.headers()['idempotency-key'],body:request.postDataBuffer()});
        if (failures-- > 0) return json({error:{detail:'Storage temporarily unavailable'}},503);
        if (refusal) return json({error:{detail:'Use a valid PNG, JPEG or WebP image'}},422);
        return json({url:'/api/v2/team/icon',content_type:'image/png'});
      }
      if (p === '/api/me') return json({id:'ana',role,name:'Ana',email:'ana@example.com',cloud,registered:true,config:{version:'dev',company_name:'Acme'}});
      if (p === '/api/v2/config') return json({version:'dev',company_name:'Acme'});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people:[],teams:{}});
      if (p === '/api/status') return json({cloud,keeper_alive:true,active:[],queued:[],recent_runs:[],health_issues:[]});
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/operations') return json({machines:[],services:[]});
      if (p.endsWith('/watch')) return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=team]').click();
    const input = page.locator('#settings-team-icon-file'), status = page.locator('#settings-team-icon-status');
    await page.waitForFunction(() => !document.querySelector('#settings-team-icon').hidden);
    await input.setInputFiles({name:'logo.gif',mimeType:'image/gif',buffer:logo});
    assert.match(await status.innerText(), /PNG, JPEG or WebP/);
    await input.setInputFiles({name:'large.png',mimeType:'image/png',buffer:Buffer.alloc(1048577)});
    assert.match(await status.innerText(), /up to 1 MB/);
    assert.equal(writes.length,0);
    failures = 1;
    await input.setInputFiles({name:'logo.png',mimeType:'image/png',buffer:logo});
    await page.waitForFunction(() => document.querySelector('#settings-team-icon-change').disabled);
    await page.evaluate(async () => { await loadSettings(); route(); });
    assert.equal(await page.locator('#settings-team').isVisible(),true);
    await page.waitForFunction(() => document.querySelector('#settings-team-icon-status').textContent === 'Team icon saved');
    assert.equal(writes.length,2);
    assert.equal(writes[0].key,writes[1].key);
    assert.deepEqual(writes[0].body,logo);
    assert.equal(await page.locator('#settings-team-icon-change').isEnabled(),true);
    if (process.env.TICO_ICON_SCREENSHOTS) {
      fs.mkdirSync(process.env.TICO_ICON_SCREENSHOTS,{recursive:true});
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path:path.join(process.env.TICO_ICON_SCREENSHOTS,'settings-team-desktop-dark.png')});
      await page.setViewportSize({width:390,height:844});
      await page.evaluate(() => { window.ticoTheme.set('light'); setDrawer(false); });
      await page.waitForFunction(() => document.querySelector('#side').getBoundingClientRect().right <= 0);
      await page.screenshot({path:path.join(process.env.TICO_ICON_SCREENSHOTS,'settings-team-phone-light.png')});
    }
    refusal = true;
    await input.setInputFiles({name:'bad.png',mimeType:'image/png',buffer:Buffer.from('invalid')});
    await page.waitForFunction(() => document.querySelector('#settings-team-icon-status').textContent.includes('Use a valid'));
    assert.equal(await page.locator('#settings-team-icon').isVisible(),true);
    refusal = false; failures = 3;
    await input.setInputFiles({name:'retry.png',mimeType:'image/png',buffer:logo});
    await page.waitForFunction(() => document.querySelector('#settings-team-icon-status').textContent.includes('Could not confirm'));
    const pending = writes.at(-1).key;
    await input.setInputFiles({name:'retry.png',mimeType:'image/png',buffer:logo});
    await page.waitForFunction(() => document.querySelector('#settings-team-icon-status').textContent === 'Team icon saved');
    assert.equal(writes.at(-1).key,pending);
    role = 'human';
    await page.reload();
    await page.locator('#settings-tabs').waitFor();
    assert.equal(await page.locator('[data-settings-tab=team]').count(),0);
    role = 'owner'; cloud = false;
    await page.reload();
    await page.locator('#settings-tabs').waitFor();
    assert.equal(await page.locator('[data-settings-tab=team]').count(),0);
    assert.deepEqual(errors,[]);
    console.log('Team icon: owner-only control, bounded binary uploads, stable retries, refresh preservation and phone layout passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
