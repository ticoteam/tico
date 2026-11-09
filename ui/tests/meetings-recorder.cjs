// Meetings' Tico Recorder link: in the Mac app with the Recorder installed it is Record, which asks the app to open the
// Recorder (on this org, which the app knows; the page names none). In a browser, an app too old to offer it, or an
// app without the Recorder installed, it stays the download, until Tico has seen one of the person's copies lately;
// a copy older than the newest release shows one quiet update link. Neither shows on a phone.
const {webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const DMG = 'https://github.com/ticoteam/tico/releases/latest/download/Tico-Recorder.dmg';

async function open(browser, {features, installed, recorder, width = 1240}) {
  const page = await browser.newPage({serviceWorkers:'block', viewport:{width, height:820},
    userAgent:'Mozilla/5.0 AppleWebKit/605.1.15 TicoHub'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(({features, installed}) => {
    window.EventSource = undefined;
    window.__posted = [];
    // Set once the page has asked Tico about the person's Recorder and drawn the answer.
    const fetched = window.fetch;
    window.fetch = function (...args) {
      const r = fetched.apply(window, args);
      const done = () => setTimeout(() => { window.__recorderAsked = true; }, 50);
      if (String(args[0]?.url || args[0]).includes('/v2/meetings/recorder')) r.then(done, done);
      return r;
    };
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
    if (p === '/api/v2/meetings/recorder') return recorder ? json({download_url:DMG, latest:'0.3.0', ...recorder}) : route.fulfill({status:404, contentType:'application/json', body:'{}'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/meetings', {waitUntil:'domcontentloaded'});
  await page.locator('#meet-recorder').waitFor({state:'attached'});
  await page.waitForFunction(() => window.__recorderAsked);
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

  // Tico has not seen a copy lately: just the download link (no hint), no update line.
  const shots = process.env.TICO_UI_SHOTS;
  const shoot = async (page, name) => {
    if (!shots) return;
    for (const theme of ['light', 'dark']) {
      await page.evaluate(v => window.ticoTheme?.set(v), theme);
      await page.locator('.meet-review-bar').screenshot({path: require('node:path').join(shots, `${name}-${theme}.png`), animations:'disabled'});
    }
  };
  for (const recorder of [{version:'', last_seen_at:null, recent:false, update_available:false},
                          {version:'0.2.0', last_seen_at:'2026-09-01T00:00:00Z', recent:false, update_available:false}]) {
    const {page, errors} = await open(browser, {features:null, recorder});
    const link = page.locator('a#meet-recorder');
    await link.waitFor();
    assert.equal(await link.getAttribute('href'), DMG);
    assert.equal(await page.locator('#meet-recorder-update').isVisible(), false);
    assert.equal(await page.locator('.meet-review-bar').evaluate(bar => /Drag to Applications/.test(bar.textContent)), false, 'no install hint');
    if (!recorder.version) await shoot(page, 'meetings-recorder-download');
    assert.deepEqual(errors, []);
    await page.close();
  }

  // A copy seen lately: no download. Behind the newest release: one quiet update link to the download.
  for (const behind of [false, true]) {
    const {page, errors} = await open(browser, {features:null, recorder:{version:'0.2.0', last_seen_at:new Date().toISOString(), recent:true, update_available:behind}});
    assert.equal(await page.locator('#meet-recorder').isVisible(), false);
    const update = page.locator('#meet-recorder-update');
    assert.equal(await update.isVisible(), behind);
    if (behind) {
      assert.equal(await update.innerText(), 'Recorder update available');
      assert.equal(await update.getAttribute('href'), DMG);
      await shoot(page, 'meetings-recorder-update');
    }
    assert.deepEqual(errors, []);
    await page.close();
  }

  // The Mac app with the Recorder installed still says Record, with the update line beside it when behind.
  {
    const {page, errors} = await open(browser, {features:['openRecorder'], installed:true, recorder:{version:'0.2.0', last_seen_at:new Date().toISOString(), recent:true, update_available:true}});
    await page.locator('button#meet-recorder', {hasText:'Record'}).waitFor();
    assert.equal(await page.locator('#meet-recorder-update').isVisible(), true);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // On a phone neither shows (it is a Mac app), and the bar does not scroll sideways.
  for (const recorder of [{version:'', last_seen_at:null, recent:false, update_available:false},
                          {version:'0.2.0', last_seen_at:new Date().toISOString(), recent:true, update_available:true}]) {
    const {page, errors} = await open(browser, {features:null, recorder, width:390});
    for (const id of ['#meet-recorder', '#meet-recorder-update']) assert.equal(await page.locator(id).isVisible(), false, id);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll at 390px');
    if (recorder.update_available) await shoot(page, 'meetings-recorder-phone');
    assert.deepEqual(errors, []);
    await page.close();
  }

  await browser.close();
  console.log('meetings recorder: Record in the Mac app with the Recorder, the download until a copy is seen, the update line when behind');
})().catch(error => { console.error(error); process.exit(1); });
