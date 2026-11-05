// The Meetings page (ui/app/meetings.js pageNotes, ui/meeting-importers.js): a header with search and one
// "Add notes" button, an empty state of source tiles (Granola, Zoom, Google Meet, Close) alone, then a Sources
// link (ui/app/meetings-sources.js) whose dialog lists every source with its status and error, Remove and Connect;
// filters and rows only once there is a meeting, setup in a dialog, and a transcript upload inside Add notes.
// Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {replay: recorderLiveSharing} = require('../../recorder/tests/live-sharing.cjs');

const ago = hours => new Date(Date.now() - hours * 36e5).toISOString();
const meeting = (id, title, source, hours, extra = {}) => ({id, title, source, kind: 'meeting', status: 'done', started: ago(hours),
  created: ago(hours), duration_ms: 1800000, participants: [{name: 'Ana', email: 'ana@acme.example'}, {name: 'Ben'}, {name: 'Cara'}],
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
  {id: 'zoom', name: 'Zoom', status: 'error', last_succeeded_at: ago(9), error_code: 'auth_failed', error: 'The tool refused the credential.'}];

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
    if (p === '/api/employees') return json(world.employees || []);
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
    if (/^\/api\/meetings\/[^/]+\/items$/.test(p)) return json(world.items || {id: '', can_push: true, sections: {doc: [], task: [], feature: []}, counts: {proposed: 0}});
    if (/^\/api\/meetings\/[^/]+\/items\/[^/]+\/push$/.test(p)) {
      const body = JSON.parse(route.request().postData() || '{}');
      world.pushes.push({path: p, body});
      const item = Object.values(world.items?.sections || {}).flat().find(row => row.id === p.split('/').at(-2));
      if (!item.detail.owner) {
        const docBot = (world.employees || []).find(employee => employee.slug === 'doc-updater');
        if (!docBot) return route.fulfill({status: 422, contentType: 'application/json', body: JSON.stringify({error: {code: 'owner', detail: 'Pick who this doc update is for, then push again.'}})});
        item.detail.owner = 'bot:' + docBot.id;
      }
      item.status = 'pushed'; item.version += 1;
      return json({pushed: true, item});
    }
    if (/^\/api\/meetings\/[^/]+\/items\/[^/]+$/.test(p) && method === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      const item = Object.values(world.items?.sections || {}).flat().find(row => row.id === p.split('/').at(-1));
      Object.assign(item, body, {detail: {...item.detail, ...(body.detail || {})}, version: item.version + 1, updated_by: 'human:ana'});
      world.itemEdits.push(body);
      return json({item});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors, context};
}
const world = (extra = {}) => ({role: 'owner', sources: NONE, meetings: [], importers: structuredClone(IMPORTERS), saved: [], imported: [], pushes: [], itemEdits: [], ...extra});
async function shot(page, name) {
  if (!process.env.TICO_MEETINGS_SCREENSHOTS) return;
  fs.mkdirSync(process.env.TICO_MEETINGS_SCREENSHOTS, {recursive: true});
  await page.screenshot({path: path.join(process.env.TICO_MEETINGS_SCREENSHOTS, name + '.png'), fullPage: true, animations: 'disabled'});
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
      // Once something is connected the large tiles give way to a plain line and the quiet Sources link.
      await page.waitForFunction(() => !document.querySelector('.meet-blank') && /No shared meetings yet/.test(document.querySelector('#notes-rows')?.textContent || ''));
      assert.equal(await page.locator('#meet-sources .meet-src-btn').innerText(), 'Sources');
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
      await page.locator('#meet-sources .meet-src-btn').click();
      assert.equal(await page.locator('#meet-src-dialog [data-src=close] a').getAttribute('href'), '#/integrations/close-crm');
      await page.keyboard.press('Escape');
      await page.locator('#meet-src-dialog').waitFor({state: 'detached'});

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

      // A renamed bot still gets the default because the client matches its stable slug, not its display name.
      const item = {id: 'i2', section: 'doc', text: 'Update the guide', status: 'proposed', quote: '', at_ms: null, version: 1,
        detail: {document: 'docs/guide.md', change: 'Use the new terminology'}};
      w = world({employees: [{id: 'writer-id', slug: 'doc-updater', name: 'renamed-writer', display_name: 'Documentation', status: 'active'}],
        meetings: [meeting('m6', 'Docs call', 'zoom', 1)],
        items: {id: 'm6', can_push: true, counts: {proposed: 1}, sections: {doc: [item], task: [], feature: []}}});
      ({page, errors, context} = await open(browser, {width: 1280, height: 900}, w, {colorScheme: scheme}));
      await page.goto('https://tico-ui.test/#/meetings');
      await page.locator('.meet-row .note-title').first().click();
      const owner = page.locator('#meet-sections [data-pending=i2] select[data-pending-field=owner]');
      await owner.waitFor();
      assert.match(await owner.locator('option').first().innerText(), /Doc-updater bot \(default\)/);
      const sent = page.waitForResponse(response => response.url().endsWith('/items/i2/push'));
      await page.locator('#meet-sections [data-pending=i2] [data-send]').click();
      assert.equal((await sent).status(), 200);
      assert.equal(w.items.sections.doc[0].detail.owner, 'bot:writer-id');
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

      // ---- Sources: a red dot while one fails; connected first with status, last sync and why; Remove; Connect
      w = world({sources: SOME, meetings: MEETINGS});
      ({page, errors, context} = await open(browser, {width: 1280, height: 900}, w, {colorScheme: scheme}));
      await page.goto('https://tico-ui.test/#/meetings');
      await page.locator('.meet-row').first().waitFor();
      assert.equal(await page.locator('#meet-granola, .mg-row').count(), 0, 'no Granola row on the page');
      const sourcesBtn = page.locator('#meet-sources .meet-src-btn');
      await sourcesBtn.locator('.dot').waitFor();
      await sourcesBtn.click();
      const panel = page.locator('#meet-src-dialog');
      await panel.locator('[data-src=zoom]').waitFor();
      assert.deepEqual(await panel.locator('ul[aria-label=Connected] > li').evaluateAll(els => els.map(e => e.dataset.src)), ['granola', 'zoom', 'close']);
      assert.deepEqual(await panel.locator('ul[aria-label="Not connected"] > li').evaluateAll(els => els.map(e => e.dataset.src)), ['google-meet']);
      const zoom = panel.locator('[data-src=zoom]');
      assert.equal(await zoom.getAttribute('data-state'), 'error');
      assert.match(await zoom.locator('.msrc-line').innerText(), /Zoom[\s\S]*Error[\s\S]*Synced 9h ago/);
      assert.equal(await zoom.locator('.msrc-err').innerText(), 'The tool refused the credential.');
      assert.equal(await zoom.locator('[data-act=imp-connect]').innerText(), 'Reconnect');
      await shot(page, 'sources-desktop-' + scheme);
      // Remove asks first; a dismissed confirm changes nothing.
      page.once('dialog', d => d.dismiss());
      await zoom.locator('[data-act=imp-remove]').click();
      assert.deepEqual(w.saved, []);
      w.sources = SOME.filter(r => r.id !== 'zoom');
      page.once('dialog', d => { assert.equal(d.message(), 'Remove Zoom?'); d.accept(); });
      await zoom.locator('[data-act=imp-remove]').click();
      await panel.locator('ul[aria-label="Not connected"] [data-src=zoom]').waitFor();
      assert.deepEqual(w.saved, [{source: 'zoom', enabled: false, runner_id: ''}]);
      await sourcesBtn.locator('.dot').waitFor({state: 'detached'});
      // Connect opens that importer's setup over the panel.
      await panel.locator('[data-src=google-meet] [data-act=imp-connect]').click();
      await page.locator('dialog[aria-label="Connect Google Meet"] form[data-importer=google-meet]').waitFor();
      assert.deepEqual(errors, []);
      await context.close();

      // ---- phone and desktop, both themes: the panel fits without sideways scroll
      for (const [device, viewport, theme] of [['phone', {width: 390, height: 844}, 'dark'], ['phone', {width: 390, height: 844}, 'light'], ['desktop', {width: 1280, height: 900}, 'light']]) {
        w = world({sources: SOME, meetings: MEETINGS});
        ({page, errors, context} = await open(browser, viewport, w, {colorScheme: theme}));
        await page.goto('https://tico-ui.test/#/meetings');
        await page.locator('.meet-row').first().waitFor();
        await page.locator('#meet-sources .meet-src-btn').click();
        await page.locator('#meet-src-dialog [data-src=zoom] .msrc-err').waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), theme + ': page fits');
        const box = await page.locator('#meet-src-dialog').boundingBox();
        assert(box.x >= 16 && box.x + box.width <= viewport.width - 16, theme + ': 16px gutter');
        assert(await page.locator('#meet-src-dialog').evaluate(d => d.scrollWidth <= d.clientWidth), theme + ': panel fits');
        for (const b of await page.locator('#meet-src-dialog .msrc-actions > *').all()) {
          const r = await b.boundingBox();
          assert(r.x + r.width <= box.x + box.width, theme + ': buttons on screen');
        }
        await shot(page, `sources-${device}-${theme}`);
        assert.deepEqual(errors, []);
        await context.close();
      }

      // ---- not the owner: Sources shows status but offers no setup or Remove
      w = world({role: 'member', sources: SOME, meetings: MEETINGS});
      ({page, context} = await open(browser, {width: 1280, height: 900}, w, {colorScheme: scheme}));
      await page.goto('https://tico-ui.test/#/meetings');
      await page.locator('.meet-row').first().waitFor();
      await page.locator('#meet-sources .meet-src-btn').click();
      await page.locator('#meet-src-dialog [data-src=zoom]').waitFor();
      assert.equal(await page.locator('#meet-src-dialog [data-src=google-meet] [data-act=imp-connect]').isDisabled(), true);
      assert.equal(await page.locator('#meet-src-dialog [data-act=imp-remove], #meet-src-dialog [data-src=zoom] [data-act=imp-connect]').count(), 0);
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

      // A Doc update without a doc-updater bot offers a real owner picker on the pending row.
      {
        const viewport = {width: 1280, height: 900};
        const item = {id: 'i1', section: 'doc', text: 'Ship the pricing change', status: 'proposed', quote: '', at_ms: null, version: 1,
          detail: {document: 'docs/pricing.md', change: 'Ship on Friday'}};
        w = world({meetings: [meeting('m5', 'Docs call', 'zoom', 1)],
          items: {id: 'm5', can_push: true, counts: {proposed: 1}, sections: {doc: [item], task: [], feature: []}}});
        ({page, errors, context} = await open(browser, viewport, w, {colorScheme: scheme}));
        await page.goto('https://tico-ui.test/#/meetings');
        await page.locator('.meet-row .note-title').first().click();
        const owner = page.locator('#meet-sections [data-pending=i1] select[data-pending-field=owner]');
        await owner.waitFor();
        assert.match(await owner.locator('option').first().innerText(), /Choose who this is for/);
        const refused = page.waitForResponse(response => response.url().endsWith('/items/i1/push'));
        await page.locator('#meet-sections [data-pending=i1] [data-send]').click();
        assert.equal((await refused).status(), 422);
        await page.locator('#meet-sections [data-pending=i1] [data-owner-error]').waitFor();
        assert.match(await page.locator('#meet-sections [data-pending=i1] [data-owner-error]').innerText(), /Pick who this doc update is for/);
        const saved = page.waitForResponse(response => response.url().endsWith('/items/i1') && response.request().method() === 'POST');
        await owner.selectOption('human:ben');
        assert.equal((await saved).status(), 200);
        const sent = page.waitForResponse(response => response.url().endsWith('/items/i1/push'));
        await page.locator('#meet-sections [data-pending=i1] [data-send]').click();
        assert.equal((await sent).status(), 200);
        assert.equal(w.items.sections.doc[0].status, 'pushed');
        assert.equal(w.items.sections.doc[0].detail.owner, 'human:ben');
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    // The native window's shared-meeting entry point belongs to this hosted meeting flow too.
    await recorderLiveSharing(browser);
    console.log('meetings ok: 7 browser scenarios, including Sources, owner choice, default bot routing, permissions, and mixed-version setup');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
