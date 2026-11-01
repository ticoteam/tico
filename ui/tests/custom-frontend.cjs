// The example frontend (examples/custom-frontend) against a real Tico server on another origin:
// CORS with a bearer session, sign-in through built-in OIDC (code, PKCE, token), org chart, a chat whose
// reply streams in, tasks and Needs you. The server is ui/tests/support/frontend_server.py.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const readline = require('node:readline');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

const ROOT = path.join(__dirname, '../..');
const PYTHON = process.env.TICO_PYTHON || 'python3';

// True once something accepts connections on the port.
function listening(port){return new Promise(resolve=>{const s=net.connect(port,'127.0.0.1',()=>{s.destroy();resolve(true);});s.on('error',()=>resolve(false));});}
function freePort(){return new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const {port}=s.address();s.close(()=>resolve(port));});});}

function start(cmd,args,ready){
  const child=spawn(cmd,args,{cwd:ROOT,stdio:['ignore','pipe','pipe']});
  let err='';child.stderr.on('data',d=>{err+=d;});
  return new Promise((resolve,reject)=>{
    child.on('exit',code=>reject(new Error(`${cmd} ${args.join(' ')} exited ${code}: ${err.slice(-1500)}`)));
    const timer=setTimeout(()=>reject(new Error('server did not start: '+err.slice(-1500))),t(60000));
    readline.createInterface({input:child.stdout}).on('line',line=>{const v=ready(line);if(v){clearTimeout(timer);resolve({child,value:v});}});
  });
}

async function python(){
  const probe=spawn(PYTHON,['-c','import uvicorn,httpx,jwt,cryptography,yaml'],{stdio:'ignore'});
  const ok=await new Promise(r=>probe.on('exit',c=>r(c===0)));
  if(!ok){
    if(process.env.CI)throw new Error(PYTHON+' lacks the backend requirements (pip install -r backend/requirements-dev.txt)');
    console.log('skipped: '+PYTHON+' lacks the backend requirements; set TICO_PYTHON to a Python that has them');
    process.exit(0);
  }
}

(async()=>{
  await python();
  const webPort=await freePort(), origin=`http://localhost:${webPort}`;
  // The example is served the way its README says: python3 -m http.server, on a port this test picked.
  const webChild=spawn(PYTHON,['-m','http.server',String(webPort),'--bind','127.0.0.1','-d','examples/custom-frontend'],{cwd:ROOT,stdio:'ignore'});
  for(const end=Date.now()+t(15000);!await listening(webPort)&&Date.now()<end;)await new Promise(r=>setTimeout(r,50));
  assert.ok(await listening(webPort),'the example is served on '+origin);
  const browser=await chromium.launch({headless:true,channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const servers=[webChild];
  try {
    for(const mode of ['none','oidc']){
      const tico=await start(PYTHON,['ui/tests/support/frontend_server.py','--auth',mode,'--origin',origin],l=>{try{return JSON.parse(l);}catch{return null;}});
      servers.push(tico.child);
      const {url,token}=tico.value;
      const context=await browser.newContext({viewport:{width:1000,height:800}});
      const page=await context.newPage();
      const problems=[];
      page.on('pageerror',e=>problems.push(String(e)));
      // The page's config.js names the server: the test's own, instead of the example's placeholder.
      await page.route('**/config.js',route=>route.fulfill({contentType:'application/javascript',body:`window.TICO_URL=${JSON.stringify(url)};`}));
      await page.goto(origin+'/');
      await page.waitForSelector('#signin:not([hidden])');

      if(mode==='none'){
        await page.click('#dev summary');
        await page.fill('#dev-token',token);
        await page.click('#dev-go');
      }else{
        await page.click('#sign-in');                       // -> /auth/login -> provider -> /auth/callback -> back here with #tico_code
        await page.waitForSelector('#tabs:not([hidden])');
        assert.equal(page.url(),origin+'/','the one-time code is removed from the address bar');
        assert.deepEqual(await context.cookies(url),[],'the frontend gets a bearer session, not a cookie on the Tico host');
      }
      await page.waitForSelector('#tabs:not([hidden])');
      assert.equal(await page.textContent('#company'),'Acme HQ');
      assert.equal(await page.textContent('#who'),'ana@acme.example');

      // Org chart: people and bots, nested by who reports to whom.
      await page.waitForSelector('#org li');
      const org=await page.textContent('#org');
      for(const name of ['Ana Rivera','Ben Okafor','Chief of Staff','Ops'])assert.match(org,new RegExp(name),name+' is on the chart');
      const parentOf=name=>page.evaluate(n=>{const li=[...document.querySelectorAll('#org li')].find(l=>l.firstChild.textContent===n);const up=li&&li.parentElement.closest('li');return up?up.firstChild.textContent:null;},name);
      assert.equal(await parentOf('Ben Okafor'),'Ana Rivera','Ben reports to Ana');
      assert.equal(await parentOf('Ops'),'Chief of Staff','Ops reports to the Chief of Staff');
      assert.equal(await parentOf('Chief of Staff'),'Ana Rivera');
      assert.equal(await parentOf('Ana Rivera'),null,'Ana is at the top');
      assert.match(await page.locator('#org li').filter({hasText:/^Ops/}).first().locator('.badge').first().textContent(),/bot/);

      // Chat: send a message; the reply arrives word by word before it is final.
      await page.click('#tabs [data-tab=chat]');
      await page.click('#chat-bots button:has-text("Ops")');
      await page.fill('#text','Hello ops');
      const posted=page.waitForResponse(r=>r.url()===url+'/api/v2/chat/ops'&&r.request().method()==='POST');
      await page.click('#send button');
      const sent=await (await posted).json();
      await page.waitForSelector('.msg.me:has-text("Hello ops")');
      // The live text read in the same step that finds it: the stream may end right after.
      const partial=await (await page.waitForFunction(()=>[...document.querySelectorAll('.msg.live')].find(m=>m.textContent.includes('Hello from ops'))?.textContent,null,{timeout:t(30000)})).jsonValue();
      assert.ok('Hello from ops. The reply is streaming to your own frontend, one word at a time.'.startsWith(partial.trim()),'the reply so far is shown while it streams: '+partial);
      await page.waitForFunction(()=>[...document.querySelectorAll('.msg')].some(m=>m.textContent.includes('one word at a time.'))&&!document.querySelector('.msg.live'),null,{timeout:t(30000)});
      assert.equal(await page.locator('.msg:has-text("Hello from ops")').count(),1,'the final reply replaces the live text');
      const chatBearer=await page.evaluate(()=>sessionStorage.getItem('tico.session'));
      const messages=await context.request.get(url+'/api/v2/conversations/'+sent.conversation.id+'/messages',
        {headers:{Authorization:'Bearer '+chatBearer}});
      assert.equal(messages.status(),200);
      const finalMessages=(await messages.json()).messages;
      const replies=finalMessages.filter(m=>m.sender_id==='bot:ops'&&(m.in_reply_to===sent.message.id||(m.answers||[]).includes(sent.message.id)));
      assert.equal(replies.length,1,'exactly one final answer belongs to the submitted Hello ops input');
      assert.equal(replies[0].body,'Hello from ops. The reply is streaming to your own frontend, one word at a time.');
      assert.equal(await page.locator('.msg.live').count(),0,'no live duplicate remains after the linked final answer');
      // Coming back to the chat later shows the same conversation.
      await page.click('#tabs [data-tab=org]');
      await page.click('#tabs [data-tab=chat]');
      await page.click('#chat-bots button:has-text("Ops")');
      await page.waitForSelector('.msg:has-text("Hello from ops")');

      // Tasks and Needs you.
      await page.click('#tabs [data-tab=tasks]');
      await page.waitForSelector('#tasks td:has-text("Draft the weekly update")');
      assert.match(await page.textContent('#tasks'),/Review the launch plan/);
      await page.click('#tabs [data-tab=needs]');
      await page.waitForSelector('#needs li:has-text("Review the launch plan")');

      // Files: a bot's linked document, opened at its provider.
      await page.click('#tabs [data-tab=files]');
      await page.waitForSelector('#files select');
      await page.selectOption('#files select','ops');
      await page.waitForSelector('#files li:has-text("Launch plan")');
      const opens=page.locator('#files a:has-text("Open in Google")');
      assert.equal(await opens.getAttribute('href'),'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit');
      assert.equal(await opens.getAttribute('target'),'_blank');

      // The server refuses what is not allowed: another origin gets no CORS headers, a bad token no data.
      const other=await context.request.get(url+'/api/v2/me',{headers:{Origin:'https://evil.example',Authorization:'Bearer '+(mode==='none'?token:'x')}});
      assert.equal(other.headers()['access-control-allow-origin'],undefined);

      // Sign out ends the session on the server.
      const bearer=await page.evaluate(()=>sessionStorage.getItem('tico.session'));
      await page.click('#sign-out');
      await page.waitForSelector('#signin:not([hidden])');
      assert.equal(await page.evaluate(()=>sessionStorage.getItem('tico.session')),null);
      if(mode==='oidc'){
        const after=await context.request.get(url+'/api/v2/me',{headers:{Authorization:'Bearer '+bearer}});
        assert.equal(after.status(),401,'the revoked session no longer works');
      }
      assert.deepEqual(problems,[],'no script errors');
      console.log(`custom frontend (${mode==='none'?'TICO_AUTH_PROXY=none, owner token':'built-in OIDC sign-in, PKCE code exchange'}): org chart, streamed chat, tasks, Needs you, sign out`);
      await context.close();
      tico.child.kill();
    }
  }finally{
    await browser.close();
    for(const s of servers)s.kill();
  }
})().catch(error=>{console.error(error);process.exit(1);});
