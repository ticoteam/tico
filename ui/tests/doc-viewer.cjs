// Markdown files open inline: a link to a Tico file that is Markdown opens
// formatted in a pop-up with Download; any other file still downloads. Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
// Offline stand-in for marked (a CDN script): headings, lists, bold and [text](url) links.
const markedStub = () => { window.marked = {parse: src => String(src).split(/\n\n+/).map(block => {
  const inline = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  const h = block.match(/^(#{1,3}) (.*)$/); if (h) return `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`;
  if (/^- /m.test(block)) return '<ul>' + block.split('\n').filter(l => l.startsWith('- ')).map(l => `<li>${inline(l.slice(2))}</li>`).join('') + '</ul>';
  return `<p>${inline(block)}</p>`; }).join('')}; };
(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless:true}) : await chromium.launch({channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844},serviceWorkers:'block',acceptDownloads:true});
    const errors=[],uploads=[],sends=[];let offline=true;let slow=0;
    page.on('pageerror',e=>errors.push(e.message));
    const bot=(name,display_name)=>({name,display_name,host:'keeper',status:'active',can_chat:true,schedules:[]});
    const bots=[bot('legal','Legal'),bot('seo','AI SEO'),bot('cmo','AI CMO'),bot('finance','Finance'),bot('sales-ops','Sales Ops'),bot('designer','Designer')];
    await page.context().route('**/*',route=>{
      const url=new URL(route.request().url()),p=url.pathname;
      if(/\/marked\.min\.js$/.test(p))return route.fulfill({contentType:'application/javascript',body:`(${markedStub})()`});
      const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
      if(p==='/')return route.fulfill({contentType:'text/html',body:html});
      const ui=p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if(ui){
        const file=uiFile(ui[1]);
        if(fs.existsSync(file))return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript',body:fs.readFileSync(file,'utf8')});
      }
      if(p==='/api/me')return json({id:'ana',name:'Ana',role:'owner',cloud:true});
      if(p==='/api/employees')return json(bots);
      if(p==='/api/issues')return json([]);
      if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
      if(p==='/api/v2/files/file-md-0001')return route.fulfill({contentType:'application/octet-stream',headers:{'content-disposition':"attachment; filename*=UTF-8''review-packet.md"},body:'# Review packet\n\n- **Towels** v2\n- Fee v3\n'});
      if(p==='/api/v2/files/file-png-0001')return route.fulfill({contentType:'application/octet-stream',headers:{'content-disposition':"attachment; filename*=UTF-8''storyboard.png"},body:Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000'+'1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082','hex')});
      const meta=p.match(/^\/api\/v2\/files\/([^/]+)\/meta$/);
      if(meta)return json({id:meta[1],name:{'file-md-0001':'review-packet.md','file-png-0001':'storyboard.png','file-pdf-0001':'lease.pdf'}[meta[1]]||'x',size:1200,content_type:'application/octet-stream'});
      if(p==='/assets/cover.png')return route.fulfill({contentType:'image/png',body:Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000'+'1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082','hex')});
      if(p==='/api/v2/files/file-pdf-0001')return route.fulfill({contentType:'application/octet-stream',headers:{'content-disposition':"attachment; filename*=UTF-8''lease.pdf"},body:'%PDF-1.4 fixture'});
      if(/youtube-nocookie\.com$/.test(url.hostname))return route.fulfill({contentType:'text/html',body:'<p>player</p>'});
      if(p==='/api/v2/conversations'){const bot=url.searchParams.get('chat_with');
        return json({conversations:bot?[{id:'c-'+bot,kind:'chat',scope:'personal',participants:['human:ana','bot:'+bot]}]:[]});}
      if(p.startsWith('/api/v2/conversations/c-')&&p.endsWith('/snapshot')){const bot=p.split('/')[4].slice(2);
        const body=JSON.stringify({messages:[{id:'m-'+bot,from_actor:'bot:'+bot,body:bot==='legal'?'I need your **review** of these:\n\n- [review packet](https://tico-ui.test/api/v2/files/file-md-0001)\n- [storyboard](https://tico-ui.test/api/v2/files/file-png-0001)\n- [cover photo](https://tico-ui.test/assets/cover.png)\n- [walkthrough](https://youtu.be/dQw4w9WgXcQ)\n- [lease](https://tico-ui.test/api/v2/files/file-pdf-0001)':bot==='finance'?'I gave Inbox [quote instructions](https://tico-ui.test/tasks/979a861b-a309-43db-a443-732a3674de81). See [goals](https://tico-ui.test/#/goals) and [the lender](https://lender.example/).':'Hello from '+bot,created:new Date().toISOString()}],execution:null});
        return setTimeout(()=>route.fulfill({contentType:'application/json',body}),slow);}
      if(p==='/api/v2/tasks/979a861b-a309-43db-a443-732a3674de81')return json({task:{id:'979a861b-a309-43db-a443-732a3674de81',title:'Seek written financing quotes',owner:'bot:inbox',requester:'bot:finance',status:'todo',lane:'company',labels:[],links:[],parts:{total:0,done:0},version:1,updated:new Date().toISOString()},children:[]});
      if(p==='/api/v2/tasks'){const owner=url.searchParams.get('owner');
        const body=JSON.stringify({tasks:owner?[{id:'t-'+owner,title:'Task for '+owner,owner:'bot:'+owner,requester:'human:ana',status:'doing',lane:'company',labels:[],links:[],parts:{total:0,done:0},version:1,updated:new Date().toISOString()}]:[]});
        return setTimeout(()=>route.fulfill({contentType:'application/json',body}),slow);}
      if(p==='/api/v2/chat/legal'){
        sends.push(route.request().headers()['idempotency-key']);
        if(offline)return route.abort('internetdisconnected');
        return json({conversation:{id:'legal-chat'},message:{id:'m1',from_actor:'human:ana',body:'Hello offline',created:new Date().toISOString()}});
      }
      if(p==='/api/v2/uploads/chat/legal'){
        uploads.push({path:p,body:route.request().postDataBuffer().toString()});
        if(false)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{detail:'Fixture upload unavailable'}})});
        return json({conversation:{id:'legal-upload'},message:{id:'m1',from_actor:'human:ana',body:'Review this fixture',created:new Date().toISOString()}});
      }
      return json({});
    });
    await page.setViewportSize({width:1200,height:800});
    await page.goto('https://tico-ui.test/#/bot/legal/chat');
    await page.getByRole('link',{name:'review packet'}).click();
    await page.locator('#doc-viewer[open] h1').waitFor();
    assert.equal(await page.locator('#doc-viewer h1').innerText(),'Review packet');
    assert.equal(await page.locator('#doc-viewer strong').innerText(),'Towels');
    assert.equal(await page.locator('#doc-viewer h2').innerText(),'review-packet.md');
    const [saved]=await Promise.all([page.waitForEvent('download'),page.locator('#doc-viewer [data-doc-download]').click()]);
    assert.equal(saved.suggestedFilename(),'review-packet.md');
    await page.locator('#doc-viewer [data-doc-close]').click();
    assert.equal(await page.evaluate(()=>document.querySelector('#doc-viewer').open),false);
    assert.equal(await page.locator('#doc-viewer').isVisible(),false,'a closed viewer is gone');
    // Images, video and other content open in the app too, in the same viewer,
    // with arrows through everything in the message; an image link gets a thumbnail in place.
    await page.locator('.inline-thumb.ready img').first().waitFor();
    await page.waitForFunction(()=>document.querySelectorAll('.inline-thumb').length===2);
    // A file link with no extension in its words ("storyboard") learns what it is from the file's
    // name, so it gets a thumbnail too; "lease" is marked PDF and "review packet" DOC.
    assert.equal(await page.getByRole('link',{name:'lease'}).getAttribute('data-media'),'PDF');
    assert.equal(await page.getByRole('link',{name:'review packet'}).getAttribute('data-media'),'DOC');
    assert.equal(await page.getByRole('link',{name:'walkthrough'}).getAttribute('data-media'),'VIDEO');
    await page.getByRole('link',{name:'storyboard'}).click();
    await page.locator('#doc-viewer[open] .viewer-img').waitFor();
    assert.equal(await page.locator('#doc-viewer h2').innerText(),'storyboard.png');
    assert.equal(await page.locator('#doc-viewer .viewer-count').innerText(),'2 / 5');
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, 'viewer-image-desktop.png')});
    assert.equal(await page.evaluate(()=>document.querySelector('#doc-viewer').classList.contains('viewer-media')),true);
    const [png]=await Promise.all([page.waitForEvent('download'),page.locator('#doc-viewer [data-doc-download]').click()]);
    assert.equal(png.suggestedFilename(),'storyboard.png');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(()=>document.querySelector('#doc-viewer .viewer-count')?.textContent==='3 / 5');
    assert.equal(await page.locator('#doc-viewer .viewer-img').count(),1,'the cover photo');
    await page.keyboard.press('ArrowRight');
    await page.locator('#doc-viewer .viewer-embed iframe[src^="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"]').waitFor();
    await page.keyboard.press('ArrowRight');
    await page.locator('#doc-viewer .viewer-pdf iframe').waitFor();
    assert.equal(await page.locator('#doc-viewer h2').innerText(),'lease.pdf');
    // Its blob: Open works in a browser tab; the desktop app cannot hand a blob: URL to the browser.
    assert.equal(await page.locator('#doc-viewer a[href^="blob:"]').count(),1,'Open in a browser');
    await page.evaluate(()=>document.body.classList.add('native'));
    await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowRight');
    await page.locator('#doc-viewer .viewer-pdf iframe').waitFor();
    assert.equal(await page.locator('#doc-viewer a[href^="blob:"]').count(),0,'no Open in the desktop app');
    await page.evaluate(()=>document.body.classList.remove('native'));
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>document.querySelector('#doc-viewer').open),false);
    // Clicking the cover photo's thumbnail opens it at its place.
    await page.locator('.inline-thumb').nth(1).click();
    await page.waitForFunction(()=>document.querySelector('#doc-viewer .viewer-count')?.textContent==='3 / 5');
    await page.locator('#doc-viewer [data-doc-close]').click();
    // On a phone the viewer fills the screen.
    await page.setViewportSize({width:390,height:844});
    await page.locator('.inline-thumb').nth(1).click();
    await page.locator('#doc-viewer[open] .viewer-img').waitFor();
    const box=await page.locator('#doc-viewer').boundingBox();
    assert(box.width>=388 && box.height>=840,'full screen on a phone: '+JSON.stringify(box));
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, 'viewer-image-phone.png')});
    await page.locator('#doc-viewer [data-doc-close]').click();
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, 'viewer-inline-phone.png')});
    // A task link in a message opens the task pop-up in place.
    await page.goto('https://tico-ui.test/#/bot/finance/chat');
    // Links into the hub open in place (no new window, which the desktop app drops); others still open outside.
    assert.equal(await page.getByRole('link',{name:'goals'}).getAttribute('target'),null);
    assert.equal(await page.getByRole('link',{name:'quote instructions'}).getAttribute('target'),null);
    assert.equal(await page.getByRole('link',{name:'the lender'}).getAttribute('target'),'_blank');
    await page.getByRole('link',{name:'quote instructions'}).click();
    await page.locator('#task-modal[open]').getByText('Seek written financing quotes').first().waitFor();
    assert.equal(new URL(page.url()).hash,'#/bot/finance/chat');
    assert.deepEqual(errors,[]);
    console.log('doc-viewer: ok (Markdown formatted; images, the thumbnail, YouTube and PDF in the one viewer with arrows)');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
