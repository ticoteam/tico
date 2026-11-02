// The desktop app is one ordinary window: no right-rail button or mode, and the page stays usable as the person
// drags it narrow (the compact layout up to 760px, as the rail used to give) or wide.
const {webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const VISUAL = 'https://visuals.acme-files.example', NEXT_VISUAL = 'https://next.acme-files.example';
const TOKEN = 'YQ.' + 'a'.repeat(43);
const bridge = fs.readFileSync(require('node:path').join(__dirname, '../../app/src/main.rs'), 'utf8')
  .match(/const BRIDGE_SCRIPT: &str = r#"([\s\S]*?)"#;/)[1];

// Browser fixture of the actual bridge wrapper and app, not proof of native WebView navigation/IPC.
async function visualHandshake(browser) {
  const page = await browser.newPage({serviceWorkers:'block', userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [], reads = [];
  page.on('pageerror', e => {
    // WebKit reports its expected denial while Playwright blocks service workers in sandboxed frames.
    if (e.message !== "Service Worker is disabled because the context is sandboxed and lacks the 'allow-same-origin' flag") errors.push(e.message);
  });
  await page.addInitScript({content:`
    (() => {
    if (window.top !== window) return;
    window.EventSource = undefined;
    window.__calls = []; window.__pending = []; window.__nativeOrigin = '';
    window.__TAURI__ = {core:{invoke: (command, args) => {
      if (command !== 'bridge' || args.name !== 'visualOrigin') return Promise.resolve(null);
      window.__calls.push(args.body);
      return new Promise((resolve, reject) => window.__pending.push({resolve, reject, origin:args.body.origin}));
    }}};
    ${bridge}
    })();
  `});
  let config = {company_name:'Acme', visual_origin:VISUAL}, visual = VISUAL, revision = 1;
  let authenticated = true, holdEmployees = null;
  const message = () => ({id:'m1', from_actor:'bot:ops', body:'Visual revision '+revision,
    created_at:'2026-01-01T00:00:00Z', refs:{attachments:[{id:'f1', file_id:'f1', version:revision,
      current_version:revision, ref:'f1@'+revision, title:'Plan', kind:'page', name:'plan.html',
      size:100, content_type:'text/html', url:'/api/v2/files/f1', src:visual+'/visuals/'+TOKEN}]}});
  const task = {id:'t1', title:'Visual task', owner:'bot:ops', requester:'human:ana', status:'review', lane:'company',
    rank:1, version:1, created_at:'2026-01-01T00:00:00Z', updated_at:'2026-01-01T00:00:00Z', body:'Plan',
    parts:{total:0, done:0}, labels:[], links:[], attachments:[]};
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType:'application/json', body:JSON.stringify(body)});
    if ([new URL(VISUAL).hostname, new URL(NEXT_VISUAL).hostname].includes(url.hostname)) {
      reads.push(url.origin);
      return route.fulfill({contentType:'text/html', body:'<p>Plan</p>', headers:{'Cross-Origin-Resource-Policy':'cross-origin'}});
    }
    if (p === '/') return route.fulfill({contentType:'text/html', body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json(authenticated
      ? {id:'ana', email:'ana@acme.example', role:'owner', cloud:true, config}
      : {config}); // a mere HTTP 200/config object is not an authenticated person
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') {
      if (holdEmployees) { holdEmployees(route); return; }
      return json([{name:'ops', display_name:'Ops', status:'active', host:'keeper', can_chat:true}]);
    }
    if (p === '/api/humans') return json({people:[{id:'ana', name:'Ana'}], groups:[]});
    if (p === '/api/status') return json({cloud:true, keeper_alive:true, health_issues:[]});
    if (p === '/api/v2/status') return json({bots:[{bot:'ops', state:'idle', open_tasks:0}]});
    if (p === '/api/v2/needs-you') return json({items:[]});
    if (p === '/api/v2/conversations') return json({conversations:[{id:'c-ops', kind:'chat', scope:'personal', participants:['human:ana','bot:ops']}]});
    if (p === '/api/v2/conversations/c-ops/snapshot') return json({messages:[message()], execution:null});
    if (p === '/api/v2/tasks/t1') return json({task, comments:[{...message(), kind:'say'}], events:[], children:[], parent:null, mover:true, can_comment:true});
    if (p === '/api/v2/tasks/t1/files') return json({files:[]});
    return json(unrouted(p));
  });
  const waitCall = async (index, origin) => {
    await page.waitForFunction(index => window.__calls.length > index, index);
    assert.equal(await page.evaluate(index => window.__calls[index].origin, index), origin);
  };
  const ack = (index, accepted) => page.evaluate(({index, accepted}) => {
    const call = window.__pending[index]; window.__nativeOrigin = accepted ? call.origin : ''; call.resolve(accepted);
  }, {index, accepted});
  const refresh = () => page.evaluate(() => { window.__refresh = refreshConfig(); });
  await page.goto('https://tico-ui.test/#/bot/ops/chat', {waitUntil:'domcontentloaded'});
  await waitCall(0, '');
  await page.waitForFunction(() => typeof visualSrc === 'function');
  assert.equal(await page.evaluate(() => visualSrc({src:'/visuals/local'})), 'https://tico-ui.test/visuals/local',
    'same-origin local visuals keep working while a clear ACK is pending');
  await ack(0, false);
  await waitCall(1, VISUAL);
  assert.equal(await page.locator('iframe.vcard-frame').count(), 0);
  assert.deepEqual(reads, [], 'boot has not rendered a visual before the bridge ACK');
  const firstRead = page.waitForResponse(response => new URL(response.url()).origin === VISUAL);
  await ack(1, true);
  await page.locator('iframe.vcard-frame[src]').waitFor();
  await firstRead;
  assert.ok(reads.includes(VISUAL));
  await page.evaluate(task => taskModalShow(task), task);
  await page.locator('#task-modal iframe.vcard-frame[src]').waitFor();
  await page.locator('#task-modal textarea[aria-label="Message"]').fill('Keep this draft');

  config = {company_name:'Acme revised', visual_origin:NEXT_VISUAL};
  await refresh(); await waitCall(2, NEXT_VISUAL);
  visual = NEXT_VISUAL; revision++;
  await page.evaluate(() => Promise.all([v2ChatSnapshot(V2C), taskChatRead(TASK_CHAT)]));
  assert.equal(await page.locator('iframe.vcard-frame').count(), 0, 'live snapshot stays file chips during config ACK');
  assert.equal(reads.includes(NEXT_VISUAL), false);
  const nextRead = page.waitForResponse(response => new URL(response.url()).origin === NEXT_VISUAL);
  await ack(2, true); await page.evaluate(() => window.__refresh);
  await page.waitForFunction(() => document.querySelectorAll('iframe.vcard-frame[src]').length === 2);
  await nextRead;
  assert.ok(reads.includes(NEXT_VISUAL));
  assert.equal(await page.locator('#task-modal textarea[aria-label="Message"]').inputValue(), 'Keep this draft');
  const projected = await page.evaluate(({origin, token}) => {
    const before = window.__calls.length;
    applyConfig({...S.config, visual_origin:'https://untrusted.example'});
    const src = value => visualSrc({src:value});
    return {calls:window.__calls.length-before, good:src(origin+'/visuals/'+token),
      denied:[origin+'/visuals/'+token+'#x', origin+'/visuals/'+token+'?', origin+'/a/../visuals/'+token,
        origin+'/visuals/%59Q.'+'a'.repeat(43), origin+'/api/v2/me', 'https://untrusted.example/visuals/'+token,
        'https://person@'+new URL(origin).host+'/visuals/'+token].map(src), local:src('/visuals/local')};
  }, {origin:NEXT_VISUAL, token:TOKEN});
  assert.equal(projected.calls, 0, 'local applyConfig does not grant navigation');
  assert.equal(projected.good, NEXT_VISUAL+'/visuals/'+TOKEN);
  assert.ok(projected.denied.every(value => value === null));
  assert.equal(projected.local, 'https://tico-ui.test/visuals/local');

  config = {company_name:'Missing visual setting'};
  await refresh(); await waitCall(3, ''); await ack(3, false); await page.evaluate(() => window.__refresh);
  assert.equal(await page.evaluate(() => S.config.visual_origin), '');
  config = {company_name:'Grant refused', visual_origin:VISUAL};
  await refresh(); await waitCall(4, VISUAL);
  await ack(4, false);
  await waitCall(5, ''); await ack(5, false); await page.evaluate(() => window.__refresh);
  assert.equal(await page.evaluate(() => S.config.company_name), 'Grant refused');
  assert.equal(await page.evaluate(src => visualSrc({src}), VISUAL+'/visuals/'+TOKEN), null);
  config = {company_name:'Bridge unavailable', visual_origin:VISUAL};
  await refresh(); await waitCall(6, VISUAL);
  await page.evaluate(() => window.__pending[6].reject(new Error('bridge unavailable')));
  await waitCall(7, ''); await ack(7, false); await page.evaluate(() => window.__refresh);
  assert.equal(await page.evaluate(() => S.config.company_name), 'Bridge unavailable', 'bridge failure keeps the app usable');
  assert.equal(await page.evaluate(src => visualSrc({src}), VISUAL+'/visuals/'+TOKEN), null);

  config = {company_name:'Late grant', visual_origin:NEXT_VISUAL};
  await refresh(); await waitCall(8, NEXT_VISUAL);
  config = {company_name:'Queued grant', visual_origin:VISUAL};
  await page.evaluate(() => { window.__queuedAt = nativeVisualVersion(); window.__queued = refreshConfig(); });
  await page.waitForFunction(() => nativeVisualVersion() > window.__queuedAt);
  await page.evaluate(() => signedOut());
  await ack(8, true); await waitCall(9, '');
  assert.equal(await page.evaluate(src => visualSrc({src}), NEXT_VISUAL+'/visuals/'+TOKEN), null);
  await ack(9, false); await page.evaluate(() => Promise.all([window.__refresh, window.__queued]));
  assert.equal(await page.evaluate(() => window.__nativeOrigin), '');
  assert.equal(await page.evaluate(() => S.config.visual_origin), '');
  assert.equal(await page.evaluate(() => S.config.company_name), 'Bridge unavailable', 'stale ACKs cannot apply old config after sign-out');
  assert.equal(await page.evaluate(() => window.__calls.length), 10, 'the queued grant was skipped before native invocation');

  // A stale bridge rejection after sign-out also stays silent and cannot restore config.
  await page.reload({waitUntil:'domcontentloaded'});
  await waitCall(0, ''); await ack(0, false); await waitCall(1, VISUAL);
  await page.evaluate(() => signedOut());
  await page.evaluate(() => window.__pending[1].reject(new Error('old command failed')));
  await waitCall(2, ''); await ack(2, false);
  await waitCall(3, ''); await ack(3, false);
  assert.equal(await page.evaluate(() => S.config.visual_origin), '');
  assert.equal(await page.getByRole('heading', {name:'Tico server not running'}).count(), 0);

  const rosterRequest = new Promise(resolve => { holdEmployees = resolve; });
  await page.reload({waitUntil:'domcontentloaded'});
  const staleRoster = await rosterRequest;
  await waitCall(0, ''); await ack(0, false);
  await page.evaluate(() => signedOut());
  await waitCall(1, ''); await ack(1, false);
  await staleRoster.abort();
  await page.waitForFunction(() => !GET_INFLIGHT.has('/employees'));
  assert.equal(await page.getByRole('heading', {name:'Tico server not running'}).count(), 0,
    'a roster failure after sign-out cannot replace the preserved page with a server error');
  holdEmployees = null;

  // Hold the actual deferred iframe setter across sign-out, then replay cached
  // image/media recovery and Expand. Approval must be checked at the load itself.
  config = {company_name:'Deferred visual', visual_origin:VISUAL}; visual = VISUAL;
  const beforeDeferred = reads.length;
  await page.reload({waitUntil:'domcontentloaded'});
  await waitCall(0, ''); await ack(0, false); await waitCall(1, VISUAL);
  await page.evaluate(() => {
    window.__raf = requestAnimationFrame; window.__heldRAF = [];
    window.requestAnimationFrame = callback => window.__heldRAF.push(callback);
  });
  await ack(1, true);
  await page.waitForFunction(() => document.querySelector('iframe.vcard-frame[data-src]'), null, {polling:50});
  await page.evaluate(({origin, token}) => {
    const cached = (id, kind, body) => {
      const card = document.createElement('figure');
      card.id = id; card.className = 'vcard'; card.dataset.kind = kind; card.dataset.wired = '1';
      card.dataset.freshSrc = visualSrc({src:origin+'/visuals/'+token});
      card.innerHTML = '<div class="vcard-body">'+body+'</div><div class="vcard-menu-list"><a href="/api/v2/files/f1" download>Download</a></div>';
      document.body.append(card); return card;
    };
    const image = cached('cached-image', 'image', '<img class="vcard-img">');
    Object.defineProperty(image.querySelector('img'), 'complete', {value:false}); // a pending load errors only after clear
    visualPictureWire(image);
    const media = cached('cached-media', 'audio', '<div class="tf-media"><audio data-vm-src="old"></audio></div>'); vmWire(media);
    cached('cached-expand', 'page', 'Open this visual');
  }, {origin:VISUAL, token:TOKEN});
  await page.evaluate(() => signedOut());
  await waitCall(2, ''); await ack(2, false);
  await page.evaluate(() => {
    for (let batch = 0; batch < 2; batch++) {
      const callbacks = window.__heldRAF.splice(0); callbacks.forEach(callback => callback(performance.now()));
    }
    window.requestAnimationFrame = window.__raf;
    document.querySelector('#cached-image img').dispatchEvent(new Event('error'));
    document.querySelector('#cached-media audio').dispatchEvent(new Event('error'));
    visualExpand(document.getElementById('cached-expand'));
  });
  assert.equal(await page.locator('iframe[src], #cached-image img[src], #cached-media audio[src]').count(), 0);
  assert.equal(await page.locator('#vcard-full:open').count(), 0, 'refused Expand does not open an empty modal');
  for (const id of ['cached-image', 'cached-media', 'cached-expand'])
    assert.equal(await page.locator('#'+id+' [data-vcard-act="open"]').count(), 1, 'refused cached load retains a usable file action');
  assert.deepEqual(reads.slice(beforeDeferred), [], 'releasing delayed setters after clear makes no asset request');

  // Only the UI wait expires. The real native FIFO remains blocked until the
  // held invoke settles, and even its late true ACK cannot turn chips into frames.
  config = {company_name:'Timed out bridge', visual_origin:VISUAL}; visual = VISUAL;
  const now = new Date();
  await page.clock.install({time:new Date(now.getTime()-3600000)});
  await page.clock.pauseAt(now);
  await page.reload({waitUntil:'domcontentloaded'});
  await waitCall(0, ''); await ack(0, false); await waitCall(1, VISUAL);
  await page.clock.fastForward(await page.evaluate(() => NATIVE_VISUAL_WAIT_MS));
  await page.waitForFunction(() => S.me?.id === 'ana');
  assert.equal(await page.evaluate(() => S.config.company_name), 'Timed out bridge');
  assert.equal(await page.locator('iframe.vcard-frame').count(), 0);
  assert.equal(await page.evaluate(() => window.__calls.length), 2, 'timeout does not advance the unresolved native FIFO');
  await ack(1, true); await waitCall(2, '');
  assert.equal(await page.evaluate(() => window.__nativeOrigin), VISUAL, 'queued native clear has not ACKed yet');
  assert.equal(await page.evaluate(src => visualSrc({src}), VISUAL+'/visuals/'+TOKEN), null);
  await ack(2, false); await page.evaluate(() => NATIVE_VISUAL_PENDING);
  assert.equal(await page.evaluate(() => window.__nativeOrigin), '');
  assert.equal(await page.locator('iframe.vcard-frame').count(), 0);

  authenticated = false; config = {visual_origin:VISUAL};
  await page.reload({waitUntil:'domcontentloaded'});
  await waitCall(0, ''); await ack(0, false);
  await waitCall(1, '');
  await page.evaluate(() => signedOut());
  await ack(1, false); await waitCall(2, ''); await ack(2, false);
  await page.evaluate(() => NATIVE_VISUAL_PENDING);
  assert.equal(await page.evaluate(() => S.me), null, 'a clear ACK after sign-out cannot restore malformed identity/config');
  assert.ok((await page.evaluate(() => window.__calls)).every(call => call.origin === ''), 'unauthenticated 200 never grants an origin');
  assert.deepEqual(errors, []);
  await page.close();
}

// Tico file hosting with no visual address: a page's `src` is on the app's own route, which sends its frame on to the
// company's files address. The shell is told that address with the visual origin, and the page frames only once it
// acknowledged it; a shell too old to know it never hears the address, and the page stays an Open card.
async function hostedHandshake(browser, current) {
  const FILES = 'https://acme-files.ticofiles.com';
  const page = await browser.newPage({serviceWorkers:'block', userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [], reads = [];
  page.on('pageerror', e => {
    if (e.message !== "Service Worker is disabled because the context is sandboxed and lacks the 'allow-same-origin' flag") errors.push(e.message);
  });
  await page.addInitScript({content:`
    (() => {
    if (window.top !== window) return;
    window.EventSource = undefined;
    window.__calls = []; window.__pending = [];
    window.__TAURI__ = {core:{invoke: (command, args) => {
      if (command !== 'bridge' || args.name !== 'visualOrigin') return Promise.resolve(null);
      window.__calls.push(args.body);
      return new Promise(resolve => window.__pending.push(resolve));
    }}};
    ${bridge}
    ${current ? '' : "window.ticoNativeFeatures = window.ticoNativeFeatures.filter(f => f !== 'visualFiles');"}
    })();
  `});
  const config = {company_name:'Acme', visual_origin:'', visual_files:FILES, visuals:'hosted'};
  const message = {id:'m1', from_actor:'bot:ops', body:'Weekly', created_at:'2026-01-01T00:00:00Z',
    refs:{attachments:[{id:'f1', file_id:'f1', version:1, current_version:1, ref:'f1@1', title:'Weekly', kind:'page',
      name:'weekly.html', size:100, content_type:'text/html', url:'/api/v2/files/f1', src:'/visuals/'+TOKEN}]}};
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType:'application/json', body:JSON.stringify(body)});
    if (p.startsWith('/visuals/')) { reads.push(p); return route.fulfill({contentType:'text/html', body:'<p>Weekly</p>'}); }
    if (p === '/') return route.fulfill({contentType:'text/html', body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana', email:'ana@acme.example', role:'owner', cloud:true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name:'ops', display_name:'Ops', status:'active', host:'keeper', can_chat:true}]);
    if (p === '/api/humans') return json({people:[{id:'ana', name:'Ana'}], groups:[]});
    if (p === '/api/status') return json({cloud:true, keeper_alive:true, health_issues:[]});
    if (p === '/api/v2/status') return json({bots:[{bot:'ops', state:'idle', open_tasks:0}]});
    if (p === '/api/v2/needs-you') return json({items:[]});
    if (p === '/api/v2/conversations') return json({conversations:[{id:'c-ops', kind:'chat', scope:'personal', participants:['human:ana','bot:ops']}]});
    if (p === '/api/v2/conversations/c-ops/snapshot') return json({messages:[message], execution:null});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/ops/chat', {waitUntil:'domcontentloaded'});
  await page.waitForFunction(() => window.__calls.length > 0);
  await page.evaluate(() => window.__pending[0](false));
  await page.waitForFunction(() => window.__calls.length > 1);
  assert.deepEqual(await page.evaluate(() => window.__calls[1]), current ? {origin:'', files:FILES} : {origin:''},
    current ? 'the shell hears the files address' : 'an older shell never hears a field it would refuse');
  // ACK inside the UI's bridge wait (NATIVE_VISUAL_WAIT_MS): the chat renders only after it, and a timed-out
  // wait queues a clear this fake shell would never answer. Before the ACK a same-origin page src is refused.
  assert.equal(await page.evaluate(() => visualPageSrc('/visuals/' + 'YQ.' + 'a'.repeat(43))), null,
    'no frame before the shell follows it');
  await page.evaluate(accepted => window.__pending[1](accepted), current);
  if (current) {
    await page.locator('figure.vcard[data-state="ready"] iframe.vcard-frame[src]').waitFor();
    assert.equal(await page.evaluate(() => NATIVE_VISUAL_FILES), FILES);
    assert.ok(reads.length > 0);
  } else {
    await page.locator('figure.vcard[data-state="open"]').waitFor();
    assert.equal(await page.locator('iframe.vcard-frame').count(), 0);
    assert.equal(await page.evaluate(() => window.__calls.length), 2, 'an older shell gets no clear after a refused grant');
    assert.deepEqual(reads, [], 'an older shell would open the redirect in the browser');
  }
  assert.deepEqual(errors, []);
  await page.close();
}

// A promise the page never settles would otherwise hold the job until the runner's own timeout.
const watchdog = setTimeout(() => { console.error('native window: timed out'); process.exit(1); }, 480000);
let browser;
(async () => {
  browser = await webkit.launch({headless:true});
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width:1240,height:820},
    userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [], posted = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.EventSource = undefined;
    // What the shell's bridge script provides; every message the page sends it is recorded.
    window.__posted = [];
    window.ticoNative = name => ({postMessage: body => window.__posted.push([name, body])});
    window.ticoNativeFeatures = ['openWindow'];
  });
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html',body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana',email:'ana@acme.example',role:'owner',cloud:true,config:{company_name:'Acme'}});
    if (p === '/api/employees') return json([{name:'ops',display_name:'Ops',status:'active',host:'keeper',can_chat:true}]);
    if (p === '/api/humans') return json({people:[{id:'ana',name:'Ana'}],groups:[]});
    if (p === '/api/status') return json({cloud:true,keeper_alive:true,health_issues:[]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/status') return json({bots:[{bot:'ops',state:'idle',open_tasks:0}]});
    if (p === '/api/v2/needs-you') return json({items:[]});
    if (p === '/api/v2/task-types') return json({types:[]});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks', {waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'Tasks',exact:true}).waitFor();
  await page.waitForFunction(() => S.status?.keeper_alive);
  assert.equal(await page.evaluate(() => document.body.classList.contains('native')), true);
  assert.equal(await page.locator('#rail').count(), 0, 'no right-rail button');
  assert.equal(await page.evaluate(() => typeof window.ticoWindowModeChanged), 'undefined');

  // 360 is the window's minimum width, 420 what the rail was; 760 is the widest compact layout.
  for (const [width, compact] of [[360, true], [420, true], [760, true], [761, false], [1240, false]]) {
    await page.setViewportSize({width, height:820});
    await page.waitForFunction(w => window.innerWidth === w, width);
    const seen = await page.evaluate(() => {
      const shown = el => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
      const main = document.querySelector('#main').getBoundingClientRect();
      return {overflow: document.scrollingElement.scrollWidth - window.innerWidth,
              mobileNav: shown(document.querySelector('#mobile-nav')),
              sidebar: shown(document.querySelector('#side')) && document.querySelector('#side').getBoundingClientRect().left >= 0,
              mainLeft: main.left, mainRight: main.right,
              heading: shown([...document.querySelectorAll('h1,h2')].find(h => h.textContent.trim() === 'Tasks'))};
    });
    assert.ok(seen.overflow <= 0, `${width}px: page scrolls sideways by ${seen.overflow}px`);
    assert.ok(seen.heading, `${width}px: the page heading is visible`);
    assert.ok(seen.mainLeft >= 0 && seen.mainRight <= width + 0.5, `${width}px: the page fits the window`);
    assert.equal(seen.mobileNav, compact, `${width}px: compact navigation ${compact ? 'shown' : 'hidden'}`);
    if (!compact) assert.ok(seen.sidebar, `${width}px: the sidebar is shown`);
  }
  posted.push(...await page.evaluate(() => window.__posted));
  assert.deepEqual(posted.filter(([name]) => name === 'windowMode'), [], 'the page no longer asks the shell for a window mode');
  assert.deepEqual(posted.filter(([name]) => name === 'visualOrigin'), [], 'older shells never receive the new command');
  assert.deepEqual(errors, []);
  await visualHandshake(browser);
  await hostedHandshake(browser, true);
  await hostedHandshake(browser, false);
  await browser.close();
  clearTimeout(watchdog);
  console.log('native window: no rail mode; usable from 360px to 1240px');
})().catch(async error => { console.error(error); await browser?.close().catch(() => {}); process.exit(1); });
