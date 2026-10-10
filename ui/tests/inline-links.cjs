// Links in a task body read as what they are: an s3:// URI whose bucket the server maps (TICO_S3_VIEW_URLS) links
// to its view URL, an unmapped one is a file chip with Copy, an image link gets a thumbnail that opens the viewer,
// a PDF is a card, a long address is shortened, and nothing overflows a phone. Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const PNG = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000'+'1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082','hex');
// The cases clients/s3links.py is held to as well: the UI links each URI to the same view URL.
const VECTORS = JSON.parse(fs.readFileSync(path.join(__dirname, 'support', 's3-link-vectors.json'), 'utf8'));
const TASK = '5b0c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3';
const VIEW = 'https://d1234example.cloudfront.net';
const UNMAPPED = 's3://other-bucket/email-marketing/deliverables/0f8e5c1e-1111-2222-3333-444455556666/2026-09-09-follow-up-draft-final.md';
const LONG = 'https://docs.example.com/projects/launch/2026/plans/a-very-long-folder-name/overview.html?tab=1';
const BODY = ['Two drafts for review.', '',
  '- Draft: s3://acme-files/email/deliverables/1234/2026-09-09-launch.md',
  '- Old copy: ' + UNMAPPED,
  '- [hero image](s3://acme-files/img/hero.png)',
  '- [cover](https://cdn.example/assets/cover.png)',
  '- https://cdn.example/docs/brief.pdf',
  '- ' + LONG,
  '- Written as code: `s3://acme-files/in-code.md`'].join('\n');
(async () => {
  const chrome = process.env.TICO_BROWSER !== 'webkit';
  const browser = chrome ? await chromium.launch({channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined,headless:true}) : await webkit.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block'});
    if (chrome) await page.context().grantPermissions(['clipboard-read','clipboard-write'],{origin:'https://tico-ui.test'});
    const errors=[],referers=[];
    page.on('pageerror',e=>errors.push(e.message));
    const bot={name:'finance',display_name:'Finance',host:'keeper',status:'active',can_chat:true,schedules:[]};
    await page.context().route('**/*',route=>{
      const url=new URL(route.request().url()),p=url.pathname;
      const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
      if(/\/marked\.min\.js$/.test(p))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(uiFile('vendor/marked.min.js'),'utf8')});
      if(p==='/')return route.fulfill({contentType:'text/html',body:html});
      const ui=p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if(ui&&fs.existsSync(uiFile(ui[1])))return route.fulfill({contentType:ui[1].endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(uiFile(ui[1]),'utf8')});
      if(/\.png$/.test(p)&&url.hostname!=='tico-ui.test'){referers.push(route.request().headers().referer||'');return route.fulfill({contentType:'image/png',body:PNG});}
      if(/\.pdf$/.test(p))return route.fulfill({contentType:'application/pdf',body:'%PDF-1.4 fixture'});
      if(p==='/api/me')return json({id:'ana',name:'Ana',role:'owner',cloud:true,config:{app_name:'Tico',s3_view_urls:VECTORS.mapping}});
      if(p==='/api/employees')return json([bot]);
      if(p==='/api/issues')return json([]);
      if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
      if(p==='/api/v2/conversations')return json({conversations:url.searchParams.get('chat_with')?[{id:'c-finance',kind:'chat',scope:'personal',participants:['human:ana','bot:finance']}]:[]});
      if(p==='/api/v2/conversations/c-finance/snapshot')return json({messages:[{id:'m1',from_actor:'bot:finance',body:`Please review [the drafts](https://tico-ui.test/tasks/${TASK}).`,created:new Date().toISOString()}],execution:null});
      if(p===`/api/v2/tasks/${TASK}`)return json({task:{id:TASK,title:'Review the launch drafts',body:BODY,owner:'human:ana',requester:'bot:finance',status:'open',lane:'company',labels:[],links:[],parts:{total:0,done:0},version:1,updated:new Date().toISOString()},children:[]});
      if(p==='/api/v2/tasks')return json({tasks:[]});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/bot/finance/chat');
    await page.getByRole('link',{name:'the drafts'}).click();
    const desc=page.locator('#task-modal[open] .tdesc.md').first();
    await desc.waitFor();
    // The mapped bucket: the bare URI is a link to the view URL, and a .md draft shows as a card naming the file.
    const draft=desc.locator(`a[href="${VIEW}/email/deliverables/1234/2026-09-09-launch.md"]`);
    assert.equal(await draft.getAttribute('target'),'_blank');
    assert.equal(await draft.locator('.md-card-name').innerText(),'2026-09-09-launch.md');
    assert.equal(await draft.locator('.md-card-host').innerText(),'d1234example.cloudfront.net');
    // An unmapped bucket: a chip with the file name, the full URI in its title, and Copy; not a link.
    const chip=desc.locator('.s3-chip');
    assert.equal(await chip.count(),1);
    assert.equal(await chip.locator('.s3-chip-name').innerText(),'2026-09-09-follow-up-draft-final.md');
    assert.equal(await chip.getAttribute('title'),UNMAPPED);
    assert.equal(await chip.locator('a').count(),0);
    await page.getByRole('button',{name:'Copy '+UNMAPPED}).click();
    if (chrome) assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),UNMAPPED);
    // Image links: a thumbnail beside each, loaded without a referrer, that opens the one viewer; Escape closes it.
    const hero=desc.getByRole('link',{name:'hero image'});
    assert.equal(await hero.getAttribute('href'),VIEW+'/img/hero.png');
    await page.waitForFunction(()=>document.querySelectorAll('#task-modal .tdesc .inline-thumb.ready').length===2);
    assert.equal(await desc.locator('.inline-thumb img').first().getAttribute('referrerpolicy'),'no-referrer');
    assert.deepEqual(referers.filter(Boolean),[]);
    await desc.getByRole('button',{name:'View cover'}).click();
    await page.locator('#doc-viewer[open] .viewer-img').waitFor();
    assert.equal(await page.locator('#doc-viewer h2').innerText(),'cover');
    await page.keyboard.press('Escape');
    await page.waitForFunction(()=>!document.querySelector('#doc-viewer').open);
    // A PDF is a card: type, name and host, opening outside; long addresses are host and a short path.
    const pdf=desc.locator('a.md-card[data-media="PDF"]');
    assert.equal(await pdf.getAttribute('href'),'https://cdn.example/docs/brief.pdf');
    assert.equal(await pdf.locator('.md-card-name').innerText(),'brief.pdf');
    assert.equal(await pdf.locator('.md-card-host').innerText(),'cdn.example');
    const long=desc.locator(`a[href="${LONG}"]`);
    assert.equal(await long.innerText(),'docs.example.com/projects/…/overview.html…');
    assert.equal(await long.getAttribute('title'),LONG);
    // Code stays as written; no raw s3:// is a link or shows as text outside code.
    assert.equal(await desc.locator('code').innerText(),'s3://acme-files/in-code.md');
    assert.equal(await desc.locator('code a').count(),0);
    assert.equal(await page.locator('a[href^="s3:"]').count(),0);
    assert.equal(await desc.evaluate(el=>{const c=el.cloneNode(true);c.querySelectorAll('code').forEach(x=>x.remove());return c.textContent.includes('s3://');}),false);
    // Nothing is wider than a 390 px phone's column.
    const fit=await desc.evaluate(el=>{const r=el.getBoundingClientRect();
      return {scroll:el.scrollWidth-el.clientWidth,over:[...el.querySelectorAll('a, .s3-chip')].filter(x=>x.getBoundingClientRect().right>r.right+1).map(x=>x.outerHTML.slice(0,80))};});
    assert.deepEqual(fit,{scroll:0,over:[]});
    // The shared cases: the same view URLs as the hub rewrite, chips for the unmapped, and code left as written.
    const seen=await page.evaluate(cases=>cases.map(c=>{const d=document.createElement('div');d.innerHTML=safeMd(c.text);
      return {view:[...d.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),unmapped:[...d.querySelectorAll('.s3-chip')].map(x=>x.title),
              raw:(()=>{d.querySelectorAll('code, .s3-chip').forEach(x=>x.remove());return d.textContent.includes('s3://');})()};}),VECTORS.cases);
    assert.deepEqual(seen,VECTORS.cases.map(c=>({view:c.view,unmapped:c.unmapped,raw:false})));
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TICO_SCREENSHOT_DIR,'inline-links-phone.png')});
    assert.deepEqual(errors,[]);
    console.log('inline-links: ok (mapped s3 link, unmapped chip with Copy, thumbnails in the viewer, PDF card, short link, phone width)');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
