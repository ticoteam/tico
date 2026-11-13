// The Rules page header (ui/app/org-rules.ts, ui/styles/org-rules.css) at desktop 1280 and 1024 and phone 390: on each tab
// (Internal, External, Due, Suggested) every tab link is the topmost element at its centre, no tab overlaps the header's
// actions (Internal's search, External's + Add rule and + Add document, Due's + Add due date), the actions are in view,
// and a real click on each tab opens it, with the header's rule-checks line at its longest. The search box once covered all four tabs at 1280, and + Add rule covered Suggested.
import type * as RealHubSupport from './support/real-hub.cts';
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

const TABS = ['internal', 'external', 'due', 'suggested'] as const;
const HASH: Record<string, string> = {internal: '#/rules', external: '#/rules/external', due: '#/rules/due', suggested: '#/rules/suggested'};
// What each tab draws into the header's actions (Dana is a person, so External and Due show their buttons).
const ACTS: Record<string, string[]> = {internal: ['.rr-q'], external: ['[data-ru-add]', '[data-ru-add-doc]'], due: ['[data-ru-add-due]'], suggested: []};

const FULL_STATS = {on: true, checks_today: 12480, unchecked_today: 1320, usd_left: 1840, people_usd: 312.45};

const ready = (page: Page) => page.waitForFunction(() => {
  const who = document.querySelector('#account .account-email')?.textContent || '';
  return who !== '' && who !== 'Signing in…' && (document.querySelector('#main')?.children.length as number) > 0;
}, null, {timeout: t(20000)});
const go = async (page: Page, hash: string) => { await page.evaluate(h => { location.hash = h; }, hash); };
const onTab = (page: Page, tab: string) => page.waitForFunction(k => document.querySelector(`.ru-tabs a[data-ru-tab="${k}"]`)?.classList.contains('cur'),
  tab, {timeout: t(15000)});

// Every tab link that the header covers, overlaps or pushes out of view, and every action not in view.
function headerProblems(page: Page, acts: string[]) {
  return page.evaluate((acts: string[]) => {
    const bad: string[] = [];
    const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
    const inView = (r: DOMRect) => r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= vw + 0.5 && r.top >= 0 && r.bottom <= vh + 0.5;
    const meets = (a: DOMRect, b: DOMRect) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
    const actRects = acts.map(sel => {
      const el = document.querySelector<HTMLElement>('.ru-acts ' + sel);
      if (!el) { bad.push(`${sel} is missing`); return null; }
      const r = el.getBoundingClientRect();
      if (!inView(r)) bad.push(`${sel} is out of view (${Math.round(r.left)}..${Math.round(r.right)} of ${vw})`);
      return [sel, r] as const;
    }).filter(x => x !== null);
    for (const a of document.querySelectorAll<HTMLElement>('.ru-tabs a[data-ru-tab]')) {
      a.scrollIntoView({block: 'nearest', inline: 'nearest'});   // a phone scrolls the tab row sideways
      const r = a.getBoundingClientRect(), k = a.dataset['ruTab'];
      if (!inView(r)) { bad.push(`tab ${k} is out of view`); continue; }
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!top || !a.contains(top)) bad.push(`tab ${k} is covered by ${top ? top.tagName.toLowerCase() + '.' + [...top.classList].join('.') : 'nothing'}`);
      for (const [sel, ar] of actRects) if (meets(r, ar)) bad.push(`tab ${k} overlaps ${sel}`);
    }
    return bad;
  }, acts);
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});
  let browser: Browser | undefined;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const problems: string[] = [];
    for (const [width, phone] of [[1280, false], [1024, false], [390, true]] as const) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        if (!phone) await page.setViewportSize({width, height: 800});
        // The header's rule-checks line at its longest, as a busy team's reads (the sweep's 1280 page had all five parts).
        await page.route('**/api/v2/rules/stats*', r => r.fulfill({json: FULL_STATS}));
        await ready(page);
        for (const from of TABS) {
          await go(page, HASH[from]!);
          await onTab(page, from);
          for (const sel of ACTS[from]!) await page.locator('.ru-acts ' + sel).waitFor({timeout: t(15000)});
          // The rule-checks line lands in the header after its own read; check the header only once it is there.
          await page.waitForFunction(() => (document.querySelector('.ru-ph .ru-checks')?.textContent || '').length > 0, null, {timeout: t(15000)});
          // While the route's fade runs, Chrome gives the pointer to the root element (ui/app/router.ts), and the fade can
          // start after the page has drawn: wait until the current tab's centre hits something other than the root.
          await page.waitForFunction(() => {
            const r = document.querySelector('.ru-tabs a.cur')?.getBoundingClientRect();
            return !!r && document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) !== document.documentElement;
          }, null, {timeout: t(15000)});
          for (const p of await headerProblems(page, ACTS[from]!)) problems.push(`${width}px on ${from}: ${p}`);
          // A real click on the next tab opens it (Playwright refuses to click a link something else covers).
          const to = TABS[(TABS.indexOf(from) + 1) % TABS.length]!;
          try {
            await page.locator(`.ru-tabs a[data-ru-tab="${to}"]`).click({timeout: t(3000)});
            await onTab(page, to);
          } catch (e) {
            problems.push(`${width}px on ${from}: clicking ${to} failed: ${(e as Error).message.split('\n')[0]}`);
          }
        }
      } finally {
        await page.context().close();
      }
    }
    assert.deepEqual(problems, [], 'Rules header problems:\n' + problems.join('\n'));
    console.log('org-rules-header: every tab clickable with its actions in view at 1280, 1024 and 390');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
