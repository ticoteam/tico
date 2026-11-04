// Overview replay: the toggle loads the last 24 hours with the viewer's rights, a bot-to-bot message draws a link
// between their desks, desks follow the replayed states, free text stays out of the frame and live comes back.
// The 3D renderer is replaced by a recording stand-in: WebGL timing is not what this checks. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SCENE = `export function createBuilding(data) {
  const rec = window.__scene = {created: (window.__scene?.created || 0) + 1, data, updates: [], links: []};
  document.body.dataset.sceneReady = 'true'; document.querySelector('#loading').hidden = true;
  return {update(d) { rec.data = d; rec.updates.push(d); }, link(a, b, kind) { rec.links.push([a, b, kind]); return true; },
          clearLinks() {}, dispose() {}};
}`;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
    const errors = [], replays = [], mode = {fail: false, cut: false};
    page.on('pageerror', e => errors.push(e.message));
    const bot = (name, over) => ({name, display_name: name.toUpperCase(), host: 'keeper', status: 'active', owner_id: 'ana', can_chat: true,
      machine: {runner_id: 'alpha', label: 'Garden Mac'}, my_access: {see: true, read: true}, ...over});
    const bots = [bot('writer'), bot('editor'), bot('vault', {my_access: {see: true, read: false}})];
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
      if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'running', focus: 'live work'}))});
      if (p === '/api/v2/replay') {
        const from = Date.parse(url.searchParams.get('from')), to = Date.parse(url.searchParams.get('to'));
        replays.push({from, to});
        if (mode.fail) return json({error: 'SECRET SERVER DETAIL'}, 500);
        const at = hours => new Date(from + hours * 3600e3).toISOString();
        return json({from: url.searchParams.get('from'), to: url.searchParams.get('to'), truncated: mode.cut, next: mode.cut ? at(2.5) : null,
          start: {tasks: [], bots: {writer: {state: 'idle', focus: 'SECRET FOCUS'}, editor: {state: 'idle', focus: ''}}},
          names: {'bot:writer': 'WRITER', 'bot:editor': 'EDITOR'},
          items: [
            {at: at(1), kind: 'run.started', who: 'bot:writer', bot: 'writer', task: {id: 't1', title: 'Launch post'}},
            {at: at(2), kind: 'chat.message', who: 'bot:writer', to: 'bot:editor', room: 'r1', text: 'Can you review the launch post?'},
            {at: at(3), kind: 'run.finished', who: 'bot:writer', bot: 'writer', exit: 'completed', text: 'SECRET SUMMARY'},
            {at: at(4), kind: 'bot.status', who: 'bot:editor', bot: 'editor', state: 'waiting_human', text: 'SECRET FOCUS'},
            {at: at(5), kind: 'task.owner', who: 'bot:writer', task: {id: 't1', title: 'Launch post'}, old: 'bot:writer', new: 'bot:editor'},
          ]});
      }
      return json(unrouted(p));
    });
    // Opened by its address, the campus is drawn once the team has loaded (switching the hash earlier would draw it
    // before the roster arrives and draw it again after).
    await page.goto('http://tico-ui.test/#/overview/campus');
    const frame = page.frameLocator('#overview-frame');
    await frame.locator('body[data-scene-ready]').waitFor();
    const scene = page.frames().find(f => f.url().includes('/overview/index.html'));
    const rec = () => scene.evaluate(() => window.__scene);
    const deskOf = (data, id) => data.groups.flatMap(g => g.members).find(m => m.id === id);
    // The first status answer may land after the campus opens; the desks follow it then, not at the next poll.
    await scene.waitForFunction(() => window.__scene.data.groups.flatMap(g => g.members).find(m => m.id === 'bot:editor')?.state === 'running',
      null, {polling: 100, timeout: 10000});
    assert.equal(deskOf((await rec()).data, 'bot:editor').state, 'running', 'live status before the replay');

    // The toggle asks for exactly the last 24 hours, ending now.
    const before = Date.now();
    await frame.locator('#replay-toggle').click();
    await frame.locator('#replay-bar').waitFor();
    assert.equal(replays.length, 1);
    assert.equal(replays[0].to - replays[0].from, 864e5, '24 hours by default');
    assert(Math.abs(replays[0].to - before) < 60e3, 'the window ends now');
    assert.equal(await frame.locator('#replay-toggle').getAttribute('aria-pressed'), 'true');

    // Playing forward, the message draws a link between the two desks and becomes a caption.
    await frame.locator('#replay-speeds [data-speed="3600"]').click();
    await scene.waitForFunction(() => window.__scene.links.some(([a, b, k]) => a === 'bot:writer' && b === 'bot:editor' && k === 'message'), null, {polling: 100});
    await frame.locator('#replay-captions li', {hasText: 'WRITER → EDITOR: Can you review the launch post?'}).waitFor();
    await scene.waitForFunction(() => window.__scene.links.some(([, , k]) => k === 'handoff'), null, {polling: 100});

    // Played to its end, the replay goes back to live.
    await frame.locator('#replay').waitFor({state: 'hidden'});
    assert.equal(deskOf((await rec()).data, 'bot:editor').state, 'running', 'the live status again');
    assert.equal(await frame.locator('#replay-toggle').getAttribute('aria-pressed'), 'false');

    // Scrubbed to the end, desks show the replayed states: the editor needs a person, the restricted bot stays restricted.
    // From here playback is held (no animation frames), so a replay can't play out and close before it is scrubbed.
    await scene.evaluate(() => { window.requestAnimationFrame = () => 0; });
    await frame.locator('#replay-toggle').click();
    await frame.locator('#replay-bar').waitFor();
    await frame.locator('#replay-range').evaluate(el => { el.value = '1000'; el.dispatchEvent(new Event('input')); });
    const ended = (await rec()).data;
    assert.equal(deskOf(ended, 'bot:editor').state, 'needs');
    assert.equal(deskOf(ended, 'bot:writer').state, 'idle');
    assert.equal(deskOf(ended, 'bot:vault').state, 'restricted');
    const shown = await scene.evaluate(() => document.body.innerText);
    assert(!JSON.stringify(ended).includes('SECRET') && !shown.includes('SECRET'), 'focus and run summaries stay out of the campus');

    // Scrubbing back replays the states without drawing links again.
    const linksBefore = (await rec()).links.length;
    await frame.locator('#replay-range').evaluate(el => { el.value = '100'; el.dispatchEvent(new Event('input')); });
    assert.equal(deskOf((await rec()).data, 'bot:writer').state, 'running', 'an hour and a bit in, the writer is working');
    assert.equal((await rec()).links.length, linksBefore);

    // Back to live restores the current status.
    await frame.locator('#replay-live').click();
    assert(await frame.locator('#replay').isHidden());
    assert.equal(deskOf((await rec()).data, 'bot:editor').state, 'running');
    assert.equal(await frame.locator('#replay-toggle').getAttribute('aria-pressed'), 'false');

    // A cut-short day ends at its cutoff instead of playing on with frozen desks.
    mode.cut = true;
    await frame.locator('#replay-toggle').click();
    await frame.locator('#replay-note', {hasText: 'This replay stops at'}).waitFor();
    await frame.locator('#replay-range').evaluate(el => { el.value = '1000'; el.dispatchEvent(new Event('input')); });
    assert.equal(deskOf((await rec()).data, 'bot:writer').state, 'running', 'the end is the cutoff, mid-run');
    await frame.locator('#replay-live').click();

    // A load error says so plainly and Back to live still leads out.
    mode.fail = true;
    await frame.locator('#replay-toggle').click();
    await frame.locator('#replay-note', {hasText: 'The replay could not be loaded.'}).waitFor();
    assert(!(await frame.locator('#replay').innerText()).includes('SECRET'));
    await frame.locator('#replay-live').click();
    assert(await frame.locator('#replay').isHidden());
    assert(await frame.locator('#replay-toggle').isVisible());
    assert.deepEqual(errors, []);
    console.log('Overview replay: 24-hour window, bot-to-bot link, replayed desks, private text and return to live passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
