// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/updates.cjs
// Every request is intercepted; this test never contacts the hub or a bot.
// Updates is a feed with Daily and Weekly toggles; each bot reports in once a day
// (Friday: the week in review); read state is per person and marked as cards are seen; a reply shows
// on the update and goes to the bot's chat. On a phone Updates takes Tasks' place in the bottom bar.
// "Getting updates and working through them should be very very snappy."
// TICO_SCREENSHOT_DIR=<dir> saves the review screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = require('./support/load.cjs');
const shots = process.env.TICO_SCREENSHOT_DIR;
const now = Date.now(), iso = ms => new Date(now + ms).toISOString(), hour = 3600e3;
const day = ms => new Date(now + ms).toLocaleDateString('en-CA', {timeZone: 'America/Los_Angeles'});
const bots = [['seo', 'AI SEO'], ['finance', 'Finance'], ['cmo', 'AI CMO'], ['game', 'Temp Game']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', can_chat: true, team: 'marketing', owner_id: name === 'game' ? 'ben' : 'ana'}));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  const posted = [];
  let updates = [
    {id: 'u-seo', bot: 'seo', kind: 'daily', day: day(0), headline: 'Published the vacation rental checklist page',
     body: '- Published the vacation rental checklist page, 1,240 words\n- Linked it from six older posts\n- Pitching it to three host newsletters next',
     created_at: iso(-2 * hour), updated_at: iso(-2 * hour), read: false, replies: 0},
    {id: 'u-fin', bot: 'finance', kind: 'daily', day: day(0), headline: 'Brex balance is fine; Canva retry scheduled',
     body: '- Brex has $13,000 available, so no cash warning\n- Rechecking the Canva payment on Oct 16', created_at: iso(-3 * hour),
     updated_at: iso(-3 * hour), read: false, replies: 1},
    {id: 'u-game', bot: 'game', kind: 'daily', day: day(0), headline: 'x', body: '- Ben\'s bot shipped a [level](https://game.example/level)', created_at: iso(-hour), updated_at: iso(-hour), read: true, replies: 0},
    {id: 'u-cmo', bot: 'cmo', kind: 'daily', day: day(-86400e3), headline: 'Drafted October content plan',
     body: '- Drafted the October content plan', created_at: iso(-26 * hour), updated_at: iso(-26 * hour), read: true, replies: 0},
  ];
  const weekly = [{id: 'w-seo', bot: 'seo', kind: 'weekly', day: day(0), headline: 'Week: 4 pages shipped, rankings up on 2 of 3 goals',
    body: '- Shipped four pages this week\n- Organic signups are on track', created_at: iso(-hour), updated_at: iso(-hour), read: false, replies: 0,
    slides: {goal: 'Grow organic signups to 400 a month; at 310 and on pace.',
             tracked: [{name: 'Organic signups', unit: 'signups', value: 310, spark: [240, 262, 281, 310], status: 'green', target_label: '400 by Dec 31', fresh: true}],
             kpis: [{name: 'Pages shipped', value: '4', series: [1, 2, 2, 4]}],
             done: ['Shipped four landing pages', 'Linked them from six older posts'], focus: ['Pitch the checklist to three newsletters'], blockers: []}},
    {id: 'w-cmo', bot: 'cmo', kind: 'weekly', day: day(-7 * 86400e3), headline: 'Old bullets', body: '- An older week, posted as bullets',
     created_at: iso(-170 * hour), updated_at: iso(-170 * hour), read: true, replies: 0}];
  const threads = {'u-fin': [{id: 'm1', sender_id: 'human:ana', body: 'Re your update "Brex balance is fine": Thanks, flag anything under $10k', created_at: iso(-2 * hour)},
                             {id: 'm2', sender_id: 'bot:finance', body: 'Will do.', created_at: iso(-hour)}]};
  // Older updates past the feed's page: only the one-update route has them (a From: link on an older card).
  const older = {'u-seo-old': {id: 'u-seo-old', bot: 'seo', kind: 'daily', day: day(-86400e3), headline: 'Outlined the checklist page',
                               body: '- Outlined the checklist page', created_at: iso(-27 * hour), updated_at: iso(-27 * hour), read: true, replies: 0},
                 'u-game-old': {id: 'u-game-old', bot: 'game', kind: 'daily', day: day(-86400e3), headline: 'Sketched a level',
                                body: '- Sketched the first level', created_at: iso(-28 * hour), updated_at: iso(-28 * hour), read: true, replies: 0}};
  const oneAsked = [];
  let pageTwo = null, holdOne = null;               // a second daily page for Load older; a held one-update reply
  const archivedIds = new Set(), archiveRequests = [];
  let archiveFails = false, nextListGate = null, holdList = null, holdReply = null, keepUnread = null;
  const isArchived = u => archivedIds.has(u.id) || !!u.superseded;
  const feed = (kind, archive = false, includeArchive = false) => {
    const source = kind === 'weekly' ? weekly : updates;
    const list = includeArchive ? source : source.filter(u => isArchived(u) === archive);
    return {updates: list, missed: kind === 'weekly' || archive ? [] : [{bot: 'game', kind: 'daily', day: day(0), reason: 'its run stopped part-way'}],
            unread: source.filter(u => !isArchived(u) && !u.read).length, next_before: pageTwo && kind !== 'weekly' && !archive ? 'p2' : null, today: {posted: 2, missed: 1, queued: 1}};
  };
  const open = async (viewport) => {
    const page = await browser.newPage({viewport, serviceWorkers: 'block', ...(viewport.width < 600 ? {isMobile: true, hasTouch: true} : {})});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // Every value the rail badge shows, in order: seen-is-read can lower it within a second of the first paint.
    await page.addInitScript(() => {
      localStorage.setItem('tico.updates.layout', 'list');   // the flat feed; the org-chart tree is updates-tree.cjs
      window.__badges = [];
      new MutationObserver(() => {
        const b = document.querySelector('.side-scroll [data-upd-badge]');
        const v = b && !b.hidden ? b.textContent : '';
        if (v && v !== window.__badges.at(-1)) window.__badges.push(v);
      }).observe(document, {subtree: true, childList: true, characterData: true, attributes: true});
    });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      if (p === '/api/v2/updates/unread') return json({unread: updates.filter(u => !isArchived(u) && !u.read).length});
      if (p === '/api/v2/updates/archive' && req.method() === 'POST') {
        const body = req.postDataJSON(); archiveRequests.push(body);
        if (archiveFails) return json({error: {code: 'storage_unavailable', detail: 'Synthetic archive failure'}}, 500);
        for (const id of body.ids) { if (body.archived) archivedIds.add(id); else archivedIds.delete(id); }
        return json({updated_at: body.ids.length, archived: body.archived, unread: updates.filter(u => !isArchived(u) && !u.read).length});
      }
      if (p === '/api/v2/updates' && req.method() === 'GET' && url.searchParams.get('before') === 'p2') {
        return json({updates: pageTwo, missed: [], unread: 0, next_before: null});
      }
      if (p === '/api/v2/updates' && req.method() === 'GET') {
        const payload = feed(url.searchParams.get('kind'), url.searchParams.get('archive') === 'true',
          url.searchParams.get('include_archive') === 'true');
        if (holdList) await holdList;                   // held until the test has seen the cached paint
        if (nextListGate) { const gate = nextListGate; nextListGate = null; gate.received = true; await gate.held; }
        return json(payload);                            // a real round trip; delayed replies can be stale
      }
      if (p === '/api/v2/updates/read') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        for (const u of updates) if ((body.all || body.ids.includes(u.id)) && u.id !== keepUnread) u.read = body.read !== false;
        return json({marked: body.ids.length, read: body.read !== false});
      }
      const reply = p.match(/^\/api\/v2\/updates\/([^/]+)\/reply$/);
      if (reply) {
        const body = req.postDataJSON(); posted.push({path: p, body});
        if (holdReply) await holdReply;                 // held until the test has seen the pending reply
        const msg = {id: 'm-new', sender_id: 'human:ana', body: `Re your update "x": ${body.text}`, created_at: new Date().toISOString(), conversation_id: 'c-seo'};
        threads[reply[1]] = [...(threads[reply[1]] || []), msg];
        return json({message: msg, thread: threads[reply[1]]});
      }
      const one = p.match(/^\/api\/v2\/updates\/([^/]+)$/);
      if (one) {
        oneAsked.push(one[1]);
        if (holdOne) await holdOne;
        const u = updates.find(x => x.id === one[1]) || older[one[1]];
        return u ? json({update: u, thread: threads[one[1]] || []}) : json({error: {code: 'not_found', detail: 'Update not found'}}, 404);
      }
      return json(unrouted(p));
    });
    return {page, errors};
  };
  try {
    // Desktop: Overview leads the rail; Updates keeps its unread badge and default route.
    const {page, errors} = await open({width: 1280, height: 900});
    await page.goto('http://tico-ui.test/#/updates');
    await page.locator('#upd-feed .upd-card').first().waitFor();
    // Search shows a temporary bot with its flag, not the flag's markup, and "temp" finds it.
    await page.keyboard.press('Control+k');
    for (const words of ['Game', 'temp']) {
      await page.locator('#search-input').fill(words);
      const game = page.locator('#search-results li[data-href="#/bot/game"]');
      await game.waitFor();
      assert.match(await game.innerText(), /Game/, `search finds the temporary bot by "${words}"`);
      assert.doesNotMatch(await game.innerText(), /<span|class=|Temp /, 'its flag is not shown as markup or the word Temp');
      assert.equal(await game.locator('.proj').count(), 1, 'its flag is shown');
    }
    await page.evaluate(() => document.querySelector('#search-modal').close());
    assert.equal(await page.locator('.side-scroll .nav-link:visible').first().getAttribute('data-nav'), 'overview');
    await page.waitForFunction(() => window.__badges.length > 0);
    assert.equal(await page.evaluate(() => window.__badges[0]), '2', 'the badge opens on the unread count');
    // Just the updates, each only its bullets: no title, no sections, no greeting,
    // no day headers, no who did not report, no More / Less.
    // My bots is on by default: Ben's bot's update is hidden until it is off.
    assert.equal(await page.locator('#upd-mine').getAttribute('aria-pressed'), 'true');
    assert.deepEqual(await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd)), ['u-seo', 'u-fin', 'u-cmo']);
    // All bots paints at once from the cards already loaded; its own load (held here) then sets its order.
    let releaseAll; holdList = new Promise(r => { releaseAll = r; });
    await page.locator('#upd-mine').click();
    assert.deepEqual(await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd)), ['u-seo', 'u-fin', 'u-game', 'u-cmo']);
    holdList = null; releaseAll();
    await page.waitForFunction(() => UPD && !UPD.mine && !UPD.loading && !UPD.fromCache);
    await page.locator('#upd-mine').click();
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.updates.mine')), '1');
    assert.equal(await page.locator('#upd-mine .upd-lbl').isVisible(), true, 'words on a desktop');
    assert.equal(await page.locator('#upd-feed .upd-headline, #upd-feed .upd-day, #upd-today, .upd-missed, [data-upd-open], .upd-body.clamp').count(), 0);
    assert.match(await page.locator('[data-upd="u-seo"] .upd-body').innerText(), /Published the vacation rental checklist page/);
    // Seen is read: the visible unread cards are marked in one small request.
    await page.waitForFunction(() => !document.querySelector('#upd-feed .upd-card.unread'));
    await page.waitForFunction(() => document.querySelector('.side-scroll [data-upd-badge]')?.hidden === true);
    for (const end = Date.now() + t(10000); !posted.some(x => x.path === '/api/v2/updates/read') && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    const reads = posted.filter(x => x.path === '/api/v2/updates/read');
    assert.equal(reads.length, 1, 'batched: ' + JSON.stringify(reads));
    assert.deepEqual(reads[0].body.ids.sort(), ['u-fin', 'u-seo']);
    if (shots) await page.screenshot({path: path.join(shots, 'updates-desktop.png')});
    // No Unread filter. Unread first, then newest; marking one unread (or read)
    // never moves anything while the page is open.
    assert.equal(await page.locator('#upd-unread').count(), 0);
    // The action row is icons, each named for a screen reader and a tooltip; no words on the card.
    const acts = page.locator('[data-upd="u-cmo"] .upd-actions .upd-act');
    assert.deepEqual(await acts.evaluateAll(els => els.map(b => [b.getAttribute('aria-label'), b.title, b.querySelector('.nav-icon')?.textContent])),
      [['Reply', 'Reply', 'chat_bubble'], ['Mark unread', 'Mark unread', 'mark_email_unread'], ['Archive', 'Archive', 'archive']]);
    assert.deepEqual(await acts.evaluateAll(els => els.map(b => [...b.childNodes].filter(n => !n.classList?.contains('nav-icon')).map(n => n.textContent).join('').trim())),
      ['', '', ''], 'no visible words');
    assert.equal(await page.locator('#upd-feed [data-upd-kids]').count(), 0, 'the List has no reports toggle');
    await page.locator('[data-upd="u-cmo"] [data-upd-toggle]').click();
    assert.equal(await page.locator('[data-upd="u-cmo"] [data-upd-toggle]').getAttribute('aria-label'), 'Mark read', 'Mark unread marked it unread');
    assert.deepEqual(await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd)), ['u-seo', 'u-fin', 'u-cmo'],
      'nothing re-sorts while you are on the page');
    // Archive failure leaves the card and its unsent reply draft in place. A later successful
    // keyboard activation moves it to the per-person Archive; restoring returns it to Inbox.
    await page.locator('[data-upd="u-cmo"] [data-upd-reply]').click();
    const archiveDraft = page.locator('[data-upd="u-cmo"] textarea');
    await archiveDraft.fill('Keep this reply draft');
    archiveFails = true;
    await page.locator('[data-upd="u-cmo"] [data-upd-archive]').click();
    await page.waitForFunction(() => {
      const b = document.querySelector('[data-upd="u-cmo"] [data-upd-archive]'); return b && !b.disabled;
    });
    assert.equal(await page.locator('[data-upd="u-cmo"] textarea').inputValue(), 'Keep this reply draft');
    assert.equal(archiveRequests.length, 1);
    archiveFails = false;
    let releaseStale;
    const staleGate = {held: new Promise(r => { releaseStale = r; }), received: false};
    nextListGate = staleGate;
    const staleList = page.waitForResponse(r => r.url().includes('/api/v2/updates?') && r.url().includes('kind=daily'));
    await page.evaluate(() => {
      window.__staleLoadDone = false;
      void updLoad(UPD).finally(() => { window.__staleLoadDone = true; });
    });
    for (const end = Date.now() + t(30000); !staleGate.received && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert(staleGate.received, 'the stale inbox request is held before archiving');
    const archiveButton = page.locator('[data-upd="u-cmo"] [data-upd-archive]');
    await archiveButton.focus(); await page.keyboard.press('Enter');
    await page.locator('[data-upd="u-cmo"]').waitFor({state: 'detached'});
    releaseStale(); await staleList;                    // the stale payload returns after the archive write
    await page.waitForFunction(() => window.__staleLoadDone);
    assert.equal(await page.locator('[data-upd="u-cmo"]').count(), 0, 'a delayed old inbox load cannot restore an archived card');
    assert.deepEqual(archiveRequests[1], {ids: ['u-cmo'], archived: true});
    await page.locator('#upd-view').click();
    await page.waitForFunction(() => location.hash === '#/updates?view=archive' && document.querySelector('[data-upd="u-cmo"]'));
    assert.equal(await page.locator('#upd-allread').isHidden(), true);
    assert.equal(await page.locator('.upd-head h1').innerText(), 'Archive', 'the reader knows they are in the Archive');
    assert.equal(await page.locator('[data-upd="u-cmo"] [data-upd-archive]').getAttribute('aria-label'), 'Restore');
    assert.equal(await page.locator('[data-upd="u-cmo"] [data-upd-archive] .nav-icon').textContent(), 'unarchive');
    await page.locator('[data-upd="u-cmo"] [data-upd-archive]').click();
    await page.locator('[data-upd="u-cmo"]').waitFor({state: 'detached'});
    assert.deepEqual(archiveRequests[2], {ids: ['u-cmo'], archived: false});
    await page.locator('#upd-view').click();
    await page.locator('[data-upd="u-cmo"]').waitFor();
    assert.equal(await page.locator('.upd-head h1').innerText(), 'Updates');
    await page.locator('[data-upd="u-cmo"] [data-upd-reply]').click();
    assert.equal(await page.locator('[data-upd="u-cmo"] textarea').inputValue(), 'Keep this reply draft');
    // Current loaded bot history is explicitly allowed to request archived rows.
    // A reply shows at once, then goes to the bot's chat (the hub does both).
    await page.locator('[data-upd="u-seo"] [data-upd-reply]').click();
    const box = page.locator('[data-upd="u-seo"] textarea');
    await box.fill('Pitch it to Northwind Homes first');
    let sendReply; holdReply = new Promise(r => { sendReply = r; });
    await box.press('Enter');
    await page.locator('[data-upd="u-seo"] .upd-msg.pending').waitFor();   // shown while the round trip is held
    holdReply = null; sendReply();
    await page.locator('[data-upd="u-seo"] .upd-msg:not(.pending)').waitFor();
    assert.deepEqual(posted.filter(x => x.path.endsWith('/reply')).at(-1), {path: '/api/v2/updates/u-seo/reply', body: {text: 'Pitch it to Northwind Homes first'}});
    assert.match(await page.locator('[data-upd="u-seo"] .upd-msg').last().innerText(), /Pitch it to Northwind Homes first/);
    assert.match(await page.locator('[data-upd="u-seo"] .upd-hint').innerText(), /Goes to AI SEO's chat too/);
    // A thread with replies opens with them.
    await page.locator('[data-upd="u-fin"] .upd-body').click();
    await page.locator('[data-upd="u-fin"] .upd-msg.bot').waitFor();
    if (shots) await page.screenshot({path: path.join(shots, 'updates-desktop-thread.png')});
    // Keyboard: j / k move the selection.
    await page.locator('#upd-feed').click({position: {x: 5, y: 5}});
    await page.keyboard.press('Escape');
    await page.evaluate(() => document.activeElement?.blur());
    // Coming back from Archive is a new visit, so the order depends on what was seen meanwhile; j j is the second card.
    const visitOrder = await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd));
    assert.deepEqual([...visitOrder].sort(), ['u-cmo', 'u-fin', 'u-seo']);
    await page.keyboard.press('j'); await page.keyboard.press('j');
    assert.equal(await page.locator('#upd-feed .upd-card.sel').getAttribute('data-upd'), visitOrder[1]);
    // Weekly: the toggle swaps the feed; Friday's cards say so.
    await page.locator('[data-upd-kind="weekly"]').click();
    await page.waitForFunction(() => location.hash === '#/updates?kind=weekly' && document.querySelector('[data-upd="w-seo"]'));
    assert.match(await page.locator('[data-upd="w-seo"] .upd-pill').innerText(), /Week in review/);
    // A week in review is five slides, one in view; the arrow, a dot or ← / → turns them. An older week shows its bullets.
    const deck = page.locator('[data-upd="w-seo"] [data-upd-deck]');
    assert.deepEqual(await deck.locator('.upd-slide-h').allInnerTexts(), ['Goal', 'KPIs', 'Done last week', 'Focus next week', 'Biggest blockers']);
    assert.equal(await deck.locator('.upd-kpi').count(), 2);
    assert.equal(await deck.locator('.upd-kpi .kspark polyline').count(), 2, 'each KPI with a series draws its line');
    const shown = () => deck.locator('[data-upd-go][aria-current]').getAttribute('data-upd-go');
    assert.equal(await shown(), '0');
    await deck.locator('[data-upd-step="1"]').click();
    await page.waitForFunction(() => document.querySelector('[data-upd="w-seo"] [data-upd-go="1"]')?.hasAttribute('aria-current'));
    if (shots) await page.screenshot({path: path.join(shots, 'updates-weekly-slides.png')});
    await deck.locator('[data-upd-go="4"]').click();
    await page.waitForFunction(() => document.querySelector('[data-upd="w-seo"] [data-upd-go="4"]')?.hasAttribute('aria-current'));
    assert.match(await deck.locator('.upd-slide').nth(4).innerText(), /Nothing blocking/);
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press('j');
    await page.keyboard.press('ArrowLeft');
    await page.waitForFunction(() => document.querySelector('[data-upd="w-seo"] [data-upd-go="3"]')?.hasAttribute('aria-current'));
    assert.equal(await page.locator('[data-upd="w-cmo"] [data-upd-deck]').count(), 0);
    assert.match(await page.locator('[data-upd="w-cmo"] .upd-body').innerText(), /posted as bullets/);
    // Snappy: coming back to Daily paints from this tab's cache before the network answers, and the
    // unread one (AI CMO, marked unread above) now comes first.
    // Seeing AI CMO in Archive or Inbox above may have marked it read; another device marks it unread again.
    keepUnread = 'u-cmo'; updates.find(u => u.id === 'u-cmo').read = false;
    let release; holdList = new Promise(r => { release = r; });
    await page.evaluate(() => { location.hash = '#/updates'; });
    await page.locator('[data-upd="u-seo"]').waitFor();   // painted while the network reply is still held
    holdList = null; release();
    await page.waitForFunction(() => document.querySelector('#upd-feed .upd-card')?.dataset.upd === 'u-cmo');
    keepUnread = null;
    assert.deepEqual(errors, []);
    await page.close();

    // An older manager card's From: link opens the update it was written from, even when that one is past the feed's
    // page and its author has posted since (their page shows their latest): it loads under the card and is selected.
    // My bots doesn't hide it; a link that can't load goes to its author as before.
    const cmoCard = updates.find(u => u.id === 'u-cmo');
    cmoCard.inputs = [{id: 'u-seo-old', author: 'seo'}, {id: 'u-game-old', author: 'game'}, {id: 'u-fin-gone', author: 'finance'}];
    const linked = await open({width: 1280, height: 900});
    const lp = linked.page;
    await lp.goto('http://tico-ui.test/#/updates');
    const cmoFrom = lp.locator('[data-upd="u-cmo"] .upd-from');
    await cmoFrom.waitFor();
    await cmoFrom.locator('[data-upd-input="u-seo-old"]').click();
    await lp.locator('#upd-feed [data-upd="u-seo-old"].sel').waitFor();
    assert.match(await lp.locator('[data-upd="u-seo-old"] .upd-body').innerText(), /Outlined the checklist page/, 'the linked update, not the newer one');
    assert.match(lp.url(), /#\/updates$/, 'the link stays on the page');
    let order = await lp.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd));
    assert.equal(order[order.indexOf('u-cmo') + 1], 'u-seo-old', 'under the card that links it: ' + order);
    assert.equal(await lp.locator('#upd-mine').getAttribute('aria-pressed'), 'true');
    await cmoFrom.locator('[data-upd-input="u-game-old"]').click();
    await lp.locator('#upd-feed [data-upd="u-game-old"].sel').waitFor();
    order = await lp.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd));
    assert.equal(order.filter(id => id === 'u-seo-old').length, 1, 'shown once: ' + order);
    await cmoFrom.locator('[data-upd-input="u-seo-old"]').click();      // already under it: selected, not loaded again
    await lp.locator('#upd-feed [data-upd="u-seo-old"].sel').waitFor();
    assert.deepEqual(oneAsked.filter(id => id.endsWith('-old')), ['u-seo-old', 'u-game-old']);
    for (let n = 0; n < 2; n++) await lp.locator('[data-upd="u-cmo"] [data-upd-toggle]').click();   // each writes the cache
    const cards = () => lp.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd));
    const loaded = () => lp.waitForFunction(() => UPD && !UPD.loading && !UPD.fromCache);
    // On a repeat visit the cached cards paint first; a first page that lands after the link opened its update keeps
    // that update under its card and selected. Load older then brings it again with an older one: each shows once.
    pageTwo = [older['u-seo-old'], {id: 'u-fin-old', bot: 'finance', kind: 'daily', day: day(-2 * 86400e3), headline: 'Paid the rent',
      body: '- Paid the October rent', created_at: iso(-50 * hour), updated_at: iso(-50 * hour), read: true, replies: 0}];
    await lp.evaluate(() => { location.hash = '#/updates?view=archive'; });
    await lp.waitForFunction(() => UPD?.view === 'archive' && !UPD.loading);
    let releaseFirst; holdList = new Promise(r => { releaseFirst = r; });
    const fresh = {id: 'u-seo-new', bot: 'seo', kind: 'daily', day: day(0), headline: 'New', body: '- Posted while you read',
                   created_at: iso(0), updated_at: iso(0), read: false, replies: 0};
    updates.push(fresh);                                      // the held first page brings a newer card that sorts first
    await lp.evaluate(() => { location.hash = '#/updates'; });
    await cmoFrom.waitFor();                                  // painted from the cache while the first page is held
    assert.equal(await lp.evaluate(() => UPD.fromCache), true);
    assert.equal(await lp.locator('#upd-feed [data-upd="u-seo-old"]').count(), 0, 'a link\'s update is not cached');
    await cmoFrom.locator('[data-upd-input="u-seo-old"]').click();
    await lp.locator('#upd-feed [data-upd="u-seo-old"].sel').waitFor();
    holdList = null; releaseFirst();
    await loaded();
    updates.splice(updates.indexOf(fresh), 1);
    order = await cards();
    assert.equal(order[order.indexOf('u-cmo') + 1], 'u-seo-old', 'still under its card after the first page: ' + order);
    assert.equal(await lp.locator('#upd-feed [data-upd="u-seo-old"].sel').count(), 1, 'and still selected');
    await lp.locator('[data-upd-more]').click();
    await lp.locator('#upd-feed [data-upd="u-fin-old"]').waitFor();
    order = await cards();
    assert.deepEqual([order.filter(id => id === 'u-seo-old').length, order.filter(id => id === 'u-fin-old').length], [1, 1], 'once each: ' + order);
    pageTwo = null;
    // Leaving Updates while the linked update loads: its late reply neither moves the page nor adds a card.
    let releaseOne; holdOne = new Promise(r => { releaseOne = r; });
    await lp.evaluate(() => { location.hash = '#/updates?view=archive'; });
    await lp.waitForFunction(() => UPD?.view === 'archive' && !UPD.loading);
    await lp.evaluate(() => { location.hash = '#/updates'; });
    await loaded();
    const asked = oneAsked.length;
    await cmoFrom.locator('[data-upd-input="u-game-old"]').click();
    for (const end = Date.now() + t(10000); oneAsked.length === asked && Date.now() < end;) await new Promise(r => setTimeout(r, 20));
    assert.equal(oneAsked.at(-1), 'u-game-old');
    await lp.evaluate(() => { location.hash = '#/updates?view=archive'; });
    await lp.waitForFunction(() => UPD?.view === 'archive' && !UPD.loading);
    const late = lp.waitForResponse(r => r.url().endsWith('/api/v2/updates/u-game-old'));
    holdOne = null; releaseOne();
    await late;
    await lp.evaluate(() => new Promise(r => setTimeout(r, 200)));
    assert.equal(await lp.evaluate(() => location.hash), '#/updates?view=archive', 'the late reply does not navigate');
    assert.equal(await lp.locator('#upd-feed [data-upd="u-game-old"]').count(), 0, 'nor adds a card to the new page');
    await lp.evaluate(() => { location.hash = '#/updates'; });
    await cmoFrom.waitFor();
    await cmoFrom.locator('[data-upd-input="u-fin-gone"]').click();
    await lp.waitForFunction(() => location.hash === '#/bot/finance');
    assert.deepEqual(linked.errors.filter(e => !/404/.test(e)), []);
    await lp.close();
    delete cmoCard.inputs;

    // Phone: Tasks and Updates are in the bottom bar; Tasks stays in More too (the tour points at it there).
    const phone = await open({width: 390, height: 844});
    const p = phone.page;
    await p.goto('http://tico-ui.test/#/updates');
    await p.locator('#upd-feed .upd-card').first().waitFor();
    assert.deepEqual(await p.locator('#mobile-nav .mobile-nav-label').allInnerTexts(), ['Team', 'Search', 'Ask Tico', 'Tasks', 'Updates', 'More']);
    // Six items; Ask Tico (the Liquid Dot) sits in the middle of the bar, and the items on each side of it share their
    // side evenly.
    const nav = () => p.locator('#mobile-nav .mobile-nav-item').evaluateAll(items => items.map(el => { const r = el.getBoundingClientRect(); return {w: r.width, mid: r.left + r.width / 2}; }));
    const even = list => Math.max(...list.map(i => i.w)) - Math.min(...list.map(i => i.w)) < 1;
    const items = await nav();
    assert.equal(items.length, 6);
    assert.ok(Math.abs(items[2].mid - 195) < 1, `Ask Tico in the middle: ${items[2].mid}`);
    assert.ok(even(items.slice(0, 2)) && even(items.slice(3)), 'each side spaced evenly');
    // The six fit a 360px phone: no label is cut and the bar does not scroll sideways.
    await p.setViewportSize({width: 360, height: 800});
    await p.waitForFunction(() => document.querySelector('#mobile-nav').getBoundingClientRect().width <= 360);
    assert.equal(await p.locator('#mobile-nav').evaluate(nav => nav.scrollWidth <= nav.clientWidth &&
      [...nav.querySelectorAll('.mobile-nav-label')].every(l => { const r = l.getBoundingClientRect(), item = l.parentElement.getBoundingClientRect();
        return r.left >= item.left && r.right <= item.right; })), true, 'the bar fits 360px');
    if (shots) await p.screenshot({path: path.join(shots, 'updates-phone-360.png')});
    await p.setViewportSize({width: 390, height: 844});
    await p.waitForFunction(() => document.querySelector('#mobile-nav').getBoundingClientRect().width > 380);
    assert.equal(await p.locator('#mobile-nav [data-nav="overview"]').count(),0);
    // Day, week, unread and my bots are icons on a phone.
    for (const sel of ['[data-upd-kind="daily"]', '[data-upd-kind="weekly"]', '#upd-mine']) {
      assert.equal(await p.locator(sel + ' .nav-icon').isVisible(), true, sel + ' shows its icon');
      assert.equal(await p.locator(sel + ' .upd-lbl').isVisible(), false, sel + ' hides its word');
    }
    assert.equal(await p.locator('#mobile-nav [data-nav="updates"]').evaluate(el => el.classList.contains('cur')), true);
    const card = await p.locator('#upd-feed .upd-card').first().boundingBox();
    assert(card.x >= 8 && card.x + card.width <= 390 - 8, 'cards fit the phone with a gutter');
    if (shots) await p.screenshot({path: path.join(shots, 'updates-phone.png')});
    // A week's slides fit the phone and swipe sideways inside the card.
    await p.evaluate(() => { location.hash = '#/updates?kind=weekly'; });
    const track = p.locator('[data-upd="w-seo"] .upd-track');
    await track.waitFor();
    const deckBox = await track.boundingBox();
    assert(deckBox.x >= 8 && deckBox.x + deckBox.width <= 390 - 8, 'the slides fit the phone with a gutter');
    assert(await track.evaluate(el => el.scrollWidth >= el.clientWidth * 4.9), 'five slides side by side');
    if (shots) await p.screenshot({path: path.join(shots, 'updates-phone-weekly.png')});
    await track.evaluate(el => el.scrollTo({left: el.clientWidth * 2}));
    await p.waitForFunction(() => document.querySelector('[data-upd="w-seo"] [data-upd-go="2"]')?.hasAttribute('aria-current'));
    if (shots) await p.screenshot({path: path.join(shots, 'updates-phone-weekly-done.png')});
    await p.evaluate(() => { location.hash = '#/updates'; });
    await p.locator('[data-upd="u-seo"]').waitFor();
    await p.locator('#upd-mine').click();
    await p.locator('[data-upd="u-game"]').waitFor();
    await p.waitForFunction(() => UPD && !UPD.mine && !UPD.loading && !UPD.fromCache);   // this scope's load has repainted
    const dispatchDrag = async (target, dx, dy) => {
      const box = await target.boundingBox(); assert(box, 'gesture target is visible');
      const x = box.x + Math.min(20, box.width / 3), y = box.y + Math.min(20, box.height / 2);
      await target.dispatchEvent('pointerdown', {pointerId: 7, pointerType: 'touch', clientX: x, clientY: y});
      await target.dispatchEvent('pointerup', {pointerId: 7, pointerType: 'touch', clientX: x + dx, clientY: y + dy});
    };
    let swipeCount = archiveRequests.length;
    const game = p.locator('[data-upd="u-game"]');
    await dispatchDrag(game.locator('.upd-body'), 2, -120);   // vertical scroll is not archive
    assert.equal(archiveRequests.length, swipeCount);
    await dispatchDrag(game.locator('.upd-body a'), -140, 0); // links keep their normal navigation
    assert.equal(archiveRequests.length, swipeCount);
    await game.locator('[data-upd-reply]').click();
    await dispatchDrag(game.locator('textarea'), -140, 0);   // reply input is interactive
    assert.equal(archiveRequests.length, swipeCount);
    await dispatchDrag(game, -140, 0);                       // left swipe on the card archives
    await p.locator('[data-upd="u-game"]').waitFor({state: 'detached'});
    assert.deepEqual(archiveRequests.at(-1), {ids: ['u-game'], archived: true});
    // The swipe's click guard applies only to that card; the Archive header remains usable.
    await p.locator('#upd-view').click();
    await p.waitForFunction(() => location.hash === '#/updates?view=archive' && document.querySelector('[data-upd="u-game"]'));
    await p.locator('[data-upd="u-game"] [data-upd-archive]').click();
    await p.locator('[data-upd="u-game"]').waitFor({state: 'detached'});
    assert.deepEqual(archiveRequests.at(-1), {ids: ['u-game'], archived: false});
    await p.locator('#upd-view').click(); await p.locator('[data-upd="u-game"]').waitFor();
    const beforeDeckGesture = archiveRequests.length;
    await p.evaluate(() => { location.hash = '#/updates?kind=weekly'; });
    const phoneDeck = p.locator('[data-upd="w-seo"] [data-upd-deck]'); await phoneDeck.waitFor();
    await dispatchDrag(phoneDeck, -140, 0);                  // the weekly carousel keeps its swipe
    assert.equal(archiveRequests.length, beforeDeckGesture, 'weekly slide gesture never archives');
    await p.evaluate(() => { location.hash = '#/updates'; });
    await p.locator('[data-upd="u-seo"]').waitFor();
    await p.locator('#mobile-more').click();
    await p.locator('body.drawer').waitFor();
    assert.equal(await p.locator('.side-scroll [data-nav="tasks"]').isVisible(), true, 'Tasks is in More');
    assert.equal(await p.locator('.side-scroll [data-nav="overview"]').isVisible(), true, 'Overview remains in More');
    assert.deepEqual(phone.errors, []);
    await p.close();

    // Nothing yet: one short line, with no sentence under it and no Chat with us (Support is the ? in the sidebar).
    updates = []; pageTwo = null;
    const none = await open({width: 1280, height: 900});
    await none.page.goto('http://tico-ui.test/#/updates');
    const empty = none.page.locator('#upd-feed .upd-empty');
    await empty.waitFor();
    assert.equal(await empty.evaluate(el => [...el.childNodes].filter(n => !n.classList?.contains('nav-icon')).map(n => n.textContent).join('').trim()), 'No updates yet.');
    assert.equal(await none.page.locator('#main .talk-line, #main [data-chat-with-us]').count(), 0, 'no Chat with us');
    assert.deepEqual(none.errors, []);
    await none.page.close();
    console.log('PASS: Updates first in the rail with an unread badge, just the bullets (no title, sections, greeting, day headers or missed list), seen-is-read in one batched request, mark unread without re-sorting, unread first on the next visit, an instant reply that goes to the bot\'s chat, threads, j/k, Daily/Weekly toggle, a week in review as five swipeable slides with KPI lines, cached paint, and on a phone Tasks and Updates in a six-item bottom bar (with Support) that fits 360px, with Tasks also in More.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
