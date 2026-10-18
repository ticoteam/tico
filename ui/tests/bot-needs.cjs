// A bot's task rail leads with Needs you: what this bot needs from the person (its own tasks for them, a question it
// asked on another bot's task, even a finished one, a task of its own waiting on them, a question outside any task),
// then Active, then Assigned to others. The org tree's number is the Needs you rows, and stays so when the list moves;
// a task in Needs you is not listed again under Active or Assigned to others. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');

const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const bots = ['lead', 'qa', 'senior'].map(name => ({name, display_name: {lead: 'Head of Engineering', qa: 'QA Engineer',
  senior: 'Senior Engineer'}[name], org_parent: '', host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}));
const task = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:lead', requester: 'human:ana', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created: ago(600), updated: ago(60), ...over});
const T = {
  own: task('t-own', 'Ship the release notes'),
  forAna: task('t-ana', 'Pick the release day', {owner: 'human:ana', requester: 'bot:lead', status: 'open'}),
  waits: task('t-waits', 'Turn on the screen default', {status: 'waiting', waiting_on: 'human:ana', note: 'Waiting on Ana for the bucket keys.'}),
  asked: task('t-asked', 'Support access fixes', {owner: 'bot:senior', requester: 'bot:lead', status: 'done',
    ask: {id: 'q-1', body: 'Should full access refuse credential changes?', from_actor: 'bot:lead', to_actor: 'human:ana', created: ago(30)}}),
  qa: task('t-qa', 'Run the release check', {owner: 'bot:qa', requester: 'bot:lead', status: 'doing'}),
};
const LOOSE = {kind: 'ask', id: 'm-loose', message_id: 'm-loose', title: 'Is Friday fine for the summary?', first_line: 'Is Friday fine for the summary?',
  body: 'Is Friday fine for the summary?', requester: 'bot:lead', origin_actor: 'bot:lead', created: ago(20),
  ask: {id: 'm-loose', from_actor: 'bot:lead', to_actor: 'human:ana', body: 'Is Friday fine for the summary?', created: ago(20)}};
const NEEDS = [{...T.asked, kind: 'question'}, {...T.forAna, kind: 'task'}, {...T.waits, kind: 'waiting'}, LOOSE,
  {...task('t-qa-ana', 'Approve the test plan', {owner: 'human:ana', requester: 'bot:qa', status: 'open'}), kind: 'task'}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let needs = NEEDS;
    await page.route('**/*', route => {
      const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      // A list, as the server sends it: the catch-all {} broke the tree drawn before Needs you whenever it landed ahead
      // of /api/v2/status, and the page never opened (v0.3.54 release check).
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
      if (p === '/api/v2/needs-you') return json({items: needs, went_away: null});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') {
        const all = Object.values(T);
        if (q.get('owner') === 'lead') return json({tasks: all.filter(t => t.owner === 'bot:lead')});
        if (q.get('requester') === 'lead') return json({tasks: all.filter(t => t.requester === 'bot:lead' && t.status !== 'done')});
        return json({tasks: []});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/bot/lead/tasks');
    const rail = page.locator('#pane-tasks');
    await rail.locator('#t-needs .trow').first().waitFor();
    await rail.locator('#t-open .trow').first().waitFor();

    // Needs you, Active, then Assigned to others, before Updates, Files and Routines.
    const order = await rail.evaluate(el => [...el.children].map(c => c.id || c.className.split(' ').find(k => k.startsWith('bot-'))));
    assert.deepEqual(order.slice(0, 3), ['bot-needs', 'bot-active', 'bot-assigned'], JSON.stringify(order));
    assert.equal((await rail.locator('#bot-needs-h').innerText()).replace(/\s+/g, ' ').trim().toLowerCase(), 'needs you 4');

    // Exactly what the org tree counts for this bot: its task for Ana, its question on Senior's finished task, its own
    // task waiting on her and its question outside any task; QA's task for her is QA's.
    const rows = await rail.locator('#t-needs .trow .ttl, #t-needs [data-ask] .req-text').allInnerTexts();
    assert.deepEqual(rows.map(r => r.trim()).sort(), ['Head of Engineering: Is Friday fine for the summary?', 'Pick the release day',
      'Support access fixes', 'Turn on the screen default'].sort(), JSON.stringify(rows));
    const badge = page.locator('#tree a[href="#/bot/lead"] .cnt.needs');
    assert.equal((await badge.innerText()).trim(), '4', 'the tree number is the Needs you rows');
    assert.equal(await page.evaluate(() => needsMeCount('lead')), 4);

    // Listed once: Active keeps the bot's other work, Assigned to others what it asked of another bot.
    assert.deepEqual((await rail.locator('#t-open .trow .ttl').allInnerTexts()).map(s => s.trim()), ['Ship the release notes']);
    assert.equal((await rail.locator('#cnt-active').innerText()).trim(), '1');
    assert.deepEqual((await rail.locator('#t-assigned .trow .ttl').allTextContents()).map(s => s.trim()), ['Run the release check']);
    assert.equal((await rail.locator('#cnt-assigned').innerText()).trim(), '1');

    // A half-typed answer to the question outside any task survives a redraw of the list.
    const loose = rail.locator('#t-needs details[data-ask="m-loose"]');
    await loose.locator('summary').click();
    await loose.locator('textarea').fill('Friday works');
    await page.evaluate(() => botNeedsChanged('lead'));
    assert.equal(await rail.locator('#t-needs details[data-ask="m-loose"] textarea').inputValue(), 'Friday works');

    // The question answered elsewhere leaves Needs you: the section and the tree's number move together.
    needs = needs.filter(it => it.id !== 't-asked' && it.id !== 'm-loose');
    await page.evaluate(async () => { await v2Refresh(); renderTree(); });
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '2');
    assert.equal((await badge.innerText()).trim(), '2');
    assert.equal(await rail.locator('#t-needs .trow, #t-needs [data-ask]').count(), 2);

    // Nothing needs Ana: the section goes, and the tasks that were there are back where they belong.
    needs = [];
    await page.evaluate(async () => { await v2Refresh(); renderTree(); });
    await page.waitForFunction(() => document.querySelector('#bot-needs')?.hidden === true);
    assert.equal(await page.locator('#tree a[href="#/bot/lead"] .cnt.needs').count(), 0);
    assert.deepEqual((await rail.locator('#t-open .trow .ttl').allInnerTexts()).map(s => s.trim()).sort(), ['Ship the release notes', 'Turn on the screen default']);
    assert.deepEqual((await rail.locator('#t-assigned .trow .ttl').allTextContents()).map(s => s.trim()).sort(), ['Pick the release day', 'Run the release check']);
    assert.deepEqual(errors, []);
    console.log('bot-needs: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
