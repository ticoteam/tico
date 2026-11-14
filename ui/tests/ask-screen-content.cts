import type {Route} from 'playwright';
// Offline browser check that an OK's Approve waits for the exact content it is for (docs/pipelines-v3.md S3):
// - a letter's OK reads the letter's own preview (GET /api/v2/letters/{id}) and its proof; Approve is off until the proof is
//   drawn, then sends the letter's shown_hash;
// - a letter whose proof does not load leaves Approve off; Send back still works;
// - a file's OK reads the pinned version (GET /api/v2/files/{id}/versions/{n}) and shows it; Approve sends the OK's hash;
// - a stage's OK (no grant) is off while its page loads, on once it is shown, and off when its page does not load;
// - a support report's OK shows the message and the version and install ID that go with it, off until they are read;
// - an OK of a message shows every other field its content has too;
// - a page requirement's OK reads the page at its pinned version; Approve is off until it is shown, then sends that page;
//   a refresh that brings another version does not approve it: the new one is read and shown, and needs a new click.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const need = (rid: string, line: string, hour: number, shows: string[] = []) => ({kind: 'ask', id: rid, task_id: 't-' + rid, requirement_id: rid, line,
  since: `2026-11-09T${String(hour).padStart(2, '0')}:00:00Z`, task: brief('t-' + rid, line),
  ask: {requirement_id: rid, kind: 'ok', line, who: 'human:ana', asked_by: 'bot:pm', choices: null, may: ['ok', 'no', 'thumbs'], met: false,
    shows, standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const letter = (id: string, rid: string, blob: string) => ({id, approval_id: rid, status: 'waiting_for_ok', proof: `/api/v2/files/${blob}`,
  recipient: {name: 'Jo Rivera', address_line1: '1 Main St', address_city: 'Springfield'}, mail_class: 'usps_first_class', page_count: 2,
  total_micros: 1840000, shown_hash: 'c'.repeat(64)});
const gate = () => { let open!: () => void; const p = new Promise<void>(r => { open = r; }); return {p, open}; };

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const needs = [
    need('r1', 'Mail the lease letter', 10),
    need('r2', 'Mail the notice', 11),
    need('r3', 'Approve the pricing notes', 12),
    need('r4', 'Approve the launch post', 13, ['page:p1']),
    need('r5', 'Approve the plan', 14, ['page:p2']),
    need('r6', 'Send this report to Tico support?', 15),
    need('r7', 'Approve the reply', 16),
    {...need('r8', 'Spec page', 17), ask: {...need('r8', 'Spec page', 17).ask, kind: 'page', may: ['ok', 'no'], shows: ['page:pf@2'],
      page: {file_id: 'pf', version: 2, ref: 'page:pf@2'}}},
  ];
  const proof = gate(), page1 = gate(), compose = gate(), specPage = gate();
  let needsVersion = String(needs.length);
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  const pdf = '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n';
  const pg = (id: string, title: string) => ({id, title, body: `${title} body.`, kind: 'doc', owner_id: 'bot:pm', updated_at: '2026-11-09T10:00:00Z'});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
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
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: needsVersion, next: null});
      // Letters: the grant's content is the letter's terms; the proof is read from the letter's own preview.
      if (p === '/api/v2/oks/r1') return json({requirement_id: 'r1', subject: 'letter:L1', content: {letter_id: 'L1', approval_hash: 'h'}, shown_hash: 'd'.repeat(64), standing: 'waiting', task_id: 't-r1'});
      if (p === '/api/v2/oks/r2') return json({requirement_id: 'r2', subject: 'letter:L2', content: {letter_id: 'L2', approval_hash: 'h'}, shown_hash: 'd'.repeat(64), standing: 'waiting', task_id: 't-r2'});
      if (p === '/api/v2/letters/L1') return json(letter('L1', 'r1', 'b1'));
      if (p === '/api/v2/letters/L2') return json(letter('L2', 'r2', 'b2'));
      if (p === '/api/v2/files/b1') { await proof.p; return route.fulfill({contentType: 'application/pdf', body: pdf}); }
      if (p === '/api/v2/files/b2') return json({error: 'not_found'}, 404);
      if (p === '/api/v2/oks/r3') return json({requirement_id: 'r3', subject: 'file:f1', content: {file: 'f1', version: 2, name: 'pricing.md', digest: 'x'}, shown_hash: 'e'.repeat(64), standing: 'waiting', task_id: 't-r3'});
      if (p === '/api/v2/files/f1/versions/2') return route.fulfill({contentType: 'text/markdown', body: '# Pricing\n\nPro is $29 a month.'});
      if (p === '/api/v2/oks/r4' || p === '/api/v2/oks/r5') return json({error: 'not_found'}, 404);
      if (p === '/api/v2/pages/p1') { await page1.p; return json({page: pg('p1', 'Launch post')}); }
      if (p === '/api/v2/pages/p2') return json({error: 'server_error'}, 500);
      if (p === '/api/v2/oks/r6') return json({requirement_id: 'r6', subject: 'support:k1', content: {message: 'Sync stopped at 9am.', content_sha256: 'f'.repeat(64), include_ids: true}, shown_hash: '1'.repeat(64), standing: 'waiting', task_id: 't-r6'});
      if (p === '/api/v2/support/compose') { await compose.p; return json({enabled: true, version: '0.6.0', install_id: 'inst-42', to: 'hq.tico.example'}); }
      if (p === '/api/v2/oks/r7') return json({requirement_id: 'r7', subject: 'hq_reply:x', content: {subject: 'Re: refund', body: 'We refunded it.', to: 'jo@acme.example'}, shown_hash: '2'.repeat(64), standing: 'waiting', task_id: 't-r7'});
      if (p === '/api/v2/oks/r8') return json({error: 'not_found'}, 404);
      if (p === '/api/v2/files/pf') {
        const v = url.searchParams.get('v');
        assert.ok(v === '2' || v === '3', 'the page is read at the version the OK is for');
        await specPage.p;
        return route.fulfill({contentType: 'text/html', headers: {'content-disposition': 'inline; filename="spec.html"'}, body: `<h1>Importer spec v${v}</h1>`});
      }
      if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
        writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
        const m = /^\/api\/v2\/tasks\/(t-r\d)\/asks\/(r\d)\/(ok|answer)$/.exec(p);
        if (m) return json({task: brief(m[1]!, 'x'), answer_id: 'a-' + m[2], requirement_id: m[2]});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    const approve = dialog.locator('[data-dc="approve"]');
    const next = async (line: string) => {
      await dialog.locator('[data-dc="next"]').click();
      await dialog.locator('.dc-q', {hasText: line}).waitFor();
    };

    // r1: the letter. Its terms are up, its proof is still on the way: Approve is off.
    await dialog.locator('.dc-q', {hasText: 'Mail the lease letter'}).waitFor();
    await page.waitForTimeout(400);
    assert.equal(await approve.isDisabled(), true, 'Approve waits for the proof');
    proof.open();
    await dialog.locator('iframe.dc-proof').waitFor();
    await dialog.getByText('Jo Rivera').waitFor();
    assert.equal(await approve.isDisabled(), false);

    // r2: a letter whose proof does not load: Approve stays off, Send back still goes.
    await next('Mail the notice');
    await dialog.locator('.dc-doc .err, .dc-doc + .err, .err').first().waitFor();
    assert.equal(await approve.isDisabled(), true, 'no proof, no Approve');
    assert.equal(await dialog.locator('iframe.dc-proof').count(), 0);
    await dialog.locator('#dc-notes').fill('Fix the proof.');
    await dialog.locator('[data-dc="sendback"]:not([disabled])').click();
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    assert.deepEqual(writes.at(-1)?.p, '/api/v2/tasks/t-r2/asks/r2/answer');
    await dialog.locator('.dc-q', {hasText: 'Mail the lease letter'}).or(dialog.locator('.dc-q', {hasText: 'Approve the pricing notes'})).first().waitFor();

    // Back on r1 (if the screen went there): Approve sends the letter's own hash.
    if ((await dialog.locator('.dc-q').innerText()).includes('Mail the lease letter')) {
      await dialog.locator('[data-dc="approve"]:not([disabled])').click();
      await dialog.locator('.dc-q', {hasText: 'Approve the pricing notes'}).waitFor();
    } else {
      await dialog.locator('[data-dc="prev"]').click();
      await dialog.locator('.dc-q', {hasText: 'Mail the lease letter'}).waitFor();
      await dialog.locator('[data-dc="approve"]:not([disabled])').click();
      await dialog.locator('.dc-q', {hasText: 'Approve the pricing notes'}).waitFor();
    }
    assert.deepEqual(writes.find(w => w.p === '/api/v2/tasks/t-r1/asks/r1/ok')?.body, {shown_hash: 'c'.repeat(64)});

    // r3: the file at its pinned version, shown; Approve sends the OK's hash.
    await dialog.getByText('Pro is $29 a month.').waitFor();
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();
    await dialog.locator('.dc-q', {hasText: 'Approve the launch post'}).waitFor();
    assert.deepEqual(writes.find(w => w.p === '/api/v2/tasks/t-r3/asks/r3/ok')?.body, {shown_hash: 'e'.repeat(64)});

    // r4: a stage's OK: off while its page loads, on once it is shown.
    await page.waitForTimeout(400);
    assert.equal(await approve.isDisabled(), true, 'Approve waits for the page');
    page1.open();
    await dialog.getByText('Launch post').first().waitFor();
    await dialog.locator('[data-dc="approve"]:not([disabled])').waitFor();

    // r5: a stage's OK whose page does not load: Approve stays off.
    await next('Approve the plan');
    await dialog.locator('.err').first().waitFor();
    await page.waitForTimeout(200);
    assert.equal(await approve.isDisabled(), true, 'a missing page leaves Approve off');

    // r6: a support report: the message, then the ids that go with it; Approve waits for them.
    await next('Send this report to Tico support?');
    await page.waitForTimeout(300);
    assert.equal(await approve.isDisabled(), true, 'Approve waits for the ids that go with it');
    compose.open();
    await dialog.getByText('Sync stopped at 9am.').waitFor();
    await dialog.getByText('Install ID inst-42', {exact: false}).waitFor();
    assert.match(await dialog.locator('.dc-doc').innerText(), /Version 0\.6\.0/);
    await dialog.locator('[data-dc="approve"]:not([disabled])').waitFor();

    // r7: a message's other fields (who it goes to) are shown with it, as labelled lines; the exact content under Details.
    await next('Approve the reply');
    await dialog.getByText('We refunded it.', {exact: true}).waitFor();
    assert.match(await dialog.locator('.dc-doc').innerText(), /jo@acme\.example/);
    assert.equal(await dialog.locator('.dc-doc .dc-details .dc-raw').count(), 1, 'the exact content is under Details');

    // r8: a page requirement's OK: off until the page at version 2 is shown; Approve sends that version.
    await next('Spec page');
    await page.waitForTimeout(300);
    assert.equal(await approve.isDisabled(), true, 'Approve waits for the page at its version');
    specPage.open();
    await dialog.locator('iframe.dc-page').waitFor();
    assert.match(await dialog.locator('iframe.dc-page').getAttribute('srcdoc') ?? '', /Importer spec v2/);
    await dialog.locator('[data-dc="approve"]:not([disabled])').waitFor();
    // The bot attaches version 3 and the list is read again: the screen still shows version 2, so Approve sends nothing
    // and reads version 3 instead.
    const r8 = needs.at(-1)!;
    r8.ask = {...r8.ask, shows: ['page:pf@3'], page: {file_id: 'pf', version: 3, ref: 'page:pf@3'}};
    needsVersion = 'v3';
    await page.evaluate(() => (window as unknown as {v2Refresh: () => Promise<void>}).v2Refresh());
    await page.waitForTimeout(300);
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();
    await dialog.getByText('It changed. Look again.').waitFor();
    await dialog.locator('iframe.dc-page[title="Version 3"]').waitFor();
    assert.equal(writes.some(w => w.p === '/api/v2/tasks/t-r8/asks/r8/answer'), false, 'version 3 was never on screen when Approve was clicked');
    await dialog.locator('[data-dc="approve"]:not([disabled])').click();
    for (let i = 0; i < 50 && !writes.some(w => w.p === '/api/v2/tasks/t-r8/asks/r8/answer'); i++) await page.waitForTimeout(100);
    assert.deepEqual(writes.find(w => w.p === '/api/v2/tasks/t-r8/asks/r8/answer')?.body, {value: 'ok', page_version: 'pf@3', version: 3});

    assert.deepEqual(errors, []);
    console.log('PASS: Approve waits for the letter proof, the file version, the stage pages and a page at its version; what does not load leaves it off.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
