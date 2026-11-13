import type {Route} from 'playwright';
// Rules › Internal's map is readable at first open (ui/app/org-rules.ts rrMapPaint, ui/styles/org-rules.css): drawn at
// its own size, never shrunk (a mouse drags it, ui/app/drag-pan.ts); no map text under 11px and each at 4.5:1 or more;
// nothing muted until a team, person or bot is selected (Everyone dims nothing), and then muted, never faded; a line's
// chip says its count, led by the other party's name when its card is out of view; each panel control on one row with
// its source ("Default", "From …") on its label line. Every request is intercepted (the Acme fixture,
// fixtures/redesign-org.json). LEGIBLE_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
declare const window: Window & typeof globalThis & {ticoTheme: {set(theme: string): void}};
type Page = import('playwright').Page;
type Fx = {groups: {id: string}[]; people: {id: string; name: string}[]; employees: {name: string}[]; me: {id: string}; org_groups: unknown[]};
type Line = {id: string; sender: string; receiver: string; per_day: number | null; rule: {id: string; text: string} | null};
const uiDir = path.join(__dirname, '..');
const fx: Fx = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/redesign-org.json'), 'utf8'));
const SHOTS = process.env.LEGIBLE_SHOTS || '';
const ORIGIN = 'http://tico-ui.test';
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900}, SMALL = {width: 1000, height: 640};
const PARTIES = ['*', ...fx.groups.map(g => 'group:' + g.id), ...fx.people.map(p => 'human:' + p.id), ...fx.employees.map(e => 'bot:' + e.name)];
const RULE = 'Decisions only. Put the question in one line and the choices under it.';
// Everyone's lines to each person (3 a day, with a rule), Builder's three, and a read-only line from SDR.
const LINES: Line[] = [
  ...fx.people.map((p, i) => ({id: 'e-' + p.id, sender: '*', receiver: 'human:' + p.id, per_day: 3, rule: {id: 'r-e' + i, text: RULE}})),
  {id: 'l-1', sender: 'bot:builder', receiver: 'group:sales', per_day: 0, rule: null},
  {id: 'l-2', sender: 'bot:builder', receiver: 'bot:reviewer', per_day: 20, rule: {id: 'r-review', text: 'Only send finished pull requests.'}},
  {id: 'l-3', sender: 'bot:sdr', receiver: 'bot:builder', per_day: null, rule: {id: 'r-sdr', text: 'Ask for demo builds through the Sales Lead.'}},
];

async function openReach(browser: import('playwright').Browser, {viewport = DESKTOP, hash = '#/rules', theme = 'dark'} = {}) {
  const config = {company_name: 'Acme'};
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== ORIGIN) return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...fx.me, config});
    if (p === '/api/employees') return json(fx.employees);
    if (p === '/api/humans') return json({people: fx.people, org_groups: fx.org_groups});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json(fx.groups);
    if (p === '/api/v2/rules') return json({rules: [], next_cursor: null});
    if (p === '/api/v2/reach') return json({defaults: {mode: 'nearby', book: 'anyone', chats_per_day: 200, tasks_per_day: 60, person_mode: 'open'},
      settings: [{party: 'group:sales', mode: 'closed', chats_per_day: 40, tasks_per_day: null}],
      // SDR's line is read-only: the viewer may change everything but SDR.
      lines: LINES.map(l => ({...l, may_edit: l.sender !== 'bot:sdr'})), may_edit: PARTIES.filter(x => x !== 'bot:sdr')});
    return json(unrouted(p));
  });
  await page.goto(ORIGIN + '/' + hash);
  await page.locator('.rr').waitFor();
  await page.evaluate(v => window.ticoTheme.set(v), theme);
  await page.evaluate(() => document.fonts.ready);
  await settle(page);
  return {page, errors};
}
// The map repaints on a frame and after a scroll comes to rest (120ms).
const settle = (page: Page) => page.waitForTimeout(250);
async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
}
const panel = (page: Page) => page.locator('.rr-side .rr-panel');
async function until(fn: () => unknown, what: string) {
  for (let i = 0; i < 50; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail(what);
}

// Every piece of map text but avatar initials: its rendered size, its contrast on what is under it, and how faded it is.
type Txt = {text: string; px: number; ratio: number; opacity: number; color: string; party: string};
const mapText = (page: Page, under = '.rr-map') => page.evaluate((sel: string) => {
  const cnv = document.createElement('canvas'); cnv.width = cnv.height = 1;
  const ctx = cnv.getContext('2d', {willReadFrequently: true})!;
  const rgba = (c: string): [number, number, number, number] => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0]!, d[1]!, d[2]!, d[3]! / 255]; };
  const over = (top: number[], bottom: number[]) => [0, 1, 2].map(i => top[i]! * top[3]! + bottom[i]! * (1 - top[3]!));
  const lum = (c: number[]) => { const f = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]!) + 0.7152 * f(c[1]!) + 0.0722 * f(c[2]!); };
  const backdrop = (el: Element): number[] => {
    const layers: number[][] = [];
    for (let e: Element | null = el; e; e = e.parentElement) { const c = rgba(getComputedStyle(e).backgroundColor); if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let base = [255, 255, 255];
    for (const c of layers.reverse()) base = over(c, base);
    return base;
  };
  const root = document.querySelector(sel)!, out: Txt[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement!;
    if (!n.textContent!.trim() || el.closest('.av') || !el.getClientRects().length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden') continue;
    let opacity = 1;
    for (let e: Element | null = el; e && e !== root.parentElement; e = e.parentElement) opacity *= Number(getComputedStyle(e).opacity);
    const bg = backdrop(el), fg = over(rgba(cs.color), bg), [a, b] = [lum(fg), lum(bg)];
    const scale = el.getBoundingClientRect().height / Math.max(1, (el as HTMLElement).offsetHeight || el.getBoundingClientRect().height);
    out.push({text: n.textContent!.trim(), px: parseFloat(cs.fontSize) * scale, ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), opacity,
      color: cs.color, party: el.closest<HTMLElement>('[data-rr-party]')?.dataset.rrParty || ''});
  }
  return out;
}, under);

(async () => {
  const browser = await chromium.launch();
  try {
    // AC-1, AC-2: first open, a computer and a phone, dark and light.
    for (const [vp, label] of [[DESKTOP, 'desktop'], [PHONE, 'phone']] as const) for (const theme of ['dark', 'light']) {
      const {page, errors} = await openReach(browser, {viewport: vp, theme});
      const what = `${label} ${theme}`;
      assert.equal(await page.locator('.rr-cv').evaluate(el => getComputedStyle(el).transform), 'none', `${what}: the map is not scaled`);
      const texts = await mapText(page);
      assert.ok(texts.length > 20, `${what}: map text found (${texts.length})`);
      for (const t of texts) assert.ok(t.px >= 11, `${what}: "${t.text}" is ${t.px}px`);
      const cut = await page.locator('.rr-map .rdo-tn').evaluateAll((els: HTMLElement[]) => els.filter(e => e.scrollWidth > e.clientWidth + 0.5).map(e => `${e.textContent} ${e.scrollWidth}/${e.clientWidth} in ${e.closest(".rdo-g")!.getBoundingClientRect().width}`));
      assert.deepEqual(cut, [], `${what}: no team tab is cut`);
      assert.equal(await page.locator('.rr-map.has-sel').count(), 0, `${what}: nothing muted at open`);
      assert.deepEqual(await page.locator('.rr-pane').evaluate(el => [el.scrollLeft, el.scrollTop]), [0, 0], `${what}: the pane opens at 0,0`);
      if (label === 'desktop') {
        for (const t of texts) {
          assert.equal(t.opacity, 1, `${what}: "${t.text}" is not faded`);
          assert.ok(t.ratio >= 4.5, `${what}: "${t.text}" at ${t.ratio.toFixed(2)}:1`);
        }
        const every = (await page.locator('.rr-all').boundingBox())!, pane = (await page.locator('.rr-pane').boundingBox())!;
        assert.ok(every.x >= pane.x && every.y >= pane.y && every.x + every.width <= pane.x + pane.width && every.y + every.height <= pane.y + pane.height, `${what}: Everyone in view`);
        // AC-8 for Everyone at the panel's 360px.
        await panelRows(page, what + ' Everyone');
      }
      await shot(page, `open-${label}-${theme}`);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // AC-3: a bot selected: every other card that has no line with it is muted, not faded, and still 4.5:1.
    for (const theme of ['dark', 'light']) {
      const {page, errors} = await openReach(browser, {theme, hash: '#/rules?party=bot%3Abuilder'});
      await until(async () => (await page.locator('.rr-map.has-sel').count()) === 1, 'has-sel on a bot');
      const partners = new Set(['bot:builder', 'group:sales', 'bot:reviewer', 'bot:sdr']);
      const muted = await page.evaluate(() => getComputedStyle(document.querySelector('.rr-panel')!).getPropertyValue('--muted').trim());
      const mutedRgb = await page.evaluate((c: string) => { const d = document.createElement('i'); d.style.color = c; document.body.append(d); const v = getComputedStyle(d).color; d.remove(); return v; }, muted);
      const texts = (await mapText(page)).filter(t => t.party && !partners.has(t.party));
      assert.ok(texts.length > 10, `${theme}: unrelated cards found`);
      for (const t of texts) {
        assert.equal(t.opacity, 1, `${theme}: "${t.text}" is not faded`);
        assert.ok(t.ratio >= 4.5, `${theme}: "${t.text}" at ${t.ratio.toFixed(2)}:1`);
      }
      for (const t of texts.filter(x => x.party.startsWith('bot:') || x.party.startsWith('human:'))) assert.equal(t.color, mutedRgb, `${theme}: "${t.text}" muted`);
      // The selected card and its partners keep their ink.
      assert.notEqual(await page.locator('.rr-map [data-rr-party="bot:builder"] b').evaluate(el => getComputedStyle(el).color), mutedRgb);
      assert.notEqual(await page.locator('.rr-map [data-rr-party="bot:reviewer"] b').evaluate(el => getComputedStyle(el).color), mutedRgb);
      await shot(page, `builder-${theme}`);
      // AC-8 for a bot, a team and a person.
      await panelRows(page, theme + ' Builder');
      for (const [p, name] of [['group:sales', 'Sales'], ['human:cara', 'Cara Lee']] as const) {
        await page.locator(`.rr-map [data-rr-party="${p}"]`).click();
        await until(async () => (await panel(page).locator('h2').innerText()) === name, name);
        await panelRows(page, `${theme} ${name}`);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }

    // AC-4: a search with one match: the rest muted at 4.5:1, the match ringed.
    {
      const {page, errors} = await openReach(browser);
      await page.locator('.ru-acts .rr-q').fill('build');
      await settle(page);
      assert.deepEqual(await page.locator('.rr-map .hit').evaluateAll((els: HTMLElement[]) => els.map(e => e.dataset.rrParty)), ['bot:builder']);
      for (const t of (await mapText(page)).filter(x => x.party !== 'bot:builder')) {
        assert.equal(t.opacity, 1, `search: "${t.text}" is not faded`);
        assert.ok(t.ratio >= 4.5, `search: "${t.text}" at ${t.ratio.toFixed(2)}:1`);
      }
      assert.match(await page.locator('.rr-map .hit').evaluate(el => getComputedStyle(el).boxShadow), /2px/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // AC-5, AC-6: a pane narrower than the chart. A mouse drag pans and selects nothing; a still click selects. A chip
    // names a card out of view and only its count once the card is in view.
    {
      const {page, errors} = await openReach(browser, {viewport: SMALL});
      const pane = page.locator('.rr-pane');
      const room = await pane.evaluate(el => el.scrollWidth - el.clientWidth);
      assert.ok(room >= 200, `the chart is 200px wider than the pane (${room})`);
      // Chips at open: Everyone's three lines, each its count; a person out of view is named first, uncut.
      const chips = await page.locator('.rr-chip').evaluateAll((els: HTMLElement[]) => els.map(e => ({id: e.dataset.rrLine, text: e.innerText, title: e.title,
        cut: [...e.querySelectorAll<HTMLElement>('.rr-cn')].some(n => n.scrollWidth > n.clientWidth) || e.scrollWidth > e.clientWidth})));
      assert.equal(chips.length, fx.people.length);
      const inView = await page.evaluate(() => { const v = document.querySelector('.rr-pane')!.getBoundingClientRect(); return [...document.querySelectorAll<HTMLElement>('.rr-cv [data-rr-party^="human:"]')]
        .map(e => { const r = e.getBoundingClientRect(); return [e.dataset.rrParty!.slice(6), r.left >= v.left && r.right <= v.right && r.top >= v.top && r.bottom <= v.bottom] as const; }); });
      const off = inView.filter(x => !x[1]).map(x => x[0]);
      assert.ok(off.length > 0, 'a person is out of view at open');
      for (const c of chips) {
        const id = c.id!.slice(2), name = fx.people.find(p => p.id === id)!.name;
        assert.equal(c.text, off.includes(id) ? `${name} · 3 a day` : '3 a day', `chip to ${name}`);
        assert.equal(c.cut, false, `chip to ${name} is not cut`);
        assert.equal(c.title, `Everyone to ${name}: 3 a day · ${RULE}`);
        assert.match(await panel(page).locator(`[data-rr-line="${c.id}"]`).innerText(), new RegExp(`${name}\\s+3 a day\\s+Decisions only`));
      }
      await shot(page, 'small-open');
      // Drag left 200px with the mouse, starting on a card: the pane moves, nothing is selected.
      const card = (await page.locator('.rr-cv .rdo-card').first().boundingBox())!;
      await page.mouse.move(card.x + 20, card.y + 10);
      await page.mouse.down();
      for (let i = 1; i <= 10; i++) await page.mouse.move(card.x + 20 - 20 * i, card.y + 10);
      await page.mouse.up();
      assert.ok(Math.abs(await pane.evaluate(el => el.scrollLeft) - 200) <= 1, `scrolled 200 (${await pane.evaluate(el => el.scrollLeft)})`);
      await settle(page);
      assert.equal(await panel(page).locator('h2').innerText(), 'Everyone', 'a drag selects nothing');
      assert.doesNotMatch(page.url(), /party=/);
      // Scrolled so a named card is in view: its chip is its count alone.
      const target = off[0]!;
      await page.locator(`.rr-cv [data-rr-party="human:${target}"]`).evaluate(el => el.scrollIntoView({block: 'center', inline: 'center'}));
      await settle(page);
      assert.equal(await page.locator(`.rr-chip[data-rr-line="e-${target}"]`).innerText(), '3 a day', 'in view: the count alone');
      // A still click selects.
      await page.locator(`.rr-cv [data-rr-party="human:${target}"]`).click();
      await until(async () => (await panel(page).locator('h2').innerText()) === fx.people.find(p => p.id === target)!.name, 'a click selects');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // AC-8 on a phone: Builder's panel.
    {
      const {page, errors} = await openReach(browser, {viewport: PHONE, hash: '#/rules?party=bot%3Abuilder'});
      await panel(page).waitFor();
      await panelRows(page, 'phone Builder');
      await shot(page, 'phone-builder');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('rules-graph-legible: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });

// Each of the panel's controls keeps its buttons on one row, none cut, and each source tag sits at the right of its label
// line ("Default", "From …").
async function panelRows(page: Page, what: string) {
  const rows = await panel(page).evaluate(p => {
    const box = (e: Element) => e.getBoundingClientRect();
    return {
      segs: [...p.querySelectorAll('.rr-set .ru-seg')].map(s => ({id: (s as HTMLElement).dataset.ruSeg, tops: [...s.querySelectorAll('button')].map(b => Math.round(box(b).top)),
        cut: [...s.querySelectorAll('button')].some(b => b.scrollWidth > b.clientWidth) || s.scrollWidth > s.clientWidth})),
      tags: [...p.querySelectorAll('.rr-set .rr-from')].map(t => {
        const line = t.closest('.rr-row-h, .rr-cap')!, l = box(line), r = box(t), label = line.firstElementChild!;
        return {text: (t as HTMLElement).innerText, gap: l.right - r.right, sameLine: Math.abs((r.top + r.bottom) / 2 - (box(label).top + box(label).bottom) / 2) < 6};
      }),
      width: box(p).width,
    };
  });
  assert.ok(rows.segs.length >= 1, `${what}: controls drawn`);
  for (const s of rows.segs) {
    assert.equal(new Set(s.tops).size, 1, `${what}: ${s.id} on one row (${s.tops})`);
    assert.equal(s.cut, false, `${what}: ${s.id} not cut`);
  }
  for (const t of rows.tags) {
    assert.match(t.text, /^(Default|From .+)$/, `${what}: source "${t.text}"`);
    assert.ok(t.gap <= 1, `${what}: "${t.text}" right-aligned (${t.gap}px short)`);
    assert.ok(t.sameLine, `${what}: "${t.text}" on its label line`);
  }
}
