// The saved Recorder panel uses the real window and a delayed Tauri write.
require('../../ui/tests/support/browser.cjs');
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const root = path.resolve(__dirname, '../ui');
    await page.route('http://recorder.example/**', route => {
      const file = path.resolve(root, '.' + new URL(route.request().url()).pathname);
      assert.ok(file.startsWith(root + path.sep));
      return route.fulfill({path:file});
    });
    await page.goto('http://recorder.example/preview/index.html?scene=library');
    await page.evaluate(() => {
      const a = window.__PREVIEW__.state.meetings.m1.actions;
      a.saved = true; a.actor = 'human:dana'; a.shared_url='https://app.acme.example';
      a.shared = {can_send:true, audience:['human:sam','human:dana'],
        presence:{actors:[{actor:'human:sam',name:'Sam'}]},
        sections:{task:[{version:1,id:'shared-1',section:'task',text:'Draft',status:'proposed',detail:{owner:'human:sam',due:'2026-12-01T17:00:00.000Z',body:'Older details'}}]}};
      Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedText=text;}},configurable:true});
      window.pendingFixture = {a, calls:0, inflight:0, max:0, fail:false};
      const original = window.__TAURI__.core.invoke;
      window.pendingFixture.original=original;
      window.__TAURI__.core.invoke = async (cmd, args) => {
        if (cmd !== 'shared_item') return original(cmd, args);
        const f = window.pendingFixture;
        f.calls++; f.inflight++; f.max = Math.max(f.max, f.inflight);
        if (f.hold) { const gate=f.hold; f.hold=null; await gate; }
        f.inflight--;
        if (f.fail) { f.fail=false; throw new Error('Offline'); }
        const item = a.shared.sections.task[0];
        if (args.body.version !== item.version) return {conflict:structuredClone(item)};
        item.version++;
        if (args.body.text !== undefined) item.text=args.body.text;
        Object.assign(item.detail,args.body.detail || {});
        item.updated_by='human:dana';
        if (args.action === 'send') { item.status='pushed'; item.result_ref='created-task'; f.sent=structuredClone(item); f.lastSend=structuredClone(args.body); }
        return {item:structuredClone(item)};
      };
    });
    await page.locator('.row[data-id="m1"]').click();
    await page.getByText('Sam is here',{exact:true}).waitFor();
    const card=page.locator('[data-shared-item="shared-1"]');
    const title=card.getByRole('textbox',{name:'Title',exact:true});
    const owner=card.getByRole('combobox',{name:'Owner',exact:true});
    const send=card.locator('[data-act="shared-send"]');
    // The task card is Title and Owner, then Send: no Due or Details, even when the item holds them.
    assert.equal(await card.locator('textarea,input,select').count(),2);
    assert.equal(await card.getByText(/^(Due|Details)$/).count(),0);
    assert.equal(await card.locator('input[type="date"]').count(),0);
    // Typing while a save is in flight keeps the newer words and saves them next.
    await page.evaluate(() => { const f=window.pendingFixture; f.hold=new Promise(resolve=>f.release=resolve); });
    await title.fill('Saving draft');
    await page.waitForFunction(()=>window.pendingFixture.inflight===1);
    await title.fill('Newer words');
    await title.evaluate(el=>el.blur());
    assert.equal(await title.inputValue(),'Newer words');
    await page.evaluate(()=>window.pendingFixture.release());
    await page.waitForFunction(()=>window.pendingFixture.a.shared.sections.task[0].text==='Newer words' && !window.pendingFixture.inflight);
    // A failed save offers Retry; someone else's change shows as Current and keeps my words.
    await page.evaluate(()=>window.pendingFixture.fail=true);
    await title.fill('Retry draft');
    await title.evaluate(el=>el.blur());
    await page.getByRole('button',{name:'Retry',exact:true}).waitFor();
    await page.evaluate(()=>{const item=window.pendingFixture.a.shared.sections.task[0];item.text='Someone else';item.version++;});
    await page.getByRole('button',{name:'Retry',exact:true}).click();
    await page.getByText(/Current: Someone else/).waitFor();
    assert.equal(await title.inputValue(),'Retry draft');
    await page.getByRole('button',{name:'Retry',exact:true}).click();
    await page.waitForFunction(()=>window.pendingFixture.a.shared.sections.task[0].text==='Retry draft');
    // Owner saves on its own and names the Send button.
    await owner.selectOption('human:dana');
    await page.waitForFunction(()=>window.pendingFixture.a.shared.sections.task[0].detail.owner==='human:dana' && !window.pendingFixture.inflight);
    await card.getByRole('button',{name:'Send to Dana Reyes',exact:true}).waitFor();
    // Send waits for the save in flight; a newer server version stops it, and the second Send goes out.
    await page.evaluate(() => { const f=window.pendingFixture; f.hold=new Promise(resolve=>f.release=resolve); });
    await title.fill('Before send');
    await page.waitForFunction(()=>window.pendingFixture.inflight===1);
    await title.fill('Final words');
    await page.evaluate(()=>{const item=window.pendingFixture.a.shared.sections.task[0];item.detail.owner='human:sam';item.version++;});
    await send.click();
    await page.evaluate(()=>window.pendingFixture.release());
    await page.getByText(/Current:.*human:sam/).waitFor();
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent),undefined);
    assert.equal(await title.inputValue(),'Final words');
    await card.getByRole('button',{name:'Send to Sam Okafor',exact:true}).click();
    await page.waitForFunction(()=>window.pendingFixture.sent?.text==='Final words');
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent.detail.owner),'human:sam');
    // The due date and details the item already held still go out, without a field for them.
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent.detail.due),'2026-12-01T17:00:00.000Z');
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent.detail.body),'Older details');
    assert.equal(await page.evaluate(()=>window.pendingFixture.max),1);
    assert.equal(await page.locator('[data-shared-row="shared-1"]').getByRole('button',{name:'Open',exact:true}).getAttribute('data-url'),'https://app.acme.example/#/task/created-task');
    // A refresh during typing moves Owner to the server's value and leaves the focused Title and its caret alone.
    await page.evaluate(() => {
      const a=window.pendingFixture.a;
      a.shared.sections.task[0].status='proposed';
      const f=window.pendingFixture; f.hold=new Promise(resolve=>f.release=resolve);
    });
    await title.fill('Title only');
    await page.waitForFunction(()=>window.pendingFixture.inflight===1);
    await title.evaluate(el=>{el.focus();el.setSelectionRange(2,4);});
    await page.evaluate(()=>{const item=window.pendingFixture.a.shared.sections.task[0];item.detail.owner='human:dana';item.version++;});
    await page.evaluate(()=>window.pendingFixture.release());
    await page.getByRole('button',{name:'Retry',exact:true}).waitFor();
    assert.equal(await title.inputValue(),'Title only');
    await page.waitForFunction(()=>document.querySelector('[data-shared-item="shared-1"] [data-shared-field="owner"]').value==='human:dana');
    assert.deepEqual(await title.evaluate(el=>[document.activeElement===el,el.selectionStart,el.selectionEnd]),[true,2,4]);
    const version=await page.evaluate(()=>window.pendingFixture.a.shared.sections.task[0].version);
    await send.evaluate(el=>el.click());
    await page.waitForFunction(()=>window.pendingFixture.sent?.text==='Title only');
    assert.deepEqual(await page.evaluate(()=>window.pendingFixture.lastSend),{text:'Title only',version});
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent.detail.owner),'human:dana');
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent.detail.body),'Older details');
    assert.equal(await page.evaluate(()=>window.pendingFixture.sent.version),version+1);
    // Sent or dismissed by someone else while I was editing: my unsaved Title and Owner stay, read-only, to copy.
    for (const [status, inFlight] of [['pushed',true],['dismissed',false]]) {
      const id='recovery-'+status;
      await page.evaluate(({id})=>{
        const f=window.pendingFixture;
        f.a.shared.sections.task=[{id,version:1,section:'task',text:'Remote title',status:'proposed',detail:{owner:'human:sam',body:'Remote details'}}];
      },{id});
      const row=page.locator(`[data-shared-item="${id}"]`);
      await row.waitFor();
      if(inFlight) await page.evaluate(()=>{const f=window.pendingFixture;f.hold=new Promise(resolve=>f.release=resolve);});
      await row.evaluate(el=>{
        for(const [key,value] of [['text','My unsaved title'],['owner','human:dana']]) {
          const field=el.querySelector(`[data-shared-field="${key}"]`);
          field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));
        }
      });
      if(inFlight) await page.waitForFunction(()=>window.pendingFixture.inflight===1);
      await page.evaluate(({status})=>{
        const item=window.pendingFixture.a.shared.sections.task[0];
        item.status=status;item.updated_by='human:sam';item.text='Sent title';item.detail.owner='human:sam';item.version++;
      },{status});
      const closed=page.locator(`[data-shared-row="${id}"]`);
      await closed.getByText(status==='pushed'?'Sent by Sam Okafor':'Dismissed by Sam Okafor',{exact:true}).waitFor();
      assert.match(await closed.locator('pre').textContent(),/My unsaved title[\s\S]*human:dana/);
      assert.equal(await closed.locator('textarea,input,select').count(),0);
      if(inFlight) {
        await page.evaluate(()=>window.pendingFixture.release());
        await page.waitForFunction(()=>!window.pendingFixture.inflight);
        assert.match(await closed.locator('pre').textContent(),/human:dana/);
        await closed.getByRole('button',{name:'Copy',exact:true}).click();
        assert.match(await page.evaluate(()=>window.copiedText),/My unsaved title[\s\S]*human:dana/);
      } else await closed.getByRole('button',{name:'Close',exact:true}).click();
      await closed.waitFor({state:'detached'});
    }
    await page.evaluate(()=>window.pendingFixture.a.shared.sections.task=[{id:'gone-test',version:1,section:'task',text:'Draft',status:'proposed',detail:{owner:'human:sam'}}]);
    await page.locator('[data-shared-item]').getByRole('textbox',{name:'Title',exact:true}).fill('Still editing');
    await page.evaluate(()=>window.pendingFixture.a.shared={gone:true});
    await page.locator('[data-shared-item]').waitFor({state:'detached'});
    await page.evaluate(() => {
      const original = window.__TAURI__.core.invoke;
      window.pendingFixture.original=original;
      window.__TAURI__.core.invoke = async (cmd, args) => {
        if (cmd !== 'send_item') return original(cmd, args);
        const f = window.pendingFixture, local = f.a.items.find(it => it.id === args.id);
        const server = {id:'server-'+local.id,version:2,section:'task',status:'pushed',
          text:'Original server title',detail:{owner:'human:sam'},updated_by:'human:sam',result_ref:'existing-task'};
        local.sent=true; local.task_id=server.result_ref;
        local.closed_draft={item:server,text:local.title,detail:{owner:local.owner}};
        f.adopted=structuredClone(server);
        return {};
      };
    });
    for (const close of ['Copy','Close']) {
      const id='native-'+close.toLowerCase();
      await page.evaluate(id => {
        const a=window.pendingFixture.a;
        a.saved=false; a.shared=null; a.shared_scope='partial-'+id;
        a.items=[{id,title:'Original server title',owner:'human:sam',owner_name:'Sam Okafor',can_send:true,sent:false}];
      },id);
      const local=page.getByRole('textbox',{name:'Task',exact:true});
      await local.fill('My native unsaved title');
      await local.press('Tab');
      await page.waitForFunction(()=>window.pendingFixture.a.items[0].title==='My native unsaved title');
      await page.getByRole('button',{name:'Create',exact:true}).click();
      const closed=page.locator(`[data-shared-row="server-${id}"]`);
      await closed.getByText('Sent by Sam Okafor',{exact:true}).waitFor();
      assert.match(await closed.locator('pre').textContent(),/My native unsaved title/);
      assert.equal(await closed.locator('textarea,input,select').count(),0);
      // Save recovers and switches to server cards with a new connection scope.
      await page.evaluate(() => {
        const f=window.pendingFixture;
        f.a.saved=true; f.a.shared_scope+='-saved';
        f.a.shared={can_send:true,sections:{task:[{...f.adopted,version:1,status:'proposed'}]}};
      });
      await page.locator('[data-shared-panel]').waitFor();
      await closed.getByText('Sent by Sam Okafor',{exact:true}).waitFor();
      assert.match(await closed.locator('pre').textContent(),/My native unsaved title/);
      await page.evaluate(()=>{
        const f=window.pendingFixture;
        f.a.shared.sections.task=[f.adopted];
        f.a.linked={tasks:[{task_id:'existing-task',title:'Original server title',number:42,status:'open'}]};
      });
      await closed.getByText('Sent by Sam Okafor',{exact:true}).waitFor();
      assert.match(await closed.locator('pre').textContent(),/My native unsaved title/);
      await closed.getByRole('button',{name:close,exact:true}).click();
      await closed.locator('pre').waitFor({state:'detached'});
      if(close==='Copy') assert.match(await page.evaluate(()=>window.copiedText),/My native unsaved title/);
      await page.evaluate(()=>{window.pendingFixture.a.shared_scope+='-refresh';window.pendingFixture.a.linked=null;});
      await page.getByText('Original server title · Sent',{exact:false}).waitFor();
      assert.equal(await page.locator('[data-shared-row] pre').count(),0);
    }
    // A Save recovery also keeps local fields when the server is still proposed but has moved on.
    await page.evaluate(() => {
      const a=window.pendingFixture.a;
      const current={id:'changed-on-save',section:'task',status:'proposed',version:2,text:'New remote title',detail:{owner:'human:sam'},updated_by:'human:sam'};
      a.saved=true; a.shared_scope='save-recovery';
      a.shared={can_send:true,sections:{task:[current]}};
      a.items=[{id:'local-save',title:'My retry title',sent:false,closed_draft:{item:current,text:'My retry title',detail:{owner:'human:dana',body:'My retry details'}}}];
    });
    const moved=page.locator('[data-shared-row="changed-on-save"]');
    await moved.getByText('Changed by Sam Okafor',{exact:true}).waitFor();
    assert.match(await moved.locator('pre').textContent(),/My retry title[\s\S]*My retry details/);
    assert.equal(await moved.locator('textarea,input,select').count(),0);
    await moved.getByRole('button',{name:'Copy',exact:true}).click();
    assert.match(await page.evaluate(()=>window.copiedText),/My retry title/);
    await moved.getByRole('textbox',{name:'Title',exact:true}).waitFor();
    assert.equal(await moved.getByRole('textbox',{name:'Title',exact:true}).inputValue(),'New remote title');
    // Linking controls and remote rows stay in the saved shared panel.
    await page.evaluate(() => {
      const f=window.pendingFixture, a=f.a;
      a.items=[]; a.can_link=true; a.link_scope=['m1','acme'];
      a.shared_scope='saved-links'; a.linked_browser='https://app.acme.example';
      a.linked={tasks:[{task_id:'linked-task',number:45,title:'Linked pricing',status:'open'}]};
      a.shared={can_send:true,sections:{task:[{id:'link-proposal',version:1,section:'task',status:'proposed',text:'Pricing follow up',proposed_task_id:'linked-task',proposed_number:45,detail:{}}]}};
      window.__TAURI__.core.invoke=async (cmd,args) => {
        if(cmd==='task_picker') return {tasks:[{id:'unnumbered',number:null,title:'Unnumbered target',status:'open'}],next_offset:null};
        if(cmd==='shared_item') {
          f.linkAction=args.action; f.linkBody=args.body;
          const item=a.shared.sections.task[0]; item.status='pushed';item.result_ref='linked-task';item.version++;
          return {item:structuredClone(item),pushed:true};
        }
        return f.original(cmd,args);
      };
    });
    await page.getByRole('button',{name:'Link #45',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:/^Send/}).count(),0);
    await page.getByRole('button',{name:'Link',exact:true}).waitFor();
    await page.getByText('Linked pricing',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Unlink',exact:true}).waitFor();
    await page.getByRole('button',{name:'Link #45',exact:true}).click();
    await page.waitForFunction(()=>window.pendingFixture.linkAction==='link');
    assert.deepEqual(await page.evaluate(()=>window.pendingFixture.linkBody),{});
    await page.locator('[data-shared-row="link-proposal"]').waitFor({state:'detached'});
    assert.equal(await page.getByText('Linked pricing',{exact:false}).count(),1);
    await page.getByRole('button',{name:'Add',exact:true}).click();
    await page.getByRole('textbox',{name:'Add a task',exact:true}).fill('Added after Save');
    assert.equal(await page.getByRole('textbox',{name:'Add a task',exact:true}).inputValue(),'Added after Save');
        await page.getByRole('button',{name:'Link',exact:true}).click();
    await page.getByRole('button',{name:'Unnumbered target · open',exact:true}).waitFor();
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    assert.deepEqual(errors,[]);
    console.log('Recorder: in-flight draft, retry and final Send passed');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
