// Meetings' Tico Recorder link: in the Mac app with the Recorder installed it is Record, which asks the app to open the
// Recorder (on this org, which the app knows; the page names none). In a browser, an app too old to offer it, or an
// app without the Recorder installed, it stays the download.
const {webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const DMG = 'https://github.com/ticoteam/tico/releases/latest/download/Tico-Recorder.dmg';

async function open(browser, {features, installed}) {
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width:1240,height:820},
    userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(({features, installed}) => {
    window.EventSource = undefined;
    window.__posted = [];
    if (!features) return;
    window.ticoNative = name => ({postMessage: body => {
      window.__posted.push([name, body]);
      return Promise.resolve(name === 'hasRecorder' ? installed : null);
    }});
    window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', ...features];
  }, {features, installed});
  await page.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({contentType:'application/json', body:JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType:'text/html', body:html});
    if (p.startsWith('/tico/ui/')) {
      const file = uiFile(p.slice('/tico/ui/'.length));
      if (fs.existsSync(file)) return route.fulfill({path:file});
    }
    if (p === '/api/me') return json({id:'ana', email:'ana@acme.example', role:'owner', cloud:true, config:{company_name:'Acme Lights'}});
    if (p === '/api/status') return json({cloud:true, keeper_alive:true, health_issues:[]});
    if (p === '/api/employees' || p === '/api/issues') return json([]);
    if (p === '/api/v2/live-meetings') return json({meetings:[], count:0});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/meetings', {waitUntil:'domcontentloaded'});
  await page.locator('#meet-recorder').waitFor();
  return {page, errors};
}
const posted = (page, name) => page.evaluate(n => window.__posted.filter(([k]) => k === n).map(([, body]) => body), name);

(async () => {
  const browser = await webkit.launch({headless:true});

  // The Mac app with the Recorder installed: Record, and a click asks the app to open it.
  {
    const {page, errors} = await open(browser, {features:['openRecorder'], installed:true});
    const record = page.locator('button#meet-recorder', {hasText:'Record'});
    await record.waitFor();
    assert.equal(await record.getAttribute('href'), null);
    await record.click();
    assert.deepEqual(await posted(page, 'openRecorder'), [null], 'the page names no org or address');
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Not installed, an app too old to offer it, and a browser: the download link, and nothing opens.
  for (const [features, installed] of [[['openRecorder'], false], [[], true], [null, true]]) {
    const {page, errors} = await open(browser, {features, installed});
    if (features?.length) await page.waitForFunction(() => window.__posted.some(([n]) => n === 'hasRecorder'));
    const link = page.locator('a#meet-recorder');
    assert.equal(await link.innerText(), 'Get Tico Recorder');
    assert.equal(await link.getAttribute('href'), DMG);
    assert.deepEqual(await posted(page, 'openRecorder'), []);
    assert.equal((await posted(page, 'hasRecorder')).length, features?.length ? 1 : 0, 'only an app that offers it is asked');
    assert.deepEqual(errors, []);
    await page.close();
  }

  await browser.close();
  console.log('meetings recorder: Record in the Mac app with the Recorder, the download everywhere else');
})().catch(error => { console.error(error); process.exit(1); });
