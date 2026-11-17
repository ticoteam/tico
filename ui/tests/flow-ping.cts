import type {Route} from 'playwright';
// Offline browser check of "Want more? Ping your bots" under Flow's All done (ui/app/decide.ts decidePing), against a
// fake of POST /api/v2/flow/ping (backend/flow_ping.py):
// - All done offers the ping; pressing it asks the reader's reports and says "Asking your bots";
// - a pinged bot's question coming into Needs you comes on screen by itself;
// - Flow narrowed to one bot (its page's Flow mark, decideBotsFlow) shows that bot's items alone, names it in the
//   crumb, and its ping asks that bot;
// - a ping that found no bots says so and offers the button again;
// - a team with no bots opens on All done, not on everyone's items;
// - ⋯'s Close task shows only to someone who may close the task (the manager of the bot that asked).
// FLOW_SHOTS=<folder> saves All done, Asking your bots and the narrowed Flow at phone size.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Item = Record<string, unknown> & {id: string};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const question = (n: number, bot: string, line: string): Item => {
  const task = {id: 't' + n, title: line, number: n, pipeline_id: null, stage_id: null, status: 'open', owner_id: bot,
    requester_id: bot, assignee_id: null, due_at: null, late: null, snoozed: false,
    turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-15T10:00:00Z', until: null, ask: true, requirement_id: null},
    version: 1};
  return {kind: 'ask', id: 'r' + n, task_id: task.id, requirement_id: 'r' + n, line, since: '2026-11-15T10:00:00Z', task,
    ask: {requirement_id: 'r' + n, kind: 'answer', line, who: 'human:ana', asked_by: bot, choices: ['Yes', 'No'], may: ['answer'],
      met: false, shows: [], standing: 'waiting', answer: null}};
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], pings: unknown[] = [];
    let needs: Item[] = [], pinged: {id: string; name: string}[] = [{id: 'pm', name: 'PM'}];
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{slug: 'pm', display_name: 'PM', status: 'active'}, {slug: 'ops', display_name: 'Ops', status: 'active'}]);
      if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: []});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: needs.map(x => x.id).join(',') || '0', next: null});
      if (p.startsWith('/api/v2/oks/')) return json({error: 'not_found'}, 404);
      const t = p.match(/^\/api\/v2\/tasks\/(t\d+)$/);
      if (t) return json({task: {...(needs.find(x => x.task_id === t[1])?.task as object), created_at: '2026-11-15T09:00:00Z', attachments: [], links: [], body: ''}});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});
      if (p === '/api/v2/flow/ping') { pings.push(JSON.parse(r.postData() || '{}')); return json({pinged, recent: []}); }
      const m = p.match(/^\/api\/v2\/tasks\/(t\d+)\/asks\/(r\d+)\/answer$/);
      if (m && r.method() === 'POST') { needs = needs.filter(x => x.id !== m[2]); return json({task: {id: m[1]}, answer_id: 'a-' + m[2], requirement_id: m[2]}); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = page.locator('#need-card');
    const ping = dialog.getByRole('button', {name: 'Want more? Ping your bots'});
    const shot = async (name: string) => {
      if (!process.env.FLOW_SHOTS) return;
      await page.waitForTimeout(900);   // past Flow's "Getting ready to flow" (DEC_START_MS)
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-phone.png`)});
    };

    // ---- 1. All done offers the ping; it asks the reader's reports and says so.
    needs = [question(1, 'bot:pm', 'Launch in March?')];
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.evaluate('decideFlow(document.body)');
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await dialog.getByText('All done').waitFor();
    await ping.waitFor();
    await shot('all-done');
    await ping.click();
    await dialog.getByText('Asking your bots').waitFor();
    assert.deepEqual(pings, [{scope: 'reports'}]);
    assert.equal(await ping.count(), 0);
    await shot('asking');

    // ---- 2. The pinged bot's question comes in and comes on screen by itself.
    needs = [question(2, 'bot:pm', 'Price per seat or per company?')];
    await page.evaluate('v2Refresh()');
    await dialog.locator('.dc-q', {hasText: 'Price per seat or per company?'}).waitFor();
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await dialog.getByText('All done').waitFor();
    await dialog.locator('[data-dc="close"]').first().click();

    // ---- 3. Narrowed to one bot: its items alone, its name in the crumb, its ping asks it.
    needs = [question(3, 'bot:ops', 'Move the backup to Sunday?'), question(4, 'bot:pm', 'Drop the free plan?')];
    await page.evaluate('v2Refresh()');
    await page.locator('[data-need="r4"]').waitFor();
    await page.evaluate("decideBotsFlow(document.body, ['bot:pm'], 'PM', {scope: 'bot', id: 'pm'})");
    await dialog.locator('.dc-q', {hasText: 'Drop the free plan?'}).waitFor();
    assert.equal(await dialog.getByText('Move the backup to Sunday?').count(), 0, 'another bot’s item stays out');
    assert.ok((await dialog.locator('.dc-head').innerText()).includes('PM'), 'the crumb names the bot');
    await shot('narrowed');
    // ⋯'s Close task shows only to someone who may close the task: here, once PM reports to Ana (not a task mover).
    const more = dialog.locator('[data-dc="ask-more"]'), closeIt = dialog.locator('[role="menuitem"][data-dc="close-task"]');
    await page.evaluate('S.me.mover = false; decidePaintSide(DEC)');
    assert.equal(await more.count(), 0, 'not theirs to close: in the task\'s chat ⋯ holds nothing else');
    await page.evaluate("S.emps.find(e => e.slug === 'pm').reports_to = 'human:ana'; decidePaintSide(DEC)");
    await more.click();
    await closeIt.waitFor();
    await page.keyboard.press('Escape');   // on a phone ⋯ is a sheet over its own button
    await closeIt.waitFor({state: 'detached'});
    await page.evaluate('S.me.mover = true');
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await dialog.getByText('All done').waitFor();
    pinged = [];
    await ping.click();
    // ---- 4. Nobody to ask: said, and the button comes back.
    await ping.waitFor();
    assert.deepEqual(pings.at(-1), {scope: 'bot', id: 'pm'});

    await dialog.locator('[data-dc="close"]').first().click();

    // ---- 5. A team with no bots: its Flow walks nothing (All done), not everyone's items.
    needs = [question(5, 'bot:ops', 'Rotate the keys tonight?')];
    await page.evaluate('v2Refresh()');
    await page.locator('[data-need="r5"]').waitFor();
    await page.evaluate("decideBotsFlow(document.body, [], 'Empty', {scope: 'group', id: 'empty'})");
    await dialog.getByText('All done').waitFor();
    assert.equal(await dialog.getByText('Rotate the keys tonight?').count(), 0, 'an empty team walks no one’s items');

    // The page fits a phone: nothing scrolls sideways.
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no sideways scroll');
    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: All done pings your bots, says Asking your bots, new questions come on screen; one bot\'s Flow shows and pings it alone.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
