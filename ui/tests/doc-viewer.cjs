// Markdown files open inline: a link to a Tico file that is Markdown opens
// formatted in a pop-up with Download; any other file still downloads. Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
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
    const errors=[],uploads=[],sends=[],outside=[];let offline=true;let slow=0;
    // serviceWorkers:'block' is Playwright's own script in every frame; a sandboxed frame refuses it.
    page.on('pageerror',e=>{if(!/Service worker is disabled because the context is sandboxed/i.test(e.message))errors.push(e.message);});
    const bot=(name,display_name)=>({name,display_name,host:'keeper',status:'active',can_chat:true,schedules:[]});
    const bots=[bot('legal','Legal'),bot('seo','AI SEO'),bot('cmo','AI CMO'),bot('finance','Finance'),bot('sales-ops','Sales Ops'),bot('designer','Designer')];
    await page.context().route('**/*',route=>{
      const url=new URL(route.request().url()),p=url.pathname;
      if(url.hostname==='evil.test'){outside.push(url.href);return route.fulfill({contentType:'text/html',body:'<p>outside</p>'});}
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
      if(p==='/api/v2/files/file-html-0001')return route.fulfill({contentType:'application/octet-stream',headers:{'content-disposition':"attachment; filename*=UTF-8''launch.html"},
        body:'<!doctype html><html><head><meta http-equiv="refresh" content="0;url=https://evil.test/refresh"><style>h1{color:rgb(200,0,0)}</style>'
          +'<link rel="stylesheet" href="https://evil.test/style.css"></head><body><h1>Launch page</h1><img src="https://evil.test/pixel.png">'
          +'<img alt="dot" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">'
          +'<script>document.body.dataset.ran="yes";fetch("/api/me")</script><script src="https://evil.test/x.js"></script>'
          +'<svg width="120" height="20"><a href="https://evil.test/svg"><text x="0" y="15">SVG link</text></a></svg>'
          +'<div><template shadowrootmode="open"><a href="https://evil.test/shadow">Shadow link</a></template></div>'
          +'<iframe srcdoc="&lt;a href=&quot;https://evil.test/nested&quot;&gt;Nested link&lt;/a&gt;"></iframe>'
          +'<a href="https://evil.test/more">Read more</a><form action="https://evil.test/form"><button>Go</button></form></body></html>'});
      const meta=p.match(/^\/api\/v2\/files\/([^/]+)\/meta$/);
      if(meta)return json({id:meta[1],name:{'file-md-0001':'review-packet.md','file-png-0001':'storyboard.png','file-pdf-0001':'lease.pdf','file-html-0001':'launch.html'}[meta[1]]||'x',size:1200,content_type:'application/octet-stream'});
      if(p==='/assets/cover.png')return route.fulfill({contentType:'image/png',body:Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000'+'1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082','hex')});
      if(p==='/api/v2/files/file-pdf-0001')return route.fulfill({contentType:'application/octet-stream',headers:{'content-disposition':"attachment; filename*=UTF-8''lease.pdf"},body:'%PDF-1.4 fixture'});
      if(/youtube-nocookie\.com$/.test(url.hostname))return route.fulfill({contentType:'text/html',body:'<p>player</p>'});
      if(p==='/api/v2/conversations'){const bot=url.searchParams.get('chat_with');
        return json({conversations:bot?[{id:'c-'+bot,kind:'chat',scope:'personal',participants:['human:ana','bot:'+bot]}]:[]});}
      if(p.startsWith('/api/v2/conversations/c-')&&p.endsWith('/snapshot')){const bot=p.split('/')[4].slice(2);
        const body=JSON.stringify({messages:[{id:'m-'+bot,sender_id:'bot:'+bot,body:bot==='legal'?'I need your **review** of these:\n\n- [review packet](https://tico-ui.test/api/v2/files/file-md-0001)\n- [storyboard](https://tico-ui.test/api/v2/files/file-png-0001)\n- [cover photo](https://tico-ui.test/assets/cover.png)\n- [walkthrough](https://youtu.be/dQw4w9WgXcQ)\n- [lease](https://tico-ui.test/api/v2/files/file-pdf-0001)':bot==='designer'?'Here is the [launch page](https://tico-ui.test/api/v2/files/file-html-0001); the [style guide](https://evil.test/guide.html) is on the web.':bot==='finance'?'I gave Inbox [quote instructions](https://tico-ui.test/tasks/979a861b-a309-43db-a443-732a3674de81). See [goals](https://tico-ui.test/#/goals) and [the lender](https://lender.example/).':'Hello from '+bot,created_at:new Date().toISOString()}],execution:null});
        return setTimeout(()=>route.fulfill({contentType:'application/json',body}),slow);}
      if(p==='/api/v2/tasks/979a861b-a309-43db-a443-732a3674de81')return json({task:{id:'979a861b-a309-43db-a443-732a3674de81',title:'Seek written financing quotes',owner_id:'bot:inbox',requester_id:'bot:finance',status:'todo',lane:'company',labels:[],links:[],parts:{total:0,done:0},version:1,updated_at:new Date().toISOString()},children:[]});
      if(p==='/api/v2/tasks'){const owner=url.searchParams.get('owner');
        const body=JSON.stringify({tasks:owner?[{id:'t-'+owner,title:'Task for '+owner,owner_id:'bot:'+owner,requester_id:'human:ana',status:'doing',lane:'company',labels:[],links:[],parts:{total:0,done:0},version:1,updated_at:new Date().toISOString()}]:[]});
        return setTimeout(()=>route.fulfill({contentType:'application/json',body}),slow);}
      if(p==='/api/v2/chat/legal'){
        sends.push(route.request().headers()['idempotency-key']);
        if(offline)return route.abort('internetdisconnected');
        return json({conversation:{id:'legal-chat'},message:{id:'m1',sender_id:'human:ana',body:'Hello offline',created_at:new Date().toISOString()}});
      }
      if(p==='/api/v2/uploads/chat/legal'){
        uploads.push({path:p,body:route.request().postDataBuffer().toString()});
        if(false)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{detail:'Fixture upload unavailable'}})});
        return json({conversation:{id:'legal-upload'},message:{id:'m1',sender_id:'human:ana',body:'Review this fixture',created_at:new Date().toISOString()}});
      }
      return json(unrouted(p));
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
    // An HTML file shows as it looks, in a sandboxed frame: its styles and embedded pictures, but no script,
    // nothing fetched from the network, no refresh away and no form; its links open in a new tab.
    await page.setViewportSize({width:1200,height:800});
    await page.goto('https://tico-ui.test/#/bot/designer/chat');
    assert.equal(await page.getByRole('link',{name:'style guide'}).getAttribute('data-media'),null,'a web page stays a link');
    await page.waitForFunction(()=>document.querySelector('a[href$="file-html-0001"]')?.dataset.media==='HTML');
    await page.getByRole('link',{name:'launch page'}).click();
    const frameEl=page.locator('#doc-viewer[open] .viewer-html iframe.html-view');
    await frameEl.waitFor();
    assert.equal(await page.locator('#doc-viewer h2').innerText(),'launch.html');
    assert.equal(await frameEl.getAttribute('sandbox'),'allow-popups allow-popups-to-escape-sandbox');
    assert.equal(await frameEl.getAttribute('src'),null,'never loaded from a Tico address');
    const srcdoc=await frameEl.getAttribute('srcdoc');
    assert.match(srcdoc,/^<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none';/);
    assert.doesNotMatch(srcdoc,/refresh/);
    const inner=page.frameLocator('#doc-viewer .viewer-html iframe');
    await inner.locator('h1').waitFor();
    assert.equal(await inner.locator('h1').innerText(),'Launch page');
    assert.equal(await inner.locator('h1').evaluate(h=>getComputedStyle(h).color),'rgb(200, 0, 0)','its own styles apply');
    assert.equal(await inner.locator('img[alt="dot"]').evaluate(i=>i.decode().then(()=>i.naturalWidth)),1,'an embedded picture shows');
    assert.equal(await inner.locator('a[href$="/more"]').getAttribute('target'),'_blank');
    // Links the plain rewrite would miss open a new tab too, and the frame stays on the file.
    const popups=[];page.context().on('page',p=>popups.push(p));
    await inner.getByText('SVG link').click();
    await inner.getByText('Shadow link').click();
    await inner.frameLocator('iframe').getByText('Nested link').click();
    await page.waitForTimeout(500);
    assert.equal(popups.length,3,'each link opened a new tab');
    for (const p of popups) await p.close();
    await inner.getByRole('button',{name:'Go'}).click();
    await page.waitForTimeout(500);
    assert.equal(await inner.locator('body').getAttribute('data-ran'),null,'no script ran');
    assert.equal(await inner.locator('h1').innerText(),'Launch page','still the file: no link, refresh or form sent it away');
    // The three clicked links reached their remote pages in new tabs; nothing else was fetched.
    assert.deepEqual(outside.filter(u=>!/\/(svg|shadow|nested)$/.test(u)),[],'nothing fetched from the network');
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, 'viewer-html-desktop.png')});
    const [saved2]=await Promise.all([page.waitForEvent('download'),page.locator('#doc-viewer [data-doc-download]').click()]);
    assert.equal(saved2.suggestedFilename(),'launch.html');
    await page.locator('#doc-viewer [data-doc-close]').click();
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
    console.log('doc-viewer: ok (Markdown formatted; images, the thumbnail, YouTube and PDF in the one viewer with arrows; HTML in a sandboxed frame)');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
