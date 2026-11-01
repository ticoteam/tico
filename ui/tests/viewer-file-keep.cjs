// Files kept for the visit (fileKeep, ui/app/viewer.js) stay within a 64 MB budget per cache, but the file just
// loaded is never the one let go: a picture over the budget on its own opened blank, in the viewer and as a chat
// picture. Older files nothing shows still go. Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const zlib = require('node:zlib');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
// A 1x1 PNG carrying a private ancillary chunk (decoders skip it) that takes it past the 64 MB budget.
const chunk = (type, data) => {
  const head = Buffer.alloc(8); head.writeUInt32BE(data.length); head.write(type, 4, 'latin1');
  const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(Buffer.concat([head.subarray(4), data])));
  return Buffer.concat([head, data, crc]);
};
const tiny = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000'+'1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082','hex');
const BIG = Buffer.concat([tiny.subarray(0, tiny.length - 12), chunk('tiCo', Buffer.alloc(65e6)), tiny.subarray(tiny.length - 12)]);
const files = {
  'file-big-0001': ['poster.png', BIG],
  'file-pdf-0001': ['lease.pdf', Buffer.from('%PDF-1.4 fixture')],
  'file-png-0002': ['chart.png', tiny],
};
(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless:true}) : await chromium.launch({channel:process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1200,height:800},serviceWorkers:'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.context().route('**/*', route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = body => route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType:'text/html',body:html});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(uiFile('vendor/marked.min.js'),'utf8')});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) {
        const file = uiFile(ui[1]);
        if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript',body:fs.readFileSync(file,'utf8')});
      }
      if (p === '/api/me') return json({id:'ana',name:'Ana',role:'owner',cloud:true});
      if (p === '/api/employees') return json([{name:'legal',display_name:'Legal',host:'keeper',status:'active',can_chat:true,schedules:[]}]);
      if (p.endsWith('/watch')) return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
      const file = p.match(/^\/api\/v2\/files\/([^/]+)(\/meta)?$/), known = file && files[file[1]];
      if (known && file[2]) return json({id:file[1],name:known[0],size:known[1].length,content_type:'application/octet-stream'});
      if (known) return route.fulfill({contentType:'application/octet-stream',headers:{'content-disposition':`attachment; filename*=UTF-8''${known[0]}`},body:known[1]});
      if (p === '/api/v2/conversations') return json({conversations:url.searchParams.get('chat_with') ? [{id:'c-legal',kind:'chat',scope:'personal',participants:['human:ana','bot:legal']}] : []});
      if (p === '/api/v2/conversations/c-legal/snapshot') return json({messages:[{id:'m-legal',sender_id:'bot:legal',
        body:'The [poster](https://tico-ui.test/api/v2/files/file-big-0001) and the [lease](https://tico-ui.test/api/v2/files/file-pdf-0001).',
        created_at:new Date().toISOString()}],execution:null});
      return json(unrouted(p));
    });
    // In the page: whether a blob: URL still reads, and the width a picture at that URL decodes to (0 when it can't).
    const reads = url => page.evaluate(u => fetch(u).then(r => r.ok, () => false), url);
    const width = url => page.evaluate(u => { const i = new Image(); i.src = u; return i.decode().then(() => i.naturalWidth, () => 0); }, url);
    const viewerImg = page.locator('#doc-viewer[open] .viewer-img');
    await page.goto('https://tico-ui.test/#/bot/legal/chat');
    // The poster on its own is over the budget (too big for a thumbnail), and opens in the viewer.
    await page.getByRole('link',{name:'poster'}).click();
    await viewerImg.waitFor();
    assert.equal(await page.locator('#doc-viewer h2').innerText(), 'poster.png');
    assert.equal(await viewerImg.evaluate(i => i.decode().then(() => i.naturalWidth, () => 0)), 1, 'the viewer shows a picture over the budget');
    const poster = await viewerImg.getAttribute('src');
    assert.equal(await reads(poster), true, 'its URL reads');
    // The next file, the lease, lets the poster go once nothing shows it: the budget still holds for older files.
    await page.keyboard.press('ArrowRight');
    const pdf = page.locator('#doc-viewer[open] .viewer-pdf iframe');
    await pdf.waitFor();
    const lease = await pdf.getAttribute('src');
    assert.equal(await reads(lease), true, 'the lease reads');
    assert.deepEqual(await page.evaluate(() => [...VIEW_FILES.keys()]), ['file-pdf-0001'], 'the unshown poster was let go');
    assert.equal(await reads(poster), false, 'and its URL revoked');
    // Back to the poster: while it is on screen it keeps the total over the budget, and a file loaded meanwhile
    // still shows; the lease, shown nowhere now, goes instead.
    await page.keyboard.press('ArrowLeft');
    await viewerImg.waitFor();
    assert.equal(await viewerImg.evaluate(i => i.decode().then(() => i.naturalWidth, () => 0)), 1, 'the poster again');
    const shown = await viewerImg.getAttribute('src');
    const chart = await page.evaluate(() => viewFile('file-png-0002', 'chart.png').then(f => f.url));
    assert.equal(await width(chart), 1, 'the file just loaded shows');
    assert.equal(await reads(shown), true, 'the poster on screen stays');
    assert.deepEqual(await page.evaluate(() => [...VIEW_FILES.keys()]), ['file-big-0001', 'file-png-0002']);
    assert.equal(await reads(lease), false, 'the unshown lease went');
    await page.keyboard.press('Escape');
    // A chat picture over the budget goes through the same keep, into its own cache.
    const picture = await page.evaluate(() => chatImageURL({id:'file-big-0001', name:'poster.png', content_type:'image/png'}));
    assert.equal(await width(picture), 1, 'a chat picture over the budget shows');
    assert.deepEqual(errors, []);
    console.log('viewer-file-keep: ok (a file over the keep budget shows in the viewer and as a chat picture; older unshown files still go)');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
