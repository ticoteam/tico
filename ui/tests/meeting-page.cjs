// One saved meeting (ui/app/meetings.js meetPaint): a header with faces and Team / Private, Share and ⋯ beside
// Close, one task list with each task once, Notes edited in place, and the transcript beside it (below on a phone).
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const ago = hours => new Date(Date.now() - hours * 36e5).toISOString();
const PEOPLE = [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben', email: 'ben@acme.example'}];
const BOTS = [{id: 'ops', name: 'ops', display_name: 'Ops', status: 'active', schedules: []},
  {id: 'sales', name: 'sales', display_name: 'Sales', status: 'active', schedules: []}];
const base = (id, extra = {}) => ({id, title: 'Weekly ops sync', source: 'tico-live', client: 'tico-recorder', kind: 'meeting', status: 'done',
  review_state: 'live', started: ago(3), created: ago(3), duration_ms: 1800000, can_edit: true, version: 3, private: false,
  participants: [{name: 'Ana', email: 'ana@acme.example'}, {name: 'Ben', email: 'ben@acme.example'}],
  note: 'Ship on Friday.', notes: '', transcript_readable: '', outbox: {doc: [], task: [], feature: []},
  turns: [{speaker: 'Ana', start_ms: 5000, text: 'We ship the pricing page on Friday.'}, {speaker: 'Ben', start_ms: 9000, text: 'I will book the review.'}], ...extra});

function world() {
  return {
    meetings: {
      // Shared with Ops: its delivery task and follow-up, one created item and one linked task, overlapping on purpose.
      m1: base('m1', {live_meeting_id: 'lm1', sent: {task: 't-sent', bot: 'ops'}, delivery: {status: 'sent', destination: 'ops', task_id: 't-sent'},
        outcome: {bot: 'ops', task_id: 't-sent', status: 'doing', summary: 'Reading the call.', tasks: [{id: 't-follow', title: 'Draft the quote', status: 'open'}, {id: 't-made', title: 'Book the review', status: 'doing'}]}}),
      m2: base('m2', {title: 'Renewal call', private: true}),
    },
    items: {
      m1: [{id: 'i1', version: 1, section: 'task', text: 'Send Dana the pricing sheet', detail: {owner: 'human:ben'}, status: 'proposed', created_by: 'brain'},
           {id: 'i2', version: 1, section: 'task', text: 'Book the review', detail: {owner: 'human:ben'}, status: 'pushed', result_ref: 't-made'},
           {id: 'i3', version: 1, section: 'task', text: 'Rename the repo', detail: {}, status: 'dismissed', updated_by: 'human:ana'}],
      m2: [],
    },
    linked: {m1: [{task_id: 't-made', number: 12, title: 'Book the review', status: 'doing'}], m2: []},
    sent: [], edits: [],
  };
}
async function open(browser, viewport, w, theme) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760, colorScheme: theme});
  await context.addInitScript(value => { localStorage.setItem('tico.theme', value); }, theme);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    const body = () => JSON.parse(route.request().postData() || '{}');
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
    if (p === '/api/humans') return json({people: PEOPLE});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/meetings/sources') return json({sources: []});
    if (p === '/api/v2/meetings') return json({meetings: Object.values(w.meetings), pending_count: 0});
    let m = p.match(/^\/api\/meetings\/(m\d)$/);
    if (m) return json(w.meetings[m[1]]);
    if ((m = p.match(/^\/api\/meetings\/(m\d)\/items$/))) return json({id: m[1], can_send: true, audience: null, sections: {task: w.items[m[1]], doc: [], feature: []}});
    if ((m = p.match(/^\/api\/v2\/meetings\/(m\d)\/tasks$/))) return json({tasks: w.linked[m[1]], next_cursor: null});
    if (p === '/api/v2/live-meetings/lm1') return json({id: 'lm1', bots: [{bot: 'ops', name: 'Ops'}],
      chat: [{id: 'c1', actor: 'human:ana', role: 'human', text: 'Ops, note the Friday date.', at_ms: 6000}]});
    if ((m = p.match(/^\/api\/meetings\/(m\d)\/send$/)) && method === 'POST') {
      const b = body(); w.sent.push({id: m[1], ...b});
      Object.assign(w.meetings[m[1]], {sent: {task: 't-new', bot: b.bot_id}, delivery: {status: 'sent', destination: b.bot_id, task_id: 't-new'}});
      return json({bot: b.bot_id, task: 't-new'});
    }
    if ((m = p.match(/^\/api\/meetings\/(m\d)\/edit$/)) && method === 'POST') {
      const b = body(); w.edits.push({id: m[1], ...b});
      const rec = w.meetings[m[1]]; Object.assign(rec, b); rec.version++;
      return json({version: rec.version});
    }
    return json(unrouted(p));
  });
  return {page, errors, context};
}
async function shot(page, name) {
  if (!process.env.TICO_MEETINGS_SCREENSHOTS) return;
  fs.mkdirSync(process.env.TICO_MEETINGS_SCREENSHOTS, {recursive: true});
  await page.screenshot({path: path.join(process.env.TICO_MEETINGS_SCREENSHOTS, name + '.png'), animations: 'disabled'});
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [device, viewport, theme] of [['desktop', {width: 1280, height: 860}, 'light'], ['desktop', {width: 1280, height: 860}, 'dark'],
                                             ['phone', {width: 390, height: 844}, 'light'], ['phone', {width: 390, height: 844}, 'dark']]) {
      const where = `${device}-${theme}`;
      const w = world();
      const {page, errors, context} = await open(browser, viewport, w, theme);
      await page.goto('https://tico-ui.test/#/meetings?meeting=m1');
      const detail = page.locator('#notes-modal[open] #notes-detail');
      await detail.locator('[data-linked-task="t-made"]').waitFor();
      await detail.locator('[data-pending="i1"]').waitFor();
      // Faces: the people and the bot that sat in, each named on hover or tap.
      await page.locator('#meet-faces [data-face-bot="ops"]').waitFor();
      assert.deepEqual(await page.locator('#meet-faces .mp-face').evaluateAll(els => els.map(e => e.dataset.name)), ['Ana', 'Ben', 'Ops'], where);
      await shot(page, `meeting-page-${where}`);
      // One list: the created item, the linked task and the bot's follow-up that are the same task show once.
      const rows = await detail.locator('#meet-sections li .t').allInnerTexts();
      assert.equal(rows.filter(t => t === 'Book the review').length, 1, `${where}: no duplicate task`);
      assert.ok(rows.includes('Draft the quote') && rows.includes('Review by Ops'), `${where}: made tasks listed: ${rows}`);
      // The status and the next part are separated, never run together ("doingOpen").
      const linked = await detail.locator('[data-linked-task="t-made"]').innerText();
      assert.match(linked, /#12 · Doing/, where);
      assert.doesNotMatch(linked, /Open/, where);
      // Dismissed items wait behind one toggle.
      assert.equal(await detail.getByText('Rename the repo').count(), 0, where);
      await detail.getByRole('button', {name: 'Dismissed (1)'}).click();
      await detail.getByText('Rename the repo').waitFor();
      // The retired sections and bottom bar are gone.
      for (const gone of ['#meet-send', '#meet-dest', '#meet-instructions', '#live-comments', '[data-sec=doc]', '[data-sec=feature]', '[data-note-section]'])
        assert.equal(await page.locator(gone).count(), 0, `${where}: ${gone} is gone`);
      for (const text of [/Doc updates/, /Feature requests/, /Meeting context/, /Not sent to a bot yet/, /Add instructions/, /Comments/])
        assert.doesNotMatch(await detail.innerText(), text, where);
      // The transcript is shown, with the chat a tab beside it.
      await detail.getByText('We ship the pricing page on Friday.').waitFor();
      await detail.getByRole('tab', {name: 'Chat'}).click();
      await detail.getByText('Ops, note the Friday date.').waitFor();
      await detail.getByRole('tab', {name: 'Transcript'}).click();
      await detail.getByRole('button', {name: 'Search transcript'}).click();
      await detail.getByRole('searchbox', {name: 'Find in transcript'}).fill('review');
      assert.equal(await detail.locator('[data-tbody] mark').count(), 1, where);
      // Nothing scrolls sideways.
      const overflow = await page.evaluate(() => [document.documentElement.scrollWidth - innerWidth,
        document.querySelector('#notes-detail').scrollWidth - document.querySelector('#notes-detail').clientWidth]);
      assert.ok(overflow.every(n => n <= 1), `${where}: sideways scroll ${overflow}`);
      if (device === 'desktop') {
        const [main, side] = await Promise.all(['.mp-main', '#meet-side'].map(s => detail.locator(s).boundingBox()));
        assert.ok(side.x >= main.x + main.width - 1, `${where}: transcript is a right column`);
      } else {
        const [main, side] = await Promise.all(['.mp-main', '#meet-side'].map(s => detail.locator(s).boundingBox()));
        assert.ok(side.y >= main.y + main.height - 1, `${where}: transcript is below`);
      }
      // A shared meeting offers only the bot it went to.
      await page.getByRole('button', {name: 'Share'}).click();
      assert.equal(await page.locator('[data-share-bot]').count(), 1, where);
      assert.equal(await page.locator('[data-share-bot="ops"]').isDisabled(), true, where);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#meet-share').evaluate(d => d.open), false, `${where}: Escape closes the menu first`);
      assert.equal(await page.locator('#notes-modal').evaluate(d => d.open), true, where);

      if (device === 'desktop' && theme === 'light') {
        // Notes save in place.
        const note = page.getByRole('textbox', {name: 'Notes'});
        await note.fill('Ship on Friday. Ben books the review.');
        await note.blur();
        for (let i = 0; i < 100 && !w.edits.length; i++) await page.waitForTimeout(50);
        assert.deepEqual(w.edits[0], {id: 'm1', note: 'Ship on Friday. Ben books the review.', version: 3});
        // Share adds a bot to a meeting not yet shared, and the bot joins the faces.
        await page.goto('https://tico-ui.test/#/meetings?meeting=m2');
        await page.locator('#meet-heading', {hasText: 'Renewal call'}).waitFor();
        assert.equal(await page.locator('[data-vis=private]').getAttribute('aria-pressed'), 'true');
        await page.getByRole('button', {name: 'Share'}).click();
        await page.getByRole('menuitemcheckbox', {name: 'Sales'}).click();
        await page.locator('#meet-faces [data-face-bot="sales"]').waitFor();
        assert.deepEqual(w.sent, [{id: 'm2', bot_id: 'sales', instructions: ''}]);
        // Team / Private writes through edit.
        await page.locator('[data-vis=team]').click();
        await page.locator('[data-vis=team][aria-pressed=true]').waitFor();
        assert.deepEqual(w.edits.at(-1), {id: 'm2', private: false, version: 3});
        // ⋯ holds Delete.
        await page.getByRole('button', {name: 'More'}).click();
        await page.getByRole('menuitem', {name: 'Delete'}).waitFor();
      }
      assert.deepEqual(errors, [], where);
      await context.close();
    }
    console.log('meeting-page: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
