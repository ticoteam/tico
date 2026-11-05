// The registry module's policy writes, inheritance, cancellation and responsive layouts over a fake API.
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport: {width, height: 1000}});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.setContent('<main id="settings"></main><main id="detail"></main>');
      await page.addStyleTag({content: 'body{font:14px sans-serif;margin:16px;--line:#ccc;--muted:#666}button,input,select{font:inherit}main{min-width:0}.ib-field{display:grid;grid-template-columns:180px minmax(0,1fr);gap:10px;padding:10px 0}@media(max-width:760px){.ib-field{grid-template-columns:1fr}}'});
      await page.addStyleTag({content: fs.readFileSync(path.join(root, 'styles/inboxes.css'), 'utf8')});
      await page.addScriptTag({content: `
        const specs = {}, panels = {}, writes = [];
        let refreshes = 0;
        const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        const inboxesSetting = (key, spec) => specs[key] = spec;
        const inboxesPanel = (key, spec) => panels[key] = spec;
        const inboxesCard = (id, title, body) => '<section id="'+id+'"><h2>'+esc(title)+'</h2>'+body+'</section>';
        const toast = () => {};
        const doc = {company: {}, inbox: {}, bot: {}, effective: {mode:'ask_first',what:'replies_only',to:'anyone',daily_cap:50,mailbox_daily_cap:200,company_daily_cap:1000,pacing_seconds:10,undo_seconds:120,ask_new_address:true,ask_attachments:true,ask_many_recipients:true,ask_legal_money:true,who_can_ok:'bot_managers',approvers:[],sources:{mode:'company'}}};
        const send = {id:'pending-one',subject:'<script>unsafe</script>',recipients:['a@vendor.example'],state:'pending',send_after:'2026-11-04T12:00:00Z',can_cancel:true,actor:'bot:ops',decision:{reasons:[]},content_sha256:'a'.repeat(64),provider_message_id:'provider-1'};
        const get = async p => p.startsWith('/v2/email/sends/') ? {send:{...send,attachments:[{name:'proof.pdf',size:5,part:1,sha256:'b'.repeat(64)}]},body:'Frozen <script>unsafe</script>',bodies:[{type:'text/plain',text:'Frozen <script>unsafe</script>'}],preview_complete:true} : p === '/v2/org' ? {bots:[{id:'ops',display_name:'Ops'}]} : p.startsWith('/v2/email/sends') ? {sends:[send],next_cursor:null} : structuredClone(doc);
        const put = async (p,b) => {writes.push({p,b,method:'PUT'}); return doc;};
        const post = async (p,b) => {writes.push({p,b,method:'POST'}); return {};};
        const ctx = {admin:true,once:(k,f)=>f(),live:()=>true,refresh:()=>refreshes++};
      `});
      await page.addScriptTag({path: path.join(root, 'app/email-preview.js')});
      await page.addScriptTag({path: path.join(root, 'app/inboxes-sending.js')});
      await page.evaluate(() => specs['email.sending'].render(document.querySelector('#settings'), ctx));
      const form = page.locator('#settings [data-send-form]');
      await form.locator('[name=mode]').selectOption('on_their_own');
      await form.locator('[name=ask_attachments]').uncheck();
      await form.locator('[name=undo_seconds]').selectOption('600');
      await form.locator('[type=submit]').click();
      await page.waitForFunction(() => refreshes === 1);
      const write = await page.evaluate(() => writes[0]);
      assert.equal(write.p, '/v2/email/sending-policy');
      assert.equal(write.b.mode, 'on_their_own');
      assert.equal(write.b.ask_attachments, false);
      assert.equal(write.b.undo_seconds, 600);
      await page.evaluate(() => panels['email.sending'].render(document.querySelector('#detail'), {address:'support@acme.example',can_manage:true,accesses:[{grantee_type:'bot',grantee_id:'ops',name:'Ops'}]}, ctx));
      assert.match(await page.locator('#inbox-waiting-to-send').textContent(), /<script>unsafe<\/script>/);
      assert.equal(await page.locator('#detail script').count(), 0);
      await page.locator('[data-send-preview]').click();
      await page.locator('.email-send-preview pre').waitFor();
      assert.equal(await page.locator('.email-send-preview pre').textContent(), 'Frozen <script>unsafe</script>');
      assert.equal(await page.locator('.email-send-preview script').count(), 0);
      assert.equal(await page.locator('.email-send-preview a').getAttribute('href'), '/api/v2/email/sends/pending-one/attachments/1');
      await page.locator('[data-send-cancel]').click();
      await page.waitForFunction(() => writes.some(w => w.p.endsWith('/pending-one/cancel')));
      await page.locator('#detail [data-send-inherit]').click();
      await page.waitForFunction(() => writes.some(w => w.b.inherit === true));
      await page.locator('[data-send-audit]').click();
      await page.locator('[data-send-activity] code').waitFor();
      assert.match(await page.locator('[data-send-activity]').textContent(), /provider-1/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Email Sending: settings, inheritance, cancel, audit and phone layout passed');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exitCode = 1;});
