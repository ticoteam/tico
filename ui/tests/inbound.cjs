// Company inbound table: external text cannot execute, scope filtering is explicit, phone/desktop fit.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.TICO_BROWSER_CHANNEL===undefined?'chrome':process.env.TICO_BROWSER_CHANNEL||undefined});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000},serviceWorkers:'block'});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const calls=[];
    const inbox={inbox_id:'ibx_a',kind:'email',name:'Account logins',address:'agent-acme-login@tico.team',state:'active',created_by:'human:ana',used_for:'',period_end:null,cancel_pending:0};
    const evil='<img src=x onerror="window.INBOUND_PWNED=1">';
    let preview=evil;
    const phoneRequests=[{request_id:'prq_quoted',name:'Monthly logins',state:'quoted',price_micros:14000000,quoted_until:Date.now()/1000+3600}];
    await page.route('**/*', route=>{
      const request=route.request(),url=new URL(request.url()),p=url.pathname;
      const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
      const file=p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if(file&&fs.existsSync(uiFile(file[1])))return route.fulfill({contentType:file[1].endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(uiFile(file[1]),'utf8')});
      if(p.startsWith('/vendor/')&&fs.existsSync(uiFile(p.slice(1))))return route.fulfill({body:fs.readFileSync(uiFile(p.slice(1)))});
      if(p.endsWith('.js'))return route.fulfill({contentType:'application/javascript',body:''});
      if(p==='/')return route.fulfill({contentType:'text/html',body:html});
      if(p.startsWith('/api/v2/inbound/')){
        calls.push({p,query:url.search,method:request.method(),body:request.postDataJSON()});
        if(p.endsWith('/inboxes'))return json({inboxes:[inbox],configured:true,last_sync:1800000000,error:'',gaps:[]});
        if(p.endsWith('/messages'))return json({messages:[{seq:1,message_id:'ses:test:ibx_a',inbox_id:'ibx_a',kind:'email',received_at:1800000000,from:'Login service',subject:'Sign in',preview,has_code:true}],next:null,cursor:1});
        if(p.endsWith('/code'))return json({code:'123456',untrusted:true});
        if(p.includes('/messages/'))return json({message_id:'ses:test:ibx_a',text:'Your code is 123456. '+evil,next_offset:null,untrusted:true});
        if(p.endsWith('/phone-requests')){
          if(request.method()==='GET')return json({phone_requests:phoneRequests,next:null});
          const created={request_id:'prq_new',name:request.postDataJSON().name,state:'requested',price_micros:null,quoted_until:null};
          phoneRequests.push(created);return json({phone_request:created});
        }
        if(p.includes('/phone-requests/')){
          const selected=phoneRequests.find(r=>p.includes('/'+r.request_id+'/'));
          selected.state=p.endsWith('/accept')?'accepted':'cancelled';
          return json({phone_request:selected});
        }
      }
      const config={version:'0.3.0',app_name:'Tico'};
      if(p==='/api/me')return json({id:'ana',role:'owner',name:'Ana',email:'ana@acme.example',cloud:true,registered:true,config});
      if(p==='/api/v2/config')return json(config);
      if(p==='/api/employees'||p==='/api/issues')return json([]);
      if(p==='/api/humans'||p==='/api/people')return json({people:[{id:'ana',name:'Ana'}],teams:{}});
      if(p==='/api/status')return json({cloud:true,active:[],queued:[],recent_runs:[],keeper_alive:true,health_issues:[]});
      if(p==='/api/v2/status')return json({bots:[]});
      if(p==='/api/v2/needs-you')return json({items:[]});
      if(p==='/api/v2/operations')return json({machines:[],services:[]});
      if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings/company-inbox');
    await page.locator('[data-inbound-read]').waitFor();
    assert.match(await page.locator('#inbound-messages').innerText(),/onerror/);
    assert.equal(await page.locator('#inbound-messages img').count(),0);
    await page.locator('#inbound-filter select[name=inboxes]').selectOption('ibx_a');
    await page.locator('#inbound-filter button[type=submit]').click();
    await page.waitForFunction(()=>!GET_INFLIGHT.size);
    assert(calls.some(c=>c.p.endsWith('/messages')&&c.query.includes('inbox_ids=ibx_a')));
    await page.locator('[data-inbound-read]').click();
    await page.locator('.inbound-dialog pre').filter({hasText:'123456'}).waitFor();
    assert.equal(await page.locator('.inbound-dialog img').count(),0);
    assert.equal(await page.evaluate(()=>window.INBOUND_PWNED),undefined);
    await page.locator('.inbound-dialog [data-close]').click();
    await page.locator('[data-inbound-code]').click();
    await page.locator('.inbound-dialog pre').filter({hasText:'123456'}).waitFor();
    await page.locator('.inbound-dialog [data-close]').click();
    await page.locator('#inbound-create input[name=name]').fill('Login number');
    await page.locator('#inbound-phone').click();
    await page.locator('#inbound-status').filter({hasText:'No number has been purchased'}).waitFor();
    assert(calls.some(c=>c.p.endsWith('/phone-requests')&&c.method==='POST'&&c.body.name==='Login number'));
    await page.getByText('Phone requests',{exact:true}).click();
    await page.locator('[data-inbound-phone-accept=prq_quoted]').waitFor();
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('[data-inbound-phone-accept=prq_quoted]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-inbound-phone-accept=prq_quoted]'));
    assert(calls.some(c=>c.p.endsWith('/prq_quoted/accept')&&c.body.price_micros===14000000));
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('[data-inbound-phone-cancel=prq_new]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-inbound-phone-cancel=prq_new]'));
    assert(!calls.some(c=>c.p.endsWith('/inboxes')&&c.method==='POST'));
    assert(!calls.some(c=>c.p.endsWith('/phone-quote')));
    preview='Your verification code is ••••••.';
    await page.locator('#inbound-newest').click();
    await page.locator('#inbound-messages').filter({hasText:'Your verification code'}).waitFor();
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'||a.effect?.getTiming().iterations===Infinity));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'page fits '+width);
      if(process.env.TICO_SHOTS)await page.screenshot({path:process.env.TICO_SHOTS+'/inbound-'+width+'.png'});
    }
    await page.evaluate(()=>{S.me.role='human';S.me.bot_admin=false;return inboundMount();});
    assert.equal(await page.locator('#inbound-phone').count(),0);
    assert.equal(await page.locator('#inbound-phone-requests').count(),0);
    assert.deepEqual(errors,[]);
    console.log('Inbound passed: safe text, scope, code reveal, approval requests/quote acceptance/cancellation, admin UI, desktop and phone.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
