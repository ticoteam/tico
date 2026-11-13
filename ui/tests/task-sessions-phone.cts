// Offline browser regression: on a phone (390 px) each row of a task's Sessions list has a Copy and a Remove button
// a thumb can hit. Each is at least a 40x40 px tap area (padding, not a bigger icon), and the two do not touch, so a
// miss on Copy does not land on Remove. Remove asks once before it takes the session off: Cancel keeps it, OK removes it.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
type Viewport = {width: number; height: number};
// S is the page's app state. It is declared here, not as a global: other tests declare S with other shapes.
declare const S: {emps?: unknown[]};
declare var taskModalShow: (task: unknown) => unknown;

const SHOTS = process.env.TICO_UI_SHOTS || '';
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
const at = new Date().toISOString();
// Two sessions with both buttons: an earlier owner's bot session (copies its resume command) and one a person linked.
const links = [
  {id: 'l-bot', kind: 'session', url: 'session:th-1', title: '', detail: JSON.stringify({bot: 'botops', harness: 'claude', thread_id: 'th-1'})},
  {id: 'l-own', kind: 'session', url: 'session:claude --resume 7f3c2a9e-51d4-4b7a-9c10-2e8d6f4b1a77 --project tico-work-long-folder-name', title: '', detail: '{}'},
];
const task = {id: 't-s', title: 'Track the sessions', owner_id: 'bot:botops', requester_id: 'human:ana', status: 'open', version: 1,
  created_at: at, updated_at: at, conversation_id: 'r-s', links};

type Box = {x: number; y: number; width: number; height: number};
const apart = (a: Box, b: Box) => a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;

async function run(viewport: Viewport, shot: string) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors: string[] = [], removes: unknown[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!)))
        return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/tasks') return json({tasks: [task]});
      if (p === '/api/v2/pipelines') return json({pipelines: []});
      if (p === '/api/v2/tasks/t-s/links' && req.method() === 'POST') {
        const body = req.postDataJSON() as {remove?: string};
        removes.push(body.remove);
        task.links = task.links.filter(l => l.id !== body.remove);
        return json({links: task.links});
      }
      if (p === '/api/v2/tasks/t-s') return json({task, comments: [], events: [], can_comment: true, children: []});
      if (p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/botops');
    await page.waitForFunction(() => typeof taskModalShow === 'function' && S.emps?.length);
    await page.evaluate(t => taskModalShow(t), task);
    // A phone shows Sessions in the task window's Info tab (task #409).
    if (viewport.width <= 390) await page.locator('#task-modal[open] [data-task-tab="info"]').click();
    const rows = page.locator('#task-modal .task-sessions .tlink.session');
    await rows.first().waitFor();
    assert.equal(await rows.count(), 2);
    for (let i = 0; i < 2; i++) {
      const row = rows.nth(i);
      await row.scrollIntoViewIfNeeded();
      const copy = (await row.locator('button.tcopy').boundingBox())!, drop = (await row.locator('button.x').boundingBox())!;
      for (const [name, box] of [['Copy', copy], ['Remove', drop]] as const) {
        assert.ok(box.width >= 40 && box.height >= 40, `row ${i + 1}: ${name} is a 40x40 tap area, not ${box.width}x${box.height}`);
      }
      // A long session name is cut short, so neither button is pushed out of its row (the row clips what overflows).
      const box = (await row.boundingBox())!;
      for (const b of [copy, drop]) assert.ok(b.x >= box.x && b.x + b.width <= box.x + box.width + 0.5 && b.x + b.width <= 390, `row ${i + 1}: both buttons are inside the row`);
      assert.ok(apart(copy, drop), `row ${i + 1}: Copy and Remove do not overlap`);
      const gap = Math.max(drop.x - (copy.x + copy.width), copy.x - (drop.x + drop.width));
      assert.ok(gap >= 4, `row ${i + 1}: Copy and Remove have space between them (${gap} px)`);
      // The icons stay their size: the tap area is padding.
      assert.ok(((await row.locator('button.tcopy svg').boundingBox())!).width <= 12, 'the copy icon is not enlarged');
      // The whole 40x40 takes the tap: the row's rounded, clipped end must not cut off a corner (a tap 2 px in from each).
      for (const [name, sel] of [['Copy', 'button.tcopy'], ['Remove', 'button.x']] as const) {
        const missed = await row.locator(sel).evaluate(btn => {
          const r = btn.getBoundingClientRect();
          return [[r.left + 2, r.top + 2], [r.right - 2, r.top + 2], [r.left + 2, r.bottom - 2], [r.right - 2, r.bottom - 2]]
            .filter(([x, y]) => !btn.contains(document.elementFromPoint(x!, y!))).length;
        });
        assert.equal(missed, 0, `row ${i + 1}: every corner of ${name}'s tap area takes the tap`);
      }
    }
    if (shot) await page.screenshot({path: shot});

    // Remove asks once. Cancel keeps the session; OK takes it off.
    const asked: string[] = [];
    page.once('dialog', d => { asked.push(d.message()); void d.dismiss(); });
    await rows.nth(1).locator('button.x').click();
    await page.waitForTimeout(300);
    assert.equal(asked.length, 1, 'Remove asks before it takes the session off');
    assert.deepEqual(removes, [], 'Cancel keeps the session');
    assert.equal(await rows.count(), 2);
    page.once('dialog', d => { asked.push(d.message()); void d.accept(); });
    await rows.nth(1).locator('button.x').click();
    await page.waitForFunction(() => document.querySelectorAll('#task-modal .task-sessions .tlink.session').length === 1);
    assert.deepEqual(removes, ['l-own'], 'OK removes that one session');
    assert.equal(asked.length, 2);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

(async () => {
  const shot = (name: string): string => SHOTS ? require('node:path').join(SHOTS, name) : '';
  await run({width: 390, height: 844}, shot('task-sessions-phone.png'));
  console.log('ok');
})().catch(e => { console.error(e); process.exit(1); });
