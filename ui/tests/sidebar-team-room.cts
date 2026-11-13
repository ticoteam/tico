// The sidebar leaves room for the team and never cuts a name (spec "Sidebar: room for the team, names never cut").
//  - With no fold kept, Company and Library are folded, so at 1440×900 at least nine bot rows sit wholly above the
//    sidebar's footer (AC-4).
//  - Every group and bot expanded, at the sidebar's 236px default and its 200px minimum, dark and light, down to a seat
//    four levels deep: no name is cut or clamped, no row's content is wider than the row, and at 236px no word is split across lines (AC-3).
//  - At most one count per row: beside a Needs-you count the DM unread is a 6px dot with the same words; alone it stays
//    a number. A folded manager whose report needs you puts its dot on the chevron, not in the row (AC-5).
//  - A goal, branch or onboarding mark rides the name's last word (AC-6). A seat's "Recruiting" and applicants count sit
//    on a line under its name, so they take none of its room (Validate, rc.31).
//  - The Team header holds TEAM, its chevron, + and ⋯ only; ⋯ opens Contacts, Only bots I can open and Recently viewed
//    first, in words, by mouse and by keyboard, and says on itself which switch is on (AC-7).
// Fixtures only, no network.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const t = (ms: number) => ms * (Number(process.env.TICO_TEST_TIMEOUT_SCALE) || 1);
declare function renderTree(): void;
declare const THREADS: {dms: Record<string, number> | null};
declare const RECRUIT: {counts: Record<string, number>};
declare function renderTree(): void;

const ORIGIN = 'https://tico-ui.test';
const FULL = {see: true, read: true, write: true};
type Bot = Record<string, unknown>;
const bot = (name: string, display_name: string, team: string, reports_to: string, extra: Bot = {}): Bot => ({name, display_name, team,
  org_parent: reports_to.startsWith('human:') ? 'p:' + reports_to.slice(6) : 'b:' + reports_to, reports_to, host: 'keeper', status: 'active',
  state: 'active', can_chat: true, my_access: FULL, users: [{id: 'chris', name: 'Chris'}], owner_id: 'chris', revision: 1, ...extra});
// A demo-like team: the long titles the 13 Nov sweep found cut ("Retail Partners M", "DevOps Enginee").
const BOTS: Bot[] = [
  bot('assistant', 'Assistant', '', 'human:chris', {helper: true}),
  bot('product', 'Product Manager', 'product', 'human:chris'),
  bot('ui-owner', 'UI Owner', 'product', 'product', {goal_active: true}),
  bot('eng-manager', 'Engineering Manager', 'engineering', 'human:chris'),
  bot('firmware', 'Firmware Engineer', 'engineering', 'eng-manager'),
  bot('devops', 'DevOps Engineer', 'engineering', 'eng-manager', {shared_from: 'devops-main', owner_id: 'chris'}),
  bot('qa', 'Quality Assurance Specialist', 'engineering', 'eng-manager', {onboarding_state: 'needs_onboarding'}),
  bot('hardware', 'Hardware Test Engineer', 'engineering', 'eng-manager'),
  bot('retail', 'Retail Partners Manager', 'sales', 'human:chris'),
  bot('partnerships', 'Partnerships Development Manager', 'sales', 'retail', {goal_active: true}),
  bot('supply', 'Supply Chain Coordinator', 'sales', 'retail'),
  bot('support', 'Customer Support Lead', 'sales', 'retail'),
  // Seats as deep as production's (group › Growth Manager › seat: a 93px name box at the 200px minimum, where rc.31 cut
  // two of them at three lines) and a level deeper (› Demand Generation Lead › seat: 72px).
  bot('growth', 'Growth Manager', 'growth', 'human:chris'),
  bot('demand', 'Demand Generation Lead', 'growth', 'growth'),
];
const PEOPLE = [{id: 'chris', name: 'Chris Lee', email: 'chris@acme.example', org_parent: '', team: ''}];
const GROUPS = [{id: 'product', name: 'Product', org_parent: '', order: 0}, {id: 'engineering', name: 'Engineering', org_parent: '', order: 1},
  {id: 'sales', name: 'Sales and Partners', org_parent: '', order: 2}, {id: 'growth', name: 'Growth', org_parent: '', order: 3}];
// A seat comes with the roster (/api/humans), placed by org_parent like a bot.
const seat = (id: string, title: string, team: string, reports_to: string, extra: Bot = {}): Bot => ({id, title, team, org_parent: 'b:' + reports_to,
  reports_to, kind: 'seat', stage: 'recruiting', description: '', ...extra});
const SEATS = [seat('s-ops', 'Operations Analyst', 'sales', 'retail'),
  seat('s-cro', 'Conversion Rate Optimization Owner', 'growth', 'growth'), seat('s-leads', 'Lead Generation Owner', 'growth', 'growth'),
  seat('s-cro-deep', 'Conversion Rate Optimization Owner', 'growth', 'demand'),
  seat('s-cloud', 'Marketing Attribution Analyst', 'growth', 'demand', {agent: 'cloud'})];

async function open(browser: import('playwright').Browser, {width = 1440, height = 900, keep = {} as Record<string, string>} = {}) {
  const context = await browser.newContext({viewport: {width, height}, serviceWorkers: 'block', reducedMotion: 'reduce'});
  await context.addInitScript((kept: Record<string, string>) => {
    if (sessionStorage.getItem('kept')) return;              // once per test, so a reload keeps what the page changed
    sessionStorage.setItem('kept', '1');
    for (const [k, v] of Object.entries(kept)) localStorage.setItem(k, v);
  }, keep);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== ORIGIN) return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p.startsWith('/vendor/')) {
      const file = path.join(__dirname, '..', p);
      if (fs.existsSync(file)) return route.fulfill({contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript', body: fs.readFileSync(file)});
    }
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'chris', name: 'Chris Lee', email: 'chris@acme.example', role: 'owner', company_role: 'owner', cloud: true,
      config: {company_name: 'Acme', onboarding_needed: false}});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/humans') return json({people: PEOPLE, seats: SEATS, org_groups: GROUPS});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, version: 'v0'});
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [], catch_up: 0, catch_up_ids: [], next_cursor: null, dms: {}});
    if (p === '/api/v2/threads') return json({attention: [], threads: [], dms: [], bot_dms: {}, next_cursor: null});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto(ORIGIN + '/#/home');
  await page.locator('#tree a.node[data-org="b:firmware"]').waitFor({state: 'attached', timeout: t(20000)});
  await page.evaluate(() => document.fonts.ready.then(() => 0));
  return {context, page, errors};
}

// Unfold every group, manager and seat in the Team list.
async function expandAll(page: import('playwright').Page) {
  for (let i = 0; i < 40; i++) {
    const folded = page.locator('#tree .chev.col');
    if (!await folded.count()) return;
    await folded.first().click();
  }
  throw new Error('the Team list never unfolded');
}
// Every name in the Team list that shows, and whether any is cut, clamped, wider than its row or split inside a word.
const names = (page: import('playwright').Page) => page.evaluate(() => {
  const out: {name: string; cut: boolean; wide: boolean; split: boolean; lines: number}[] = [];
  for (const nm of document.querySelectorAll<HTMLElement>('#tree .nm')) {
    if (!nm.checkVisibility()) continue;
    const row = nm.closest<HTMLElement>('.noderow')!, rowBox = row.getBoundingClientRect();
    const wide = [...row.children].some(el => el.getBoundingClientRect().right > rowBox.right + 0.5);
    // A word split across lines: some word's characters start on two different lines.
    let split = false;
    const text = nm.firstChild ? nm : null;
    if (text) {
      const walker = document.createTreeWalker(nm, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const s = node.textContent || '';
        for (const m of s.matchAll(/\S+/g)) {
          const range = document.createRange();
          range.setStart(node, m.index!); range.setEnd(node, m.index! + m[0].length);
          const tops = new Set([...range.getClientRects()].map(r => Math.round(r.top)));
          if (tops.size > 1) split = true;
        }
      }
    }
    const lh = parseFloat(getComputedStyle(nm).lineHeight);
    out.push({name: nm.textContent!.trim(), cut: nm.scrollHeight > nm.clientHeight + 0.5 || nm.scrollWidth > nm.clientWidth + 0.5, wide, split,
      lines: Math.round(nm.getBoundingClientRect().height / lh)});
  }
  return out;
});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // AC-4: nothing kept, at 1440×900: Company and Library folded, nine or more bots wholly above the footer.
    {
      const {context, page, errors} = await open(browser);
      await expandAll(page);
      assert.equal(await page.locator('#rd-co').isVisible(), false, 'Company folded');
      assert.equal(await page.locator('#rd-lib').isVisible(), false, 'Library folded');
      const above = await page.evaluate(() => {
        const foot = document.querySelector('#side .side-footer')!.getBoundingClientRect().top;
        const scroll = document.querySelector('#side .side-scroll')!.getBoundingClientRect().bottom;
        return [...document.querySelectorAll<HTMLElement>('#tree a.node[data-org^="b:"]')]
          .filter(a => a.checkVisibility() && a.getBoundingClientRect().bottom <= Math.min(foot, scroll)).length;
      });
      assert.ok(above >= 9, `at least 9 bot rows above the footer, saw ${above}`);
      assert.deepEqual(errors, []);
      await context.close();
    }

    // AC-3, AC-6 and the seat: every name whole at 236px and 200px, dark and light; marks on the last word's line.
    {
      const {context, page, errors} = await open(browser, {keep: {'tico.theme': 'dark'}});
      // Production's recruiting seats show their applicants count, which takes room from the name.
      await page.evaluate(() => { Object.assign(RECRUIT.counts, {'s-ops': 2, 's-leads': 4, 's-cro-deep': 12}); renderTree(); });
      await expandAll(page);
      for (const theme of ['dark', 'light']) {
        await page.evaluate(th => { document.documentElement.dataset.theme = th; }, theme);
        for (const width of [236, 200]) {
          await page.evaluate(w => { document.documentElement.style.setProperty('--rail-left', w + 'px'); }, width);
          // The rail eases to its new width: measure once it is there (light 200px used to measure the 236px rail).
          await page.waitForFunction(w => Math.round(document.querySelector('#side')!.getBoundingClientRect().width) === w, width, {timeout: t(5000)});
          const seen = await names(page);
          assert.ok(seen.length >= 12, `${theme} ${width}px: the whole team is listed (${seen.length})`);
          for (const n of seen) {
            assert.equal(n.cut, false, `${theme} ${width}px: "${n.name}" is not cut`);
            assert.equal(n.wide, false, `${theme} ${width}px: "${n.name}"'s row holds its content`);
            if (width === 236) assert.equal(n.split, false, `${theme} 236px: "${n.name}" breaks between words`);
          }
          // The deep seats are really deep: at 200px the name box is production's 93px, and 72px a level down.
          const room = (id: string) => page.locator(`#tree [data-seat="${id}"]`).evaluate(a => {
            const cs = getComputedStyle(a), others = [...a.children].filter(el => !el.classList.contains('seat-text'));
            return a.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.columnGap) * others.length
              - others.reduce((w, el) => w + el.getBoundingClientRect().width, 0);
          });
          if (width === 200) {
            assert.ok(await room('s-cro') <= 95, `${theme} 200px: s-cro's name box is as narrow as production's`);
            assert.ok(await room('s-cro-deep') <= 75, `${theme} 200px: s-cro-deep's name box is a level narrower`);
          }
          assert.equal(seen.filter(n => n.name === 'Conversion Rate Optimization Owner').length, 2, `${theme} ${width}px: both deep seats listed`);
          if (width === 236) {
            // A short name with room stays on one line: the words are not stacked (a global .nm is a flex column).
            for (const short of ['Firmware Engineer', 'Growth Manager'])
              assert.equal(seen.find(n => n.name === short)!.lines, 1, `${theme} 236px: "${short}" is one line`);
            const pdm = seen.find(n => n.name.startsWith('Partnerships Development Manager'))!;
            assert.ok(pdm.lines >= 2, 'a long name wraps rather than being cut');
          }
        }
      }
      await page.evaluate(() => { document.documentElement.style.setProperty('--rail-left', '236px'); });
      // A mark sits on the line of the name's last word.
      for (const [org, mark] of [['b:ui-owner', '.tree-goal'], ['b:partnerships', '.tree-goal'], ['b:devops', '.org-branch'], ['b:qa', '.tree-setup']] as const) {
        const row = page.locator(`#tree a.node[data-org="${org}"]`);
        assert.equal(await row.locator(`.nm .nm-end ${mark}`).count(), 1, `${org}: ${mark} rides the last word`);
        const same = await row.evaluate((a, sel) => {
          const end = a.querySelector('.nm-end')!, m = a.querySelector(sel)!;
          const word = document.createRange(); word.selectNodeContents(end.firstChild!);
          const wr = word.getClientRects()[word.getClientRects().length - 1]!, mr = m.getClientRects()[0]!;   // "Your branch" may wrap after its start
          return Math.abs((wr.top + wr.bottom) / 2 - (mr.top + mr.bottom) / 2) < 8;
        }, mark);
        assert.equal(same, true, `${org}: ${mark} on the same line as the last word`);
      }
      // A seat's state word and applicants count are a line under its name, no pill, taking none of the name's room.
      const seat = page.locator('#tree [data-seat="s-ops"]');
      assert.equal(await seat.count(), 1, 'the seat is listed');
      assert.equal(await seat.locator('.pill').count(), 0, 'no Recruiting pill');
      assert.equal((await seat.locator('.nm').innerText()).replace(/\s+/g, ' ').trim(), 'Operations Analyst', 'the name is the title alone');
      assert.equal(await seat.locator('.nm .seat-word').count(), 0, 'the state word is not inside the name');
      assert.equal(await seat.locator('.seat-line .cnt').innerText(), '2', 'the applicants count sits beside the state word');
      assert.equal((await seat.locator('.seat-word').innerText()).replace(/\s+/g, ' ').trim(), '· Recruiting', 'read as "· Recruiting"');
      // At the 200px minimum, three levels down: every seat's name and its state word whole, the word under the name.
      await page.evaluate(() => { document.documentElement.style.setProperty('--rail-left', '200px'); });
      for (const id of ['s-ops', 's-cro', 's-leads', 's-cro-deep', 's-cloud']) {
        const box = await page.locator(`#tree [data-seat="${id}"]`).evaluate(a => {
          const nm = a.querySelector<HTMLElement>('.nm')!, word = a.querySelector<HTMLElement>('.seat-word')!;
          const seen = [...word.childNodes].filter(n => n.nodeType === Node.TEXT_NODE);
          const r = document.createRange(); r.selectNodeContents(seen[seen.length - 1]!);
          return {nameBottom: nm.getBoundingClientRect().bottom, wordTop: r.getBoundingClientRect().top, word: word.textContent,
            cut: word.scrollWidth > word.clientWidth + 0.5 || a.scrollWidth > a.clientWidth + 0.5};
        });
        assert.ok(box.wordTop >= box.nameBottom - 0.5, `${id}: the state word is on its own line under the name`);
        assert.equal(box.cut, false, `${id}: the state word and row are whole at 200px`);
        assert.equal(box.word, id === 's-cloud' ? ' · Connecting' : ' · Recruiting');
      }
      await page.evaluate(() => { document.documentElement.style.setProperty('--rail-left', '236px'); });
      assert.deepEqual(errors, []);
      await context.close();
    }

    // AC-5: one count per row; the folded manager's dot on its chevron.
    {
      const {context, page, errors} = await open(browser);
      await page.evaluate(() => {
        const w = window as unknown as {needsMeCounts: () => Map<string, number>};
        w.needsMeCounts = () => new Map([['bot:firmware', 1], ['bot:hardware', 2]]);
        THREADS.dms = {'bot:firmware': 4, 'bot:devops': 2};   // the viewer's DM unread (threads.ts threadsDmUnread)
        renderTree();
      });
      await expandAll(page);
      const firmware = page.locator('#tree a.node[data-org="b:firmware"]');
      assert.equal((await firmware.locator('.cnt.needs').innerText()).trim(), '1', 'the Needs-you count');
      const dot = firmware.locator('.cnt.tree-dm');
      assert.equal(await dot.getAttribute('class'), 'cnt tree-dm dot', 'the DM unread is a dot beside it');
      assert.equal((await dot.innerText()).trim(), '', 'with no number');
      assert.equal(await dot.getAttribute('aria-label'), '4 unread from Firmware Engineer', 'and the same words');
      assert.deepEqual(await dot.evaluate(el => { const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }), [6, 6], 'a 6px dot');
      const devops = page.locator('#tree a.node[data-org="b:devops"] .cnt.tree-dm');
      assert.equal((await devops.innerText()).trim(), '2', 'alone, the DM unread stays a number');
      // Fold the Engineering Manager: its report needs you, so the dot rides its chevron.
      const chev = page.locator('#tree [data-toggle="b:eng-manager"]');
      await chev.click();
      await page.waitForFunction(() => document.querySelector('#tree [data-toggle="b:eng-manager"]')?.classList.contains('col'));
      const folded = page.locator('#tree [data-toggle="b:eng-manager"]');
      assert.equal(await folded.evaluate(el => el.classList.contains('needs')), true, 'the dot is on the chevron');
      assert.equal(await folded.getAttribute('aria-label'), 'Expand Engineering Manager · something inside needs you');
      assert.equal(await page.locator('#tree a.node[data-org="b:eng-manager"] .dot.needs').count(), 0, 'not in the row');
      assert.equal(await folded.evaluate(el => getComputedStyle(el, '::after').width), '6px');
      assert.deepEqual(errors, []);
      await context.close();
    }

    // On a phone ⋯ is the only way to Contacts and the switches: the rail's 40px target.
    {
      const {context, page, errors} = await open(browser, {width: 390, height: 844});
      await page.locator('#mobile-more').click();
      await page.waitForFunction(() => document.body.classList.contains('drawer'));
      assert.deepEqual(await page.locator('#org-more').evaluate(el => { const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }), [40, 40], '⋯ is 40×40 on a phone');
      assert.deepEqual(errors, []);
      await context.close();
    }

    // AC-7: the Team header and its ⋯ menu.
    {
      const {context, page, errors} = await open(browser);
      const head = await page.locator('.org-head').evaluate(h => [...h.children].filter(el => (el as HTMLElement).checkVisibility()).map(el => el.id || el.className));
      assert.deepEqual(head, ['section-toggle', 'org-add-group', 'org-more'], 'TEAM and its chevron, +, ⋯');
      const more = page.locator('#org-more'), menu = page.locator('#team-more-menu');
      assert.equal(await more.getAttribute('aria-label'), 'Team options');
      assert.equal(await more.evaluate(el => el.classList.contains('on')), false);
      // Keyboard: Enter opens with focus on the first row, arrows move, Space toggles and keeps it open, Escape closes.
      await more.focus();
      await page.keyboard.press('Enter');
      await menu.waitFor();
      assert.deepEqual(await menu.locator(':is(a,button)').evaluateAll(els => els.map(el => (el.textContent || '').replace(/contact_page|person|history|check/g, '').trim())),
        ['Contacts', 'Only bots I can open', 'Recently viewed first'], 'the rows, in words');
      assert.equal(await page.evaluate(() => document.activeElement?.textContent?.includes('Contacts')), true, 'focus on Contacts');
      const box = await menu.boundingBox(), anchor = await more.boundingBox();
      assert.ok(box && anchor && Math.abs(box.x - Math.max(8, anchor.x + anchor.width - box.width)) < 1.5 && box.x + box.width <= 1440
        && Math.abs(box.y - (anchor.y + anchor.height + 4)) < 1.5, 'right edge on ⋯ (never nearer the window edge than 8px), 4px below it');
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('ArrowDown');
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.orgMore), 'history');
      await page.keyboard.press(' ');
      assert.equal(await menu.isVisible(), true, 'a switch keeps the menu open');
      assert.equal(await page.locator('#team-more-menu [data-org-more="history"]').getAttribute('aria-checked'), 'true');
      assert.equal(await page.locator('#tree').evaluate(el => el.classList.contains('history')), true, 'the list sorts by recently viewed');
      assert.equal(await more.evaluate(el => el.classList.contains('on')), true, 'the dot on ⋯');
      assert.equal(await more.getAttribute('aria-label'), 'Team options · Recently viewed first');
      await page.keyboard.press('Escape');
      assert.equal(await menu.isVisible(), false, 'Escape closes');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'org-more', 'focus back on ⋯');
      // Mouse: Only bots I can open, with the list's own way back.
      await more.click();
      await page.locator('#team-more-menu [data-org-more="mine"]').click();
      assert.equal(await more.getAttribute('aria-label'), 'Team options · Only bots I can open · Recently viewed first');
      assert.match(await page.locator('#tree .org-filter-note').innerText(), /^Only bots you can open · Show all$/);
      // Kept across a reload.
      await page.reload();
      await page.locator('#tree a.node[data-org="b:firmware"]').waitFor({state: 'attached', timeout: t(20000)});
      assert.equal(await page.locator('#org-more').getAttribute('aria-label'), 'Team options · Only bots I can open · Recently viewed first', 'kept across a reload');
      await page.locator('#tree [data-org-show-all]').click();
      assert.equal(await page.locator('#tree .org-filter-note').count(), 0, 'Show all turns the filter off');
      assert.equal(await page.locator('#org-more').getAttribute('aria-label'), 'Team options · Recently viewed first');
      assert.deepEqual(errors, []);
      // Contacts opens its page and closes the menu (this fixture serves no contacts, so the page's own errors are not read).
      await page.locator('#org-more').click();
      await page.locator('#team-more-menu a[data-nav="contacts"]').click();
      await page.waitForFunction(() => location.hash === '#/library/contacts');
      assert.equal(await page.locator('#team-more-menu').isVisible(), false);
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('sidebar-team-room: ok');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
