// Cloud agents on Recruit (ui/app/cloud-agents.js, backend/cloud_agents.py): the agents grid is four across and two
// across on a phone, a request files a seat and opens its Connecting page, the team builder's Request link opens the same
// dialog for the team it names, the seat sits on the chart as a dashed node with its own pill, Add opens that agent's
// connect steps, and the Connecting pulse moves opacity only and stops under reduced motion. The agents are the
// backend's own catalog. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

// The agents the backend lists (listing() in backend/cloud_agents.py), so the grid shows the lines people see.
const AGENTS = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3',
  ['-c', 'import json; from backend.cloud_agents import listing; print(json.dumps(listing()))'],
  {cwd: path.join(__dirname, '../..')}).toString());
// A bot that is not a built-in helper, so it is the team's head that a seat reports to.
const bots = [{name: 'rust-engineer', display_name: 'Rust Engineer', org_parent: 'p:ana', reports_to: 'ana', team: 'eng', host: 'keeper',
  status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}];
const requested = {id: 's-devin', kind: 'seat', stage: 'recruiting', title: 'Devin', description: '', team: 'eng',
  reports_to: 'human:ana', created: '2026-10-31T00:00:00Z', org_parent: 'p:ana', agent: 'devin', agent_name: 'Devin',
  request_state: 'requested'};
const detailOf = seat => {
  const known = AGENTS.find(a => a.id === seat.agent) || {id: seat.agent, name: seat.agent_name, maker: '', site: '', line: ''};
  return {seat, agent: known, steps: ['requested', 'building', 'ready'], tasks: [{id: 't-1', title: 'Fix the flaky retry test', status: 'open'}]};
};

async function open(browser, viewport, {reducedMotion = 'no-preference'} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], calls = [], state = {seats: [requested], failRequest: false};
  page.on('pageerror', error => errors.push(error.message));
  await page.emulateMedia({reducedMotion});
  await page.addInitScript(() => localStorage.setItem('hub.collapsed.v3.ana', '[]'));
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana', org_parent: ''}], seats: state.seats, org_groups: [{id: 'eng', name: 'Engineering'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
    if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/agent-skill') return json({mcp_url: 'https://mcp.tico.test/api/v2/mcp', access_bypass: false});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/cloud-agents' && method === 'GET') return json({agents: AGENTS});
    const detail = p.match(/^\/api\/v2\/seats\/([^/]+)\/request$/);
    if (detail && method === 'GET') {
      const seat = state.seats.find(s => s.id === detail[1]);
      return seat ? json(detailOf(seat)) : json({error: {code: 'not_found', detail: 'Seat not found', retryable: false}}, 404);
    }
    const asked = p.match(/^\/api\/v2\/cloud-agents\/([^/]+)\/request$/) || p.match(/^\/api\/v2\/seats\/([^/]+)\/request-agent$/);
    if (asked && method === 'POST') {
      const body = request.postDataJSON();
      calls.push({method, path: p, body});
      if (state.failRequest) return json({error: {code: 'requested', detail: 'A cloud agent is already requested for this seat', retryable: false}}, 409);
      const seat = {...requested, id: 's-new', title: asked[1] === 'other' ? body.name : requested.title,
        agent: asked[1], agent_name: body.name || 'Devin'};
      state.seats = [...state.seats.filter(s => s.id !== seat.id), seat];
      return json(seat);
    }
    const close = p.match(/^\/api\/v2\/seats\/([^/]+)\/close$/);
    if (close && method === 'POST') {
      calls.push({method, path: p, body: request.postDataJSON()});
      state.seats = state.seats.filter(s => s.id !== close[1]);
      return json({id: close[1], closed: true});
    }
    return json(unrouted(p));
  });
  return {page, errors, calls, state};
}

// The Connecting pulse keyframes change opacity and nothing else.
const pulseIsOpacityOnly = page => page.evaluate(() => {
  for (const sheet of document.styleSheets) for (const rule of sheet.cssRules) {
    if (rule.type === CSSRule.KEYFRAMES_RULE && rule.name === 'cloud-wait') {
      return [...rule.cssRules].every(frame => [...frame.style].every(property => property === 'opacity'));
    }
  }
  return null;
});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // Desktop: the chart, the Connecting page, the grid, a request, Add, another agent, the team builder's link, and the cancel.
    {
      const {page, errors, calls} = await open(browser, {width: 1300, height: 900});
      await page.goto('https://tico-ui.test/#/tasks');
      const node = page.locator('#tree [data-seat="s-devin"]');
      await node.waitFor();
      assert.ok(await node.evaluate(el => el.hasAttribute('data-seat-agent')), 'a cloud agent seat is marked as one');
      assert.equal(await node.locator('.pill.connecting').count(), 1, 'and shows the Connecting pill');
      assert.equal(await node.locator('.seat-agent').evaluate(el => getComputedStyle(el).borderTopStyle), 'dashed', 'with a dashed node');

      await node.click();
      const seatDialog = page.locator('dialog.cloud-seat');
      await seatDialog.locator('.cloud-step').nth(2).waitFor();
      assert.equal(await seatDialog.locator('.cloud-step').count(), 3, 'three steps: requested, building, ready');
      assert.equal(await seatDialog.locator('.cloud-step').first().evaluate(el => el.classList.contains('now')), true, 'the first step is the one in progress');
      assert.equal(await seatDialog.locator('[aria-current="step"]').count(), 1);
      assert.equal(await seatDialog.locator('.cloud-waiting li').count(), 1, 'the first task waits on the seat');
      assert.equal(await seatDialog.locator('.cloud-pulse').evaluate(el => getComputedStyle(el).animationName), 'cloud-wait');
      assert.equal(await pulseIsOpacityOnly(page), true, 'the pulse animates opacity only');
      page.once('dialog', dialog => dialog.accept());
      await seatDialog.locator('[data-cloud-cancel]').click();
      await seatDialog.waitFor({state: 'detached'});
      assert.deepEqual(calls.map(c => c.path), ['/api/v2/seats/s-devin/close'], 'cancelling closes the seat');

      // The grid from Team's plus menu: four across, two of them Add, six Request.
      await page.evaluate(() => window.cloudAgentsOpen('eng'));
      const grid = page.locator('dialog.cloud-open');
      await grid.locator('.cloud-card').nth(7).waitFor();
      assert.equal(await grid.locator('.cloud-card').count(), 8);
      assert.equal(await grid.locator('[data-cloud-add]').count(), 2);
      assert.equal(await grid.locator('[data-cloud-request]').count(), 6);
      assert.equal(await grid.locator('.cloud-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 4);

      // A request files the seat in this team, reporting to its head, and opens its Connecting page.
      const request = page.locator('dialog.cloud-request');
      await grid.locator('[data-cloud-request="devin"]').click();
      await request.waitFor();
      assert.equal(await request.locator('.cloud-note .ldot').count(), 1, 'the placeholder note carries the Liquid Dot');
      await request.locator('textarea[name="first_task"]').fill('Map the sign-in errors');
      await request.locator('button[type="submit"]').click();
      await seatDialog.locator('.cloud-step').nth(2).waitFor();
      await request.waitFor({state: 'detached'});
      assert.deepEqual(calls.at(-1), {method: 'POST', path: '/api/v2/cloud-agents/devin/request',
        body: {first_task: 'Map the sign-in errors', team: 'eng', reports_to: 'rust-engineer'}});
      await seatDialog.locator('[data-close]').first().click();
      await seatDialog.waitFor({state: 'detached'});

      // Add: a supported agent opens its own connect steps (the MCP URL is that agent's), not the picker.
      await grid.locator('[data-cloud-add="claude"]').click();
      const connect = page.locator('dialog.connect-agent');
      await connect.waitFor();
      assert.equal(await connect.locator('[data-url]').innerText(), 'https://mcp.tico.test/api/v2/mcp');
      assert.equal(await connect.locator('[data-create]').count(), 1, 'with its token step');
      await page.keyboard.press('Escape');
      await connect.waitFor({state: 'hidden'});

      // Another agent: the typed name is the agent, and the request says so.
      await grid.locator('input[name="name"]').fill('Acme Agent');
      await grid.locator('form[data-cloud-other] button[type="submit"]').click();
      await request.waitFor();
      await request.locator('button[type="submit"]').click();
      await seatDialog.locator('.cloud-step').nth(2).waitFor();
      assert.deepEqual(calls.at(-1), {method: 'POST', path: '/api/v2/cloud-agents/other/request',
        body: {first_task: '', name: 'Acme Agent', team: 'eng', reports_to: 'rust-engineer'}});
      await seatDialog.locator('[data-close]').first().click();
      await seatDialog.waitFor({state: 'detached'});

      // The team builder's Request link: the same dialog and the same seat, filed in the team and under the head it names.
      const pick = {...AGENTS.find(a => a.id === 'devin'), source: 'cloud'};
      await page.evaluate(p => window.cloudAgentRequest(p, {team: 'eng', reports_to: 'rust-engineer'}), pick);
      await request.waitFor();
      await request.locator('button[type="submit"]').click();
      await seatDialog.locator('.cloud-step').nth(2).waitFor();
      assert.deepEqual(calls.at(-1), {method: 'POST', path: '/api/v2/cloud-agents/devin/request',
        body: {first_task: '', team: 'eng', reports_to: 'rust-engineer'}});
      await seatDialog.locator('[data-close]').first().click();
      await seatDialog.waitFor({state: 'detached'});

      // The same link with a supported agent adds it: the request route refuses one, so nothing is filed.
      const count = calls.length;
      await page.evaluate(p => window.cloudAgentRequest(p), {...AGENTS.find(a => a.id === 'claude'), source: 'cloud'});
      await connect.waitFor();
      assert.equal(await connect.locator('[data-url]').innerText(), 'https://mcp.tico.test/api/v2/mcp');
      assert.equal(calls.length, count, 'a supported agent files no request');
      await page.keyboard.press('Escape');
      await connect.waitFor({state: 'hidden'});

      assert.deepEqual(errors, []);
      console.log('cloud-agents: desktop ok');
      await page.close();
    }

    // A refused request keeps its dialog open and says so, so the person can try again.
    {
      const {page, errors, state} = await open(browser, {width: 1300, height: 900});
      await page.goto('https://tico-ui.test/#/tasks');
      await page.waitForSelector('#tree [data-seat="s-devin"]');
      state.failRequest = true;
      await page.evaluate(() => window.cloudAgentsOpen('eng'));
      await page.locator('dialog.cloud-open [data-cloud-request="jules"]').click();
      await page.locator('dialog.cloud-request button[type="submit"]').click();
      await page.locator('dialog.cloud-request [data-cloud-error]').waitFor({state: 'visible'});
      assert.equal(await page.locator('dialog.cloud-request button[type="submit"]').isDisabled(), false);
      assert.deepEqual(errors, []);
      console.log('cloud-agents: refusal ok');
      await page.close();
    }

    // Phone: the grid is two across, the request dialog fits the screen, and nothing scrolls sideways.
    {
      const {page, errors} = await open(browser, {width: 390, height: 844});
      await page.goto('https://tico-ui.test/#/tasks');
      await page.waitForSelector('#tree');
      await page.evaluate(() => window.cloudAgentsOpen('eng'));
      await page.locator('dialog.cloud-open .cloud-card').nth(7).waitFor();
      assert.equal(await page.locator('dialog.cloud-open .cloud-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 2);
      await page.locator('dialog.cloud-open [data-cloud-request="devin"]').click();
      const box = await page.locator('dialog.cloud-request').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= 390, 'the request dialog fits the phone');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no sideways scroll');
      assert.deepEqual(errors, []);
      console.log('cloud-agents: phone ok');
      await page.close();
    }

    // Reduced motion: the Connecting pulse stops, on the chart and on the seat's page.
    {
      const {page, errors} = await open(browser, {width: 1300, height: 900}, {reducedMotion: 'reduce'});
      await page.goto('https://tico-ui.test/#/tasks');
      await page.waitForSelector('#tree [data-seat="s-devin"]');
      assert.equal(await page.locator('#tree [data-seat="s-devin"] .pill.connecting').evaluate(el => getComputedStyle(el).animationName), 'none');
      await page.locator('#tree [data-seat="s-devin"]').click();
      await page.locator('dialog.cloud-seat .cloud-pulse').waitFor();
      assert.equal(await page.locator('dialog.cloud-seat .cloud-pulse').evaluate(el => getComputedStyle(el).animationName), 'none');
      assert.deepEqual(errors, []);
      console.log('cloud-agents: reduced motion ok');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
