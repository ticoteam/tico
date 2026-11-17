import type {Route} from 'playwright';
// Offline browser check of a stage approval in Flow (docs/dev/pipelines-v3.md "Stage approvals name a document"): the
// bot wrote the task's Spec in a stage that approves it, so the ask is "Approve the Spec?" and opens on the Spec tab at
// the version submitted, with one version line ("v2 · submitted for approval · changed: …"). Approve sends the key (the
// visit and `<file id>@<version>`). When the bot wrote v3 meanwhile, the key is stale: nothing is approved, the list is
// read again and v3 is shown, with "It changed. Look again."; Approve then sends v3's key and the item goes.
// TICO_SHOTS=<folder> saves the approval and the refusal, desktop and phone, light and dark.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Item = Record<string, unknown> & {id: string};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const brief = () => ({id: 't1', title: 'Pipeline boards show where work waits', number: 41, pipeline_id: 'product', stage_id: 'spec',
  status: 'open', owner_id: 'bot:tpo', requester_id: 'human:ana', assignee_id: 'bot:tpo', due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: null, line: 'Waiting on a person', since: ago(30), until: null, ask: true, requirement_id: 'r1'}, version: 5});
const NOTES: Record<number, string> = {1: 'First draft', 2: 'Empty state for a pipeline with no tasks', 3: 'Counts are per stage'};
/** The item as Needs you lists it once version `n` is submitted and checked. */
const approval = (n: number): Item => ({kind: 'ask', id: 'v1:r1', task_id: 't1', requirement_id: 'r1', since: ago(30), line: 'Spec', task: brief(),
  ask: {requirement_id: 'r1', kind: 'page', line: 'Spec', who: null, asked_by: 'bot:tpo', choices: null, may: ['ok', 'no'], met: false,
    shows: [`page:f-spec@${n}`], standing: 'waiting', answer: null, page: {file_id: 'f-spec', version: n, ref: `page:f-spec@${n}`},
    approval: {document: 'Spec', visit_id: 'visit-7', file_id: 'f-spec', version: n, unchecked: false}}});
const spec = (n: number) => ({name: 'Spec', file_id: 'f-spec', version: n, by: 'bot:tpo', at: ago(5),
  versions: Array.from({length: n}, (_, i) => n - i).map(v => ({n: v, by: 'bot:tpo', at: ago(v === n ? 5 : 60 * (n - v)), note: NOTES[v]}))});
const page = (n: number) => `<!doctype html><html><body><h1>Pipeline boards</h1><p>Spec v${n}.</p>`
  + '<h2>Goal</h2><p>Each stage shows how many tasks wait there and on whom.</p>'
  + `<h2>Scope</h2><ul><li>Stage counts</li><li>Who each task waits on</li>${n > 1 ? '<li>An empty pipeline says so</li>' : ''}${n > 2 ? '<li>Counts per stage</li>' : ''}</ul>`
  + '<h2>Done when</h2><p>A person can tell where work is stuck without opening a task.</p></body></html>';
const round = (n: number) => ({file_id: 'f-spec', version: n, at: ago(5), by: 'bot:tpo', replaced: false, note: NOTES[n] ?? '', changed: [], answered: [],
  jev: {verdict: 'met', reason: 'Goal, scope and acceptance criteria are there'}, answers: []});

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], sent: Record<string, unknown>[] = [];
    // `submitted`: the version the bot wrote last; `listed`: the one Needs you lists (read again after a refusal).
    let submitted = 2, listed = 2, needs: Item[] = [approval(2)];
    const view = await browser.newPage({viewport: {width: 1200, height: 860}, serviceWorkers: 'block'});
    view.on('pageerror', (error: Error) => errors.push(error.message));
    await view.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{slug: 'tpo', id: 'tpo', display_name: 'Tasks Feature Owner', status: 'active'}]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: []});
      if (p === '/api/v2/needs-you') {
        listed = submitted;
        needs = needs.map(n => n.id === 'v1:r1' ? approval(listed) : n);
        return json({actor: 'human:ana', count: needs.length, items: needs, version: needs.map(n => `${n.id}@${listed}`).join(','), next: null});
      }
      if (p === '/api/v2/oks/r1') return json({error: 'not_found'}, 404);   // a stage's approval: no grant
      if (p === '/api/v2/flow/opinions') return json({opinions: []});
      if (p === '/api/v2/tasks/t1/asks/r1/rounds') return json({rounds: [round(listed)], notices: []});
      if (p === '/api/v2/tasks/t1' && r.method() === 'GET') return json({task: {...brief(), body: '', created_at: ago(60 * 30), attachments: [],
        links: [], conversation_id: 'c1', documents: [spec(submitted)]}});
      if (p === '/api/v2/files/f-spec') return route.fulfill({contentType: 'text/html', body: page(Number(url.searchParams.get('v')) || submitted)});
      if (p === '/api/v2/threads/c1/timeline') return json({thread: {id: 'c1', title: 'Pipeline boards', me: {can_post: true}}, messages: [
        {id: 'm1', seq: 1, sender_id: 'human:ana', body: 'Boards should show where work waits.', kind: 'say', created_at: ago(60 * 26), conversation_id: 'c1'}], next_cursor: null});
      if (r.method() === 'POST' && p === '/api/v2/tasks/t1/asks/r1/answer') {
        const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        sent.push(body);
        // answers._page_version: the key's version is not the one submitted now.
        if (body['page_version'] !== `f-spec@${submitted}`) {
          return json({error: 'task_changed', detail: 'A newer version is submitted. Look again.',
            approval: {document: 'Spec', visit_id: 'visit-7', file_id: 'f-spec', version: submitted}}, 409);
        }
        needs = [];
        return json({task: {id: 't1'}, answer_id: 'a-r1', requirement_id: 'r1'});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = view.locator('#need-card');
    const approve = dialog.locator('.dc-side [data-key] [data-dc="approve"]');
    // The version line: over the document on the left (desktop), and over its tab (phone).
    const tab = dialog.locator('.dc-side > .dc-docpane [data-doc-ver] .doc-ver-t');
    const said = async (where: typeof tab) => (await where.textContent() ?? '').trim();
    const leftSays = (text: string) => view.waitForFunction(t => document.querySelector('.decide .dc-thing > .dc-docview [data-doc-ver] .doc-ver-t')?.textContent?.trim() === t, text);
    /** How light the document's own page is in a frame (0 black .. 1 white): its body's background, else its root's. */
    const frameLight = (sel: string) => dialog.locator(sel).contentFrame().locator('body').evaluate(body => {
      const css = (el: Element) => getComputedStyle(el).backgroundColor;
      const bg = css(body).replace(/\s/g, '') === 'rgba(0,0,0,0)' ? css(body.ownerDocument.documentElement) : css(body);
      const [r = 0, g = 0, b = 0] = (bg.match(/[\d.]+/g) || []).map(Number);
      return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    });
    const setTheme = async (theme: string) => {
      await view.evaluate(t => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set(t), theme);
      await view.waitForTimeout(150);
    };
    const shots = process.env.TICO_SHOTS;
    const shoot = async (name: string) => {
      if (!shots) return;
      for (const [width, height, size] of [[1200, 860, 'desktop'], [390, 844, 'phone']] as const) {
        await view.setViewportSize({width, height});
        await view.waitForTimeout(500);   // past a phone's calm (decideCalm)
        for (const theme of ['light', 'dark']) {
          await view.evaluate(t => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set(t), theme);
          await view.waitForTimeout(150);
          await view.screenshot({path: path.join(shots, `${name}-${size}-${theme}.png`)});
        }
      }
      await view.setViewportSize({width: 1200, height: 860});
      await view.waitForTimeout(200);
    };

    // ---- 1. The approval opens on its document, at the version submitted, with one version line.
    await view.goto('https://tico-ui.test/#/tasks');
    await view.locator('[data-need="v1:r1"]').waitFor();
    await view.evaluate('decideFlow(document.body)');
    const V2 = 'v2 · submitted for approval · changed: Empty state for a pipeline with no tasks';
    await leftSays(V2);
    assert.equal(await dialog.locator('.dc-msg-ask .dc-q').first().innerText(), 'Approve the Spec?');
    assert.doesNotMatch(await dialog.innerText(), /Jev|checking/i, 'no "Jev", no checking state');
    // The wide screen shows the document as the phone's tab does: in the app's colours, light or dark.
    const wide = '.dc-thing > .dc-docview iframe.html-view';
    await dialog.locator(wide).contentFrame().locator('body', {hasText: 'Spec v2.'}).waitFor();
    /** The frame's lightness once it settles: a theme change draws the document again, in a new frame. */
    const lightOnce = async (ok: (n: number) => boolean) => {
      let n = NaN;
      for (let i = 0; i < 30; i++) {
        n = await frameLight(wide).catch(() => NaN);
        if (ok(n)) return n;
        await view.waitForTimeout(100);
      }
      return n;
    };
    await setTheme('dark');
    const dark = await lightOnce(n => n < 0.3);
    assert.ok(dark < 0.3, `desktop dark: the document's page is dark (${dark})`);
    await setTheme('light');
    const light = await lightOnce(n => n > 0.7);
    assert.ok(light > 0.7, `desktop light: the document's page is light (${light})`);
    await approve.and(view.locator(':not([disabled])')).waitFor();
    // A phone opens it on the Spec tab, at that version, with the same line.
    await view.setViewportSize({width: 390, height: 844});
    await view.waitForFunction(() => document.querySelector('.decide [data-dc-tab="doc:Spec"]')?.getAttribute('aria-selected') === 'true');
    await dialog.locator('.dc-side > .dc-docpane iframe.html-view').contentFrame().locator('body', {hasText: 'Spec v2.'}).waitFor();
    assert.equal(await said(tab), V2);
    await view.setViewportSize({width: 1200, height: 860});
    await shoot('flow-approval');

    // ---- 2. The bot wrote v3 meanwhile: the key is stale, nothing is approved, and v3 is shown.
    submitted = 3;
    await approve.click();
    const V3 = 'v3 · submitted for approval · changed: Counts are per stage';
    await leftSays(V3);
    await dialog.locator('.dc-thing .dc-changed', {hasText: 'It changed. Look again.'}).waitFor();
    assert.deepEqual(sent[0], {value: 'ok', version: 5, page_version: 'f-spec@2', visit_id: 'visit-7'}, 'the whole key');
    assert.equal(needs.length, 1, 'nothing approved');
    await view.setViewportSize({width: 390, height: 844});
    await view.waitForFunction(t => document.querySelector('.decide .dc-docpane [data-doc-ver] .doc-ver-t')?.textContent?.trim() === t, V3);
    await dialog.locator('.dc-side > .dc-docpane iframe.html-view').contentFrame().locator('body', {hasText: 'Spec v3.'}).waitFor();
    await dialog.locator('.dc-side > .dc-docnote', {hasText: 'It changed. Look again.'}).waitFor();
    await view.setViewportSize({width: 1200, height: 860});
    await shoot('flow-stale-refusal');

    // ---- 3. Approve again: v3's key, and the item goes.
    await approve.and(view.locator(':not([disabled])')).waitFor();
    await approve.click();
    await view.waitForFunction(() => !document.querySelector('.decide [data-dc="approve"]') || document.querySelector('.decide .dc-done'));
    assert.deepEqual(sent.at(-1), {value: 'ok', version: 5, page_version: 'f-spec@3', visit_id: 'visit-7'});
    assert.equal(needs.length, 0);
    assert.deepEqual(errors, []);
    console.log('flow-approval: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
