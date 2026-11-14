// The one page header (ui/app/page-head.ts, ui/styles/page-head.css; spec "One page header on every screen", task #338)
// on every week-one screen, on a real hub as Dana: at 1440x900 the title is the header's first item, 16px/600, at the same
// left edge on every screen (AC-1); at most one filled button, worded, no "+", 32px, last in the header, and none on Usage,
// Updates and Learnings (AC-2); every tab is a .ph-tab at 13px with only the current one on --surface2 at 600, and every
// single-choice filter (.ph-seg) has that look with no shadow (AC-3); at 390x844 the title and the button share a row
// (Tasks instead is one row of icons at 390 and 360, the UI Owner's call),
// tabs are 40px in their own row and nothing scrolls sideways (AC-4); at 1100x800 the header is one row and the button in
// view (AC-5); Docs', Tools', Meetings' and Updates' ⋯ list what moved there and Escape closes it (AC-6).
import type * as RealHubSupport from './support/real-hub.cts';
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

// `iconRow`: on a phone the header is one dense row of icons with the title kept for screen readers only (Tasks).
// `row`: the header's own one-row height where it is not the shared 41px (Tasks' 56px row, spec "Pipeline board: one-row header").
type Screen = {name: string; hash: string; tabs?: boolean; button?: boolean; more?: string[]; iconRow?: boolean; opens?: [string, string]; row?: number};
// The week-one table. `button: false` = no filled button allowed; undefined = at most one.
const SCREENS: Screen[] = [
  {name: 'Strategy', hash: '#/strategy', tabs: true},
  {name: 'Goals', hash: '#/strategy/goals', tabs: true},
  {name: 'Budget', hash: '#/strategy/budget', tabs: true},
  {name: 'Tasks', hash: '#/tasks', button: false, iconRow: true, more: ['New task', 'Archived'], row: 56},
  {name: 'Automations', hash: '#/automations', button: true},
  {name: 'Listening', hash: '#/listening', button: true},
  {name: 'Rules', hash: '#/rules', tabs: true},
  {name: 'Docs', hash: '#/docs', button: true, more: ['Import a file…', 'Add source…']},
  {name: 'Tools', hash: '#/tools', more: ['Keys', 'Settings'], opens: ['Keys', '^#/tools/keys']},
  // Sources joins Meetings' ⋯ once a source is connected (the source tiles stand in for it before); the seeded hub has none.
  {name: 'Meetings', hash: '#/meetings', tabs: true, more: ['Settings']},
  {name: 'Usage', hash: '#/usage', tabs: true, button: false},
  {name: 'Updates', hash: '#/updates', tabs: true, button: false, more: ['Archive'], opens: ['Archive', 'view=archive']},
  {name: 'Archive', hash: '#/updates?view=archive', button: false},
  {name: 'Learnings', hash: '#/learnings', button: false},
];

const ready = (page: Page) => page.waitForFunction(() => {
  const who = document.querySelector('#account .account-email')?.textContent || '';
  return who !== '' && who !== 'Signing in…' && (document.querySelector('#main')?.children.length as number) > 0;
}, null, {timeout: t(20000)});

async function open(page: Page, s: Screen) {
  await page.evaluate(h => { location.hash = h; }, s.hash);
  await page.locator('#main header.ph > h1.ph-t').first().waitFor({timeout: t(15000)});
  await page.waitForTimeout(t(800));   // buttons and counts a page fills once its data is in
  await page.mouse.move(1, 1);         // no hover look on a tab
}

// Everything AC-1..AC-3 measure, read in the page. Problems come back as lines.
function desktopProblems(page: Page, s: Screen) {
  return page.evaluate((s: Screen) => {
    const bad: string[] = [];
    const head = document.querySelector<HTMLElement>('#main header.ph')!;
    const probe = document.createElement('div'); probe.style.background = 'var(--surface2)'; document.body.append(probe);
    const surface2 = getComputedStyle(probe).backgroundColor; probe.remove();
    const first = head.firstElementChild as HTMLElement;
    if (!first.matches('.ph-back, h1.ph-t')) bad.push(`first item is ${first.className}`);
    const h1 = head.querySelector<HTMLElement>(':scope > h1.ph-t')!, cs = getComputedStyle(h1);
    if (cs.fontSize !== '16px' || cs.fontWeight !== '600') bad.push(`title ${cs.fontSize} ${cs.fontWeight}`);
    const prim = [...head.querySelectorAll<HTMLElement>('.primary')].filter(b => b.offsetParent !== null || b.getClientRects().length);
    if (prim.length > 1) bad.push(`${prim.length} filled buttons`);
    if (s.button === false && prim.length) bad.push('a filled button where there should be none');
    if (s.button === true && !prim.length) bad.push('no filled button');
    for (const b of prim) {
      const act = b.closest('.ph-act');
      if (!act || act.parentElement !== head || head.lastElementChild !== act) bad.push('the filled button is not last, in .ph-act');
      const words = (b.textContent || '').trim();
      if (!/[A-Za-z]{2}/.test(words) || words.includes('+')) bad.push(`button words "${words}"`);
      if (Math.abs(b.getBoundingClientRect().height - 32) > 0.5) bad.push(`button ${b.getBoundingClientRect().height}px high`);
    }
    const nav = head.querySelector<HTMLElement>(':scope > .ph-tabs');
    if (s.tabs && !nav?.children.length) bad.push('no tabs');
    if (nav) {
      const tabs = [...nav.children] as HTMLElement[];
      const cur = tabs.filter(x => x.matches('.cur, [aria-current=page], [aria-selected=true]'));
      if (tabs.some(x => !x.matches('.ph-tab'))) bad.push('a tab that is not .ph-tab');
      if (cur.length !== 1) bad.push(`${cur.length} current tabs`);
      for (const x of tabs) {
        const c = getComputedStyle(x), on = cur.includes(x);
        if (c.fontSize !== '13px') bad.push(`tab "${x.textContent}" ${c.fontSize}`);
        if (on && (c.backgroundColor !== surface2 || c.fontWeight !== '600')) bad.push(`current tab ${c.backgroundColor} ${c.fontWeight}`);
        if (!on && c.backgroundColor !== 'rgba(0, 0, 0, 0)') bad.push(`tab "${x.textContent}" has a background`);
      }
    }
    for (const seg of document.querySelectorAll<HTMLElement>('#main .ph-seg')) {
      if (!seg.getClientRects().length) continue;
      if (getComputedStyle(seg).backgroundColor !== 'rgba(0, 0, 0, 0)') bad.push(`.ph-seg ${seg.className} has a track`);
      for (const b of seg.querySelectorAll<HTMLElement>(':scope > :is(button,a)')) {
        const c = getComputedStyle(b), on = b.matches('.cur,[aria-selected=true],[aria-pressed=true],[aria-current=page],[aria-checked=true]');
        if (c.boxShadow !== 'none') bad.push(`.ph-seg ${seg.className} "${b.textContent}" has a shadow`);
        if (on && (c.backgroundColor !== surface2 || c.fontWeight !== '600')) bad.push(`.ph-seg ${seg.className} selected look ${c.backgroundColor} ${c.fontWeight}`);
      }
    }
    return bad;
  }, s);
}

// Tasks on a phone: one 44 px row (every shown control centred on the scope button's line, ⋯ last), nothing accent on it,
// the title only for screen readers, nothing sideways.
function iconRowProblems(page: Page) {
  return page.evaluate(() => {
    const bad: string[] = [], head = document.querySelector<HTMLElement>('#main header.ph')!;
    const mid = (el: Element) => { const r = el.getBoundingClientRect(); return (r.top + r.bottom) / 2; };
    const scope = head.querySelector<HTMLElement>('#task-scope')!, line = mid(scope);
    const shown = [...head.querySelectorAll<HTMLElement>(':scope > .ph-tools > *, :scope > .ph-more')].filter(el => el.getClientRects().length);
    for (const el of shown) if (Math.abs(mid(el) - line) > 4) bad.push(`${el.id || el.className} is off the one row`);
    if (head.querySelector('.primary')) bad.push('a filled button on the row');
    const more = head.querySelector<HTMLElement>(':scope > .ph-more');
    if (!more || shown.at(-1) !== more) bad.push('⋯ is not last on the row');
    const h = head.getBoundingClientRect().height - parseFloat(getComputedStyle(head).borderBottomWidth);
    if (Math.abs(h - 44) > 0.5) bad.push(`the row is ${h}px high`);
    if (head.querySelector<HTMLElement>(':scope > h1.ph-t')!.getBoundingClientRect().width > 1) bad.push('the title shows');
    const de = document.documentElement;
    if (de.scrollWidth > de.clientWidth) bad.push(`the page scrolls sideways (${de.scrollWidth} > ${de.clientWidth})`);
    return bad;
  });
}

const titleLeft = (page: Page) => page.evaluate(() =>
  document.querySelector<HTMLElement>('#main header.ph')!.firstElementChild!.getBoundingClientRect().left);

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});
  let browser: Browser | undefined;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const problems: string[] = [];
    const say = (s: Screen, at: string, lines: string[]) => problems.push(...lines.map(l => `${s.name} ${at}: ${l}`));

    // 1440x900: AC-1, AC-2, AC-3, AC-6.
    {
      const page = await pageAs(browser, hub, 'dana');
      try {
        await ready(page);
        let left: number | null = null;
        for (const s of SCREENS) {
          try { await open(page, s); } catch (e) { say(s, '1440', [`no header: ${(e as Error).message.split('\n')[0]}`]); continue; }
          say(s, '1440', await desktopProblems(page, s));
          const x = await titleLeft(page);
          left ??= x;
          if (Math.abs(x - left) > 1) say(s, '1440', [`title starts at ${x}px, Strategy's at ${left}px`]);
          if (s.more) {
            const more = page.locator('#main header.ph > .ph-more');
            await more.click();
            const menu = page.locator('#main header.ph > .ph-menu:popover-open');
            await menu.waitFor({timeout: t(3000)}).catch(() => say(s, '1440', ['⋯ did not open']));
            const items = (await menu.locator(':is(button,a):visible').allInnerTexts()).map(x => x.trim());
            for (const want of s.more) if (!items.includes(want)) say(s, '1440', [`⋯ lacks "${want}" (has ${items.join(', ')})`]);
            await page.keyboard.press('Escape');
            if (await menu.count()) say(s, '1440', ['Escape left ⋯ open']);
            // A click outside closes it too.
            await more.click();
            await menu.waitFor({timeout: t(3000)}).catch(() => say(s, '1440', ['⋯ did not open again']));
            await page.locator('#main header.ph > h1.ph-t').click();
            if (await menu.count()) say(s, '1440', ['a click outside left ⋯ open']);
            // The item that moved there opens what it opened before.
            if (s.opens) {
              await more.click();
              await menu.locator(':is(button,a)', {hasText: s.opens[0]}).click();
              await page.waitForFunction(re => new RegExp(re).test(location.hash), s.opens[1], {timeout: t(5000)})
                .catch(async () => say(s, '1440', [`⋯ ${s.opens![0]} went to ${await page.evaluate(() => location.hash)}`]));
            }
          }
        }
      } finally { await page.context().close(); }
    }

    // Narrower than 1440, above a phone: 1280, 1100, 900 and 800. Every tab sits inside its row and every header control
    // is what a click at its centre hits, inside the window (Review 1: tabs slid out of their row, Ask and Live now went
    // under Add notes). Where it all fits, the header is one row (AC-5): at 1100 on every screen whose title, tabs, ⋯ and
    // button fit with their tools (all but Rules' eight tabs and Meetings).
    {
      const page = await pageAs(browser, hub, 'dana');
      try {
        await ready(page);
        for (const width of [1280, 1100, 900, 800]) {
          await page.setViewportSize({width, height: 800});
          for (const s of SCREENS) {
            try { await open(page, s); } catch { say(s, String(width), ['no header']); continue; }
            await page.waitForTimeout(t(100));   // the fit runs on the next frame
            say(s, String(width), await page.evaluate(([oneRow, row]) => {
              const bad: string[] = [], head = document.querySelector<HTMLElement>('#main header.ph')!, r = head.getBoundingClientRect();
              const vw = document.documentElement.clientWidth;
              const name = (el: Element) => el.id ? '#' + el.id : (el.textContent || '').trim().slice(0, 24) || el.className;
              const nav = head.querySelector<HTMLElement>(':scope > .ph-tabs');
              if (nav) {
                const nr = nav.getBoundingClientRect();
                for (const x of nav.children) {
                  const xr = x.getBoundingClientRect();
                  if (xr.left < nr.left - 0.5 || xr.right > nr.right + 0.5) bad.push(`tab "${name(x)}" is outside its row`);
                }
              }
              const controls = [...head.querySelectorAll<HTMLElement>(':scope > .ph-tabs > *, :scope > .ph-tools :is(button, a, input, summary, select), :scope > .ph-more, :scope > .ph-act :is(button, a)')]
                .filter(el => el.getClientRects().length && !el.closest('details:not([open]) > :not(summary), [popover]:not(:popover-open)') && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).opacity !== '0');
              for (const el of controls) {
                const cr = el.getBoundingClientRect();
                if (cr.right > vw + 0.5 || cr.left < -0.5) { bad.push(`${name(el)} is past the window`); continue; }
                const hit = document.elementFromPoint(cr.left + cr.width / 2, cr.top + cr.height / 2);
                if (!hit || !(el.contains(hit) || hit.contains(el))) bad.push(`${name(el)} is covered by ${hit ? name(hit) : 'nothing'}`);
              }
              if (oneRow) {
                // One row is 41px under the top padding (.ph-pad adds 8px above, on layouts that zero #main's padding).
                const tall = r.height - parseFloat(getComputedStyle(head).paddingTop);
                if (tall > (row ?? 41) + 0.5) bad.push(`header ${Math.round(tall)}px high under its top padding: more than one row`);
              }
              return bad;
            }, [width === 1100 && !['Rules', 'Meetings'].includes(s.name), s.row ?? null] as const));
          }
        }
      } finally { await page.context().close(); }
    }

    // 390x844, a phone: AC-4.
    {
      const page = await pageAs(browser, hub, 'dana', {phone: true});
      try {
        await ready(page);
        for (const s of SCREENS) {
          try { await open(page, s); } catch { say(s, '390', ['no header']); continue; }
          if (s.iconRow) {
            for (const width of [390, 360]) {
              await page.setViewportSize({width, height: 844});
              say(s, String(width), await iconRowProblems(page));
            }
            await page.setViewportSize({width: 390, height: 844});
            continue;
          }
          say(s, '390', await page.evaluate(() => {
            const bad: string[] = [], head = document.querySelector<HTMLElement>('#main header.ph')!;
            const h1 = head.querySelector<HTMLElement>(':scope > h1.ph-t')!.getBoundingClientRect();
            if (!h1.width) bad.push('the title is hidden');
            const b = head.querySelector<HTMLElement>('.ph-act .primary');
            if (b) {
              const br = b.getBoundingClientRect();
              if (Math.abs((br.top + br.bottom) / 2 - (h1.top + h1.bottom) / 2) > 4) bad.push('the filled button is not on the title row');
            }
            const nav = head.querySelector<HTMLElement>(':scope > .ph-tabs');
            if (nav?.children.length) {
              const nr = nav.getBoundingClientRect();
              if (nr.top < h1.bottom - 0.5) bad.push('tabs are not in their own row');
              for (const x of nav.children) if ((x as HTMLElement).getBoundingClientRect().height < 39.5) bad.push(`tab "${x.textContent}" under 40px`);
            }
            const de = document.documentElement;
            if (de.scrollWidth > de.clientWidth) bad.push(`the page scrolls sideways (${de.scrollWidth} > ${de.clientWidth})`);
            return bad;
          }));
        }
      } finally { await page.context().close(); }
    }

    assert.deepEqual(problems, [], 'page header problems:\n' + problems.join('\n'));
    console.log(`page-head: ${SCREENS.length} screens share one header at 1440, 1100 and 390`);
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
