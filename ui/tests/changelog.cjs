// Catch-up uses the server's read state, acknowledges only filtered entries, and survives another sign-in.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const shots = process.env.TICO_SCREENSHOT_DIR;
(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const ctx = await browser.newContext({viewport:{width:1440,height:900}, serviceWorkers:'block'});
    const page = await ctx.newPage();
    const errors = [], posts = [], read = new Set();
    let failRead = false, failLoad = false;
    const entries = [
      {id:'release-0.3.23', title:'Tico v0.3.23', version:'0.3.23', source:'release', kind:'product', shipped_at:'2026-10-05T00:00:00Z',
       bullets:['Relate tasks to each other, including follow-ups, duplicates and blockers.', 'Long chats show an outline so you can jump back to any prompt.', 'Task and conversation updates arrive as they happen.', 'See what each bot learned from its recent work.', 'Releases reach your team faster.'],
       url:'https://github.com/ticoteam/tico/releases/tag/v0.3.23'},
      {id:'release-0.3.22', title:'Tico v0.3.22', version:'0.3.22', source:'release', kind:'product', shipped_at:'2026-10-04T00:00:00Z',
       bullets:['Delete a task made by mistake and restore it from the trash.', 'See which person a task is waiting on.'], url:'javascript:alert(1)'},
      {id:'activity-1', title:'<img src=x onerror=alert(1)>', kind:'activity', shipped_at:'2026-10-06T00:00:00Z', bullets:['A bot completed its work.'], task_id:'task-1'}
    ];
    const count = () => entries.filter(r => r.kind === 'product' && !read.has(r.id)).length;
    const config = () => ({version:'0.3.23', app_name:'Tico', changelog:{unread_count:count()}});
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType:ui[1].endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(uiFile(ui[1]),'utf8')});
      const asset = p.startsWith('/assets/') || p.startsWith('/vendor/') ? uiFile(p.slice(1)) : p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : '';
      if (asset && fs.existsSync(asset) && fs.statSync(asset).isFile()) return route.fulfill({body:fs.readFileSync(asset)});
      if (p.endsWith('.js')) return route.fulfill({contentType:'application/javascript',body:''});
      if (p === '/') return route.fulfill({contentType:'text/html',body:html});
      if (p === '/api/me') return json({id:'ana', role:'viewer', name:'Ana', email:'ana@acme.example', cloud:true, registered:true, config:config()});
      if (p === '/api/v2/config') return json(config());
      if (p === '/api/v2/changelog') {
        if (failLoad) return json({error:{detail:'Could not load changes'}},503);
        const params = new URL(req.url()).searchParams;
        const matched = entries.map(r => ({...r, unread:r.kind === 'product' && !read.has(r.id)})).filter(r => (params.get('kind') !== 'product' || r.kind === 'product') && (params.get('unread') !== 'true' || r.unread) && (!params.get('q') || (r.title + ' ' + r.bullets.join(' ')).toLowerCase().includes(params.get('q').toLowerCase())));
        return json({entries:matched, unread_count:count(), next_offset:null, current_version:'0.3.23', drafts:[], can_review:false, can_add:false});
      }
      if (p === '/api/v2/changelog/read') {
        posts.push(req.postDataJSON());
        if (failRead) return json({error:{detail:'Could not save read state'}},503);
        req.postDataJSON().ids.forEach(id => read.add(id));
        return json({unread_count:count()});
      }
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people:[],teams:{}});
      if (p === '/api/status') return json({cloud:true,active:[],queued:[],recent_runs:[],keeper_alive:true,health_issues:[]});
      if (p === '/api/v2/status') return json({bots:[]});
      if (p === '/api/v2/needs-you') return json({items:[]});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/tasks');
    await page.locator('#changelog-notice').waitFor();
    await page.locator('#changelog-notice').click();
    await page.locator('.release-entry').first().waitFor();
    assert.equal(await page.locator('.release-entry').count(),2);
    assert.equal(await page.locator('[data-add-update]').isVisible(),false);
    assert.equal(posts.length,0,'opening the changelog does not acknowledge unseen changes');
    await page.locator('.release-more summary').click();
    assert.equal(await page.locator('.release-more li').count(),1);
    assert.equal(await page.locator('a[href^="javascript:"]').count(),0);
    if (shots) {
      fs.mkdirSync(shots,{recursive:true});
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path:path.join(shots,'changelog-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      await page.evaluate(() => setDrawer(false));
      await page.waitForFunction(() => document.querySelector('#side').getBoundingClientRect().right <= 1);
      await page.screenshot({path:path.join(shots,'changelog-phone.png'),fullPage:true});
      await page.setViewportSize({width:1440,height:900});
    }
    await page.getByRole('searchbox',{name:'Search changes'}).fill('restore');
    assert.equal(await page.locator('.release-entry').count(),1);
    failRead = true;
    await page.locator('[data-read-changes]').click();
    await page.waitForFunction(() => !document.querySelector('[data-read-changes]').disabled);
    assert.equal(await page.locator('.release-new').count(),1,'a failed write leaves the shown change unread');
    assert.equal(count(),2);
    failRead = false;
    await page.locator('[data-read-changes]').click();
    await page.waitForFunction(() => document.querySelector('#changelog-notice').textContent.includes('1 update'));
    assert.deepEqual(posts.at(-1).ids,['release-0.3.22']);
    await page.reload();
    await page.locator('.release-entry').first().waitFor();
    assert.equal(await page.locator('.release-entry').count(),1,'a later sign-in reads the persisted state');
    await page.locator('[data-read-changes]').click();
    await page.waitForFunction(() => document.querySelector('#changelog-notice').hidden);
    await page.getByRole('combobox',{name:'Filter changes'}).selectOption('product');
    await page.waitForFunction(() => document.querySelectorAll('.release-entry').length === 2);
    assert.equal(await page.locator('.release-entry').count(),2,'read changes remain in product history');
    await page.getByRole('combobox',{name:'Filter changes'}).selectOption('');
    await page.waitForFunction(() => document.querySelectorAll('.release-entry').length === 3);
    assert.equal(await page.locator('.release-entry').count(),3);
    assert.equal(await page.locator('.release-card img').count(),0,'untrusted activity titles are escaped');
    failLoad = true;
    await page.reload();
    await page.locator('[data-retry-changes]').waitFor();
    failLoad = false;
    await page.locator('[data-retry-changes]').click();
    await page.locator('.release-entry').first().waitFor();
    assert.deepEqual(errors,[]);
    await ctx.close();
    console.log('PASS: changelog catch-up, filtered acknowledgement, persisted read state, retry, escaped content and mobile layout');
  } finally { await browser.close(); }
})().catch(e => {console.error(e);process.exit(1);});
