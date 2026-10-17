// Overview live status: with the live stream connected, a bot's status event moves its desk on the campus at once,
// without reading the status again and without waiting for the 2-minute refresh.
// The 3D renderer is replaced by a recording stand-in: WebGL timing is not what this checks. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const SCENE = `export function createBuilding(data) {
  const rec = window.__scene = {data, updates: 0};
  document.body.dataset.sceneReady = 'true'; document.querySelector('#loading').hidden = true;
  return {update(d) { rec.data = d; rec.updates++; }, link() { return true; }, clearLinks() {}, dispose() {}};
}`;
const block = (seq, event, data) => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify({seq, ...data})}\n\n`;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
    const errors = [], streams = [], reads = {status: 0};
    page.on('pageerror', e => errors.push(e.message));
    const bot = name => ({name, display_name: name.toUpperCase(), host: 'keeper', status: 'active', operator: 'ana', can_chat: true,
      machine: {runner_id: 'alpha', label: 'Garden Mac'}, my_access: {see: true, read: true}});
    const bots = [bot('writer'), bot('editor')];
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/tico/ui/overview/building.js') return route.fulfill({contentType: 'application/javascript', body: SCENE});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
          : name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, config: {company_name: 'Acme Studio'}});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/computers') return json({computers: [{id: 'alpha', label: 'Garden Mac'}]});
      if (p === '/api/v2/status') { reads.status++; return json({bots: bots.map(b => ({bot: b.name, state: 'running', focus: 'live work'}))}); }
      if (p === '/api/v2/events') { streams.push(route); return; }        // held open until the test sends
      return json({});
    });
    // The newest stream the page holds, answered with `blocks` (it then ends, and the page reconnects).
    const send = async (...blocks) => {
      const deadline = Date.now() + t(8000);
      while (!streams.length) {
        if (Date.now() > deadline) throw new Error('no live stream');
        await new Promise(r => setTimeout(r, 50));
      }
      const stream = streams.pop();
      for (const old of streams.splice(0)) await old.abort().catch(() => {});
      await stream.fulfill({contentType: 'text/event-stream', body: blocks.join('')});
    };

    await page.goto('http://tico-ui.test/');
    await page.waitForFunction(() => location.hash === '#/updates');
    await page.evaluate(() => location.hash = '#/overview');
    const frame = page.frameLocator('#overview-frame');
    await frame.locator('body[data-scene-ready]').waitFor();
    const scene = page.frames().find(f => f.url().includes('/overview/index.html'));
    const desk = id => scene.evaluate(id => window.__scene.data.groups.flatMap(g => g.members).find(m => m.id === id), id);
    await scene.waitForFunction(() => window.__scene.data.groups.flatMap(g => g.members).find(m => m.id === 'bot:editor')?.state === 'running',
      null, {timeout: t(8000)});
    const statusReads = reads.status;

    // The editor stops to ask a person: the event alone moves its desk, and the writer's stays as it was.
    await send(block(10, 'ready', {}), block(11, 'bots', {bot: 'editor', status: {bot: 'editor', state: 'waiting_human', focus: 'Approve the post'}}));
    await scene.waitForFunction(() => window.__scene.data.groups.flatMap(g => g.members).find(m => m.id === 'bot:editor')?.state === 'waiting_human',
      null, {timeout: t(3000), polling: 50})
      .catch(async e => { throw new Error('the desk did not follow the live status event: ' + JSON.stringify(await desk('bot:editor')) + ' ' + e.message); });
    assert.equal((await desk('bot:writer')).state, 'running');
    assert.equal(reads.status, statusReads, 'the desk came from the event, not from reading the status again');

    // A bot whose status line is gone shows no current status, as the refresh would show it.
    await send(block(12, 'bots', {bot: 'writer', status: null}));
    await scene.waitForFunction(() => window.__scene.data.groups.flatMap(g => g.members).find(m => m.id === 'bot:writer')?.state === 'unknown',
      null, {timeout: t(3000), polling: 50});
    assert.equal((await desk('bot:writer')).detail, 'No current status available.');
    assert.equal(reads.status, statusReads);
    assert.deepEqual(errors, []);
    console.log('overview live: a bot status event moves its desk without a status read or the 2-minute refresh: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
