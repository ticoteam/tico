// The chat, offline. Fixtures only, no network.
//  - offline retry: a message the network dropped sends itself when the connection is back, once, with the
//    same request id.
//  - live reply: a bot's separate messages stay apart (the server joins them with a blank line,
//    execution.text and execution.parts) and show as separate paragraphs, not one run-on line
//    ("keep the bot planned.I've filed the build").
//  - outline: a prompt on a page not loaded yet is paged in and brought into view; the draft stays put.
//  - activity fold: task and note cards in a row fold into one "N updates" row; older history loads only at the top.
//  - side threads: the chat is the person and the bot; bot-to-bot talk, relays, notices and notes fold into one line
//    per run, open in place, and never push the conversation's last message off the bottom. Another bot's conversation
//    comes as its newest message until it is opened.
//  - stays at the bottom: new messages, streamed text and images keep the newest line in view; scrolled up, nothing
//    moves and a "New reply" pill takes you down.
//  - Settings links: the waiting line's Add an AI provider and Sign in on Settings > Computers open Settings on that tab.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

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
      return json({conversation:{id:'legal-chat'},message:{id:'m1',sender_id:'human:ana',body:'Hello offline',created_at:new Date().toISOString()}});
    }
    if(p==='/api/v2/uploads/chat/legal'){
      uploads.push({path:p,body:route.request().postDataBuffer().toString()});
      if(false)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{detail:'Fixture upload unavailable'}})});
      return json({conversation:{id:'legal-upload'},message:{id:'m1',sender_id:'human:ana',body:'Review this fixture',created_at:new Date().toISOString()}});
    }
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/legal/chat');
  await page.waitForFunction(()=>BOT?.slug==='legal' && V2C?.rendered);
  const box=page.locator('#chat-composer textarea');
  await box.fill('Hello offline');
  await page.locator('#chat-composer').getByRole('button',{name:'Send',exact:true}).click();
  await page.getByText('Offline or connection lost. Your message will send by itself',{exact:false}).waitFor();
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
  // On touch no copy button floats in the thread; a tap on the message shows it, on its own line under the words.
  const you=page.locator('.bubble.you');
  const copy=you.getByRole('button',{name:'Copy message',exact:true});
  assert.equal(await copy.isVisible(),false,'hidden until the message is tapped');
  await you.tap();
  await copy.tap();
  assert.equal(await page.evaluate(()=>window.copiedMessage),'Hello offline');
  assert.ok((await copy.boundingBox()).width>=40,'touch target remains usable');
  const clear=await page.evaluate(()=>{
    const you=document.querySelector('.bubble.you');
    you.insertAdjacentHTML('afterend',`<div class="bubble bot reply acts"><div class="md"><p>Merged, see <a href="#/x">the pull request</a></p></div>${chatCopyHTML('Merged')}</div>`);
    const bubble=you.nextElementSibling, range=document.createRange();
    range.selectNodeContents(bubble.querySelector('.md'));
    const b=bubble.querySelector('.chat-message-copy').getBoundingClientRect();
    return {coarse:matchMedia('(pointer:coarse)').matches,clear:[...range.getClientRects()].every(r=>r.bottom<=b.top+1||r.right<=b.left+1)};
  });
  assert.deepEqual(clear,{coarse:true,clear:true},'on touch the copy target covers no reply text, so a trailing link stays tappable');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no mobile overflow');
  // The bot switcher over the chat bar: a round menu button sits left of Search and opens the side menu in one tap.
  await page.locator('.p-bot').tap();
  const menu=page.locator('#org-fan [data-org-menu]');
  await menu.waitFor();
  const row=await menu.evaluate(el=>{const m=el.getBoundingClientRect(),s=el.nextElementSibling.getBoundingClientRect();
    return {next:el.nextElementSibling.dataset.orgSearch,left:m.right<=s.left,row:Math.abs((m.top+m.bottom)-(s.top+s.bottom))<2,round:Math.abs(m.width-m.height)<2,
      label:el.getAttribute('aria-label'),controls:el.getAttribute('aria-controls'),icon:el.textContent.trim(),tab:el.tabIndex};});
  assert.deepEqual(row,{next:'',left:true,row:true,round:true,label:'Open menu',controls:'side',icon:'menu',tab:0});
  await menu.tap();
  await page.locator('body.drawer').waitFor();
  assert.equal(await page.locator('#org-fan').isHidden(),true,'the switcher closes');
  assert.equal(await page.locator('#side .side-scroll').isVisible(),true,'the side menu is open');
  await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.body.classList.contains('drawer'));
  assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('p-bot')),true,'closing returns to the bot icon');
  // From the keyboard too: Enter on the focused menu button opens the menu.
  await page.locator('.p-bot').click();
  await menu.focus();
  await page.keyboard.press('Enter');
  await page.locator('body.drawer').waitFor();
  await page.locator('.side-close').click();
  await page.waitForFunction(()=>!document.body.classList.contains('drawer'));
  assert.deepEqual(errors,[]);
  console.log('chat offline retry and touch copy: ok');
}

async function liveReply(browser) {
  const page = await browser.newPage({viewport:{width:1200,height:800},serviceWorkers:'block'});
  const errors=[],stops=[];
  page.on('pageerror',e=>errors.push(e.message));
  const now=new Date().toISOString();
  const snapshot={messages:[{id:'m1',sender_id:'human:ana',body:'Plan the build',created_at:now,
                             run:{job_id:'j1',run_id:'a1',state:'started_run'}},
                            {id:'m2',sender_id:'human:ana',recipient_id:'bot:ops',body:'Do April instead',created_at:now,
                             delivery:{run_id:'a1',mode:'next_turn'}}],
    execution:{job_id:'j1',message_id:'m1',bot:'ops',run_id:'a1',state:'running',label:'Working',
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
    if(p==='/api/v2/bots/ops/stop'){stops.push(route.request().postDataJSON());return json({run_id:'a1',stopping:true});}
    // Support off: its load 3 s in would otherwise add Report this to the copy-error toasts matched below.
    if(p==='/api/v2/support/tickets')return json({enabled:false});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.locator('#v2-live').waitFor();
  // While the bot runs, Stop sits where Send is; typing brings Send back so a follow-up can still queue.
  const composer=page.locator('#chat-composer'),stop=composer.getByRole('button',{name:'Stop',exact:true});
  await stop.waitFor();
  assert.equal(await composer.getByRole('button',{name:'Send',exact:true}).isVisible(),false);
  await composer.locator('textarea').fill('One more thing');
  assert.equal(await stop.isVisible(),false,'typing brings Send back');
  await composer.locator('textarea').fill('');
  await stop.click();
  await page.waitForFunction(()=>BOT_PILL.stopping);
  assert.deepEqual(stops,[{run_id:'a1'}],'stops the run this chat shows');
  // A message this host holds for the next turn says so, and its sender can have it replace the turn.
  const held=page.locator('[data-message="m2"] .chat-delivery');
  assert.match(await held.innerText(),/Queued for the next turn/);
  await held.getByRole('button',{name:'Stop and redirect'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-message="m2"] .chat-delivery')?.textContent.startsWith('Stopping'));
  assert.deepEqual(stops[1],{run_id:'a1',redirect:'m2'},'stops that run and names the message to start with');
  const paragraphs=await page.locator('#v2-live p').allInnerTexts();
  assert.deepEqual(paragraphs,['Keep the bot planned.','I filed the build.'],'each message is its own paragraph');
  // Avatars: a bot is an SVG blob that breathes while it answers; a person stays a circle; motion stops under reduced motion.
  const av=await page.evaluate(()=>{
    const el=document.querySelector('#bot-top .av'),path=el?.querySelector('svg.av-shape path');
    const probe=document.createElement('div');
    probe.innerHTML=personCircle('Ana Reyes',22)+botAvatar({name:'ops',icon:'rocket_launch'},22);
    document.body.append(probe);
    const [person,glyph]=probe.children;
    return {blob:!!el?.classList.contains('blob'),d:/^M[\d.]+ [\d.]+(C[-\d. ]+){6,8}Z$/.test(path?.getAttribute('d')||''),
      morph:!!el?.classList.contains('morph'),moving:getComputedStyle(el.querySelector('svg.av-shape')).animationName==='av-breathe',
      person:!person.querySelector('svg')&&getComputedStyle(person).borderRadius==='50%',
      glyph:glyph.querySelector('.av-glyph')?.textContent,same:botAvatar('ops',22)===botAvatar('ops',22),
      still:!/ morph/.test(botAvatar('ops',22))};
  });
  // Only the page's own bot breathes: the same working bot in a row or the sidebar (under 30 px) keeps still.
  assert.deepEqual(av,{blob:true,d:true,morph:true,moving:true,person:true,glyph:'rocket_launch',same:true,still:true},'bot blob, person circle');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('#bot-top .av-shape')).animationName==='none'),true,'reduced motion: no breath');
  assert.deepEqual(errors,[]);
  const pending = await page.evaluate(() => {
    const state = {slug:'ops', messages:[{id:'queued',created_at:new Date(Date.now()-30*60000).toISOString()}],
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
  assert.equal(pending.tab,'models');
  assert.match(pending.legacy,/No reply after 20 minutes/);
  // A provider is chosen but the bot's computer is not signed in to its model: the same place says so, with Sign in and Health.
  const signIn = await page.evaluate(() => {
    const state = {slug:'ops', messages:[{id:'queued',created_at:new Date(Date.now()-30*60000).toISOString()}],
      execution:{message_id:'queued',state:'queued',label:'Saved — Claude login required',readiness_reason:'model_sign_in'}};
    const el = document.createElement('div');
    el.innerHTML = v2PendingHTML(state);
    clearTimeout(state.waitTimer);
    return {text:el.textContent,tabs:[...el.querySelectorAll('a')].map(a => a.getAttribute('href') + ' ' + a.dataset.gsTab)};
  });
  assert.match(signIn.text,/Its computer is not signed in to its model/);
  assert.deepEqual(signIn.tabs,['#/settings devices','#/settings health']);
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
    return {beside:b.left>=bubble.right-1,bottom:Math.abs(b.bottom-bubble.bottom)<2,near:b.left-bubble.right<10};
  });
  assert.deepEqual(position,{beside:true,bottom:true,near:true},'copy sits just past the bubble\'s bottom right, off the words');
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
  assert.deepEqual(errors,[]);
  console.log('chat live reply and keyboard copy: ok');
}

async function outlineJump(browser) {
  const page = await browser.newPage({viewport:{width:1280,height:800},serviceWorkers:'block'});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const at=n=>new Date(Date.UTC(2026,9,5,9,n)).toISOString();
  const pair=(n,text)=>[{id:'h'+n,sender_id:'human:ana',body:text,created_at:at(n*2)},{id:'b'+n,sender_id:'bot:ops',body:'Done: '+text,created_at:at(n*2+1)}];
  const older=[...pair(0,'Check the release notes'),...pair(1,'Draft the launch email')];
  const latest=[2,3,4,5,6,7,8].flatMap(n=>pair(n,'Prompt number '+n));
  const snapshot={messages:latest,next_before:'h2',execution:null};
  const prompts=[...older,...latest].filter(m=>m.sender_id.startsWith('human:')).map(m=>({id:m.id,created_at:m.created_at,text:m.body,task:m.id==='h0'?{id:'t1',title:'Release v1'}:null}));
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
      return json({conversations:b?[{id:'c-ops',kind:'chat',scope:'personal',participants:['human:ana','bot:ops']},
        {id:'c-slack',kind:'chat',scope:'direct',participants:['human:ana','bot:ops']}]:[]});}
    if(p==='/api/v2/conversations/c-ops/snapshot')return json(snapshot);
    // A Slack thread's own room beside the usual chat (backend/slack_gateway.py opens one per thread).
    if(p==='/api/v2/conversations/c-slack/snapshot')return json({messages:[{id:'s1',kind:'say',sender_id:'bot:ops',recipient_id:'human:ana',
      body:'The chart from Slack.',created_at:'2026-10-06T09:59:00Z',refs:{}}],next_before:null,execution:null});
    if(p==='/api/v2/conversations/c-ops/outline')return json({prompts});
    if(p==='/api/v2/conversations/c-ops/messages'){pages.push(url.searchParams.get('before'));return json({messages:older,next_before:null});}
    return json(unrouted(p));
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
  assert.equal(await page.evaluate(()=>V2C.stick.atBottom),false,'a jump stops following the end');
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
  // The Slack "1 visual - view in Tico" line from a plain chat names the room the reply is in: a Slack thread's own
  // room opens in place of the usual chat, at the message; the query goes.
  await page.goto('https://tico-ui.test/?bot=ops&conversation=c-slack&message=s1');
  await page.locator('#conv-thread .jump-mark[data-message="s1"]').waitFor();
  assert.equal(new URL(page.url()).hash,'#/bot/ops');
  assert.equal(new URL(page.url()).search,'');
  // The next ordinary visit is the usual chat again, not the linked room.
  await page.evaluate(()=>{location.hash='#/tasks';});
  await page.evaluate(()=>{location.hash='#/bot/ops/chat';});
  await page.waitForFunction(()=>V2C?.loaded&&V2C.conv?.id==='c-ops');
  // In the usual room the link pages back to an older message like the outline does.
  await page.goto('https://tico-ui.test/?bot=ops&conversation=c-ops&message=h0');
  await page.locator('#conv-thread .jump-mark[data-message="h0"]').waitFor();
  // A room the reader is not in is not among their chats: the usual chat opens and says the message is not there.
  await page.goto('https://tico-ui.test/?bot=ops&conversation=c-theirs&message=x9');
  await page.locator('.toast',{hasText:'That message is not in this chat'}).waitFor();
  assert.equal(await page.evaluate(()=>V2C.conv.id),'c-ops');
  assert.deepEqual(errors,[]);
  console.log('chat outline jump: ok');
}

// Human prose that reads like a card is never folded.
async function activityFold(browser) {
  const page = await browser.newPage({viewport: {width: 1100, height: 760}, serviceWorkers: 'block'}), errors = [], pages = [];
  page.on('pageerror', e => errors.push(e.message));
  const at = n => new Date(Date.UTC(2026, 9, 6, 9, n)).toISOString();
  const activity = [
    {id:'task-created',kind:'notice',sender_id:'keeper',body:'New task from Head of Engineering: Review the next release',created_at:at(1),refs:{task:'task-1'}},
    {id:'note-one',kind:'say',sender_id:'bot:ops',body:'Cloud transcript shows performance goal. https://example.test/audit',created_at:at(2),refs:{task:'task-1',note:true}},
    {id:'task-status',kind:'notice',sender_id:'keeper',body:'Open: Review the next release',created_at:at(3),refs:{task:'task-1'}},
    {id:'note-two',kind:'say',sender_id:'bot:ops',body:'Reviewed the status note.',created_at:at(4),refs:{task:'task-1',note:true}},
  ];
  // Similar words and even a note ref do not turn human-authored prose into generated activity.
  const latest = [
    ...activity,
    {id:'human-quote',kind:'say',sender_id:'human:ana',body:'New task from Head of Engineering is only a quote here.',created_at:at(5),refs:{}},
    {id:'human-note',kind:'say',sender_id:'human:ana',body:'Note Cloud transcript shows performance goal.',created_at:at(6),refs:{task:'task-1',note:true}},
    {id:'bot-prose',kind:'say',sender_id:'bot:ops',body:'Note: I checked the release page.',created_at:at(7),refs:{task:'task-1'}},
    ...Array.from({length:12}, (_, i) => ({id:`chat-${i}`,kind:'say',sender_id:i % 2 ? 'bot:ops' : 'human:ana',body:`Regular conversation ${i}`,created_at:at(8+i),refs:{}})),
    {id:'task-done',kind:'notice',sender_id:'keeper',body:'Done: Review the next release',created_at:at(20),refs:{task:'task-1'}},
    {id:'chat-last',kind:'say',sender_id:'human:ana',body:'Thanks',created_at:at(21),refs:{}},
  ];
  const endedGoal = {id:'goal-ended',conversation_id:'c-ops',bot:'ops',objective:'Ship the finished update',status:'met',note:'Reviewed',ended_at:at(4.5),updated_at:at(4.5)};
  const snapshot = {messages: latest, next_before: 'cursor-1', execution: null, goal: endedGoal};
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html',body:html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType:ui[1].endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(uiFile(ui[1]),'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(uiFile('vendor/marked.min.js'),'utf8')});
    if (p === '/api/me') return json({id:'ana',name:'Ana',role:'owner',cloud:true});
    if (p === '/api/employees') return json([{name:'ops',display_name:'Ops',host:'keeper',status:'active',can_chat:true,schedules:[]}]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType:'text/event-stream',body:`event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n`});
    if (p === '/api/v2/conversations') return json({conversations:[{id:'c-ops',kind:'chat',scope:'personal',participants:['human:ana','bot:ops']}]});
    if (p === '/api/v2/conversations/c-ops/snapshot') return json(snapshot);
    if (p === '/api/v2/conversations/c-ops/messages') {
      const cursor = url.searchParams.get('before'); pages.push(cursor);
      return json(cursor === 'cursor-1'
        ? {messages:[{id:'older-a',kind:'say',sender_id:'human:ana',body:'Earlier question',created_at:at(0),refs:{}}],next_before:'cursor-2'}
        : {messages:[{id:'older-b',kind:'say',sender_id:'bot:ops',body:'Earlier answer',created_at:at(-1),refs:{}}],next_before:null});
    }
    if (p === '/api/v2/conversations/c-ops/goal') return json({goal:endedGoal,supported:true,commands:[]});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.waitForFunction(() => V2C?.rendered && V2C.loaded);

  const thread = page.locator('#conv-thread'), disclosure = page.locator('#conv-thread details.chat-side').first(), summary = disclosure.locator('> summary');
  assert.equal(await page.locator('#conv-thread details.chat-side').count(), 2, 'consecutive typed task and note records share one row; a lone one is a line of its own');
  assert.match(await page.locator('#conv-thread details.chat-side').nth(1).locator('> summary').innerText(), /Done: Review the next release/);
  assert.equal(await summary.locator('b').innerText(), '4 updates');
  assert.equal(await summary.locator('time').getAttribute('title'), await page.evaluate(t => fmt(t), at(4)), 'one time: the latest');
  assert.equal(await disclosure.evaluate(el => el.open), false, 'activity starts collapsed');
  await page.setViewportSize({width:320,height:740});
  const labelLines = await summary.locator('b').evaluate(el => {
    const range = document.createRange(); range.selectNodeContents(el);
    return range.getClientRects().length;
  });
  assert.equal(labelLines, 1, 'the collapsed activity label occupies one line at phone width');
  await page.setViewportSize({width:1100,height:760});

  assert.equal(await thread.locator('.chat-goal-line').count(), 1, 'the ended goal is not absorbed into the activity row');
  const topOrder = await thread.evaluate(el => [...el.children].filter(x => x.matches('.chat-side,.chat-goal-line'))
    .map(x => x.classList.contains('chat-side') ? 'activity' : 'goal'));
  assert.deepEqual(topOrder.slice(0,2), ['activity','goal'], 'goal-line insertion stays after grouped activity');
  assert.equal(await thread.locator('.bubble').count(), 15, 'ordinary chat remains in its own messages');
  assert.match(await thread.innerText(), /New task from Head of Engineering is only a quote here/);
  assert.match(await thread.innerText(), /Note Cloud transcript shows performance goal\./);
  assert.match(await thread.innerText(), /Note: I checked the release page\./);

  await summary.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#conv-thread details.chat-side')?.open);
  await page.waitForFunction(() => V2C.openFolds?.has('side:task-created'));
  await page.evaluate(() => v2ChatRender(V2C));
  await page.waitForFunction(() => document.querySelector('#conv-thread details.chat-side')?.open);
  assert.match(await disclosure.innerText(), /New task from Head of Engineering: Review the next release/);
  assert.match(await disclosure.innerText(), /Cloud transcript shows performance goal\./);
  await disclosure.locator('.cs-item a[href="https://example.test/audit"]').waitFor();
  assert.equal(await disclosure.locator('time').count(), 5, 'summary and all original activity entries retain their timestamps');
  await summary.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(() => !document.querySelector('#conv-thread details.chat-side')?.open);

  const older = page.locator('#conv-older'), button = older.locator('[data-cloud-older]');
  await thread.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForFunction(() => document.querySelector('#conv-older').hidden);
  assert.equal(await older.isHidden(), true, 'older history control stays away from the latest messages');
  await thread.evaluate(el => { el.scrollTop = 0; });
  await page.waitForFunction(() => !document.querySelector('#conv-older').hidden);
  const anchorBefore = await page.evaluate(() => {
    const t = document.querySelector('#conv-thread'), a = t.querySelector('details.chat-side').getBoundingClientRect();
    return a.top - t.getBoundingClientRect().top;
  });
  await button.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(() => V2C.messages.some(m => m.id === 'older-a'));
  assert.deepEqual(pages, ['cursor-1'], 'keyboard activation requests the server cursor');
  const anchorAfter = await page.evaluate(() => {
    const t = document.querySelector('#conv-thread'), a = t.querySelector('details.chat-side').getBoundingClientRect();
    return a.top - t.getBoundingClientRect().top;
  });
  assert.ok(Math.abs(anchorAfter - anchorBefore) <= 2, 'prepending keeps the previous content anchored');
  assert.equal(await older.isHidden(), true, 'after prepending, control hides while reading away from the top');
  const secondTop = await thread.evaluate(el => { el.scrollTop = 0; return el.scrollTop; });
  assert.equal(secondTop, 0);
  await page.waitForFunction(() => !document.querySelector('#conv-older').hidden);
  await older.locator('[data-cloud-older]').click();
  await page.waitForFunction(() => V2C.messages.some(m => m.id === 'older-b'));
  assert.deepEqual(pages, ['cursor-1','cursor-2']);
  await page.waitForFunction(() => document.querySelector('#conv-older').hidden);
  assert.equal(await page.locator('#conv-older [data-cloud-older]').count(), 0, 'no control remains after history is exhausted');
  assert.deepEqual(errors, []);
  console.log('chat activity fold and top-only older control: ok');
}

// The chat is Ana and the CMO. Twenty pieces of side traffic in a row (other bots, a relay from another bot's run,
// task notices, a note) are one line; what waits on Ana stays in the conversation; its last message stays at the bottom.
async function sideThreads(browser) {
  const page = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true, serviceWorkers: 'block'}), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const at = n => new Date(Date.UTC(2026, 9, 6, 9, n)).toISOString();
  const say = (id, from, to, body, n, extra = {}) => ({id, conversation_id: 'c-cmo', kind: 'say', sender_id: from, recipient_id: to, body, created_at: at(n), refs: {}, ...extra});
  const long = 'From dot (Dots): the national continuation is delivered. ' + 'It adds reviewed records with samples in every provisional destination. '.repeat(6);
  const side = Array.from({length: 20}, (_, i) => i === 7
    ? say('relay', 'bot:cmo', 'human:ana', long, 10 + i, {prompted_by: 'bot'})
    : i === 11 ? say('note', 'bot:cmo', 'human:ana', 'Checked the census sources.', 10 + i, {refs: {task: 't1', note: true}})
    : i % 3 === 0 ? say('side-' + i, 'keeper', 'bot:cmo', `Open: census batch ${i}`, 10 + i, {kind: 'notice', refs: {task: 't1'}})
    : say('side-' + i, i % 2 ? 'bot:dots' : 'bot:cmo', i % 2 ? 'bot:cmo' : 'bot:botops', `Batch ${i} is in.`, 10 + i));
  const messages = [
    say('ask-1', 'human:ana', 'bot:cmo', 'Get me a new customer a day.', 1),
    say('reply-1', 'bot:cmo', 'human:ana', 'On it.', 2, {in_reply_to: 'ask-1', refs: {turn_id: 'turn-1'}}),
    ...side,
    say('approval', 'bot:cmo', 'human:ana', 'Approve this send: the first outreach batch', 40, {kind: 'notice', refs: {approval: 'ap-1', task: 't1'}}),
    say('ask-2', 'human:ana', 'bot:cmo', 'What did Dots find?', 41),
    say('quote', 'bot:cmo', 'human:ana', 'From dot (Dots): 198 records. My read: go with managed homes.', 42, {in_reply_to: 'ask-2', refs: {turn_id: 'turn-2'}}),
    say('late-1', 'bot:dots', 'bot:cmo', 'Switching the census to managed homes.', 43),
    say('late-2', 'keeper', 'bot:cmo', 'Done: census', 44, {kind: 'notice', refs: {task: 't1'}}),
  ];
  const snapshot = {messages, next_before: null, execution: null, goal: null};
  const talk = [{id: 'b0', conversation_id: 'c-ask', kind: 'ask', sender_id: 'bot:cmo', recipient_id: 'bot:seo', body: 'Who has Respona?', created_at: at(29), refs: {}},
                {id: 'b1', conversation_id: 'c-ask', kind: 'ask', sender_id: 'bot:cmo', recipient_id: 'bot:seo', body: 'Can SEO read Respona?', created_at: at(30), refs: {}},
                {id: 'b2', conversation_id: 'c-ask', kind: 'answer', sender_id: 'bot:seo', recipient_id: 'bot:cmo', body: 'Yes, read-only.', created_at: at(31), refs: {}}];
  const bots = ['cmo', 'dots', 'botops', 'seo'].map(name => ({name, display_name: name === 'cmo' ? 'CMO' : name[0].toUpperCase() + name.slice(1), host: 'keeper', status: 'active', can_chat: true, schedules: []}));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('bot')
      ? [{id: 'c-ask', kind: 'ask', participants: ['bot:cmo', 'bot:seo'], subject: 'Respona access'}]
      : [{id: 'c-cmo', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:cmo']}]});
    if (p === '/api/v2/conversations/c-cmo/snapshot' || p === '/api/v2/conversations/c-cmo/messages') return json(snapshot);
    if (p === '/api/v2/conversations/c-ask/messages') {
      asked.push(url.searchParams.get('limit') + (url.searchParams.get('before') ? ' before ' + url.searchParams.get('before') : ''));
      if (url.searchParams.get('limit') === '1') return json({messages: talk.slice(-1), has_more: true, next_before: 'b2'});
      return json(url.searchParams.get('before') === 'b1' ? {messages: talk.slice(0, 1)} : {messages: talk.slice(1), has_more: true, next_before: 'b1'});
    }
    return json(unrouted(p));
  });
  const asked = [];
  await page.goto('https://tico-ui.test/#/bot/cmo/chat');
  await page.waitForFunction(() => V2C?.rendered && V2C.loaded && V2C.botTalk?.length);

  // Classified on fields, not words: the reply that quotes Dots is the conversation, the relay from Dots' run is not.
  const kinds = await page.evaluate(() => Object.fromEntries(V2C.messages.map(m => [m.id, chatSideKind(m, 'cmo')])));
  assert.deepEqual([kinds['ask-1'], kinds['reply-1'], kinds.quote, kinds.approval], ['', '', '', ''], 'the person, the bot\'s replies and an approval are the conversation');
  assert.deepEqual([kinds.relay, kinds.note, kinds['side-1'], kinds['side-2'], kinds['side-3'], kinds['late-2']], ['relay', 'note', 'bots', 'bots', 'notice', 'notice']);
  assert.equal(await page.evaluate(() => chatSideKind({id: 'q', kind: 'ask', sender_id: 'bot:dots', recipient_id: 'human:ana', body: 'Which ranking?'}, 'cmo')), '', 'a question to the person waits on them');

  const thread = page.locator('#conv-thread'), runs = thread.locator('details.chat-side');
  assert.equal(await page.locator('#conv-bots').count(), 0, 'no second strip for bot-to-bot talk');
  const big = runs.first(), summary = big.locator('> summary');
  assert.equal(await big.getAttribute('data-side-count'), '21', 'the twenty side items and the bot-to-bot preview between them are one line');
  assert.match(await summary.innerText(), /^21 updates\s*· tasks, Dots, Botops \+2\s/, 'how many, who (first three), when');
  assert.equal(await big.evaluate(el => el.open), false, 'it starts folded');
  const box = await summary.boundingBox();
  assert.ok(box.height < 34, `one compact line (${box.height}px)`);
  assert.equal(await thread.locator('.bubble').count(), 4, 'four messages between Ana and the CMO');
  assert.equal(await thread.locator('> .chat-system', {hasText: 'Approve this send'}).count(), 1, 'the approval stays in the conversation');
  const order = await thread.evaluate(el => [...el.children].map(x => x.dataset.message || (x.matches('.chat-side') ? 'side' : x.className)));
  assert.deepEqual(order, ['ask-1', 'reply-1', 'side', 'approval', 'ask-2', 'side', 'quote'],
    'side items after the last reply join the line above it, so the conversation ends the thread');

  await summary.tap();
  await page.waitForFunction(() => V2C.openFolds?.has('side:side-0'));
  assert.equal(await big.locator('.cs-item').count(), 21);
  const relay = big.locator('.cs-item[data-message="relay"]'), more = relay.locator('[data-cs-more]');
  assert.ok((await relay.locator('.cs-text').boundingBox()).height < 40, 'a long item shows two lines');
  await more.tap();
  await relay.locator('[data-cs-more]', {hasText: 'Show less'}).waitFor();
  assert.ok((await big.locator('.cs-item[data-message="relay"] .cs-text').boundingBox()).height > 100, 'show more opens the whole message');
  // The bots' conversation is its newest message until opened; then its newest page joins the line.
  assert.equal(await big.locator('.cs-item[data-message="b1"]').count(), 0, 'only the newest message of the bots\' conversation');
  if (process.env.TICO_SHOTS) {
    await big.locator('.cs-item[data-message="b2"]').scrollIntoViewIfNeeded();
    await page.screenshot({path: process.env.TICO_SHOTS + '/bot-talk-preview.png'});
  }
  await big.locator('.cs-item[data-message="b2"] [data-bots-open]').tap();
  await big.locator('.cs-item[data-message="b1"]').waitFor();
  assert.equal(await big.locator('.cs-item').count(), 22);
  assert.equal(await big.locator('[data-bots-open]').count(), 1, 'more before the page: one "Earlier messages", on its oldest');
  await big.locator('.cs-item[data-message="b1"] [data-bots-open]').tap();
  await big.locator('.cs-item[data-message="b0"]').waitFor();
  assert.equal(await big.locator('.cs-item').count(), 23);
  assert.equal(await big.locator('[data-bots-open]').count(), 0);
  assert.deepEqual(asked, ['1', '50', '50 before b1'], 'a preview reads one message; opening reads one page, then the page before it');
  // Above a chat with older pages, an opened conversation still shows everything it loaded; an unopened one waits.
  assert.deepEqual(await page.evaluate(() => v2WithBotTalk({nextBefore: 'x', botTalk: [
    {id: 'opened', created_at: '2026-10-06T08:00:00Z', elsewhere: {id: 'c1', open: true}},
    {id: 'waits', created_at: '2026-10-06T08:00:00Z', elsewhere: {id: 'c2', preview: true}}]},
    [{id: 'own', created_at: '2026-10-06T09:00:00Z'}]).map(m => m.id)), ['opened', 'own']);
  if (process.env.TICO_SHOTS) {
    await big.locator('.cs-item[data-message="b2"]').scrollIntoViewIfNeeded();
    await page.screenshot({path: process.env.TICO_SHOTS + '/bot-talk-opened.png'});
  }
  await page.evaluate(() => v2ChatRender(V2C));
  assert.equal(await big.evaluate(el => el.open), true, 'a redraw keeps the line open');

  // New side traffic while reading (the open line makes the thread scroll) joins a line; a new reply brings the pill.
  await thread.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
  await page.waitForFunction(() => !V2C.stick.atBottom);
  await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, say('late-3', 'bot:botops', 'bot:cmo', 'Granted.', 45));
  assert.equal(await runs.count(), 2, 'no new line for it');
  assert.equal(await runs.nth(1).getAttribute('data-side-count'), '3');
  assert.equal(await page.locator('#conv-jump').isHidden(), true, 'side traffic is not a new reply');
  await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, say('reply-3', 'bot:cmo', 'human:ana', 'First prospects are out.', 46, {in_reply_to: 'ask-2'}));
  await page.locator('#conv-jump', {hasText: 'New reply'}).waitFor();
  await page.locator('#conv-jump').tap();
  await page.waitForFunction(() => { const t = document.querySelector('#conv-thread'); return t.scrollTop + t.clientHeight >= t.scrollHeight - 2; });
  assert.equal(await thread.evaluate(el => el.lastElementChild.dataset.message), 'reply-3');
  await summary.tap();
  await page.waitForFunction(() => !document.querySelector('#conv-thread details.chat-side').open);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no mobile overflow');
  assert.deepEqual(errors, []);
  console.log('chat side threads fold to one line: ok');
}

// Stays at the bottom: at the newest line, a new message, a streamed chunk or an image loading keeps it in view; scrolled
// up, nothing moves and a "New reply" pill appears, which takes you down and goes away. A poll that changed nothing
// leaves the thread as it is.
async function stickToBottom(browser) {
  const page = await browser.newPage({viewport: {width: 1100, height: 760}, serviceWorkers: 'block'}), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const at = n => new Date(Date.UTC(2026, 9, 6, 9, n)).toISOString();
  const say = (id, from, to, body, n, extra = {}) => ({id, conversation_id: 'c-ops', kind: 'say', sender_id: from, recipient_id: to, body, created_at: at(n), refs: {}, ...extra});
  const messages = Array.from({length: 30}, (_, i) => i % 2
    ? say('r' + i, 'bot:ops', 'human:ana', `Reply ${i}. ` + 'Here is what I found. '.repeat(12), i, {in_reply_to: 'q' + (i - 1)})
    : say('q' + i, 'human:ana', 'bot:ops', `Question ${i}`, i));
  const snapshot = {messages, next_before: null, execution: null, goal: null};
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('bot') ? []
      : [{id: 'c-ops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:ops']}]});
    if (p === '/api/v2/conversations/c-ops/snapshot' || p === '/api/v2/conversations/c-ops/messages') return json(snapshot);
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.waitForFunction(() => V2C?.rendered && V2C.loaded && document.querySelectorAll('#conv-thread .bubble').length === 30);
  const thread = page.locator('#conv-thread'), pill = page.locator('#conv-jump');
  const frames = () => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  const gap = async () => { await frames(); return thread.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight); };
  const add = async m => { messages.push(m); await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, m); };
  assert.ok(await thread.evaluate(el => el.scrollHeight > el.clientHeight * 2), 'the thread scrolls');
  assert.ok(await gap() <= 1, 'it opens at the newest line');

  // At the bottom: a new message, a reply streaming in and an image loading all keep the newest line in view.
  await add(say('q30', 'human:ana', 'bot:ops', 'And the next step?', 30));
  assert.ok(await gap() <= 1, 'a new message stays pinned');
  await page.evaluate(() => { V2C.live = {text: 'Working on it.'}; v2ChatRender(V2C); });
  assert.ok(await gap() <= 1, 'the start of a reply stays pinned');
  await page.evaluate(() => { document.querySelector('#v2-live').innerHTML += '<p>More words.</p>'.repeat(20); });
  assert.ok(await gap() <= 1, 'a streamed chunk stays pinned');
  await page.evaluate(() => { document.querySelector('#conv-thread').lastElementChild.insertAdjacentHTML('beforeend', '<div style="height:300px">image</div>'); });
  assert.ok(await gap() <= 1, 'content growing (an image loading) stays pinned');
  assert.equal(await pill.isHidden(), true, 'no pill at the bottom');

  // A poll that changed nothing does not rebuild the thread.
  assert.equal(await page.evaluate(() => { const t = document.querySelector('#conv-thread'), a = t.firstElementChild; v2ChatRender(V2C); return a === t.firstElementChild; }), true,
    'unchanged data leaves the thread alone');

  // Scrolled up: nothing that arrives moves the view; a pill says a reply came.
  const box = await thread.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -3000);
  await page.waitForFunction(() => !V2C.stick.atBottom);
  await frames();
  const top = await thread.evaluate(el => el.scrollTop);
  await page.evaluate(() => { document.querySelector('#v2-live').innerHTML += '<p>Still streaming.</p>'.repeat(10); });
  await frames();
  assert.equal(await thread.evaluate(el => el.scrollTop), top, 'a streamed chunk does not move a reader who scrolled up');
  await page.evaluate(() => { V2C.live = null; });
  await add(say('r31', 'bot:ops', 'human:ana', 'Here is the next step.', 31, {in_reply_to: 'q30'}));
  await frames();
  assert.equal(await thread.evaluate(el => el.scrollTop), top, 'a new message does not move a reader who scrolled up');
  await pill.waitFor();
  assert.match(await pill.innerText(), /New reply/);
  assert.equal(await pill.evaluate(el => el.tagName === 'BUTTON' && el.type === 'button'), true, 'the pill is a button');
  assert.ok(await gap() > 100, 'still scrolled up');

  // The pill takes you to the newest line and goes away.
  await pill.click();
  assert.ok(await gap() <= 1, 'the pill goes to the bottom');
  assert.equal(await pill.isHidden(), true, 'and hides');
  assert.equal(await page.evaluate(() => V2C.stick.atBottom), true, 'and follows again');
  await add(say('q32', 'human:ana', 'bot:ops', 'Thanks.', 32));
  assert.ok(await gap() <= 1, 'following again after the pill');
  assert.deepEqual(errors, []);
  console.log('chat stays at the bottom, or where you scrolled: ok');
}

// A message waiting on setup links to the Settings tab that fixes it, and the link lands on that tab, not on the last
// one open (one listener for every data-gs-tab link, ui/getting-started.js; the Goal Manager's is in goals.cjs).
async function settingsLinks(browser) {
  const page = await browser.newPage({viewport:{width:1200,height:800},serviceWorkers:'block'});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const now=new Date().toISOString();
  let reason='missing_provider';
  const snapshot=()=>({messages:[{id:'q1',sender_id:'human:ana',recipient_id:'bot:ops',body:'Plan the build',created_at:now}],
    execution:{message_id:'q1',bot:'ops',state:'queued',label:'Saved',readiness_reason:reason}});
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
    if(p.endsWith('/watch'))return route.fulfill({contentType:'text/event-stream',body:'event: snapshot\ndata: '+JSON.stringify(snapshot())+'\n\n'});
    if(p==='/api/v2/conversations'){const b=url.searchParams.get('chat_with');
      return json({conversations:b?[{id:'c-ops',kind:'chat',scope:'personal',participants:['human:ana','bot:ops']}]:[]});}
    if(p.startsWith('/api/v2/conversations/c-')&&p.endsWith('/snapshot'))return json(snapshot());
    if(p==='/api/v2/support/tickets')return json({enabled:false});
    return json(unrouted(p));
  });
  for (const [why, name, tab] of [['missing_provider', 'Add an AI provider', 'models'],
    ['model_sign_in', 'Sign in on Settings > Computers', 'devices']]) {
    reason=why;
    // Settings was last left on another tab, which a plain link to #/settings would reopen.
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('#settings-tabs [data-settings-tab=repos]').click();
    await page.locator('#settings-tabs [data-settings-tab=repos][aria-selected=true]').waitFor();
    await page.goto('https://tico-ui.test/#/bot/ops/chat');
    await page.locator('.conv-run.chat .thinking').getByRole('link',{name,exact:true}).click();
    await page.locator(`#settings-tabs [data-settings-tab=${tab}][aria-selected=true]`).waitFor();
    assert.equal(new URL(page.url()).hash,'#/settings/'+tab);
    assert.equal(await page.locator('#settings-'+tab).isVisible(),true,name+' opens Settings on its tab');
  }
  assert.deepEqual(errors,[]);
  await page.close();
  console.log('chat links to a Settings tab open that tab: ok');
}

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless:true}) : await chromium.launch({channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined,headless:true});
  try {
    await offlineRetry(browser);
    await liveReply(browser);
    await settingsLinks(browser);
    await outlineJump(browser);
    await activityFold(browser);
    await sideThreads(browser);
    await stickToBottom(browser);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
