import type {Page, Route} from 'playwright';
// Offline browser check of Snooze on an ask in Flow (#3769, ui/app/decide.ts), against a fake of the new routes, at
// 1280 px and on a 390 px phone:
// - an ask has Snooze in Skip's place: an icon with no word, named "Snooze", last in the answer row, 40 px (44 on a
//   phone), and no Skip;
// - its menu: 1 hour, 1 day, 1 week, 1 month, Other; Enter opens it on its first choice, ↑ ↓ move (round), Esc closes
//   it with Flow still open and focus back on the icon;
// - open, it covers no part of Approve (it opens upward from the bar on a phone), and a tap on Approve then
//   only closes it: nothing is approved;
// - 1 hour sends POST /v2/needs-you/snooze {id, until: an hour on}; the next item comes, the top bar says Snoozed, and
//   ↩ sends unsnooze and brings it back;
// - Other: a date-time box in the same menu; a time gone by sends nothing, Enter on one to come snoozes until it;
// - the end: "N snoozed" counts them, opens their list, and ↩ on one brings it back on screen.
// FLOW_SHOTS=<folder> saves snooze-desktop.png, snooze-phone.png (the open menu) and snooze-other.png (Other).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const Q = ['Approve the invoice run', 'Approve the launch email', 'Approve the price change'];
const ok = (n: number) => ({kind: 'ask', id: `v${n}:r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: Q[n - 1]!,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, `Task ${n}`),
  ask: {requirement_id: `r${n}`, kind: 'ok', line: Q[n - 1]!, who: 'human:ana', asked_by: 'human:bo', choices: null, may: ['ok', 'no'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const NOW = '2026-11-14T07:30:00.000Z';           // 23:30 on Nov 13 in Los Angeles

type Box = {x: number; y: number; width: number; height: number};
const meets = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const shots = process.env.FLOW_SHOTS;
  try {
    for (const [label, viewport] of [['desktop', {width: 1280, height: 900}], ['phone', {width: 390, height: 844}]] as const) {
      const errors: string[] = [], writes: {p: string; body: Record<string, unknown>}[] = [];
      let open = [ok(1), ok(2), ok(3)];
      const snoozed = new Map<string, string>();
      const page: Page = await browser.newPage({viewport, serviceWorkers: 'block', timezoneId: 'America/Los_Angeles', hasTouch: label === 'phone'});
      await page.clock.setFixedTime(new Date(NOW));
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', (route: Route) => {
        const r = route.request(), url = new URL(r.url()), p = url.pathname;
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (p.startsWith('/vendor/fonts/')) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(path.join(__dirname, '..', p))});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (p === '/api/me') return json({...me, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'bo', name: 'Bo'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        const ends = [...snoozed.values()].sort();
        const items = open.filter(n => !snoozed.has(n.id));
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: items.length, items, version: items.map(i => i.id).join(','), next: null, snoozed_until: ends[0] || null});
        if (p === '/api/v2/needs-you/snoozed') {
          const held = open.filter(n => snoozed.has(n.id)).map(n => ({...n, snoozed_until: snoozed.get(n.id)}));
          return json({items: held, count: held.length, next: ends[0] || null});
        }
        const okRead = p.match(/^\/api\/v2\/oks\/(r\d)$/);
        if (okRead) {
          const n = Number(okRead[1]!.slice(1));
          return json({requirement_id: okRead[1], subject: `spec:${okRead[1]}`, content: {title: Q[n - 1], body: `${Q[n - 1]}: the numbers, who gets it and when it goes out.`},
            shown_hash: String(n).repeat(64), standing: 'waiting', task_id: `t${n}`});
        }
        if (p === '/api/v2/flow/opinions') return json({opinions: []});
        if (r.method() !== 'GET' && p.startsWith('/api/')) {
          const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
          writes.push({p, body});
          if (p === '/api/v2/needs-you/snooze') { snoozed.set(String(body['id']), String(body['until'])); return json({id: body['id'], snoozed_until: body['until']}); }
          if (p === '/api/v2/needs-you/unsnooze') { snoozed.delete(String(body['id'])); return json({id: body['id']}); }
          const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/ok$/);
          if (m) { open = open.filter(n => n.requirement_id !== m[2]); return json({task: brief(m[1]!, 'Task'), requirement_id: m[2]}); }
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('[data-need="v1:r1"]').waitFor();
      await page.locator('#task-play').click();
      const dialog = page.locator('#need-card');
      const at = (n: number) => dialog.locator('.dc-q', {hasText: Q[n - 1]!}).waitFor();
      const icon = dialog.locator('.dc-side [data-dc="snooze"]'), menu = dialog.locator('.dc-snz');
      const approve = dialog.locator('[data-dc="approve"]');
      const focused = () => page.evaluate(() => document.activeElement?.textContent?.trim() || document.activeElement?.getAttribute('aria-label') || '');
      await at(1);
      await approve.and(page.locator(':not([disabled])')).waitFor();

      // The icon: no word, named Snooze, last in the answer row but for ⋯, at least 40 px (44 on a phone); no Skip.
      assert.equal(await dialog.locator('[data-dc="skip"]').count(), 0, `${label}: an ask has no Skip`);
      assert.equal(await icon.getAttribute('aria-label'), 'Snooze');
      assert.equal((await icon.textContent())!.trim(), '', `${label}: no word beside it`);
      assert.ok(await icon.evaluate(b => {
        const row = b.closest('.dc-acts')!, last = row.lastElementChild!;
        return last.contains(b) || (!!last.querySelector('[data-dc="ask-more"]') && last.previousElementSibling!.contains(b));
      }), `${label}: last in the row, ⋯ after it`);
      const ib = (await icon.boundingBox())!, min = label === 'phone' ? 44 : 40;
      assert.ok(ib.width >= min && ib.height >= min, `${label}: Snooze is ${ib.width}x${ib.height}`);

      // Keyboard: Enter opens it on its first choice; ↓ ↑ move, round; Esc closes it, focus back on the icon.
      await icon.focus();
      await page.keyboard.press('Enter');
      await menu.waitFor();
      assert.deepEqual(await menu.locator('[role="menuitem"]').allTextContents(), ['1 hour', '1 day', '1 week', '1 month', 'Other']);
      assert.equal(await focused(), '1 hour');
      await page.keyboard.press('ArrowDown');
      assert.equal(await focused(), '1 day');
      await page.keyboard.press('ArrowUp');
      await page.keyboard.press('ArrowUp');
      assert.equal(await focused(), 'Other', `${label}: ↑ from the first goes round to the last`);
      await page.keyboard.press('Escape');
      await menu.waitFor({state: 'detached'});
      assert.ok(await dialog.isVisible(), `${label}: Esc closes the menu, not Flow`);
      assert.equal(await focused(), 'Snooze', `${label}: focus is back on the icon`);
      assert.equal(await icon.getAttribute('aria-expanded'), 'false');

      // Open, it covers neither Approve nor the chat's box, and is all on screen; on a phone it opens upward from the bar.
      await icon.click();
      await menu.waitFor();
      const mb = (await menu.boundingBox())!;
      for (const [name, b] of [['Approve', approve]] as const) {
        const bb = (await b.boundingBox())!;
        assert.ok(!meets(mb, bb), `${label}: the menu is clear of ${name} (${JSON.stringify(mb)} vs ${JSON.stringify(bb)})`);
      }
      assert.ok(mb.x >= 0 && mb.y >= 0 && mb.x + mb.width <= viewport.width && mb.y + mb.height <= viewport.height, `${label}: the menu is on screen ${JSON.stringify(mb)}`);
      if (label === 'phone') assert.ok(mb.y + mb.height <= ib.y, `${label}: the menu opens upward`);
      const tall = await menu.locator('[role="menuitem"]').evaluateAll(els => els.map(e => e.getBoundingClientRect().height));
      assert.ok(tall.every(h => h >= (label === 'phone' ? 44 : 32)), `${label}: choices are ${tall.join(', ')} px`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) <= 1, `${label}: nothing scrolls sideways`);
      if (shots) { await dialog.locator('.dc-start').waitFor({state: 'detached'}); await page.waitForTimeout(400); await page.screenshot({path: path.join(shots, `snooze-${label}.png`)}); }
      // A tap on Approve with the menu open only closes it.
      await approve.click();
      await menu.waitFor({state: 'detached'});
      assert.deepEqual(writes, [], `${label}: a tap off the open menu approves nothing`);

      // 1 hour: an hour on; the next item; Snoozed in the top bar; ↩ brings it back.
      await icon.click();
      await menu.locator('[data-dc="snooze-hour"]').click();
      await at(2);
      assert.deepEqual(writes, [{p: '/api/v2/needs-you/snooze', body: {id: 'v1:r1', until: '2026-11-14T08:30:00.000Z'}}]);
      await dialog.locator('.dc-bar:not([hidden])').waitFor();
      assert.match((await dialog.locator('.dc-bar').innerText()) + (await dialog.locator('.dc-bar').getAttribute('aria-label') || '') + await dialog.locator('.dc-bar').evaluate(e => e.textContent), /Snoozed/);
      await dialog.locator('.dc-bar [data-dc="undo"]').click();
      await page.waitForFunction('decideLive(DEC).length === 3');
      assert.deepEqual(writes.slice(1), [{p: '/api/v2/needs-you/unsnooze', body: {id: 'v1:r1'}}], `${label}: ↩ unsnoozes`);
      writes.length = 0;
      if (!await dialog.locator('.dc-q', {hasText: Q[0]!}).count()) await dialog.locator('[data-dc="prev"]').click();
      await at(1);

      // Other: a time box in the same menu; one gone by sends nothing; Enter on one to come snoozes until it.
      await icon.click();
      await menu.locator('[data-dc="snooze-other"]').click();
      const box = dialog.locator('#dc-snz-at');
      await box.waitFor();
      assert.ok(await box.evaluate(e => e === document.activeElement), `${label}: Other focuses its box`);
      assert.equal(await box.getAttribute('type'), 'datetime-local');
      const ob = (await menu.boundingBox())!;
      for (const b of [approve]) assert.ok(!meets(ob, (await b.boundingBox())!), `${label}: Other is clear of the buttons`);
      if (shots && label === 'desktop') { await page.waitForTimeout(300); await page.screenshot({path: path.join(shots, 'snooze-other.png')}); }
      await box.fill('2026-11-13T09:00');
      await box.press('Enter');
      await page.waitForTimeout(200);
      assert.deepEqual(writes, [], `${label}: a time gone by sends nothing`);
      assert.ok(await menu.isVisible(), `${label}: the menu stays for another time`);
      await box.fill('2026-11-20T09:00');
      await box.press('Enter');
      await at(2);
      assert.deepEqual(writes, [{p: '/api/v2/needs-you/snooze', body: {id: 'v1:r1', until: '2026-11-20T17:00:00.000Z'}}], `${label}: 9:00 in Los Angeles`);
      writes.length = 0;

      // Approve the second, snooze the third for a week: the end counts the two snoozed, lists them, ↩ brings one back.
      await approve.and(page.locator(':not([disabled])')).waitFor();
      await page.waitForTimeout(450);                                       // a phone's 400 ms tap guard
      await approve.click();
      await at(3);
      await page.waitForTimeout(450);
      await icon.click();
      await menu.locator('[data-dc="snooze-week"]').click();
      await dialog.locator('.dc-done').waitFor();
      assert.deepEqual(writes.map(w => w.p), ['/api/v2/tasks/t2/asks/r2/ok', '/api/v2/needs-you/snooze']);
      assert.equal(writes[1]!.body['until'], '2026-11-21T07:30:00.000Z');
      const said = dialog.locator('.dc-done [data-dc="snoozed"]');
      await said.waitFor();
      assert.equal((await said.textContent())!.trim(), '2 snoozed');
      await said.click();
      const rows = dialog.locator('.dc-snzl li');
      await rows.first().waitFor();
      assert.deepEqual(await rows.allInnerTexts(), ['Task 1', 'Task 3']);
      const back = dialog.locator('.dc-snzl [data-dc="unsnooze"][data-dc-ask="v3:r3"]');
      assert.equal(await back.getAttribute('aria-label'), 'Unsnooze: Task 3');
      const bb = (await back.boundingBox())!;
      assert.ok(bb.width >= min && bb.height >= min, `${label}: ↩ is ${bb.width}x${bb.height}`);
      await back.click();
      await at(3);
      assert.deepEqual(writes.slice(2), [{p: '/api/v2/needs-you/unsnooze', body: {id: 'v3:r3'}}]);

      assert.deepEqual(errors, [], label);
      await page.close();
    }
    console.log('PASS: Snooze on an ask is a quiet icon in Skip\'s place; its menu works by keyboard, covers no button, snoozes for 1 hour or a time of your own, undoes, and the end lists the snoozed with ↩.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
