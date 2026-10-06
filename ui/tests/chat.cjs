// The chat, offline. Fixtures only, no network.
//  - offline retry: a message the network dropped sends itself when the connection is back, once, with the
//    same request id.
//  - live reply: a bot's separate messages stay apart (the server joins them with a blank line,
//    execution.text and execution.parts) and show as separate paragraphs, not one run-on line
//    ("keep the bot planned.I've filed the build").
//  - outline: a prompt on a page not loaded yet is paged in and brought into view; the draft stays put.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');

async function offlineRetry(browser) {
  const page = await browser.newPage({viewport:{width:390,height:844},hasTouch:true,serviceWorkers:'block'});
  await page.clock.install();
  const errors=[],uploads=[],sends=[];let offline=true;
  page.on('pageerror',e=>errors.push(e.message));
  const bots=[{name:'legal',display_name:'Legal',host:'keeper',status:'active',can_chat:true,schedules:[]}];
  await page.route('**/*',route=>{
    const p=new URL(route.request().url()).pathname;
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
    if(p==='/api/v2/conversations')return json({conversations:[]});
    if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
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
  await page.goto('https://tico-ui.test/#/bot/legal/chat');
  await page.waitForFunction(()=>BOT?.slug==='legal' && V2C?.rendered);
  const box=page.locator('#chat-composer textarea');
  await box.fill('Hello offline');
  await page.locator('#chat-composer').getByRole('button',{name:'Send',exact:true}).click();
  await page.getByText('No connection. Your message will send by itself',{exact:false}).waitFor();
  assert.equal(sends.length,3,'three quick tries, then it waits');
  assert.equal(await box.inputValue(),'Hello offline','the message stays in the box');
  offline=false;
  await page.evaluate(()=>window.dispatchEvent(new Event('online')));
  await page.waitForFunction(()=>!document.querySelector('#chat-composer textarea').value);
  assert.equal(sends.length,4,'sent once when back online');
  assert.equal(new Set(sends).size,1,'every try carries the same request id');
  // Waiting out the backoff sends nothing more.
  await page.clock.fastForward(60000);
  await page.waitForFunction(()=>!BOT_PILL.sending);
  assert.equal(sends.length,4);
  assert.deepEqual(errors,[]);
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable:true,
    value:{writeText:async text => {window.copiedMessage=text;}}}));
  const copy=page.locator('.bubble.you').getByRole('button',{name:'Copy message',exact:true});
  await copy.tap();
  assert.equal(await page.evaluate(()=>window.copiedMessage),'Hello offline');
  assert.ok((await copy.boundingBox()).width>=40,'touch target remains visible and usable');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no mobile overflow');
  console.log('chat offline retry and touch copy: ok');
}

async function liveReply(browser) {
  const page = await browser.newPage({viewport:{width:1200,height:800},serviceWorkers:'block'});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const now=new Date().toISOString();
  const snapshot={messages:[{id:'m1',from_actor:'human:ana',body:'Plan the build',created:now,
                             run:{job_id:'j1',attempt_id:'a1',state:'started_run'}}],
    execution:{job_id:'j1',message_id:'m1',bot:'ops',attempt_id:'a1',state:'running',label:'Working',
      text:'Keep the bot planned.\n\nI filed the build.',
      parts:[{kind:'progress',text:'Keep the bot planned.',at:now},{kind:'tool',text:'Ran hub task create',at:now},
             {kind:'reply',text:'I filed the build.',at:now}]}};
  await page.context().route('**/*',route=>{
    const url=new URL(route.request().url()),p=url.pathname;
    const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if(p==='/')return route.fulfill({contentType:'text/html',body:html});
    const ui=p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if(ui){const file=uiFile(ui[1]);
      if(fs.existsSync(file))return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript',body:fs.readFileSync(file,'utf8')});}
    if(/\/marked\.min\.js$/.test(p))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'../vendor/marked.min.js'),'utf8')});
    if(p==='/api/me')return json({id:'ana',name:'Ana',role:'owner',cloud:true});
    if(p==='/api/employees')return json([{name:'ops',display_name:'Ops',host:'keeper',status:'active',can_chat:true,schedules:[]}]);
    if(p==='/api/issues')return json([]);
    if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:'event: snapshot\ndata: '+JSON.stringify(snapshot)+'\n\n'});
    if(p==='/api/v2/conversations'){const b=url.searchParams.get('chat_with');
      return json({conversations:b?[{id:'c-ops',kind:'chat',scope:'personal',participants:['human:ana','bot:ops']}]:[]});}
    if(p.startsWith('/api/v2/conversations/c-')&&p.endsWith('/snapshot'))return json(snapshot);
    return json({});
  });
  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.locator('#v2-live').waitFor();
  const paragraphs=await page.locator('#v2-live p').allInnerTexts();
  assert.deepEqual(paragraphs,['Keep the bot planned.','I filed the build.'],'each message is its own paragraph');
  // Avatars: a bot is an SVG blob that morphs while it answers; a person stays a circle; motion stops under reduced motion.
  const av=await page.evaluate(()=>{
    const el=document.querySelector('#bot-top .av'),path=el?.querySelector('svg.av-shape path');
    const probe=document.createElement('div');
    probe.innerHTML=personCircle('Ana Reyes',22)+botAvatar({name:'ops',icon:'rocket_launch'},22);
    document.body.append(probe);
    const [person,glyph]=probe.children;
    return {blob:!!el?.classList.contains('blob'),d:/^M[\d.]+ [\d.]+(C[-\d. ]+){6,8}Z$/.test(path?.getAttribute('d')||''),
      morph:!!el?.classList.contains('morph'),moving:getComputedStyle(path).animationName==='av-morph'||!!path.querySelector('animate'),
      person:!person.querySelector('svg')&&getComputedStyle(person).borderRadius==='50%',
      glyph:glyph.querySelector('.av-glyph')?.textContent,same:botAvatar('ops',22)===botAvatar('ops',22)};
  });
  assert.deepEqual(av,{blob:true,d:true,morph:true,moving:true,person:true,glyph:'rocket_launch',same:true},'bot blob, person circle');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.evaluate(()=>{const p=document.querySelector('#bot-top .av-shape path');
    return getComputedStyle(p).animationName==='none'&&!botAvatar('ops',36).includes('<animate');}),true,'reduced motion: no morph');
  assert.deepEqual(errors,[]);
  const pending = await page.evaluate(() => {
    const state = {slug:'ops', messages:[{id:'queued',created:new Date(Date.now()-30*60000).toISOString()}],
      execution:{message_id:'queued',state:'queued',label:'Saved — no AI provider is chosen',readiness_reason:'missing_provider'}};
    const el = document.createElement('div');
    el.innerHTML = v2PendingHTML(state);
    const link = el.querySelector('a');
    const result = {text:el.textContent,href:link?.getAttribute('href'),tab:link?.dataset.gsTab};
    delete state.execution.readiness_reason;
    const legacy = v2PendingHTML(state);
    clearTimeout(state.waitTimer);
    return {...result,legacy};
  });
  assert.match(pending.text,/no AI provider is chosen/);
  assert.match(pending.text,/Add an AI provider/);
  assert.equal(pending.href,'#/settings');
  assert.equal(pending.tab,'providers');
  assert.match(pending.legacy,/No reply after 20 minutes/);
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable:true,
    value:{writeText:async text => {window.copiedMessage=text;}}}));
  const liveCopy=page.locator('#v2-live').locator('..').getByRole('button',{name:'Copy message',exact:true});
  await liveCopy.focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>window.copiedMessage),snapshot.execution.text,'keyboard copies raw live text');
  const raw='**Example** <img src=x onerror="alert(1)"> & "quotes"\nSecond line';
  await page.evaluate(text => {
    V2C.live.text=text;
    v2ChatRender(V2C);
  },raw);
  await liveCopy.click();
  assert.equal(await page.evaluate(()=>window.copiedMessage),raw,'copies updated Markdown and literal markup exactly');
  assert.equal(await page.locator('.chat-message-actions img').count(),0,'copy data cannot inject markup');
  const position=await liveCopy.evaluate(button=>{
    const b=button.getBoundingClientRect(), bubble=button.closest('.bubble').getBoundingClientRect();
    return {right:b.right<=bubble.right,bottom:b.bottom<=bubble.bottom,nearRight:bubble.right-b.right<30};
  });
  assert.deepEqual(position,{right:true,bottom:true,nearRight:true},'copy sits inside bottom right of the bubble');
  await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw new Error('Denied');};});
  await liveCopy.click();
  await page.getByText('Could not copy the message',{exact:true}).waitFor();
  // The shared fallback must report failure honestly and restore keyboard focus.
  await page.evaluate(()=>{
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
    document.execCommand=()=>false;
  });
  await liveCopy.focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(()=>[...document.querySelectorAll('.toast.err')].filter(el=>el.textContent==='Could not copy the message').length===2);
  assert.equal(await liveCopy.evaluate(button=>document.activeElement===button),true,'fallback restores focus');
  assert.equal(await page.locator('body > textarea').count(),0,'fallback removes temporary field');
  await page.evaluate(()=>{document.execCommand=()=>{window.copiedMessage=document.activeElement.value;return true;};});
  await page.keyboard.press('Space');
  assert.equal(await page.evaluate(()=>window.copiedMessage),raw,'legacy clipboard copies raw text');

  const noticeFixture = async message => page.evaluate(m=>{
    V2C.messages=[m];
    V2C.execution=null;
    V2C.live=null;
    v2ChatRender(V2C);
  },message);
  const hostilePath='<img src=x onerror="alert(1)">.md';
  const body='Finished the review.\n\nleft out of the commit: '+hostilePath+' (contains a secret)\n\nleft out of the commit: notes.txt (contains a secret)\n\nnot pushed: a commit made this turn contains a secret';
  await noticeFixture({id:'notice-structured',from_actor:'bot:ops',kind:'say',body,created:now,
    refs:{turn_id:'a-notice',run:{job_id:'j-notice',attempt_id:'a-notice'},commit_exclusions:[
      {path:hostilePath,reason:'contains a secret'},{path:'notes.txt',reason:'contains a secret'}]}});
  const noticeBubble=page.locator('#conv-thread .bubble');
  const disclosure=noticeBubble.locator('details.commit-exclusions');
  assert.equal(await disclosure.count(),1,'multiple exclusions share one disclosure');
  assert.equal(await disclosure.evaluate(el=>el.open),false,'exclusion details start collapsed');
  assert.match(await noticeBubble.innerText(),/Finished the review\./,'ordinary answer remains visible');
  assert.match(await noticeBubble.innerText(),/not pushed: a commit made this turn contains a secret/,'not-pushed warning stays visible');
  assert.match(await noticeBubble.innerText(),/2 files excluded from commit/,'summary reports the file count');
  const summary=disclosure.locator('summary');
  assert.equal(await summary.innerText(),'2 files excluded from commit','native summary has an accessible text label');
  await summary.focus();
  await page.keyboard.press('Enter');
  assert.equal(await disclosure.evaluate(el=>el.open),true,'Enter expands exclusion details');
  await page.waitForFunction(()=>V2C.openCommitExclusions.has('notice-structured'));
  await page.evaluate(()=>v2ChatRender(V2C));
  const refreshedBubble=page.locator('#conv-thread .bubble');
  assert.equal(await refreshedBubble.locator('details.commit-exclusions').evaluate(el=>el.open),true,'an ordinary live refresh preserves expansion');
  const refreshedDisclosure=refreshedBubble.locator('details.commit-exclusions');
  assert.equal(await refreshedDisclosure.locator('code').nth(0).innerText(),hostilePath,'hostile filename is shown as escaped text');
  assert.equal(await refreshedDisclosure.locator('img').count(),0,'filename cannot inject HTML');
  await summary.focus();
  await page.keyboard.press('Space');
  assert.equal(await refreshedDisclosure.evaluate(el=>el.open),false,'Space collapses exclusion details');
  await page.waitForFunction(()=>!V2C.openCommitExclusions.has('notice-structured'));
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,
    value:{writeText:async text=>{window.copiedMessage=text;}}}));
  await refreshedBubble.getByRole('button',{name:'Copy message',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.copiedMessage),body,'copy preserves the original transcript text');

  const oldNotice='left out of the commit: old/report.md (contains a secret)';
  await noticeFixture({id:'notice-legacy',from_actor:'bot:ops',kind:'say',body:'Older answer.\n\n'+oldNotice,created:now,
    refs:{turn_id:'old-attempt',run:{job_id:'old-job',attempt_id:'old-attempt'}}});
  const legacyBubble=page.locator('#conv-thread .bubble');
  assert.equal(await legacyBubble.locator('details.commit-exclusions').evaluate(el=>el.open),false,'historical bot-run suffix is collapsed');
  assert.match(await legacyBubble.innerText(),/Older answer\./);
  assert.doesNotMatch(await legacyBubble.innerText(),/old\/report\.md/);

  await noticeFixture({id:'notice-only',from_actor:'bot:ops',kind:'say',body:oldNotice,created:now,
    refs:{turn_id:'only-attempt',run:{job_id:'only-job',attempt_id:'only-attempt'}}});
  const onlyBubble=page.locator('#conv-thread .bubble');
  assert.equal(await onlyBubble.locator('.md').count(),0,'notice-only reply does not leave an empty answer block');
  assert.equal(await onlyBubble.locator('details.commit-exclusions').count(),1);

  await noticeFixture({id:'human-example',from_actor:'human:ana',kind:'say',body:'Example: '+oldNotice,created:now,refs:{}});
  const humanBubble=page.locator('#conv-thread .bubble');
  assert.equal(await humanBubble.locator('details.commit-exclusions').count(),0,'human examples never collapse');
  assert.match(await humanBubble.innerText(),/left out of the commit: old\/report\.md/);

  await noticeFixture({id:'bot-quote',from_actor:'bot:ops',kind:'say',body:'The exact text is:\n\n'+oldNotice+'\n\nKeep it visible.',created:now,
    refs:{turn_id:'quote-attempt',run:{job_id:'quote-job',attempt_id:'quote-attempt'}}});
  const quoteBubble=page.locator('#conv-thread .bubble');
  assert.equal(await quoteBubble.locator('details.commit-exclusions').count(),0,'quoted or non-terminal prose remains visible');
  assert.match(await quoteBubble.innerText(),/left out of the commit: old\/report\.md/);
  assert.deepEqual(errors,[]);
  console.log('chat live reply and keyboard copy: ok');
}

async function outlineJump(browser) {
  const page = await browser.newPage({viewport:{width:1280,height:800},serviceWorkers:'block'});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const at=n=>new Date(Date.UTC(2026,9,5,9,n)).toISOString();
  const pair=(n,text)=>[{id:'h'+n,from_actor:'human:ana',body:text,created:at(n*2)},{id:'b'+n,from_actor:'bot:ops',body:'Done: '+text,created:at(n*2+1)}];
  const older=[...pair(0,'Check the release notes'),...pair(1,'Draft the launch email')];
  const latest=[2,3,4,5,6,7,8].flatMap(n=>pair(n,'Prompt number '+n));
  const snapshot={messages:latest,next_before:'h2',execution:null};
  const prompts=[...older,...latest].filter(m=>m.from_actor.startsWith('human:')).map(m=>({id:m.id,created:m.created,text:m.body,task:m.id==='h0'?{id:'t1',title:'Release v1'}:null}));
  const pages=[];
  await page.context().route('**/*',route=>{
    const url=new URL(route.request().url()),p=url.pathname;
    const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if(p==='/')return route.fulfill({contentType:'text/html',body:html});
    const ui=p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if(ui){const file=uiFile(ui[1]);
      if(fs.existsSync(file))return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript',body:fs.readFileSync(file,'utf8')});}
    if(/\/marked\.min\.js$/.test(p))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'../vendor/marked.min.js'),'utf8')});
    if(p==='/api/me')return json({id:'ana',name:'Ana',role:'owner',cloud:true});
    if(p==='/api/employees')return json([{name:'ops',display_name:'Ops',host:'keeper',status:'active',can_chat:true,schedules:[]}]);
    if(p==='/api/issues')return json([]);
    if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:'event: snapshot\ndata: '+JSON.stringify(snapshot)+'\n\n'});
    if(p==='/api/v2/conversations'){const b=url.searchParams.get('chat_with');
      return json({conversations:b?[{id:'c-ops',kind:'chat',scope:'personal',participants:['human:ana','bot:ops']}]:[]});}
    if(p==='/api/v2/conversations/c-ops/snapshot')return json(snapshot);
    if(p==='/api/v2/conversations/c-ops/outline')return json({prompts});
    if(p==='/api/v2/conversations/c-ops/messages'){pages.push(url.searchParams.get('before'));return json({messages:older,next_before:null});}
    return json({});
  });
  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.waitForFunction(()=>V2C?.rendered&&V2C.loaded);
  await page.locator('#chat-composer textarea').fill('half-written thought');
  await page.getByRole('button',{name:'Outline',exact:true}).click();
  const rows=page.locator('#chat-outline .co-row');
  await rows.first().waitFor();
  assert.equal(await rows.count(),9,'every prompt, loaded or not');
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.id),'h8','focus starts on the newest');
  for(let i=0;i<8;i++)await page.keyboard.press('ArrowUp');
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.id),'h0');
  if(process.env.TICO_SHOTS)await page.screenshot({path:process.env.TICO_SHOTS+'/outline-desktop.png'});
  await page.keyboard.press('Enter');
  const target=page.locator('#conv-thread [data-message="h0"]');
  await target.waitFor();
  assert.deepEqual(pages,['h2'],'one older page, from the existing cursor');
  await page.waitForFunction(()=>{const t=document.querySelector('#conv-thread'),r=document.querySelector('[data-message="h0"]').getBoundingClientRect(),b=t.getBoundingClientRect();
    return r.top>=b.top-1&&r.bottom<=b.bottom+1;});
  assert.equal(await page.evaluate(()=>V2C.followLatest),false,'a jump stops following the end');
  assert.equal(await page.locator('#chat-composer textarea').inputValue(),'half-written thought','the draft stays');
  assert.equal(await page.locator('#chat-outline').evaluate(d=>d.open),false);
  // Phone: the same list is a bottom sheet with thumb-sized rows.
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Outline',exact:true}).click();
  await rows.first().waitFor();
  const sheet=await page.evaluate(()=>{const d=document.querySelector('#chat-outline').getBoundingClientRect(),r=document.querySelector('#chat-outline .co-row').getBoundingClientRect();
    return {bottom:Math.round(d.bottom)===innerHeight,full:Math.round(d.width)===innerWidth,tall:r.height>=44,overflow:document.documentElement.scrollWidth>innerWidth};});
  assert.deepEqual(sheet,{bottom:true,full:true,tall:true,overflow:false});
  if(process.env.TICO_SHOTS)await page.screenshot({path:process.env.TICO_SHOTS+'/outline-phone.png'});
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#chat-outline').evaluate(d=>d.open),false,'Escape closes');
  assert.deepEqual(errors,[]);
  console.log('chat outline jump: ok');
}

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless:true}) : await chromium.launch({channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined,headless:true});
  try {
    await offlineRetry(browser);
    await liveReply(browser);
    await outlineJump(browser);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
