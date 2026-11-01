// Imported history has no runtime credential actions; real agents retain them in their Settings tab. A human's Grok Bots
// are ordinary org-chart rows: source sections do not override their placement or ordering.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({serviceWorkers: 'block'});
  const last = new Date(Date.now() - 27 * 3600e3).toISOString();
  let fresh = false;
  const placements = {};
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { window.EventSource = undefined; });
  const bots = () => [
    {name:'history-copy',display_name:'Imported Designer',status:'active',host:'keeper',can_chat:true,owner_id:'ana',
      online:fresh,agent:{harness:'grokbot',credential:true,synced:true,last_seen_at:last}},
    {name:'live-agent',display_name:'Live Agent',status:'active',host:'keeper',can_chat:true,owner_id:'ana',
      online:false,agent:{harness:'hermes',credential:true,last_seen_at:last}},
    ...[['groky','Groky','Pinned'],['grok-scout','Grok Scout','Outreach'],['grok-poetry','Poetry Post','']].map(([name,display_name,section]) =>
      ({name,display_name,status:'active',host:'keeper',harness:'grokbot',reports_to:'human:ana',org_parent:'p:ana',owner_id:'ana',online:true,
        revision:1,...placements[name],
        agent:{harness:'grokbot',credential:true,synced:true,label:'Grok Bot',section,last_seen_at:last}})),
    {name:'dots',display_name:'Dots',status:'active',host:'keeper',harness:'dots',reports_to:'human:ana',org_parent:'p:ana',owner_id:'ana',online:true,
      agent:{harness:'dots',credential:true,synced:true,label:'Dots',section:'',last_seen_at:last}}
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
    if (p.startsWith('/vendor/') || p.startsWith('/assets/')) {
      const file = uiFile(p.slice(1));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana',role:'owner',email:'ana@acme.example',cloud:true,bot_admin:true});
    if (p === '/api/humans') return json({people:[{id:'ana',name:'Ana'},{id:'ben',name:'Ben'}],
      org_groups:[{id:'marketing',name:'Marketing',parent:''}]});
    if (p === '/api/employees') return json(bots());
    if (p === '/api/issues') return json([]);
    const definition = p.match(/^\/api\/v2\/bots\/([^/]+)\/definition$/);
    if (definition && request.method() === 'POST') {
      const body = request.postDataJSON();
      assert.equal(body.expected_revision, 1);
      placements[definition[1]] = {reports_to:body.reports_to,
        org_parent:body.reports_to.startsWith('human:') ? 'p:' + body.reports_to.slice(6) : 'b:' + body.reports_to};
      return json({});
    }
    if (p === '/api/v2/groups/marketing' && request.method() === 'PATCH') {
      const body = request.postDataJSON();
      for (const name of body.add?.bots || []) placements[name] = {...placements[name],team:'marketing'};
      return json({});
    }
    if (p === '/api/status') return json({cloud:true,keeper_alive:true,active:[],queued:[],health_issues:[]});
    if (p === '/api/v2/status') return json({bots:bots().map(b=>({bot:b.name,state:'idle',open_tasks:1}))});
    if (p === '/api/v2/tasks') return json({tasks:[],next_offset:null});
    if (p === '/api/v2/conversations') return json({conversations:[{id:'thread',scope:'personal',participants:['human:ana','bot:history-copy']}]});
    if (p.endsWith('/snapshot')) return json({messages:[]});
    if (p.endsWith('/files')) return json({files:[],total:0,can_manage:true});
    if (p.endsWith('/tools')) return json({tools:[]});
    return json(unrouted(p));
  });
  try {
    await page.goto('https://tico-ui.test/#/bot/history-copy/settings');
    await page.locator('#pane-settings:not([hidden]) #bot-editor form').waitFor();
    assert.equal(await page.locator('#pane-settings [data-agent-credential], #pane-settings [data-agent-revoke]').count(),0,
      'an import has no runtime credential to rotate or revoke');
    assert.doesNotMatch(await page.locator('#bot-alert').innerText(),/Offline/);
    assert.match(await page.locator('#bot-alert').innerText(),/History.*sync/i);
    await page.locator('#bot-top [data-tip-bot]').focus();
    await page.locator('#bot-tip:not([hidden])').waitFor();
    assert.match(await page.locator('#bot-tip').innerText(),/Last sync/);
    assert.doesNotMatch(await page.locator('#bot-tip').innerText(),/Not reporting|Last seen/);
    assert.equal(await page.evaluate(()=>S.emps.find(e=>e.name==='history-copy').agent.last_seen_at),last);
    assert.equal(await page.evaluate(()=>settingsBotProblem(S.emps.find(e=>e.name==='history-copy'))),'history not synced');
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:process.env.TICO_SCREENSHOT_DIR+'/imported-history.png'});

    fresh = true;
    await page.reload();
    await page.locator('#pane-settings:not([hidden]) #bot-editor form').waitFor();
    assert.equal(await page.locator('#bot-alert').innerText(),'');
    assert.equal(await page.locator('#pane-settings [data-agent-credential], #pane-settings [data-agent-revoke]').count(),0);
    assert.equal(await page.evaluate(()=>settingsBotProblem(S.emps.find(e=>e.name==='history-copy'))),'');

    await page.goto('https://tico-ui.test/#/bot/live-agent/settings');
    await page.waitForFunction(()=>S.route==='#/bot/live-agent/settings');   // a page change draws a frame later (its fade)
    await page.locator('#pane-settings:not([hidden]) #bot-editor form').waitFor();
    assert.match(await page.locator('#bot-alert').innerText(),/Offline with work waiting/);
    assert.equal(await page.locator('#pane-settings [data-agent-credential], #pane-settings [data-agent-revoke]').count(),2,
      'a real external agent retains its credential controls');
    await page.locator('#bot-top [data-tip-bot]').focus();
    await page.locator('#bot-tip:not([hidden])').waitFor();
    assert.match(await page.locator('#bot-tip').innerText(),/Not reporting|Last seen/);
    assert.deepEqual(errors,[]);
    assert.deepEqual(writes,[],'reading snapshots creates no credential or sends a message');
    await page.goto('https://tico-ui.test/#/');
    const node = key => page.locator(`#tree [data-org="${key}"]`);
    const children = key => node(key).locator('xpath=ancestor::li[1]').locator(':scope > ul > li > .noderow > a.node');
    await node('b:groky').waitFor();
    assert.equal(await page.locator('#tree .org-cluster, #tree [data-toggle^="c:"]').count(),0);
    // Ana, the signed-in owner, is the top of the chart: her reports sit at the top level after her row, then Ben (also
    // at the top, nobody above him) and the bots that report to no one.
    const top = () => page.locator('#tree > li > .noderow').evaluateAll(rows => rows.map(row => (row.querySelector('[data-org]') || row).dataset.org));
    assert.deepEqual(await top(),['p:ana','g:marketing','b:dots','b:grok-scout','b:groky','b:grok-poetry','p:ben','b:history-copy','b:live-agent'],
      'Grok sections and Pinned do not override ordinary sibling ordering');
    for (const name of ['groky','grok-scout','grok-poetry']) {
      assert.equal(await node('b:' + name).count(),1);
      assert.equal(await node('b:' + name).getAttribute('draggable'),'true');
      // The harness mark reads harness, model and effort (#749); these rows name no model or effort.
      assert.equal(await node('b:' + name).locator('.rt[aria-label="Grok Bot"]').count(),1);
      assert.equal(await node('b:' + name).locator('.rt').getAttribute('title'),'Grok Bot');
    }
    assert.equal(await page.locator('#tree a.node[href="#/bot/dots"]').count(),1);
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:process.env.TICO_SCREENSHOT_DIR+'/grok-org-chart.png'});

    // Use the same drop path as ordinary bots: a bot manager, a human and a department.
    await page.evaluate(() => orgDrop('b:groky','b:live-agent'));
    await page.evaluate(() => orgDrop('b:grok-scout','p:ben'));
    await page.evaluate(() => orgDrop('b:grok-poetry','g:marketing'));
    await page.evaluate(() => refresh(false));
    assert.deepEqual(await children('b:live-agent').locator('.nm').allInnerTexts(),['Groky']);
    assert.deepEqual(await children('p:ben').locator('.nm').allInnerTexts(),['Grok Scout']);
    assert.deepEqual(await children('g:marketing').locator('.nm').allInnerTexts(),['Poetry Post']);
    assert.deepEqual(writes,['/api/v2/bots/groky/definition','/api/v2/bots/grok-scout/definition','/api/v2/groups/marketing']);
    await page.evaluate(() => { collapsed.clear(); saveCollapsed(); renderTree(); });
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:process.env.TICO_SCREENSHOT_DIR+'/grok-org-chart-moved.png'});
    await page.setViewportSize({width:390,height:844});
    await page.locator('#mobile-more').click();
    assert.equal(await node('b:groky').count(),1);
    assert.equal(await node('b:groky').isVisible(),true);
    assert.equal(await page.locator('#tree .org-cluster').count(),0);
    await page.waitForFunction(() => document.getAnimations().every(animation =>
      animation.playState !== 'running' || animation.effect?.getTiming().iterations === Infinity));
    assert.equal(await node('b:groky').isVisible(),true);
    if (process.env.TICO_SCREENSHOT_DIR) await page.screenshot({path:process.env.TICO_SCREENSHOT_DIR+'/grok-org-chart-mobile.png'});
    assert.deepEqual(errors,[]);
    console.log('imported-history: ok');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
