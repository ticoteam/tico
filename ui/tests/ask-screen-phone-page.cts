import type {Route} from 'playwright';
// Offline browser check of a page's OK on a 390 px phone (the owner's review of a v3 page after two send backs), the
// ask answered in its task's chat (decide.ts decideChatMount):
// - Tico's opinion shows no line: nothing of it is drawn under the bar, however ready it is;
// - two tabs, Chat and Spec, where an OK opens; Spec shows the page, which fills the screen
//   down to the row (no short box that cuts it), its text sized for a phone (an h1 near 22 px, side padding);
// - it opens clean: no blocks marked and no diff; the version control in the ask's one meta line lists each version
//   and "Changes since v2", which marks the changed blocks;
// - the ask's meta line: who asked (cut first when long), the stage, the version and the Decision model's mark,
//   then the task as "#80 ↗"; its menu has the date and the Decision model's word and reason in full; no history cards, no "Open task" row;
// - Tico never types for the person: the chat's box stays empty.
// Each check is collected, so a run on the old code lists every one that fails. FLOW_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const RID = 'spec-ok-ana', ITEM = `v-t-a:${RID}`;
const brief = (id: string, title: string) => ({id, title, number: 80, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-13T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 7});
const TITLE = 'Decision view: a page that comes back after two send backs';
const need = {kind: 'ask', id: ITEM, task_id: 't-a', requirement_id: RID, line: 'OK Ana (version 3)', since: '2026-11-13T10:00:00Z',
  task: brief('t-a', TITLE),
  ask: {requirement_id: RID, kind: 'page', line: 'OK Ana (version 3)', who: 'human:ana', asked_by: 'bot:pm', may: ['ok', 'no'],
    met: false, shows: ['page:pa@3'], standing: 'waiting', answer: null, page: {file_id: 'pa', version: 3, ref: 'page:pa@3'}}};
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
// A bot's page with no styles of its own, long enough to need several screens.
const para = (n: number, extra = '') => Array.from({length: n}, (_, i) => `<p>Section ${i + 1}: who sees it, what it costs and when it ships.${extra}</p>`).join('');
const page = (extra: string) => `<!doctype html><html><head><title>Decision view</title></head><body><h1>${TITLE}</h1>`
  + `<p>Right side in parts.</p>${extra}${para(30)}</body></html>`;
const pages: Record<string, string> = {
  'pa@1': page('<p>First draft.</p>'),
  'pa@2': page('<p>The evidence has counts.</p>'),
  'pa@3': page('<p>The evidence has counts and sources.</p><p>Decide: OK this page.</p>'),
};
const round = (version: number, day: number, verdict: string | null, answers: unknown[]) => ({
  file_id: 'pa', version, at: `2026-11-${day}T09:00:00Z`, by: 'bot:pm', replaced: version < 3, note: '', changed: [], answered: [],
  jev: {verdict, reason: 'Clear'}, answers});
const rounds = [
  round(1, 11, 'unsure', [{by: 'human:ana', answer: 'no', note: 'Too long.', at: '2026-11-11T12:00:00Z'}]),
  round(2, 12, 'not_met', [{by: 'human:ana', answer: 'no', note: 'No sources.', at: '2026-11-12T15:00:00Z'}]),
  round(3, 13, 'met', []),
];
const LINE = 'Send back. The phone picture is still cropped at the bottom, and the meta line under it wraps onto two lines on a 390 px screen.';
const NOTE = 'The phone picture is still cropped at the bottom, so the buttons are cut off. Show the whole screen at 390 px, '
  + 'and say in the first two lines what the decision is.';
const VIEW = {width: 390, height: 844};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [], fails: string[] = [];
  const check = (ok: boolean, what: string) => { if (!ok) fails.push(what); };
  const shots = process.env.FLOW_SHOTS;
  let ready = false;   // Tico's opinion: pending until the test says it is ready
  try {
    const p = await browser.newPage({viewport: VIEW, serviceWorkers: 'block', hasTouch: true});
    p.on('pageerror', (error: Error) => errors.push(error.message));
    await p.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), at = url.pathname;
      const json = (b: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(b)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (at === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = at.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      // The icon font, so the shots show icons, not their names.
      if (at.startsWith('/vendor/fonts/')) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(path.join(__dirname, '..', at))});
      if (/\/marked\.min\.js$/.test(at)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, '../vendor/marked.min.js'), 'utf8')});
      if (at === '/api/me') return json({...me, config});
      if (at === '/api/v2/config') return json(config);
      if (at === '/api/employees') return json([{name: 'pm', display_name: 'Tasks Product Owner'}]);
      if (at === '/api/humans' || at === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (at === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (at === '/api/v2/needs-you') return json({actor: 'human:ana', count: 1, version: '1', next: null, items: [need]});
      if (at === `/api/v2/oks/${RID}`) return json({error: 'not_found'}, 404);
      if (at === `/api/v2/tasks/t-a/asks/${RID}/rounds`) return json({rounds, notices: []});
      if (at === '/api/v2/tasks/t-a') return json({task: {...brief('t-a', TITLE), conversation_id: 'c-a'}});
      // The task's room, named by its task as the server names it (backend/threads.py _shown_title).
      if (at === '/api/v2/threads/c-a/timeline') return json({thread: {id: 'c-a', title: TITLE, audience: 'task', task_id: 't-a', me: {can_post: true}}, messages: [], next_cursor: null});
      if (at === '/api/v2/flow/opinions') {
        const ids = (JSON.parse(r.postData() || '{}') as {items: string[]}).items;
        return json({opinions: ids.map(id => ready ? {id, state: 'ready', pick: 'send_back', line: LINE, note: NOTE, score: 60}
          : {id, state: 'pending', pick: '', line: '', note: '', score: null})});
      }
      const file = /^\/api\/v2\/files\/(pa)$/.exec(at);
      const text = file ? pages[`${file[1]}@${url.searchParams.get('v')}`] : undefined;
      if (text) return route.fulfill({contentType: 'text/html', headers: {'content-disposition': 'inline; filename="page.html"'}, body: text});
      if (at.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(at));
    });
    await p.goto('http://tico-ui.test/#/tasks');
    await p.locator('#task-play').click();
    const dialog = p.locator('#need-card');
    const frame = dialog.locator('.dc-thing iframe.dc-page').first();
    await dialog.locator('[data-dc-vmenu]').waitFor({state: 'attached'});
    await dialog.locator('.dc-start').waitFor({state: 'detached'}).catch(() => {});
    await p.waitForTimeout(300);

    // Tico still working: no callout.
    const say = dialog.locator('.dc-say');
    check(!await say.isVisible(), 'no empty callout while Tico is working');

    // Two tabs, Chat and Spec: an OK opens on its Spec, the page filling the screen down to the row.
    check(await dialog.locator('[data-dc="tab"][data-dc-tab="task"]').getAttribute('aria-selected') === 'true', 'an OK opens on its Spec tab');
    check(!await dialog.locator('.dc-chat').isVisible(), 'the chat is in the Chat tab');
    await dialog.locator('.dc-thing .dc-doc').first().waitFor();
    const hasFrame = await frame.count() > 0;
    check(hasFrame, 'the page opens as the page, not a diff');
    const bar = (await dialog.locator('[data-dc="approve"] >> xpath=..').boundingBox())!;
    const fb = hasFrame ? (await frame.boundingBox())! : {x: 0, y: 999, width: 0, height: 0};
    check(fb.y < 200, `the page starts near the top (${fb.y})`);
    check(fb.y + fb.height >= bar.y - 40, `the page reaches the row (${fb.y + fb.height} vs ${bar.y})`);
    if (shots) await p.screenshot({path: path.join(shots, 'phone-ok-page-before-line.png')});

    // Text sized for a phone, with side padding.
    if (hasFrame) {
      const inner = p.frameLocator('#need-card .dc-thing iframe.dc-page');
      const h1 = await inner.locator('h1').evaluate(e => parseFloat(getComputedStyle(e).fontSize));
      check(h1 >= 20 && h1 <= 24, `the page's h1 is 20-24 px (${h1})`);
      const pad = await inner.locator('h1').evaluate(e => e.getBoundingClientRect().left);
      check(pad >= 12, `the page has side padding (${pad})`);
    }

    // Opens clean: no marks, no diff, no switch over the page.
    const doc = async () => await frame.count() ? (await frame.getAttribute('srcdoc')) || '' : '';
    check(!/data-tico-changed/.test(await doc()), 'v3 opens with no blocks marked');
    check(await dialog.locator('.dc-diff').count() === 0, 'no diff');
    check(await dialog.locator('.dc-since, .dc-switch').count() === 0, 'no "Changes since" row over the page');

    // Back on Chat: the ask's meta line: stage, version, Decision model, #80 ↗; no cards, no Open task row.
    await dialog.locator('[data-dc="tab"][data-dc-tab="chat"]').click();
    const meta = dialog.locator('.dc-meta');
    check(await meta.count() === 1, 'one meta line');
    if (await meta.count()) {
      const text = (await meta.innerText()).replace(/\s+/g, ' ').trim();
      check(text === 'Tasks Product Owner · Spec · v3 · ✓ #80 ↗', `the meta line reads who asked · Spec · v3 · ✓, #80 ↗ at the right (${text})`);
      check(await meta.locator('button:visible, a:visible').count() === 2, 'the meta line is two tap targets: the line and the task');
      // A long name gives way first: the line stays one line and ends with the task.
      const cut = await meta.evaluate(e => {
        const who = e.querySelector<HTMLElement>('.dc-meta-who')!, was = who.textContent;
        who.textContent = 'A very long name for the bot that asked, longer than any phone line';
        const r = e.getBoundingClientRect(), tag = e.querySelector('[data-dc="task"]')!.getBoundingClientRect(), out = [r.height, tag.right <= r.right + 1, who.scrollWidth > who.clientWidth];
        who.textContent = was;
        return out;
      });
      check(Number(cut[0]) <= 48 && cut[1] === true && cut[2] === true, `a long name is cut, the line stays one line (${JSON.stringify(cut)})`);
      const mb = (await meta.boundingBox())!;
      check(mb.height <= 48, `the meta line is one line (${mb.height} px)`);
      check(await meta.locator('[data-dc="task"]').count() === 1, 'the task link is in the meta line');
    }
    check(await dialog.locator('.dc-open-task').count() === 0, 'no separate Open task row');
    check(await dialog.locator('.dc-hist, .dc-step').count() === 0, 'no version cards');

    // The version control: each version on one line, and Changes since v2, which marks the changed blocks.
    const ctl = dialog.locator('[data-dc-vmenu]');
    check(await ctl.count() === 1, 'a version control');
    if (await ctl.count()) {
      const cb = (await ctl.boundingBox())!;
      check(cb.height >= 44 || cb.width >= 44, `the version control is tappable (${cb.width}x${cb.height})`);
      await ctl.click();
      const info = (await dialog.locator('.dc-vmenu .dc-vinfo').allInnerTexts()).map(t => t.trim());
      check(JSON.stringify(info) === JSON.stringify(['Tasks Product Owner · Spec · v3 · Nov 13', 'Decision model: Meets the bar · Clear']),
        `the menu says the rest in full (${JSON.stringify(info)})`);
      const items = dialog.locator('.dc-vmenu [data-dc-ver]');
      check(await items.count() === 3, `the menu lists three versions (${await items.count()})`);
      const rows = (await items.allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());
      check(rows[0] === 'v1 · Nov 11 · ? · Sent back' && rows[1] === 'v2 · Nov 12 · ✗ · Sent back' && /^v3 · Nov 13 · ✓/.test(rows[2] || ''),
        `each version on one line (${JSON.stringify(rows)})`);
      const since = dialog.locator('.dc-vmenu [data-dc-since]');
      check(await since.innerText() === 'Changes since v2', `the menu has Changes since v2 (${await since.innerText().catch(() => '')})`);
      if (shots) await p.screenshot({path: path.join(shots, 'phone-ok-page-versions.png')});
      await since.click();
      await p.waitForFunction(() => /data-tico-changed/.test(document.querySelector('#need-card .dc-thing iframe.dc-page')?.getAttribute('srcdoc') || ''));
      check([...(await doc()).matchAll(/<p data-tico-changed="">/g)].length === 2, 'Changes on marks the two changed blocks');
      check(await dialog.locator('[data-dc-vmenu].on').count() === 1, 'the control shows changes are on');
      check(await dialog.locator('.dc-vmenu:not([hidden])').count() === 0, 'the menu closes');
      await ctl.click();
      await since.click();
      await p.waitForFunction(() => !/data-tico-changed/.test(document.querySelector('#need-card .dc-thing iframe.dc-page')?.getAttribute('srcdoc') || ''));
      check(await dialog.locator('[data-dc-vmenu].on').count() === 0, 'off again');
    }

    // Tico's opinion ready: no line is drawn under the bar, and the chat's box stays empty.
    ready = true;
    await p.waitForFunction("[...DEC_OP.state.values()].includes('ready')", undefined, {timeout: 8000});
    check(!await say.isVisible(), 'no line is drawn once the opinion is ready');
    check(await say.textContent() === '', 'the bar holds no line');
    check(await dialog.locator('.dc-chat .p-text').inputValue() === '', 'Tico never types for the person');
    check(await dialog.locator('.dc-chat .p-text').getAttribute('placeholder') === 'Message Tasks Product Owner', `the box names the task's owner, as the task's window does (${await dialog.locator('.dc-chat .p-text').getAttribute('placeholder')})`);
    if (shots) await p.screenshot({path: path.join(shots, 'phone-ok-page-after.png')});
    await p.close();

    assert.deepEqual(errors, []);
    assert.deepEqual(fails, [], `\n- ${fails.join('\n- ')}`);
    console.log("PASS: on a phone a page's OK opens on Chat and its Task tab gives the page the screen; it opens clean, keeps versions and changes in the ask's meta line, and Tico's opinion shows no line under the bar.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
