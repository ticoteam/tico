// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/updates-tree.cjs
// Every request is intercepted; this test never contacts the hub or a bot.
// "Update cards nested by org chart. Manager card on top, an icon expands subordinate cards. By default you
// see only cards below you in the org chart, with a toggle for the whole org chart. Unread alerts only for
// direct reports (one level below you), not deeper." Your own card lets you write or edit your update, on
// Friday the week in review too, and a manager's card links the reports' updates it was written from.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SCREENSHOT_DIR;
const fs = require('node:fs');
const now = Date.now(), iso = ms => new Date(now + ms).toISOString(), hour = 3600e3;
const today = new Date(now).toLocaleDateString('en-CA', {timeZone: 'America/Los_Angeles'});
// A manager with eight reports (Chris, Oct 19: "a comment icon, unread icon, archive icon, and a '8 dropdown'").
const EIGHT = [['seo', 'AI SEO'], ['ads', 'Ads Bot'], ['copy', 'Copywriter'], ['social', 'Social Bot'], ['pr', 'PR Bot'],
  ['events', 'Events Bot'], ['brand', 'Brand Bot'], ['email', 'Email Bot']];
const bots = [['cmo', 'AI CMO'], ...EIGHT, ['writer', 'Writer Bot']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', can_chat: true, team: 'marketing', owner_id: 'ana'}));
const update = (id, bot, body, read, more = {}) => ({id, bot, kind: 'daily', day: today, headline: body.slice(2), body, read,
  created_at: iso(-hour), updated_at: iso(-hour), replies: 0, drafted_by: null, edited_by: null, slides: null, inputs: [], ...more});
// Noon on the queue's clock (Pacific), for the Friday checks.
const THURSDAY = '2026-10-15', FRIDAY = '2026-10-16', noon = day => new Date(`${day}T19:00:00Z`);
const drafted = {goal: 'Open the Northwind office by November', done: ['Signed the lease'], focus: ['Hire an office manager'], blockers: [],
  kpis: [{name: 'Leases signed', value: '1', unit: 'lease', note: 'Northwind'}]};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  const asked = [], posted = [];
  let mine = null, mineWeek = null, unreadCount = 2, allSeen = false, eight = false;   // one more from a direct report on another day
  const tree = (scope, kind) => {
    if (kind === 'weekly') {
      return {kind, day: FRIDAY, scope: 'below', me: 'p:ana', unread: 0,
              roots: [{key: 'p:ana', author: 'human:ana', name: 'Ana', direct: false, update: mineWeek, children: []}]};
    }
    const seo = {key: 'b:seo', author: 'seo', name: 'AI SEO', direct: false, update: update('u-seo', 'seo', '- Published the checklist page', allSeen), children: []};
    const cmo = {key: 'b:cmo', author: 'cmo', name: 'AI CMO', direct: true,
                 update: update('u-cmo', 'cmo', '- The team shipped the checklist', allSeen, {inputs: [{id: 'u-seo', author: 'seo'}]}), children: [seo]};
    if (eight) {      // eight reports, one with no update yet and one with a report of its own
      cmo.children.push(...EIGHT.slice(1).map(([bot, name], i) => ({key: 'b:' + bot, author: bot, name, direct: false, children: [],
        update: i === 3 ? null : update('u-' + bot, bot, `- ${name} finished its part of the checklist launch`, allSeen)})));
      seo.children = [{key: 'b:writer', author: 'writer', name: 'Writer Bot', direct: false, update: update('u-writer', 'writer', '- Drafted the checklist page', allSeen), children: []}];
    }
    const ana = {key: 'p:ana', author: 'human:ana', name: 'Ana', direct: false, update: mine, children: [cmo]};
    return {kind: 'daily', day: today, scope: scope || 'below', me: 'p:ana', unread: 1,
            roots: scope === 'all' ? [{key: 'p:boss', author: 'human:boss', name: 'Bo', direct: false, update: null, children: [ana]}] : [ana]};
  };
  const open = async viewport => {
    const page = await browser.newPage({viewport, serviceWorkers: 'block', ...(viewport.width < 600 ? {isMobile: true, hasTouch: true} : {})});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'boss', name: 'Bo'}]});
      if (p === '/api/v2/updates/unread') { asked.push(url.search); return json({unread: unreadCount}); }
      if (p === '/api/v2/updates/tree') { asked.push(p + url.search); return json(tree(url.searchParams.get('scope'), url.searchParams.get('kind'))); }
      if (p === '/api/v2/updates/read') { posted.push({path: p, body: req.postDataJSON()}); return json({marked: 1, read: true}); }
      if (p === '/api/v2/updates' && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        if (body.kind === 'weekly') {
          mineWeek = update('u-ana-week', 'human:ana', '', true, {kind: 'weekly', day: FRIDAY, slides: body.slides, edited_by: body.replace_draft ? null : 'human:ana'});
          return json({update: mineWeek});
        }
        mine = update('u-ana', 'human:ana', body.body, true);
        return json({update: mine});
      }
      return json(unrouted(p));
    });
    return {page, errors};
  };
  try {
    const {page, errors} = await open({width: 1280, height: 900});
    await page.goto('http://tico-ui.test/#/updates');
    const cmo = page.locator('#upd-feed [data-upd="u-cmo"]');
    await cmo.waitFor();
    // Your card on top (no update yet: write one), your direct report's card under it, open and alerting.
    assert.match(await page.locator('#upd-feed .upd-node[data-depth="0"] > .upd-none').innerText(), /Ana[\s\S]*No update yet/);
    assert.equal(await cmo.locator('.upd-dot').count(), 1, 'a direct report alerts');
    assert.ok(asked.some(q => q === '?scope=direct'), 'the badge counts direct reports only');
    // The manager's card has an icon that opens its reports' cards; a report's report never alerts.
    assert.equal(await page.locator('#upd-feed [data-upd="u-seo"]').count(), 0, 'deeper cards start closed');
    const kids = page.locator('#upd-feed [data-upd-kids="b:cmo"]');
    assert.equal(await kids.getAttribute('aria-label'), 'Show 1 report');
    assert.equal(await cmo.locator('.upd-actions [data-upd-kids]').count(), 1, 'the toggle is in the card\'s action row');
    await kids.click();
    const seo = page.locator('#upd-feed [data-upd="u-seo"]');
    await seo.waitFor();
    assert.equal(await seo.locator('[data-upd-kids]').count(), 0, 'a card with no reports has no count');
    assert.equal(await seo.locator('.upd-actions .upd-act').count(), 3, 'just Reply, Mark read and Archive');
    assert.equal(await seo.locator('.upd-dot').count(), 0, 'one level down only: no dot two levels down');
    // Seen, the deeper card is marked read, and the badge (direct reports only) does not move.
    const read = id => posted.some(x => x.path === '/api/v2/updates/read' && x.body.ids?.includes(id));
    for (let i = 0; i < 50 && !(read('u-seo') && read('u-cmo')); i++) await page.waitForTimeout(100);
    assert.ok(read('u-seo') && read('u-cmo'), 'cards are read when seen, a deeper one too');
    assert.equal(await page.locator('[data-upd-badge]').first().textContent(), '1', 'reading a deeper card leaves the badge alone');
    // The manager's card links the update it was written from; the link opens that card under it and goes to it.
    await kids.click();
    await seo.waitFor({state: 'detached'});
    const from = cmo.locator('.upd-from [data-upd-input="u-seo"]');
    assert.equal(await from.innerText(), 'AI SEO');
    await from.click();
    await page.locator('#upd-feed [data-upd="u-seo"].sel').waitFor();
    assert.equal(await page.locator('#upd-feed [data-upd-kids="b:cmo"]').getAttribute('aria-expanded'), 'true');
    assert.match(page.url(), /#\/updates$/, 'the link stays on the page');
    if (shots) await page.screenshot({path: `${shots}/updates-tree.png`});
    // Whole org shows the chart from the top; the choice is kept.
    await page.locator('#upd-scope').click();
    await page.locator('#upd-feed .upd-node[data-depth="0"] > .upd-none', {hasText: 'Bo'}).waitFor();
    assert.ok(asked.includes('/api/v2/updates/tree?kind=daily&scope=all'));
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.updates.scope')), 'all');
    await page.locator('#upd-scope').click();
    await page.locator('#upd-feed .upd-node[data-depth="0"] > .upd-none', {hasText: 'Ana'}).waitFor();
    // Write your own update from your card.
    await page.locator('#upd-feed [data-upd-write]').click();
    await page.locator('#upd-feed [data-upd-editor] textarea').fill('- Signed the Northwind lease');
    await page.locator('#upd-feed [data-upd-editor] [type=submit]').click();
    await page.locator('#upd-feed [data-upd="u-ana"]').waitFor();
    assert.deepEqual(posted.find(x => x.path === '/api/v2/updates').body, {body: '- Signed the Northwind lease', kind: 'daily', replace_draft: false});
    assert.equal(await page.locator('#upd-feed [data-upd="u-ana"] [data-upd-write]').count(), 1, 'your card can be edited all day');
    assert.deepEqual(errors, []);
    await page.close();
    // Today's cards all read, but a direct report's update from another day still counts: Mark all read clears it.
    allSeen = true; unreadCount = 1;
    const later = await open({width: 1280, height: 900});
    await later.page.goto('http://tico-ui.test/#/updates');
    await later.page.locator('#upd-feed [data-upd="u-cmo"]').waitFor();
    await later.page.locator('#upd-allread:not([hidden])').click();
    assert.equal(await later.page.locator('[data-upd-badge]').first().isHidden(), true, 'the badge clears');
    assert.equal(await later.page.locator('#upd-allread').isHidden(), true, 'nothing left to mark: the button goes again');
    assert.ok(posted.some(x => x.path === '/api/v2/updates/read' && x.body.all), 'every unread one is marked');
    await later.page.close();
    // The Weekly tab: you write your week in review on Friday only, as slides, and edit it that day.
    const week = await open({width: 1280, height: 900});
    await week.page.clock.setFixedTime(noon(THURSDAY));
    await week.page.goto('http://tico-ui.test/#/updates');
    await week.page.locator('#upd-feed [data-upd="u-cmo"]').waitFor();
    assert.equal(await week.page.locator('#upd-feed [data-upd-write]').count(), 1, 'the daily any day');
    await week.page.locator('[data-upd-kind="weekly"]').click();
    const ownNone = week.page.locator('#upd-feed .upd-none', {hasText: 'Ana'});
    await ownNone.waitFor();
    assert.equal(await week.page.locator('#upd-feed [data-upd-write]').count(), 0, 'no week in review before Friday');
    await week.page.clock.setFixedTime(noon(FRIDAY));
    await week.page.locator('[data-upd-kind="daily"]').click();
    await week.page.locator('#upd-feed [data-upd="u-cmo"]').waitFor();
    await week.page.locator('[data-upd-kind="weekly"]').click();
    await ownNone.locator('[data-upd-write]').click();
    const editor = week.page.locator('#upd-feed [data-upd-editor][data-upd-kind="weekly"]');
    await editor.locator('[data-upd-slide="goal"]').fill('Open the Northwind office by November');
    await editor.locator('[data-upd-slide="done"]').fill('- Signed the lease\n\nMoved the desks in');
    await editor.locator('[data-upd-slide="focus"]').fill('Hire an office manager');
    await editor.locator('[data-upd-slide="kpis"]').fill('Leases signed: 1');
    await editor.locator('[type=submit]').click();
    await week.page.locator('#upd-feed [data-upd="u-ana-week"]').waitFor();
    assert.deepEqual(posted.filter(x => x.path === '/api/v2/updates').at(-1).body, {kind: 'weekly', replace_draft: false, slides: {
      goal: 'Open the Northwind office by November', done: ['Signed the lease', 'Moved the desks in'], focus: ['Hire an office manager'],
      blockers: [], kpis: [{name: 'Leases signed', value: '1'}]}});
    // The Assistant's draft: Edit opens it filled in, and your own words drop its label.
    mineWeek = update('u-ana-week', 'human:ana', '', true, {kind: 'weekly', day: FRIDAY, slides: drafted, drafted_by: 'assistant'});
    await week.page.locator('[data-upd-kind="daily"]').click();
    await week.page.locator('#upd-feed [data-upd="u-cmo"]').waitFor();
    await week.page.locator('[data-upd-kind="weekly"]').click();
    await week.page.locator('#upd-feed [data-upd="u-ana-week"] [data-upd-write]').click();
    assert.equal(await editor.locator('[data-upd-slide="goal"]').inputValue(), drafted.goal);
    assert.equal(await editor.locator('[data-upd-slide="done"]').inputValue(), 'Signed the lease');
    assert.equal(await editor.locator('[data-upd-slide="kpis"]').inputValue(), 'Leases signed: 1 lease');
    await editor.locator('[data-upd-slide="blockers"]').fill('Waiting on the landlord for keys');
    await editor.locator('[data-upd-own]').check();
    await editor.locator('[type=submit]').click();
    await editor.waitFor({state: 'detached'});
    assert.deepEqual(posted.filter(x => x.path === '/api/v2/updates').at(-1).body, {kind: 'weekly', replace_draft: true,
      slides: {...drafted, blockers: ['Waiting on the landlord for keys']}}, 'an unchanged KPI line keeps its unit and note');
    assert.deepEqual(week.errors, []);
    await week.page.close();
    // Eight reports: icons and "8" with a chevron at the right of the card, no "8 reports" row and no broken glyph.
    eight = true;
    const posted1 = mine; mine = null;   // your own card as "No update yet", with Write your update and its count
    const icons = new Set(fs.readFileSync(uiFile('vendor/fonts/icons.txt'), 'utf8').split('\n').filter(Boolean));
    const fitsRow = async (p, label) => {
      // Each action row, and the "No update yet" row that carries a count too: one line, no word broken over two.
      const rows = await p.locator('#upd-feed .upd-actions, #upd-feed .upd-none').evaluateAll(els => els.map(row => {
        const card = row.closest('.upd-card, .upd-none').getBoundingClientRect();
        const b = [...row.children].filter(c => getComputedStyle(c).display !== 'none').map(c => c.getBoundingClientRect());
        const texts = [], walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
        for (let n; (n = walk.nextNode());) if (n.textContent.trim() && !n.parentElement.closest('.nav-icon')) { const r = document.createRange(); r.selectNodeContents(n); texts.push(r.getClientRects().length); }
        return {line: Math.max(...b.map(r => r.top)) < Math.min(...b.map(r => r.bottom)), overlap: b.some((r, i) => i && r.left < b[i - 1].right - 0.5),
                inside: b.every(r => r.right <= card.right - 4 && r.left >= card.left), wrapped: texts.some(n => n > 1)};
      }));
      assert.ok(rows.length > 0);
      rows.forEach((r, i) => assert.deepEqual(r, {line: true, overlap: false, inside: true, wrapped: false}, `${label}: row ${i} on one line, nothing overlapping`));
    };
    const tree8 = async (viewport, label) => {
      const {page: p, errors: errs} = await open(viewport);
      await p.goto('http://tico-ui.test/#/updates');
      const boss = p.locator('#upd-feed [data-upd="u-cmo"]');
      await boss.waitFor();
      const toggle = boss.locator('[data-upd-kids="b:cmo"]');
      assert.deepEqual(await boss.locator('.upd-actions .upd-act').evaluateAll(els => els.map(b => b.getAttribute('aria-label'))),
        ['Reply', 'Mark unread', 'Archive', 'Show 8 reports'], `${label}: comment, unread, archive, then the count`);
      assert.equal(await toggle.evaluate(b => [...b.childNodes].filter(n => !n.classList?.contains('nav-icon')).map(n => n.textContent).join('').trim()), '8');
      assert.equal(await toggle.locator('.nav-icon').textContent(), 'chevron_right');
      const [row, main] = [await boss.locator('.upd-actions').boundingBox(), await toggle.boundingBox()];
      assert.ok(Math.abs(row.x + row.width - (main.x + main.width)) < 1 && main.x > row.x + row.width / 2, `${label}: the count sits at the right end of the row`);
      const words = async () => p.locator('#upd-feed').evaluate(f => [...f.querySelectorAll('*')].filter(e => !e.closest('.nav-icon') && e.childNodes.length)
        .flatMap(e => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent)).join(' '));
      assert.doesNotMatch(await words(), /\breports?\b/, `${label}: no "8 reports" row`);
      const glyphs = async () => p.locator('#upd-feed .nav-icon').evaluateAll(els => els.map(e => e.textContent.trim()));
      // With the shipped font loaded, every icon is one glyph that fits its box (a missing name overflows as letters).
      const drawn = async () => {
        await p.evaluate(() => document.fonts.ready);
        const wide = await p.locator('#upd-feed .nav-icon').evaluateAll(els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent));
        assert.deepEqual(wide, [], `${label}: every icon draws as a glyph`);
      };
      await drawn();
      for (const g of await glyphs()) assert.ok(icons.has(g), `${label}: icon "${g}" is in the shipped font (a missing one shows as a stray glyph)`);
      if (shots) await p.screenshot({path: `${shots}/updates-tree-8-closed-${label}.png`});
      await toggle.click();
      await p.locator('#upd-feed [data-upd="u-email"]').waitFor();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      assert.equal(await toggle.getAttribute('aria-label'), 'Hide 8 reports');
      assert.equal(await p.locator('#upd-feed .upd-node[data-depth="1"] > .upd-sub > .upd-node').count(), 8, `${label}: the eight cards open under it`);
      // One line from the manager's avatar down the left of its reports, with an elbow into each one's avatar; it stops
      // at the last (tree-lines.css). Measured from the boxes: the rail runs under the middle of the manager's avatar,
      // and each elbow ends where the report's avatar starts.
      const line = await p.locator('#upd-feed .upd-node[data-depth="1"] > .upd-sub').evaluate(sub => {
        const box = el => el.getBoundingClientRect(), mid = el => box(el).left + box(el).width / 2;
        const parentCard = sub.parentElement.firstElementChild, parentAv = parentCard.querySelector('.upd-av .av');
        const stub = getComputedStyle(parentCard, '::after'), sb = box(parentCard);
        return [...sub.children].map((node, i) => {
          const el = node.querySelector(':scope>.upd-none') || node, r = box(el), b = getComputedStyle(el, '::before'), a = getComputedStyle(node, '::after');
          const w = parseFloat(b.borderLeftWidth), left = r.left + el.clientLeft + parseFloat(b.left), top = r.top + el.clientTop + parseFloat(b.top);
          const av = node.firstElementChild.querySelector('.upd-av .av');
          return {i, railX: left + w / 2, parentCx: mid(parentAv), elbowRight: left + parseFloat(b.width), avLeft: box(av).left,
                  elbowY: top + parseFloat(b.height) - parseFloat(b.borderBottomWidth) / 2, avCy: box(av).top + box(av).height / 2, top,
                  prevBottom: i ? box(sub.children[i - 1]).bottom : sb.top + parentCard.clientTop + parseFloat(stub.top) + parseFloat(stub.height),
                  stubX: sb.left + parentCard.clientLeft + parseFloat(stub.left) + parseFloat(stub.borderLeftWidth) / 2,
                  same: b.borderLeftWidth === b.borderBottomWidth && b.borderBottomLeftRadius !== '0px', on: a.content !== 'none' && a.content !== 'normal'};
        });
      });
      for (const n of line) {
        assert.ok(n.same, `${label}: report ${n.i}'s elbow is one weight, and round`);
        assert.ok(Math.abs(n.railX - n.parentCx) <= 1, `${label}: the line runs under the middle of the manager's avatar (${n.railX} vs ${n.parentCx})`);
        assert.ok(Math.abs(n.elbowRight - n.avLeft) <= 1, `${label}: report ${n.i}'s elbow meets its avatar (${n.elbowRight} vs ${n.avLeft})`);
        assert.ok(Math.abs(n.elbowY - n.avCy) <= 1, `${label}: report ${n.i}'s elbow turns into its avatar's middle (${n.elbowY} vs ${n.avCy})`);
        // the first from the manager's stub (overlapping it), each next one from the foot of the report above
        assert.ok(n.i ? Math.abs(n.top - n.prevBottom) <= 1 : n.top <= n.prevBottom + 1, `${label}: report ${n.i}'s line joins the one above it (${n.top} vs ${n.prevBottom})`);
        assert.equal(n.on, n.i < line.length - 1, `${label}: the line goes on past report ${n.i} only when another follows`);
      }
      assert.ok(Math.abs(line[0].stubX - line[0].railX) <= 1, `${label}: the line leaves the manager's card under its avatar`);
      // A report's report hangs the same way, one level further in.
      await p.locator('#upd-feed [data-upd-kids="b:seo"]').click();
      await p.locator('#upd-feed [data-upd="u-writer"]').waitFor();
      const indent = await p.locator('#upd-feed .upd-sub').evaluateAll(subs => subs.map(s => s.firstElementChild.firstElementChild.getBoundingClientRect().left - s.parentElement.firstElementChild.getBoundingClientRect().left));
      assert.ok(indent.length === 3 && indent.every(v => Math.abs(v - indent[0]) < 1), `${label}: nested levels indent the same (${indent})`);
      for (const g of await glyphs()) assert.ok(icons.has(g), `${label}: icon "${g}" open is in the shipped font`);
      await drawn();
      await fitsRow(p, label);
      assert.ok(await p.evaluate(() => document.documentElement.scrollWidth) <= viewport.width, `${label}: no sideways scroll`);
      if (shots) await p.screenshot({path: `${shots}/updates-tree-8-open-${label}.png`, fullPage: true});
      await p.locator('#upd-feed [data-upd-kids="b:seo"]').click();
      await toggle.click();
      await p.locator('#upd-feed [data-upd="u-email"]').waitFor({state: 'detached'});
      assert.equal(await toggle.getAttribute('aria-label'), 'Show 8 reports', `${label}: tapping again closes them`);
      return {p, errs};
    };
    const desk8 = await tree8({width: 1280, height: 900}, 'desktop');
    assert.deepEqual(desk8.errs, []);
    await desk8.p.close();
    const phone8 = await tree8({width: 375, height: 812}, 'phone');
    // A long press names an icon and does not also act; a tap acts.
    const reply = phone8.p.locator('#upd-feed [data-upd="u-cmo"] [data-upd-reply]');
    await reply.dispatchEvent('pointerdown', {pointerType: 'touch', isPrimary: true});
    await phone8.p.waitForTimeout(600);
    assert.equal(await reply.evaluate(b => b.hasAttribute('data-tip-on') && getComputedStyle(b, '::after').content), '"Reply"', 'a long press shows the name');
    await reply.dispatchEvent('pointerup', {pointerType: 'touch', isPrimary: true});
    await reply.dispatchEvent('click');
    assert.equal(await phone8.p.locator('#upd-feed [data-upd="u-cmo"] textarea').count(), 0, 'the long press did not open the reply');
    await reply.tap();
    await phone8.p.locator('#upd-feed [data-upd="u-cmo"] textarea').waitFor();
    assert.deepEqual(phone8.errs, []);
    await phone8.p.close();
    eight = false; mine = posted1;
    // A phone: the nested cards fit the screen.
    const phone = await open({width: 360, height: 760});
    await phone.page.goto('http://tico-ui.test/#/updates');
    await phone.page.locator('#upd-feed [data-upd="u-cmo"]').waitFor();
    const wide = await phone.page.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 360, `no sideways scroll on a phone (${wide}px)`);
    await phone.page.locator('#upd-feed [data-upd-kids="b:cmo"]').click();
    await phone.page.locator('#upd-feed [data-upd="u-seo"]').waitFor();
    await fitsRow(phone.page, 'phone 360');
    if (shots) await phone.page.screenshot({path: `${shots}/updates-tree-phone.png`});
    assert.deepEqual(phone.errors, []);
    console.log('PASS: Updates nested by the org chart: your card on top, direct reports open and alerting, "8" and a chevron at the right of a card open deeper cards that never alert, hung from it on one line with an elbow each, icon-only actions named by label, title and long press, Whole org from the top of the chart (kept), the badge from direct reports only, writing your own update from your card (as a daily), From links that open the report\'s card, Mark all read for a badge from another day (gone once read), the week in review on Friday (written and edited as slides), and a phone that fits.');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
