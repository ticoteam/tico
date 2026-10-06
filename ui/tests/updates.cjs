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
const shots = process.env.TICO_SCREENSHOT_DIR;
const now = Date.now(), iso = ms => new Date(now + ms).toISOString(), hour = 3600e3;
const day = ms => new Date(now + ms).toLocaleDateString('en-CA', {timeZone: 'America/Los_Angeles'});
const bots = [['seo', 'AI SEO'], ['finance', 'Finance'], ['cmo', 'AI CMO'], ['game', 'Temp Game']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', can_chat: true, team: 'marketing', operator: name === 'game' ? 'ben' : 'ana'}));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  const posted = [];
  let updates = [
    {id: 'u-seo', bot: 'seo', kind: 'daily', day: day(0), headline: 'Published the vacation rental checklist page',
     body: '- Published the vacation rental checklist page, 1,240 words\n- Linked it from six older posts\n- Pitching it to three host newsletters next',
     created: iso(-2 * hour), updated: iso(-2 * hour), read: false, replies: 0},
    {id: 'u-fin', bot: 'finance', kind: 'daily', day: day(0), headline: 'Brex balance is fine; Canva retry scheduled',
     body: '- Brex has $13,000 available, so no cash warning\n- Rechecking the Canva payment on Oct 16', created: iso(-3 * hour),
     updated: iso(-3 * hour), read: false, replies: 1},
    {id: 'u-game', bot: 'game', kind: 'daily', day: day(0), headline: 'x', body: '- Ben\'s bot shipped a level', created: iso(-hour), updated: iso(-hour), read: true, replies: 0},
    {id: 'u-cmo', bot: 'cmo', kind: 'daily', day: day(-86400e3), headline: 'Drafted October content plan',
     body: '- Drafted the October content plan', created: iso(-26 * hour), updated: iso(-26 * hour), read: true, replies: 0},
  ];
  const weekly = [{id: 'w-seo', bot: 'seo', kind: 'weekly', day: day(0), headline: 'Week: 4 pages shipped, rankings up on 2 of 3 goals',
    body: '- Shipped four pages this week\n- Organic signups are on track', created: iso(-hour), updated: iso(-hour), read: false, replies: 0,
    slides: {goal: 'Grow organic signups to 400 a month; at 310 and on pace.',
             tracked: [{name: 'Organic signups', unit: 'signups', value: 310, spark: [240, 262, 281, 310], status: 'green', target_label: '400 by Dec 31', fresh: true}],
             kpis: [{name: 'Pages shipped', value: '4', series: [1, 2, 2, 4]}],
             done: ['Shipped four landing pages', 'Linked them from six older posts'], focus: ['Pitch the checklist to three newsletters'], blockers: []}},
    {id: 'w-cmo', bot: 'cmo', kind: 'weekly', day: day(-7 * 86400e3), headline: 'Old bullets', body: '- An older week, posted as bullets',
     created: iso(-170 * hour), updated: iso(-170 * hour), read: true, replies: 0}];
  const threads = {'u-fin': [{id: 'm1', from_actor: 'human:ana', body: 'Re your update "Brex balance is fine": Thanks, flag anything under $10k', created: iso(-2 * hour)},
                             {id: 'm2', from_actor: 'bot:finance', body: 'Will do.', created: iso(-hour)}]};
  const feed = (kind, unread) => {
    const list = (kind === 'weekly' ? weekly : updates);
    return {updates: list, missed: kind === 'weekly' ? [] : [{bot: 'game', kind: 'daily', day: day(0), reason: 'its run stopped part-way'}],
            unread: list.filter(u => !u.read).length, next_before: null, today: {posted: 2, missed: 1, queued: 1}};
  };
  const open = async (viewport) => {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true});
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      if (p === '/api/v2/updates/unread') return json({unread: updates.filter(u => !u.read).length});
      if (p === '/api/v2/updates' && req.method() === 'GET') {
        await new Promise(r => setTimeout(r, 120));        // a real round trip, so the cache paint shows first
        return json(feed(url.searchParams.get('kind')));
      }
      if (p === '/api/v2/updates/read') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        for (const u of updates) if (body.all || body.ids.includes(u.id)) u.read = body.read !== false;
        return json({marked: body.ids.length, read: body.read !== false});
      }
      const reply = p.match(/^\/api\/v2\/updates\/([^/]+)\/reply$/);
      if (reply) {
        const body = req.postDataJSON(); posted.push({path: p, body});
        await new Promise(r => setTimeout(r, 250));
        const msg = {id: 'm-new', from_actor: 'human:ana', body: `Re your update "x": ${body.text}`, created: new Date().toISOString(), conversation_id: 'c-seo'};
        threads[reply[1]] = [...(threads[reply[1]] || []), msg];
        return json({message: msg, thread: threads[reply[1]]});
      }
      const one = p.match(/^\/api\/v2\/updates\/([^/]+)$/);
      if (one) return json({update: updates.find(u => u.id === one[1]), thread: threads[one[1]] || []});
      return json({});
    });
    return {page, errors};
  };
  try {
    // Desktop: Overview leads the rail; Updates keeps its unread badge and default route.
    const {page, errors} = await open({width: 1280, height: 900});
    await page.goto('http://tico-ui.test/#/updates');
    await page.locator('#upd-feed .upd-card').first().waitFor();
    assert.equal(await page.locator('.side-scroll .nav-link:visible').first().getAttribute('data-nav'), 'overview');
    await page.waitForFunction(() => document.querySelector('.side-scroll [data-upd-badge]')?.textContent === '2');
    // Just the updates, each only its bullets: no title, no sections, no greeting,
    // no day headers, no who did not report, no More / Less.
    // My bots is on by default: Ben's bot's update is hidden until it is off.
    assert.equal(await page.locator('#upd-mine').getAttribute('aria-pressed'), 'true');
    assert.deepEqual(await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd)), ['u-seo', 'u-fin', 'u-cmo']);
    await page.locator('#upd-mine').click();
    assert.deepEqual(await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd)), ['u-seo', 'u-fin', 'u-game', 'u-cmo']);
    await page.locator('#upd-mine').click();
    assert.equal(await page.evaluate(() => localStorage.getItem('tico.updates.mine')), '1');
    assert.equal(await page.locator('#upd-mine .upd-lbl').isVisible(), true, 'words on a desktop');
    assert.equal(await page.locator('#upd-feed .upd-headline, #upd-feed .upd-day, #upd-today, .upd-missed, [data-upd-open], .upd-body.clamp').count(), 0);
    assert.match(await page.locator('[data-upd="u-seo"] .upd-body').innerText(), /Published the vacation rental checklist page/);
    // Seen is read: the visible unread cards are marked in one small request.
    await page.waitForFunction(() => !document.querySelector('#upd-feed .upd-card.unread'), null, {timeout: 5000});
    await page.waitForFunction(() => document.querySelector('.side-scroll [data-upd-badge]')?.hidden === true);
    for (const end = Date.now() + 3000; !posted.some(x => x.path === '/api/v2/updates/read') && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    const reads = posted.filter(x => x.path === '/api/v2/updates/read');
    assert.equal(reads.length, 1, 'batched: ' + JSON.stringify(reads));
    assert.deepEqual(reads[0].body.ids.sort(), ['u-fin', 'u-seo']);
    if (shots) await page.screenshot({path: path.join(shots, 'updates-desktop.png')});
    // No Unread filter. Unread first, then newest; marking one unread (or read)
    // never moves anything while the page is open.
    assert.equal(await page.locator('#upd-unread').count(), 0);
    await page.locator('[data-upd="u-cmo"] [data-upd-toggle]').click();
    assert.deepEqual(await page.locator('#upd-feed .upd-card').evaluateAll(els => els.map(e => e.dataset.upd)), ['u-seo', 'u-fin', 'u-cmo'],
      'nothing re-sorts while you are on the page');
    // A reply shows at once, then goes to the bot's chat (the hub does both).
    await page.locator('[data-upd="u-seo"] [data-upd-reply]').click();
    const box = page.locator('[data-upd="u-seo"] textarea');
    await box.fill('Pitch it to Northwind Homes first');
    const t0 = Date.now();
    await box.press('Enter');
    await page.locator('[data-upd="u-seo"] .upd-msg.pending').waitFor();
    assert(Date.now() - t0 < 200, 'the reply shows before the round trip');
    await page.locator('[data-upd="u-seo"] .upd-msg:not(.pending)').waitFor();
    assert.deepEqual(posted.at(-1), {path: '/api/v2/updates/u-seo/reply', body: {text: 'Pitch it to Northwind Homes first'}});
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
    await page.keyboard.press('j'); await page.keyboard.press('j');
    assert.equal(await page.locator('#upd-feed .upd-card.sel').getAttribute('data-upd'), 'u-fin');
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
    await page.evaluate(() => { location.hash = '#/updates'; });
    await page.waitForFunction(() => document.querySelector('[data-upd="u-seo"]'), null, {timeout: 60});
    await page.waitForFunction(() => document.querySelector('#upd-feed .upd-card')?.dataset.upd === 'u-cmo', null, {timeout: 3000});
    assert.deepEqual(errors, []);
    await page.close();

    // Phone: Updates is in the bottom bar where Tasks was; Tasks is in More.
    const phone = await open({width: 390, height: 844});
    const p = phone.page;
    await p.goto('http://tico-ui.test/#/updates');
    await p.locator('#upd-feed .upd-card').first().waitFor();
    assert.deepEqual(await p.locator('#mobile-nav .mobile-nav-label').allInnerTexts(), ['Team', 'Search', 'Updates', 'More']);
    const navWidths=await p.locator('#mobile-nav .mobile-nav-item').evaluateAll(items=>items.map(el=>el.getBoundingClientRect().width));
    assert.equal(navWidths.length,4);
    assert.ok(Math.max(...navWidths)-Math.min(...navWidths)<1,'four evenly spaced navigation items');
    assert.equal(await p.locator('#mobile-nav [data-nav="overview"]').count(),0);
    // Day, week, unread and my bots are icons on a phone.
    for (const sel of ['[data-upd-kind="daily"]', '[data-upd-kind="weekly"]', '#upd-mine']) {
      assert.equal(await p.locator(sel + ' .nav-icon').isVisible(), true, sel + ' shows its icon');
      assert.equal(await p.locator(sel + ' .upd-lbl').isVisible(), false, sel + ' hides its word');
    }
    assert.equal(await p.locator('#mobile-nav [data-nav="updates"]').evaluate(el => el.classList.contains('cur')), true);
    const card = await p.locator('#upd-feed .upd-card').first().boundingBox();
    assert(card.x >= 8 && card.x + card.width <= 390 - 8, 'cards fit the phone with a gutter');
    if (shots) { await p.waitForTimeout(200); await p.screenshot({path: path.join(shots, 'updates-phone.png')}); }
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
    await p.locator('#mobile-more').click();
    await p.locator('body.drawer').waitFor();
    assert.equal(await p.locator('.side-scroll [data-nav="tasks"]').isVisible(), true, 'Tasks is in More');
    assert.equal(await p.locator('.side-scroll [data-nav="overview"]').isVisible(), true, 'Overview remains in More');
    assert.deepEqual(phone.errors, []);
    await p.close();
    console.log('PASS: Updates first in the rail with an unread badge, just the bullets (no title, sections, greeting, day headers or missed list), seen-is-read in one batched request, mark unread without re-sorting, unread first on the next visit, an instant reply that goes to the bot\'s chat, threads, j/k, Daily/Weekly toggle, a week in review as five swipeable slides with KPI lines, cached paint, and on a phone Updates in the bottom bar with Tasks in More.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
