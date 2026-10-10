// Imported history is a snapshot, with no runtime credential actions; real agents retain them. A human's Grok Bots
// sit in one cluster under them, pinned ones first; their Dots is a plain row.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({serviceWorkers: 'block'});
  const last = new Date(Date.now() - 27 * 3600e3).toISOString();
  let fresh = false;
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { window.EventSource = undefined; });
  const bots = () => [
    {name:'history-copy',display_name:'Imported Designer',status:'active',host:'keeper',can_chat:true,operator:'ana',
      online:fresh,agent:{harness:'grokbot',credential:true,synced:true,last_seen:last}},
    {name:'live-agent',display_name:'Live Agent',status:'active',host:'keeper',can_chat:true,operator:'ana',
      online:false,agent:{harness:'hermes',credential:true,last_seen:last}},
    ...[['groky','Groky','Pinned'],['grok-scout','Grok Scout',''],['grok-poetry','Poetry Post','']].map(([name,display_name,section]) =>
      ({name,display_name,status:'active',host:'keeper',harness:'grokbot',reports_to:'human:ana',org_parent:'p:ana',operator:'ana',online:true,
        agent:{harness:'grokbot',credential:true,synced:true,label:'Grok Bot',section,last_seen:last}})),
    {name:'dots',display_name:'Dots',status:'active',host:'keeper',harness:'dots',reports_to:'human:ana',org_parent:'p:ana',operator:'ana',online:true,
      agent:{harness:'dots',credential:true,synced:true,label:'Dots',section:'',last_seen:last}}
  ];
  await page.route('**/*', route => {
    const request = route.request(), p = new URL(request.url()).pathname;
    const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    // Opening a bot page saves the recently-viewed list (sidebar.js, after 800 ms); that is the page's own
    // preference, not a credential or a message, and whether it lands before the end depends on load.
    if (request.method() !== 'GET' && !p.startsWith('/api/v2/preferences/')) writes.push(p);
    if (p === '/') return route.fulfill({contentType:'text/html',body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana',role:'owner',email:'ana@acme.example',cloud:true,bot_admin:true});
    if (p === '/api/humans') return json({people:[{id:'ana',name:'Ana'}]});
    if (p === '/api/employees') return json(bots());
    if (p === '/api/status') return json({cloud:true,keeper_alive:true,active:[],queued:[],health_issues:[]});
    if (p === '/api/v2/status') return json({bots:bots().map(b=>({bot:b.name,state:'idle',open_tasks:1}))});
    if (p === '/api/v2/tasks') return json({tasks:[],next_offset:null});
    if (p === '/api/v2/conversations') return json({conversations:[{id:'thread',scope:'personal',participants:['human:ana','bot:history-copy']}]});
    if (p.endsWith('/snapshot')) return json({messages:[]});
    if (p.endsWith('/files')) return json({files:[],total:0,can_manage:true});
    if (p.endsWith('/tools')) return json({tools:[]});
    return json({});
  });
  try {
    await page.goto('https://tico-ui.test/#/bot/history-copy/more');
    await page.locator('#pane-more:not([hidden])').waitFor();
    assert.equal(await page.locator('#pane-more [data-agent-credential], #pane-more [data-agent-revoke]').count(),0,
      'an import has no runtime credential to rotate or revoke');
    assert.doesNotMatch(await page.locator('#bot-alert').innerText(),/Offline/);
    assert.match(await page.locator('#bot-alert').innerText(),/History.*sync/i);
    await page.locator('#bot-top [data-tip-bot]').focus();
    await page.locator('#bot-tip:not([hidden])').waitFor();
    assert.match(await page.locator('#bot-tip').innerText(),/Last sync/);
    assert.doesNotMatch(await page.locator('#bot-tip').innerText(),/Not reporting|Last seen/);
    assert.equal(await page.evaluate(()=>S.emps.find(e=>e.name==='history-copy').agent.last_seen),last);
    assert.equal(await page.evaluate(()=>settingsBotProblem(S.emps.find(e=>e.name==='history-copy'))),'history not synced');
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:process.env.TICO_SCREENSHOT_DIR+'/imported-history.png'});

    fresh = true;
    await page.reload();
    await page.locator('#pane-more:not([hidden])').waitFor();
    assert.equal(await page.locator('#bot-alert').innerText(),'');
    assert.equal(await page.locator('#pane-more [data-agent-credential], #pane-more [data-agent-revoke]').count(),0);
    assert.equal(await page.evaluate(()=>settingsBotProblem(S.emps.find(e=>e.name==='history-copy'))),'');

    await page.goto('https://tico-ui.test/#/bot/live-agent/more');
    await page.locator('#pane-more:not([hidden])').waitFor();
    assert.match(await page.locator('#bot-alert').innerText(),/Offline with work waiting/);
    assert.equal(await page.locator('#pane-more [data-agent-credential], #pane-more [data-agent-revoke]').count(),2,
      'a real external agent retains its credential controls');
    await page.locator('#bot-top [data-tip-bot]').focus();
    await page.locator('#bot-tip:not([hidden])').waitFor();
    assert.match(await page.locator('#bot-tip').innerText(),/Not reporting|Last seen/);
    assert.deepEqual(errors,[]);
    assert.deepEqual(writes,[],'reading snapshots creates no credential or sends a message');
    await page.goto('https://tico-ui.test/#/');
    const cluster = page.locator('#tree li.org-cluster:not(.org-cluster-section)');
    await cluster.waitFor();
    assert.match(await cluster.locator('> .noderow').innerText(),/Grok Bot\s*3/);
    assert.equal(await cluster.locator('li.org-cluster-section').count(),0,'Pinned is not its own section');
    assert.deepEqual(await cluster.locator('> ul > li a.node .nm').allInnerTexts(),['Groky','Grok Scout','Poetry Post'],
      'Pinned leads, as in Grok');
    assert.equal(await page.locator('#tree li.org-cluster a.node[href="#/bot/dots"]').count(),0,'Dots is one agent: no cluster');
    assert.equal(await page.locator('#tree a.node[href="#/bot/dots"]').count(),1);
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:process.env.TICO_SCREENSHOT_DIR+'/grok-cluster.png'});
    assert.deepEqual(errors,[]);
    console.log('imported-history: ok');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
