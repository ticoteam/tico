// The Meetings page (ui/app/meetings.js pageNotes, ui/meeting-importers.js): a header with search and one
// "Add notes" button, a strip of source tiles (Granola, Zoom, Google Meet, Close) that each say
// Connect or Connected, an empty state of those tiles alone, filters and rows only once there is a
// meeting, setup in a dialog, and a transcript upload inside Add notes. Light and dark, desktop and phone.
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {replay: recorderLiveSharing} = require('../../recorder/tests/live-sharing.cjs');

const ago = hours => new Date(Date.now() - hours * 36e5).toISOString();
const meeting = (id, title, source, hours, extra = {}) => ({id, title, source, kind: 'meeting', status: 'done', started_at: ago(hours),
  created_at: ago(hours), duration_ms: 1800000, participants: [{name: 'Ana', email: 'ana@acme.example'}, {name: 'Ben'}, {name: 'Cara'}],
  outbox: {doc: [], task: [], feature: []}, ...extra});
const MEETINGS = [
  meeting('m1', 'Weekly ops sync', 'zoom', 30, {outbox: {doc: [{text: 'Update pricing'}], feature: [],
    task: [{text: 'Send Dana the quote', status: 'proposed'}, {text: 'Book the review', status: 'pushed'}, {text: 'Gone', status: 'dismissed'}]}}),
  meeting('m2', 'Renewal call with Dana', 'granola', 80, {outbox: {doc: [], feature: [], task: [{text: 'Follow up', status: 'proposed'}]}}),
  meeting('m3', 'Notes from standup', 'manual', 200),
];
// An older server may still send the retired importer in its configuration and sources.
const IMPORTERS = ['granola', 'fireflies', 'zoom', 'google-meet'].map(source => ({source, name: {granola: 'Granola', fireflies: 'Fireflies',
  zoom: 'Zoom', 'google-meet': 'Google Meet'}[source], enabled: false, runner_id: '', status: 'off', last_succeeded_at: null, last_import: null,
  imported_total: 0, error: '', setup: {file: `secrets/${source}.env`, keys: ['API_KEY'], doc: 'docs/meetings.md'}}));
const NONE = [{id: 'import', name: 'Import', status: 'available'}, {id: 'close', name: 'Close', status: 'needs_setup'}];
const SOME = [{id: 'import', name: 'Import', status: 'available'},
  {id: 'close', name: 'Close', status: 'syncing', last_succeeded_at: ago(0.1), last_import: ago(3)},
  {id: 'granola', name: 'Granola', status: 'syncing', last_succeeded_at: ago(0.2), last_import: ago(26)},
  {id: 'fireflies', name: 'Fireflies', status: 'needs_setup'},
  {id: 'zoom', name: 'Zoom', status: 'error', last_succeeded_at: ago(9)}];

async function open(browser, viewport, world, options = {}) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760, ...options});
  await context.addInitScript(theme => { localStorage.setItem('tico.theme', theme); }, options.colorScheme || 'dark');
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: world.role || 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/setup/getting-started') return json({items: [], done: 0, total: 0, complete: true, dismissed: true, tour_seen: true, cards_dismissed: [],
      can_build: true, owner: true, empty: {docs: true, market: true, tasks: true, updates: true, goals: true, meetings: true}});
    if (p === '/api/meetings/sources') return json({sources: world.sources});
    if (p === '/api/v2/meetings') return json({meetings: world.meetings, pending_count: 0});
    if (p === '/api/v2/meeting-importers' && method === 'GET') return json({computers: [{id: 'mac', label: 'Test Mac', platform: 'macos', online: true}], importers: world.importers});
    if (p.startsWith('/api/v2/meeting-importers/') && method === 'POST') {
      const body = JSON.parse(route.request().postData());
      world.saved.push({source: p.split('/').pop(), ...body});
      const i = world.importers.find(x => x.source === p.split('/').pop());
      Object.assign(i, {enabled: body.enabled, runner_id: body.runner_id, status: 'waiting'});
      return json({});
    }
    if (p === '/api/v2/meetings/import' && method === 'POST') { const body = JSON.parse(route.request().postData()); world.imported.push(body); return json({id: 'm9', existing: false}); }
    if (/^\/api\/meetings\/m\d$/.test(p)) return json({...world.meetings.find(m => p.endsWith(m.id)), turns: [], transcript_readable: '', outbox: {doc: [], task: [], feature: []}});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors, context};
}
const world = (extra = {}) => ({role: 'owner', sources: NONE, meetings: [], importers: structuredClone(IMPORTERS), saved: [], imported: [], ...extra});
async function shot(page, name) {
  if (!process.env.TICO_MEETINGS_SCREENSHOTS) return;
  fs.mkdirSync(process.env.TICO_MEETINGS_SCREENSHOTS, {recursive: true});
  await page.screenshot({path: path.join(process.env.TICO_MEETINGS_SCREENSHOTS, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      const scheme = 'dark';
      // ---- nothing yet, nothing connected: the empty state is the source tiles alone; Add notes is the header's
      let w = world();
      let {page, errors, context} = await open(browser, {width: 1280, height: 900}, w, {colorScheme: scheme});
      await page.goto('https://tico-ui.test/#/meetings');
      await page.locator('.meet-blank').waitFor();
      assert.equal(await page.locator('#notes-import').count(), 0, scheme + ': no Import button under the title');
      assert.equal(await page.locator('[data-gs-card=meetings]').count(), 0, scheme + ': no intro banner');
      assert.equal(await page.getByRole('button', {name: 'Add notes', exact: true}).count(), 1, scheme + ': one Add notes button');
      assert.equal(await page.locator('.meet-blank h2, .meet-blank .meet-or').count(), 0, scheme + ': no heading over the tiles');
      await shot(page, 'empty-desktop-' + scheme);
      assert.equal(await page.locator('#meet-sources').isHidden(), true, scheme + ': the large tiles stand in for the strip');
      assert.equal(await page.locator('#notes-filters').isHidden(), true, scheme + ': no filters without a meeting');

      // A tile opens that source's setup in a dialog, and saving enables it.
      await page.locator('.meet-blank [data-msrc=granola]').click();
      const dialog = page.locator('dialog[aria-label="Connect Granola"]');
      await dialog.locator('form[data-importer=granola]').waitFor();
      assert.equal(await dialog.locator('form[data-importer]').count(), 1, 'only that importer');
      assert.equal(await dialog.locator('details').getAttribute('open'), '', 'the credential steps are open when it is off');
      await dialog.locator('input[name=enabled]').check();
      await dialog.locator('select[name=runner]').selectOption('mac');
      w.sources = SOME;
      await dialog.locator('button[type=submit]').click();
      // Once something is connected the large tiles give way to a plain line and the quiet Add source menu.
      await page.waitForFunction(() => !document.querySelector('.meet-blank') && /No shared meetings yet/.test(document.querySelector('#notes-rows')?.textContent || ''));
      assert.equal(await page.locator('#meet-sources .meet-src-btn').isVisible(), true);
      assert.deepEqual(w.saved, [{source: 'granola', enabled: true, runner_id: 'mac'}]);
      await page.keyboard.press('Escape');
      await dialog.waitFor({state: 'detached'});

      // A mixed-version server cannot restore the retired setup form, even through a cached link.
      await page.evaluate(async () => {
        const host = document.createElement('section'); host.id = 'importers-fixture';
        document.querySelector('#main').appendChild(host);
        await window.mountMeetingImporters(host);
      });
      assert.deepEqual(await page.locator('#importers-fixture form').evaluateAll(forms => forms.map(f => f.dataset.importer)),
        ['granola', 'zoom', 'google-meet']);
      await page.evaluate(() => {
        document.querySelector('#importers-fixture').remove();
        window.openMeetingImporter('fireflies', 'Fireflies');
      });
      const retired = page.locator('dialog[aria-label="Connect Fireflies"]');
      await retired.getByText('Fireflies is no longer available as an importer.', {exact: false}).waitFor();
      assert.equal(await retired.locator('form').count(), 0);
      assert.match(await retired.innerText(), /Existing meetings and files remain available/);
      await page.keyboard.press('Escape');
      await retired.waitFor({state: 'detached'});

      // Close goes to its integration page.
      assert.equal(await page.locator('#meet-sources a[data-state]').first().getAttribute('href'), '#/integrations/close-crm');

      // Add notes: notes alone are filed as "manual"; a dropped or uploaded transcript files as an upload.
      await page.locator('#notes-manual').click();
      const notes = page.locator('#manual-modal');
      await notes.locator('#manual-form').waitFor();
      assert.equal(await page.locator('#import-modal').count(), 0, 'one dialog for notes and transcripts');
      assert.equal(await notes.locator('#manual-transcript').isHidden(), true);
      await notes.locator('#manual-go').click();
      assert.match(await notes.locator('#manual-msg').innerText(), /Add notes or a transcript/);
      await notes.locator('#manual-file').setInputFiles({name: 'pricing_call.vtt', mimeType: 'text/vtt', buffer: Buffer.from('WEBVTT\n\n00:00:05.000 --> 00:00:08.500\n<v Dana>Can you send pricing?</v>\n')});
      await page.waitForFunction(() => document.querySelector('#manual-text')?.value.startsWith('WEBVTT'));
      assert.equal(await notes.locator('#manual-title').inputValue(), 'pricing call');
      assert.equal(await notes.locator('#manual-source').inputValue(), 'upload');
      await notes.locator('#manual-go').click();
      await page.waitForFunction(() => !document.querySelector('#manual-modal[open]'));
      assert.equal(w.imported.length, 1);
      assert.equal(w.imported[0].source, 'upload');
      assert.equal(w.imported[0].title, 'pricing call');
      assert(w.imported[0].transcript.startsWith('WEBVTT'));
      await page.locator('#notes-manual').click();
      await notes.locator('#manual-title').fill('Standup');
      await notes.locator('#manual-notes').fill('Ship on Friday');
      await notes.locator('#manual-go').click();
      await page.waitForFunction(() => !document.querySelector('#manual-modal[open]'));
      assert.equal(w.imported[1].source, 'manual');
      assert.equal(w.imported[1].notes, 'Ship on Friday');
      assert.equal(w.imported[1].transcript, undefined);
      assert.deepEqual(errors, []);
      await context.close();

      // ---- with meetings: the strip, the filters and one row each
      w = world({sources: SOME, meetings: MEETINGS});
      ({page, errors, context} = await open(browser, {width: 1280, height: 900}, w, {colorScheme: scheme}));
      await page.goto('https://tico-ui.test/#/meetings');
      await page.locator('.meet-row').first().waitFor();
      assert.equal(await page.locator('.meet-blank').count(), 0);
      assert.equal(await page.locator('#notes-import').count(), 0);
      assert.equal(await page.locator('#meet-sources').isVisible(), true);
      assert.equal(await page.locator('#notes-filters').isVisible(), true);
      assert.deepEqual(await page.locator('#notes-filters select').evaluateAll(els => els.map(el => el.getAttribute('aria-label'))), ['When', 'Participant', 'Source', 'Status']);
      assert.deepEqual(await page.locator('.meet-row .note-title').allInnerTexts(), ['Weekly ops sync', 'Renewal call with Dana', 'Notes from standup']);
      // A row counts what a meeting turned into (pushed items), and only otherwise what is still proposed.
      assert.deepEqual(await page.locator('.meet-row').evaluateAll(rows => rows.map(r => [...r.querySelectorAll('.meet-made-chip, .meet-proposed')].map(e => e.textContent).join(' '))), ['1 task', '1 proposed', '']);
      assert.match(await page.locator('.meet-row').first().locator('.meet-meta').innerText(), /Ana, Ben \+1/);
      // Search and the Source filter narrow the list.
      await page.locator('#notes-source').selectOption('granola');
      assert.deepEqual(await page.locator('.meet-row .note-title').allInnerTexts(), ['Renewal call with Dana']);
      await page.locator('#notes-source').selectOption('');
      await page.evaluate(() => { window.opened = []; window.open = url => (window.opened.push(url), {}); });
      await page.locator('.meet-row .note-title').first().click();   // a click opens the meeting here, never only in a window
      await page.locator('#notes-modal[open] #notes-detail').waitFor();
      assert.deepEqual(await page.evaluate(() => window.opened), []);
      await page.locator('#notes-modal-window').click();              // its own window is a choice
      assert.match(await page.evaluate(() => window.opened[0]), /meeting=m1&window=1/);
      assert.equal(await page.locator('#notes-modal').evaluate(d => d.open), false);
      assert.deepEqual(errors, []);
      await context.close();

      // ---- not the owner: the tiles show status but do not open setup
      w = world({role: 'member', sources: SOME, meetings: MEETINGS});
      ({page, context} = await open(browser, {width: 1280, height: 900}, w, {colorScheme: scheme}));
      await page.goto('https://tico-ui.test/#/meetings');
      await page.locator('.meet-row').first().waitFor();
      assert.equal(await page.locator('#meet-sources [data-msrc=granola]').isDisabled(), true);
      await context.close();

      // Historical source data and filtering survive retirement.
      {
        const viewport = {width: 1280, height: 900};
        w = world({sources: SOME, meetings: [...MEETINGS, meeting('m4', 'Historical Fireflies call', 'fireflies', 30)]});
        ({page, errors, context} = await open(browser, viewport, w, {colorScheme: scheme}));
        await page.goto('https://tico-ui.test/#/meetings');
        await page.locator('.meet-row').first().waitFor();
        assert.equal(await page.locator('[data-msrc=fireflies]').count(), 0);
        assert.equal(await page.locator('#notes-source option[value=fireflies]').innerText(), 'Fireflies');
        await page.locator('#notes-source').selectOption('fireflies');
        assert.deepEqual(await page.locator('.meet-row .note-title').allInnerTexts(), ['Historical Fireflies call']);
        assert.equal(await page.locator('.meet-row[title="Fireflies"]').count(), 1);
        await shot(page, 'historical-' + (viewport.width > 760 ? 'desktop-' : 'phone-') + scheme);
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    // The native window's shared-meeting entry point belongs to this hosted meeting flow too.
    await recorderLiveSharing(browser);
    console.log('meetings ok: 4 browser scenarios, including permissions, historical data and mixed-version setup');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
