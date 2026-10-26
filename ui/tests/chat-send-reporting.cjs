// Exercise the real pinned Sentry browser SDK and final wire scrubber with synthetic config only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {scrubSentry} = require('../observability.js');

const scrubbed = scrubSentry({message:'PRIVATE', request:{url:'PRIVATE'}, breadcrumbs:[{message:'PRIVATE'}],
  exception:{values:[{value:'PRIVATE',stacktrace:{frames:[
    {filename:'https://private.test/tico/ui/app/pill.js?token=PRIVATE',lineno:42,function:'PRIVATE'},
    {filename:'https://private.test/tico/ui/app.bundle.js',lineno:7},
    {filename:'https://private.test/customer-PRIVATE.js',lineno:1}]}}]}}, 'tico:' + 'a'.repeat(64),'test','test-503');
assert.deepEqual(scrubbed.exception.values[0].stacktrace.frames,[{filename:'app/pill.js',lineno:42},{filename:'app.bundle.js',lineno:7}]);
assert.doesNotMatch(JSON.stringify(scrubbed),/PRIVATE|private\.test|request|breadcrumbs/);

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const events = [];
    let identity = 'a', enabled = false, vendorLoads = 0;
    const cors = {'access-control-allow-origin':'*', 'access-control-allow-methods':'POST, OPTIONS', 'access-control-allow-headers':'*'};
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'o123.ingest.us.sentry.io') {
        if (route.request().method() === 'OPTIONS') return route.fulfill({headers:cors,body:''});
        const envelope = route.request().postData().split('\n');
        for (let i = 1; i + 1 < envelope.length; i += 2)
          if (JSON.parse(envelope[i]).type === 'event') events.push(JSON.parse(envelope[i + 1]));
        return route.fulfill({headers:cors,body:'{}'});
      }
      assert.equal(url.hostname, 'fixture.test', 'no unexpected reporting destination');
      if (url.pathname === '/api/v2/observability' && !enabled) return route.fulfill({json:{}});
      if (url.pathname === '/assets/observability-vendors.js') vendorLoads++;
      if (url.pathname === '/api/v2/observability') return route.fulfill({json:{app:'tico', environment:'test', distinct_id:'tico:' + identity.repeat(64),
        sentry_dsn:'https://' + 'b'.repeat(32) + '@o123.ingest.us.sentry.io/123', release:'test-503'}});
      const file = {'/observability.js':'observability.js', '/assets/observability-vendors.js':'assets/observability-vendors.js'}[url.pathname];
      return route.fulfill({contentType:file ? 'text/javascript' : 'text/html',body:file ? fs.readFileSync(path.join(__dirname,'..',file),'utf8') : '<script src="/observability.js"></script>'});
    });
    await page.goto('http://fixture.test/');
    await page.evaluate(async () => { await TicoObservability.start(); TicoObservability.chatSend()({outcome:'refused',status:500}); });
    assert.equal(vendorLoads,0,'unconfigured installs never load a browser SDK');
    assert.equal(events.length,0);
    enabled = true;
    await page.evaluate(async () => {
      await TicoObservability.start();
      window.report = TicoObservability.chatSend();
      report({outcome:'slow',duration_ms:5001,retries:1,status:503,text:'PRIVATE MESSAGE',url:location.href});
      report({outcome:'refused',duration_ms:6300,retries:2,status:422,error:'PRIVATE ERROR',email:'private@fixture.test'});
    });
    const waitFor = async n => {
      const deadline = Date.now() + 10000;
      while (events.length < n && Date.now() < deadline) await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      assert.equal(events.length,n,'actual SDK event receipt');
    };
    await waitFor(2);
    assert.deepEqual(events.map(e => e.extra), [
      {outcome:'slow',duration_ms:5001,retries:1,status:503},
      {outcome:'refused',duration_ms:6300,retries:2,status:422}]);
    assert.deepEqual(events.map(e => e.tags.source), ['chat_send','chat_send']);
    assert.ok(events.every(e => e.user.id === 'tico:' + 'a'.repeat(64) && e.release === 'test-503'));
    assert.doesNotMatch(JSON.stringify(events), /PRIVATE|fixture\.test|private@|request|breadcrumbs/);
    await page.evaluate(() => { report({outcome:'unconfirmed',duration_ms:10,retries:0,status:401}); TicoObservability.response(401); report({outcome:'refused',status:401}); });
    await waitFor(3);
    assert.equal(events[2].extra.status,401,'auth failure reaches Sentry before reset');
    identity = 'c';
    await page.evaluate(async () => {
      await TicoObservability.start();
      report({outcome:'refused',status:403}); // old operation must not acquire the new identity
      TicoObservability.chatSend()({outcome:'network',duration_ms:100,retries:0,status:0});
    });
    await waitFor(4);
    assert.equal(events[3].user.id, 'tico:' + 'c'.repeat(64));
    assert.equal(events[3].extra.outcome,'network');
    await page.evaluate(() => TicoObservability.reset());
    console.log('real Sentry send diagnostics, wire privacy and account-generation fence: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
