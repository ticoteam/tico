// Tico file hosting 1b (Dev 458): a company with no visual address of its own whose files are in Tico's bucket. A page
// and an SVG show inline: the card's address is on the app's own /visuals route, which sends the browser (302) to a
// signed link on the company's ticofiles.com address; there Tico's real edge functions (infra/files-edge, Dev 457)
// check the link and serve the cleaned copy with the content policy built from its metadata. The page runs sandboxed in
// an opaque origin; a height a company-written script posts is clamped and only sizes its own card, and nothing else a
// frame posts is acted on (Dev 457 R2). Fixtures only: no AWS.
// A browser does not hand a redirect's target to Playwright's routes, so the edge is a real server on this computer
// that the browser's resolver sends the files host to (Chromium only), all on plain http (the app at localhost, which
// the edge's ancestors slot accepts); the signed links and the edge's code are those of https.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const ROOT = path.resolve(__dirname, '..', '..');
const ORIGIN = 'http://localhost:8765';
const DOMAIN = 'ticofiles.com', HOST = 'acme-files.' + DOMAIN, INSTALL = '0f1e2d3c-4b5a-4968-8776-655443322110';
const SECRET = crypto.createHmac('sha256', 'm'.repeat(32)).update('ticofiles:' + INSTALL).digest('hex');
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><text x="10" y="60" font-size="40">Hi</text></svg>';
// A company holds write keys for its own folder, so it can store any page with its own script's hash in the script
// slot: the edge allows up to four. This one posts a huge height, a height with extras and other message types.
const HOSTILE_SCRIPT = 'parent.postMessage({ticoVisualHeight: 50, navigate: "https://evil.test/nav", open: "https://evil.test/open"}, "*");'
  + 'parent.postMessage({type: "navigate", url: "https://evil.test/type"}, "*"); parent.postMessage("https://evil.test/string", "*");'
  + 'parent.postMessage({ticoVisualHeight: 1e9}, "*");'
  + 'try { top.location = "https://evil.test/top" } catch (e) {} try { parent.document.body.dataset.owned = "yes" } catch (e) {}'
  + 'try { document.cookie = "x=1" } catch (e) { document.title = "opaque" }';
// The app's own treatment (backend/visuals.py): cleaned, the height reporter appended; the slots as the app writes them.
const app = JSON.parse(execFileSync('python3', ['-I', '-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
from backend import visuals
page, svg = json.loads(sys.stdin.read())
print(json.dumps({"page": visuals.with_reporter(visuals.clean(page.encode(), "page")[0]).decode(),
                  "svg": visuals.clean(svg.encode(), "svg")[0].decode(), "hash": visuals.REPORTER_HASH}))
`], {input: JSON.stringify(['<!doctype html><html><body style="margin:0"><div style="height:300px;background:#eee">Funnel page</div>'
  + '<script>steal()</script></body></html>', SVG]), encoding: 'utf8'}));
const ANCESTORS = "'self' " + ORIGIN;
const sha = text => crypto.createHash('sha256').update(text).digest('hex');
const hostileBody = '<!doctype html><html><body style="margin:0"><p style="height:90px">Company page</p><script>' + HOSTILE_SCRIPT + '</script></body></html>';
// What is in the company's folder of Tico's bucket, by path below it: the copies the app stored (and the company's).
const OBJECTS = {
  page: {type: 'text/html; charset=utf-8', body: app.page, meta: {'tico-visual-policy-version': '1', 'tico-visual-ancestors': ANCESTORS, 'tico-visual-script': `'${app.hash}'`}},
  svg: {type: 'image/svg+xml', body: app.svg, meta: {'tico-visual-policy-version': '1', 'tico-visual-ancestors': ANCESTORS}},
  hostile: {type: 'text/html', body: hostileBody, meta: {'tico-visual-policy-version': '1', 'tico-visual-ancestors': ANCESTORS + ' *',
    'tico-visual-script': "'sha256-" + crypto.createHash('sha256').update(HOSTILE_SCRIPT).digest('base64') + "' 'unsafe-inline'"}},
  plain: {type: 'text/html; charset=utf-8', body: app.page.replace('300px', '120px'), meta: {'tico-visual-policy-version': '1', 'tico-visual-ancestors': ANCESTORS, 'tico-visual-script': `'${app.hash}'`}},
};
for (const [name, o] of Object.entries(OBJECTS)) { const h = sha('copy ' + name); o.path = `/visuals/${h.slice(0, 2)}/${h}`; }
const BY_PATH = Object.fromEntries(Object.values(OBJECTS).map(o => ['/c/' + INSTALL + o.path, o]));
// The signed link the app's route sends to (backend/tico_storage.py _signed), on this computer's edge port.
let EDGE_PORT = 0;
const link = o => {
  const e = String(Math.floor(Date.now() / 1000) + 300);
  const s = crypto.createHmac('sha256', SECRET).update(HOST + '\n' + o.path + '\n' + e).digest('base64url');
  return `http://${HOST}:${EDGE_PORT}${o.path}?e=${e}&k=k1&s=${s}`;
};

// Tico's edge, exactly as rendered into its CloudFormation template; only the key store is replaced.
const template = JSON.parse(execFileSync('python3', [path.join(ROOT, 'infra', 'files-edge', 'template.py')]));
function edge(name) {
  let source = template.Resources[name].Properties.FunctionCode;
  if (typeof source !== 'string') source = source['Fn::Join'][1].map(x => typeof x === 'string' ? x : DOMAIN).join('');
  source = source.replace("import cf from 'cloudfront';", '').replace("import crypto from 'crypto';", '');
  const record = {v: 1, prefix: 'c/' + INSTALL + '/', signing: {key_id: 'k1', secret: SECRET}};
  const context = vm.createContext({crypto, Date, cf: {kvs: () => ({get: async key => {
    if (key !== 'acme-files') throw Error('unknown');
    return JSON.stringify(record);
  }})}});
  vm.runInContext(source, context);
  return async event => JSON.parse(JSON.stringify(await context.handler(JSON.parse(JSON.stringify(event)))));
}
const gate = edge('Gate'), visualsResponse = edge('VisualsResponse');
const headersOut = h => Object.fromEntries(Object.entries(h || {}).map(([k, v]) => [k, v.value]));

const A = (id, kind) => ({id, file_id: id, version: 1, ref: id + '@1', title: 'Title ' + id, kind, name: id + (kind === 'svg' ? '.svg' : '.html'), size: 1000,
  content_type: kind === 'svg' ? 'image/svg+xml' : 'text/html', url: `/api/v2/files/${id}?v=1`, removed: false, current_version: 1,
  src: '/visuals/tok-' + id});      // backend/visuals.py decorate: on the app's own route (tico_storage.visual_ready)

const edgeAnswers = [];
// Tico's edge on the company's address: the request gate, the bucket's object, then the visuals response function.
const edgeServer = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://' + HOST);
    // CloudFront's Host has no port; the link was signed for the host alone.
    const event = {request: {method: req.method, uri: url.pathname, headers: {host: {value: req.headers.host.split(':')[0]}},
      cookies: Object.fromEntries((req.headers.cookie || '').split(/;\s*/).filter(Boolean).map(c => [c.split('=')[0], {value: c.split('=')[1]}])),
      querystring: Object.fromEntries([...url.searchParams].map(([k, v]) => [k, {value: v}]))}};
    const gated = await gate(event);
    if (gated.statusCode) { edgeAnswers.push([url.pathname, gated.statusCode]); res.writeHead(gated.statusCode, headersOut(gated.headers)); return res.end(); }
    assert.deepEqual(gated.cookies, {}, 'no cookie reaches the bucket');
    const o = BY_PATH[gated.uri];
    const origin = o ? {statusCode: 200, headers: {'content-type': {value: o.type}, 'set-cookie': {value: 'tenant=1'},
      'content-security-policy': {value: 'sandbox allow-same-origin allow-scripts'},
      ...Object.fromEntries(Object.entries(o.meta).map(([k, v]) => ['x-amz-meta-' + k, {value: v}]))}, cookies: {tenant: {value: '1'}}}
      : {statusCode: 404, headers: {'content-type': {value: 'application/xml'}}};
    const answer = await visualsResponse({request: gated, response: origin});
    edgeAnswers.push([url.pathname, answer.statusCode]);
    assert.deepEqual(answer.cookies || {}, {}, 'the edge sets no cookie');
    res.writeHead(answer.statusCode, headersOut(answer.headers));
    res.end(answer.statusCode === 200 ? o.body : '');
  } catch (e) { console.error(e); res.writeHead(500); res.end(); }
});

(async () => {
  if (process.env.TICO_BROWSER === 'webkit') { console.log('visual hosted: Chromium only (resolver rules)'); return; }
  await new Promise(done => edgeServer.listen(0, '127.0.0.1', done));
  EDGE_PORT = edgeServer.address().port;
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true,
    args: ['--host-resolver-rules=MAP ' + HOST + ' 127.0.0.1',
           // The app page comes from the fixture routes, so Chrome can't tell it is on this computer too.
           '--disable-features=BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessChecks,LocalNetworkAccessChecks,PrivateNetworkAccessSendPreflights']});
  const now = new Date().toISOString();
  const MESSAGES = [
    {id: 'm1', sender_id: 'bot:designer', body: 'The funnel page and the logo.', created_at: now, refs: {attachments: [A('f-page', 'page'), A('f-svg', 'svg')]}},
    {id: 'm2', sender_id: 'bot:designer', body: 'A page the company wrote, and a plain one.', created_at: now, refs: {attachments: [A('f-hostile', 'page'), A('f-plain', 'page')]}},
  ];
  const run = async ({viewport, hasTouch = false, label}) => {
    const context = await browser.newContext({viewport, hasTouch, isMobile: hasTouch, serviceWorkers: 'block'});
    await context.addCookies([{name: 'tico_session', value: 'secret', domain: 'localhost', path: '/', httpOnly: true}]);
    const page = await context.newPage();
    const errors = [], outside = [], appRoute = [];
    edgeAnswers.length = 0;
    page.on('pageerror', e => { if (!/sandboxed|steal/i.test(e.message)) errors.push(e.message); });
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname === 'evil.test') { outside.push(url.href); return route.fulfill({contentType: 'text/plain', body: 'no'}); }
      if (p.startsWith('/visuals/tok-')) {
        // The app's route: the reader was checked (backend/visuals.py serve), then sent on; it never serves the page.
        const id = p.slice('/visuals/tok-f-'.length);
        appRoute.push(id);
        return route.fulfill({status: 302, headers: {Location: link(OBJECTS[id]), 'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'}, body: ''});
      }
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/api/v2/config') return json({company_name: 'Acme', visuals: 'media', visual_origin: ''});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'designer', display_name: 'Designer', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/events') return;                                // live events, held open
      if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-designer', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:designer']}] : []});
      if (p === '/api/v2/conversations/c-designer/snapshot') return json({messages: MESSAGES, execution: null});
      return json(unrouted(p));
    });

    await page.goto(ORIGIN + '/#/bot/designer/chat');
    const m1 = page.locator('[data-message="m1"]'), m2 = page.locator('[data-message="m2"]');
    const card = (m, id) => m.locator(`figure.vcard[data-file="${id}"]`);
    await card(m1, 'f-page').locator('.vcard-body.ready').waitFor();
    await card(m1, 'f-svg').locator('.vcard-body.ready').waitFor();
    assert.deepEqual(await m1.locator('figure.vcard').evaluateAll(els => els.map(e => [e.dataset.kind, e.dataset.state])),
      [['page', 'ready'], ['svg', 'ready']], `${label}: a page and an SVG in Tico file hosting show inline, not as Open cards`);
    assert.equal(await card(m1, 'f-page').locator('iframe').getAttribute('src'), ORIGIN + '/visuals/tok-f-page', `${label}: the frame asks the app's own route`);
    assert.equal(await card(m1, 'f-page').locator('iframe').getAttribute('sandbox'), 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    // The frame landed on the company's address through the edge, sandboxed in an opaque origin by the edge's policy.
    const frameOf = id => page.frames().find(f => f.url().startsWith(`http://${HOST}:${EDGE_PORT}${OBJECTS[id].path}?`));
    const pageFrame = frameOf('page');
    assert(pageFrame, `${label}: the page frame is on ${HOST}: ${page.frames().map(f => f.url())}`);
    assert.deepEqual(await pageFrame.evaluate(() => { let cookie; try { cookie = document.cookie; } catch (e) { cookie = 'throws'; } return [origin, cookie]; }),
      ['null', 'throws'], `${label}: opaque origin, no cookies`);
    const height = await card(m1, 'f-page').evaluate(c => Math.round(c.querySelector('iframe').getBoundingClientRect().height));
    assert(height >= 300 && height < 340, `${label}: the reporter sized the card to the page ${height}`);
    assert(await card(m1, 'f-svg').locator('img.vcard-img').evaluate(img => img.naturalWidth > 0), `${label}: the SVG rendered`);

    // A page the company wrote itself, with its own script in the slot and a wide ancestors list: its height is
    // clamped and sizes only its own card; nothing else it posts does anything.
    const plain = card(m2, 'f-plain'), hostile = card(m2, 'f-hostile');
    await plain.scrollIntoViewIfNeeded();
    await plain.locator('.vcard-body.ready').waitFor();
    const plainH = () => plain.evaluate(c => [c.querySelector('iframe').visualHeight, Math.round(c.querySelector('iframe').getBoundingClientRect().height)]);
    const before = await plainH();
    await hostile.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.querySelector('figure.vcard[data-file="f-hostile"] iframe')?.visualHeight);
    await page.waitForTimeout(300);
    const hostileFrame = frameOf('hostile');
    assert(hostileFrame, `${label}: the company's page loaded on ${HOST}`);
    assert.equal(await hostileFrame.evaluate(() => document.title), 'opaque', `${label}: its script ran, in an opaque origin`);
    assert.equal(await hostile.evaluate(c => c.querySelector('iframe').visualHeight), 20000, `${label}: its huge height is clamped to 20000`);
    assert.deepEqual(await plainH(), before, `${label}: a message from another card's frame does not resize this card`);
    assert.equal(page.url(), ORIGIN + '/#/bot/designer/chat', `${label}: nothing navigated the app`);
    assert.equal(context.pages().length, 1, `${label}: nothing opened`);
    assert.equal(await page.evaluate(() => document.body.dataset.owned || ''), '', `${label}: the frame cannot reach the app's page`);
    assert.deepEqual(outside, [], `${label}: nothing went to evil.test`);
    // Every frame and picture came through the app's route first, then a signed edge link that answered 200.
    assert.deepEqual([...new Set(appRoute)].sort(), ['hostile', 'page', 'plain', 'svg'], `${label}: each through the app's route`);
    assert(edgeAnswers.length >= 4 && edgeAnswers.every(([, s]) => s === 200), `${label}: edge answers ${JSON.stringify(edgeAnswers)}`);
    // A link with a tampered signature, or to a path the company did not get signed, is refused by the edge.
    const signed = new URL(link(OBJECTS.page));
    const status = u => new Promise((done, fail) => http.get({host: '127.0.0.1', port: EDGE_PORT, path: u.pathname + u.search,
      headers: {host: HOST}}, r => { r.resume(); done(r.statusCode); }).on('error', fail));
    const forged = new URL(signed.href.replace(/s=[^&]+/, 's=' + 'A'.repeat(43))), other = new URL(signed.href.replace(OBJECTS.page.path, OBJECTS.svg.path));
    assert.deepEqual([await status(signed), await status(forged), await status(other)], [200, 403, 403], `${label}: only the signed link opens`);
    assert.deepEqual(errors, [], String(errors));
    await context.close();
  };
  try {
    await run({viewport: {width: 1200, height: 800}, label: 'desktop'});
    await run({viewport: {width: 390, height: 844}, hasTouch: true, label: 'phone'});
    console.log('visual hosted ok');
  } finally { await browser.close(); edgeServer.close(); }
})().catch(e => { console.error(e); process.exit(1); });
