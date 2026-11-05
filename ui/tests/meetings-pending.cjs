// Two real browser viewers share the pending panel, with a deliberately slow and failing write.
require('./support/browser.cjs');
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const sharedCode = fs.readFileSync(path.join(__dirname, '../app/shared-edit.js'), 'utf8');
const code = fs.readFileSync(path.join(__dirname, '../app/meetings.js'), 'utf8');
const item = {version:1, id:'i1', section:'task', text:'Draft', detail:{owner:'human:ben', body:'Initial'}, status:'proposed', created_by:'human:ana'};
const streams = new Set(), sessions = new Map(), errors = [];
let hold = null, release = null, fail = false, writes = 0, inflight = 0, maxInflight = 0, sent = 0, linked = 0, lastSend;
const emit = (topic, data) => { for (const res of streams) res.write(`event: ${topic}\ndata: ${JSON.stringify(data)}\n\n`); };
const actors = () => [...new Set(sessions.values())].map(id => ({actor:'human:' + id, name:id === 'ana' ? 'Ana' : 'Sam'}));
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const answer = (body, status=200) => { res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify(body)); };
  if (url.pathname === '/shared-edit.js') { res.writeHead(200, {'Content-Type':'text/javascript'}); return res.end(sharedCode); }
  if (url.pathname === '/meetings.js') { res.writeHead(200, {'Content-Type':'text/javascript'}); return res.end(code); }
  if (url.pathname === '/') {
    res.writeHead(200, {'Content-Type':'text/html'});
    return res.end(`<button id="blur">Elsewhere</button><dialog id="notes-modal" open><div id="meet-sections"></div></dialog><script>
      const API='/api', me=new URLSearchParams(location.search).get('viewer');
      const S={me:{id:me},people:[{id:'ana',name:'Ana'},{id:'ben',name:'Sam'}]};
      const $=s=>document.querySelector(s), esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const newRequestId=()=>crypto.randomUUID();
      const actorLabel=a=>a==='human:ben'?'Sam':'Ana', activeEmps=()=>[], getForget=()=>{}, toast=()=>{}, liveOn=()=>{};
      const copyText=async text=>{window.copiedText=text;};
      const taskOwnerOptions=()=>'<option value="human:ben">Sam</option>', botByRef=()=>null;
      const get=p=>fetch(API+p).then(r=>r.json());
      const post=async(p,body)=>{const r=await fetch(API+p,{method:'POST',headers:{'Content-Type':'application/json','X-Viewer':me},body:JSON.stringify(body)});const data=await r.json();if(!r.ok){const e=new Error(data.error?.detail || data.error);e.body=data;throw e;}return data;};
    </script><script src="/shared-edit.js"></script><script src="/meetings.js"></script><script>
      MEET={open:true,selected:'m1',list:[{id:'m1'}],items:{},itemDup:{}}; meetItemsWire(MEET,{id:'m1'});
    </script>`);
  }
  if (url.pathname === '/api/v2/events') {
    assert.equal(url.searchParams.get('meeting'), 'm1');
    assert.equal(url.searchParams.get('place'), 'meeting:m1');
    res.writeHead(200, {'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
    streams.add(res); res.write('event: ready\ndata: {}\n\n');
    req.on('close', () => streams.delete(res)); return;
  }
  const body = req.method === 'POST' ? JSON.parse(await new Promise(resolve => { let b='';req.on('data', c=>b+=c);req.on('end',()=>resolve(b)); })) : {};
  if (url.pathname === '/api/v2/presence') {
    if (req.method === 'POST') {
      if (body.gone) sessions.delete(body.session_id); else sessions.set(body.session_id, req.headers['x-viewer']);
      emit('presence', {place:'meeting:m1'});
    }
    return answer({actors:actors()});
  }
  if (url.pathname === '/api/meetings/m1/items' && req.method === 'GET') return answer({id:'m1',can_send:true,audience:null,sections:{task:[item],doc:[],feature:[]}});
  if (url.pathname.startsWith('/api/meetings/m1/items/i1') && req.method === 'POST') {
    const pushing = url.pathname.endsWith('/push'), linking = url.pathname.endsWith('/link');
    if (!pushing && !linking && req.headers['x-viewer'] === 'ana') {
      writes++; inflight++; maxInflight = Math.max(maxInflight, inflight);
      if (hold) { const gate=hold; hold=null; await gate; }
      inflight--;
      if (fail) { fail=false; return answer({error:'Offline'},503); }
    }
    if(!linking && body.version !== item.version) return answer({error:{detail:'This item changed',item:structuredClone(item)}},409);
    item.version++;
    if (body.text !== undefined) item.text=body.text;
    if (body.detail) Object.assign(item.detail, body.detail);
    if (body.status) item.status=body.status;
    if (pushing && item.status === 'proposed') { item.status='pushed'; sent++; lastSend=structuredClone(body); }
    if (linking) { assert.equal(item.proposed_task_id, 'existing-task'); item.status='pushed'; item.result_ref=item.proposed_task_id; linked++; }
    item.updated_by='human:' + req.headers['x-viewer'];
    answer({item,pushed:pushing}); emit('meeting_items',{meeting_id:'m1',kind:'items'}); return;
  }
  answer({});
});
function pauseWrite() { hold = new Promise(resolve => { release=resolve; }); }
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser=await chromium.launch({headless:true,channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const a=await browser.newPage({viewport:{width:1200,height:900}}), b=await browser.newPage({viewport:{width:390,height:844}});
    for(const page of [a,b]) page.on('pageerror', e=>errors.push(e.message));
    const base=`http://127.0.0.1:${server.address().port}`;
    await a.goto(base+'/?viewer=ana'); await b.goto(base+'/?viewer=ben');
    await a.getByText('Sam is here',{exact:true}).waitFor();
    const title=a.getByRole('textbox',{name:'Title',exact:true});
    pauseWrite(); await title.fill('First draft');
    await a.waitForFunction(()=>!!meetPendingDraft(MEET,'i1').flight);
    await title.fill('Newer draft');
    await b.getByRole('textbox',{name:'Details',exact:true}).fill('Sam changed the details');
    await b.locator('#blur').click();
    await b.waitForFunction(()=>MEET.items.m1.sections.task[0].detail.body==='Sam changed the details');
    assert.equal(await title.inputValue(),'Newer draft','incoming changes preserve the focused title');
    release();
    await a.getByRole('button',{name:'Retry',exact:true}).waitFor();
    assert.equal(await title.inputValue(),'Newer draft');
    await a.getByText(/Current: Draft/).waitFor();
    await a.getByRole('button',{name:'Retry',exact:true}).click();
    await a.waitForFunction(()=>!meetPendingDraft(MEET,'i1').flight && !Object.keys(meetPendingDraft(MEET,'i1').patch).length);
    assert.equal(item.text,'Newer draft'); assert.equal(item.detail.body,'Sam changed the details');
    assert.equal(maxInflight,1,'one write per item in flight');
    await a.locator('[data-add="task"]').click();
    const addText=a.getByRole('textbox',{name:'What to add',exact:true});
    await addText.fill('Keep typing here');
    await addText.evaluate(el=>el.setSelectionRange(5,11));
    item.detail.body='Another live edit'; item.version++; emit('meeting_items',{meeting_id:'m1',kind:'items'});
    await a.waitForFunction(()=>MEET.items.m1.sections.task[0].detail.body==='Another live edit');
    assert.equal(await addText.inputValue(),'Keep typing here');
    assert.deepEqual(await addText.evaluate(el=>[document.activeElement===el,el.selectionStart,el.selectionEnd]),[true,5,11]);
    await a.locator('form[data-new] [data-cancel]').click();
    await a.locator('#blur').click();
    fail=true; await title.fill('Retry words'); await a.locator('#blur').click();
    await a.getByRole('button',{name:'Retry',exact:true}).waitFor();
    item.text='Remote edit'; emit('meeting_items',{meeting_id:'m1',kind:'items'});
    await a.waitForFunction(()=>MEET.items.m1.sections.task[0].text==='Remote edit');
    assert.equal(await title.inputValue(),'Retry words','a failed save keeps its draft');
    await a.getByRole('button',{name:'Retry',exact:true}).click();
    await a.waitForFunction(()=>!meetPendingDraft(MEET,'i1').error);
    pauseWrite(); await title.fill('Sending draft');
    await a.waitForFunction(()=>!!meetPendingDraft(MEET,'i1').flight);
    await title.fill('Final words');
    item.detail.body='B saved before Send'; item.version++;
    await a.getByRole('button',{name:'Create',exact:true}).click();
    release();
    await a.getByText(/Current:.*B saved before Send/).waitFor();
    assert.equal(sent,0,'a conflicted autosave prevents Send');
    assert.equal(await title.inputValue(),'Final words');
    await a.getByRole('button',{name:'Create',exact:true}).click();
    await a.locator('[data-pending="i1"]').waitFor({state:'detached'});
    assert.equal(item.text,'Final words'); assert.equal(sent,1);
    assert.equal(item.detail.body,'B saved before Send');
    item.status='proposed'; item.version++; emit('meeting_items',{meeting_id:'m1',kind:'items'});
    await a.locator('[data-pending="i1"]').waitFor();
    await b.locator('[data-pending="i1"]').waitFor();
    pauseWrite(); await title.fill('Title only');
    await a.waitForFunction(()=>!!meetPendingDraft(MEET,'i1').flight);
    const details=a.getByRole('textbox',{name:'Details',exact:true});
    await details.evaluate(el=>{el.focus();el.setSelectionRange(2,4);});
    await b.getByRole('textbox',{name:'Details',exact:true}).fill('B saved focused Details');
    await b.locator('#blur').click();
    await b.waitForFunction(()=>MEET.items.m1.sections.task[0].detail.body==='B saved focused Details');
    release();
    await a.getByRole('button',{name:'Retry',exact:true}).waitFor();
    assert.equal(await title.inputValue(),'Title only');
    assert.equal(await details.inputValue(),'B saved focused Details');
    assert.deepEqual(await details.evaluate(el=>[document.activeElement===el,el.selectionStart,el.selectionEnd]),[true,2,4]);
    assert.equal(await a.evaluate(()=>meetPendingDraft(MEET,'i1').dirty.text),'Final words');
    const version=item.version;
    await a.getByRole('button',{name:'Create',exact:true}).evaluate(el=>el.click());
    await a.locator('[data-pending="i1"]').waitFor({state:'detached'});
    assert.deepEqual(lastSend,{text:'Title only',force:false,version});
    assert.equal(item.detail.body,'B saved focused Details');
    assert.equal(item.version,version+1);
    assert.equal(sent,2);
    for (const [action, inFlight] of [['Create', true], ['Dismiss', false]]) {
      for (const page of [a,b]) await page.evaluate(()=>MEET.pendingDrafts.clear());
      item.status='proposed'; item.version++; emit('meeting_items',{meeting_id:'m1',kind:'items'});
      await a.locator('[data-pending="i1"] [data-send]').waitFor();
      await b.locator('[data-pending="i1"] [data-send]').waitFor();
      if (inFlight) pauseWrite();
      await a.evaluate(() => {
        for (const [key,value] of [['text','My unsaved title'],['body','My unsaved details']]) {
          const field=document.querySelector(`[data-pending-field="${key}"]`);
          field.value=value; field.dispatchEvent(new Event('input',{bubbles:true}));
        }
      });
      if (inFlight) await a.waitForFunction(()=>!!meetPendingDraft(MEET,'i1').flight);
      await b.getByRole('button',{name:action,exact:true}).click();
      const closed=a.locator('[data-pending="i1"]');
      await closed.getByText(action === 'Dismiss' ? 'Dismissed by Sam' : 'Sent by Sam',{exact:true}).waitFor();
      assert.match(await closed.locator('pre').textContent(),/My unsaved title/);
      assert.match(await closed.locator('pre').textContent(),/My unsaved details/);
      assert.equal(await closed.locator('textarea,input,select').count(),0);
      if (inFlight) {
        release();
        await a.waitForFunction(()=>!meetPendingDraft(MEET,'i1').flight);
        assert.match(await closed.locator('pre').textContent(),/My unsaved details/);
        await closed.getByRole('button',{name:'Copy',exact:true}).click();
        assert.match(await a.evaluate(()=>window.copiedText),/My unsaved title[\s\S]*My unsaved details/);
      } else await closed.getByRole('button',{name:'Close',exact:true}).click();
      await closed.waitFor({state:'detached'});
      await a.evaluate(()=>meetItemsLoad(MEET,'m1'));
      assert.equal(await a.locator('[data-pending="i1"]').count(),0,'acknowledged drafts stay closed');
    }
    // A readable Recorder proposal accepts the existing task without creating another one.
    item.status='proposed'; item.proposed_task_id='existing-task'; item.detail.owner=''; item.version++;
    emit('meeting_items',{meeting_id:'m1',kind:'items'});
    const proposal=a.locator('[data-pending="i1"]');
    const link=proposal.getByRole('button',{name:'Link',exact:true});
    await link.waitFor(); assert.equal(await link.isEnabled(),true);
    assert.equal(await proposal.getByRole('button',{name:/^(Send|Create)/}).count(),0);
    const before=sent;
    await link.click(); await proposal.waitFor({state:'detached'});
    assert.equal(linked,1); assert.equal(sent,before); assert.equal(item.result_ref,'existing-task');
    // Redaction removes the pointer, so a later render offers ordinary task creation.
    item.status='proposed'; item.proposed_task_id=null; item.detail.owner='human:ben'; item.version++;
    emit('meeting_items',{meeting_id:'m1',kind:'items'});
    await proposal.getByRole('button',{name:'Create',exact:true}).waitFor();
    assert.equal(await proposal.getByRole('button',{name:'Link',exact:true}).count(),0);
    emit('meeting_items',{meeting_id:'m1',gone:true});
    await b.waitForFunction(()=>MEET.open===false);
    assert.deepEqual(errors,[]);
    assert.ok(writes>=4);
    console.log('Two viewers: merged autosave, retry, final Send, presence and gone passed');
  } finally {
    await browser.close(); for(const res of streams) res.end();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
