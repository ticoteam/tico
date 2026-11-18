import type * as Tasks from './tasks-page.cts';
import type {FrameLocator, Page} from 'playwright';
// A task's document in the task window, drawn in the app's look (ui/app/task-docs.ts: docLook, pageIsLightOnly). Three
// pages, every request intercepted:
// - a Spec with colours of its own and nothing about dark (a bot's page that read near-black on the dark app) is drawn
//   light in both themes: a white sheet inside the app;
// - a page styled through the template tokens (var(--ink), var(--surface), var(--muted), fallbacks and all) follows the
//   app in both themes, and re-themes when the app does;
// - a page that handles dark itself (a prefers-color-scheme block) keeps the app's look.
// TICO_SHOTS=<dir> saves the light-only Spec in the dark app.
declare function pageIsLightOnly(raw: string): boolean;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const shots = process.env.TICO_SHOTS || '';
const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const LIGHT = 'file-look-light', TOKENS = 'file-look-tokens', DARK = 'file-look-dark';
// Like the Spec the Strategy Product Owner bot wrote: its own light colours, no background, nothing about dark.
const LIGHT_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Ask the owner</title><style>
body{font:15px/1.5 system-ui,sans-serif;color:#1d1d1f;max-width:720px;margin:0 auto;padding:24px}
.meta{color:#666}h2{border-bottom:1px solid #e5e5e5;padding-bottom:4px}
.card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:12px}
code{background:#f3f3f3;padding:1px 4px;border-radius:4px}
</style></head><body><h1>Ask the owner when a bet's check date passes</h1><p class="meta">Strategy Product Owner · Spec</p>
<h2>Goal</h2><div class="card">When a bet's check date passes, ask its owner.</div><p><code>bets.check_date</code></p></body></html>`;
// Styled through the template tokens; its one literal colour is a fallback, which the token always beats.
const TOKENS_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Token spec</title><style>
.pg{max-width:720px;margin:0 auto;padding:16px;font:var(--t-base)/1.5 var(--sans,system-ui,sans-serif);color:var(--ink);background:var(--surface)}
.pg h1{font-size:var(--t-lg);margin:0 0 8px}
.meta{color:var(--muted)}
h2{border-bottom:1px solid var(--line);padding-bottom:4px}
#lead{border-left:3px solid var(--fail,#c53030);padding:8px 12px}
pre{background:var(--surface2);padding:8px}
</style></head><body><main class="pg"><h1>Token spec</h1><p class="meta">Styled through the template tokens.</p>
<div id="lead"><p><b>Decide:</b> OK this spec, or send it back.</p></div><h2>Details</h2><pre>bets.check_date</pre></main></body></html>`;
// Handles dark itself: a dark block for the dark colours.
const DARK_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dark spec</title><style>
h1{color:#111}
@media (prefers-color-scheme: dark){h1{color:#eee}}
</style></head><body><h1>Handles dark itself</h1><p>Some text.</p></body></html>`;
const PAGES: Record<string, string> = {[`${LIGHT}@1`]: LIGHT_HTML, [`${TOKENS}@1`]: TOKENS_HTML, [`${DARK}@1`]: DARK_HTML};
const docs = () => [
  {name: 'Light spec', file_id: LIGHT, version: 1, by: 'bot:writer', at: ago(20), versions: [{n: 1, by: 'bot:writer', at: ago(20)}]},
  {name: 'Token spec', file_id: TOKENS, version: 1, by: 'bot:writer', at: ago(20), versions: [{n: 1, by: 'bot:writer', at: ago(20)}]},
  {name: 'Dark spec', file_id: DARK, version: 1, by: 'bot:writer', at: ago(20), versions: [{n: 1, by: 'bot:writer', at: ago(20)}]},
];
const tasks = () => [task('t-3', 'Ask the owner when a bet\'s check date passes', 'spec',
  {owner_id: 'bot:writer', documents: docs()} as Parameters<typeof task>[3])];
// The pages' files and pins, as the task window reads them (the same stubs task-documents.cts uses).
const files = async (page: Page) => {
  await page.route(url => url.pathname.startsWith('/api/v2/places/'), route => route.fulfill({contentType: 'application/json', body: JSON.stringify({can_link: true, pins: []})}));
  await page.route(url => url.pathname.startsWith('/api/v2/files/'), route => {
    const url = new URL(route.request().url()), id = decodeURIComponent(url.pathname.split('/').pop()!);
    const body = PAGES[`${id}@${url.searchParams.get('v') || 1}`];
    return body ? route.fulfill({contentType: 'text/html', body}) : route.fulfill({status: 404, contentType: 'application/json', body: '{}'});
  });
};
// What pageIsLightOnly says of a page's raw HTML: true for a page of its own colours that says nothing about dark.
const CASES: [string, boolean][] = [
  ['<h1 style="color:#222">Spec</h1>', true],                                                    // a style attribute
  ['<style>h1{border-color:#ccc}</style>', true],                                                // a border colour
  ['<style>p{color:rgb(34,34,34)}</style>', true],
  ['<p style="background: white">x</p>', true],                                                  // a colour name
  ['<style>h1{margin:0;font-size:13px}</style>', false],                                         // no colour of its own
  ['<style>h1{color:var(--ink)}</style><h1 style="background:var(--surface,#fff)">x</h1>', false], // tokens, fallbacks included
  ['<style>h1{color:#222}@media (prefers-color-scheme: dark){h1{color:#eee}}</style>', false],   // says it handles dark
  ['<h1 style="color:#222;color-scheme:light dark">x</h1>', false],                              // ...in an attribute too
  ['<style>body{background:url(data:image/svg+xml,%3Csvg%3E%3Crect%20fill%3D%22%23fff%22%2F%3E%3C%2Fsvg%3E) no-repeat}</style>', false], // a picture's colours are data
  ['<style>h1{background:none;text-decoration-color:#222}</style>', false],                      // not a colour declaration
];
type Paint = {bg: string; fg: string; scheme: string; edge: string};
const paintOf = (frame: FrameLocator, sel: string) => frame.locator(sel).first().evaluate(el => {
  const s = getComputedStyle(el);
  return {bg: s.backgroundColor, fg: s.color, scheme: s.getPropertyValue('color-scheme'), edge: s.borderLeftColor};
});
const expectPaint = async (frame: FrameLocator, sel: string, want: Partial<Paint>, what: string) => {
  const got = await paintOf(frame, sel);
  assert.deepEqual(Object.fromEntries(Object.keys(want).map(k => [k, got[k as keyof Paint]])), want, `${what} (${sel})`);
};
const shot = async (page: Page, name: string) => {
  if (!shots) return;
  await page.waitForTimeout(250);
  await page.screenshot({path: path.join(shots, name + '.png')});
};
const texts = (page: Page) => page.locator('#task-modal[open] [role="tab"]').evaluateAll(els => els.map(e => (e.textContent || '').trim()));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, theme, hash: '#/task/t-3', tasks: tasks(), needs: [], beforeGoto: files});
      const win = page.locator('#task-modal[open][data-task="t-3"]');
      await win.locator('.tw-tabs').waitFor();
      // The three documents have their tabs, in order (Chat, Files and any other fixed tab are not this test's).
      const docs = ['Light spec', 'Token spec', 'Dark spec'];
      assert.deepEqual((await texts(page)).filter(t => docs.includes(t)), docs);
      // A document's frame once its tab is shown and its page drawn.
      const show = async (name: string): Promise<FrameLocator> => {
        await win.getByRole('tab', {name}).click();
        await win.locator(`.task-doc iframe[title="${name} v1"]`).waitFor();
        return win.locator('.task-doc').frameLocator('iframe.html-view');
      };
      if (theme === 'dark') {
        const got = await page.evaluate(list => list.map(raw => pageIsLightOnly(raw)), CASES.map(c => c[0]));
        assert.deepEqual(got, CASES.map(c => c[1]), 'which pages draw light (pageIsLightOnly)');

        // The light-only Spec in the dark app: a white sheet, its own text colours on it.
        const light = await show('Light spec');
        await shot(page, 'light-only-spec-dark');
        await expectPaint(light, 'html', {bg: 'rgb(255, 255, 255)', fg: 'rgb(17, 17, 17)', scheme: 'light'}, 'a light-only page is drawn light in the dark app');
        await expectPaint(light, 'body', {fg: 'rgb(29, 29, 31)'}, 'its own text colour');
        await expectPaint(light, '.meta', {fg: 'rgb(102, 102, 102)'}, 'its own grey');

        // The token page in the dark app: the dark tokens, through the template's own rules.
        const token = await show('Token spec');
        await expectPaint(token, 'html', {bg: 'rgb(16, 16, 16)', fg: 'rgb(244, 244, 244)', scheme: 'dark'}, 'a page of tokens takes the dark app');
        await expectPaint(token, '.pg', {bg: 'rgb(22, 22, 22)', fg: 'rgb(244, 244, 244)'}, 'its surface and ink are the app\'s');
        await expectPaint(token, '.meta', {fg: 'rgb(155, 155, 155)'}, 'its muted text');
        await expectPaint(token, 'pre', {bg: 'rgb(30, 30, 30)'}, 'its code surface');
        await expectPaint(token, '#lead', {edge: 'rgb(240, 128, 128)'}, 'its fail colour from the token, not the fallback');

        // The page that handles dark itself keeps the app's look.
        const dark = await show('Dark spec');
        await expectPaint(dark, 'html', {bg: 'rgb(16, 16, 16)', scheme: 'dark'}, 'a page that handles dark keeps the app\'s look');

        // Re-themed while open: the token page follows the app to light.
        await show('Token spec');
        await page.evaluate(() => (window as unknown as {ticoTheme: {set(t: string): void}}).ticoTheme.set('light'));
        const tokenNow = win.locator('.task-doc').frameLocator('iframe.html-view');
        const bgOf = () => paintOf(tokenNow, '.pg').then(p => p.bg).catch(() => '');
        for (const t0 = Date.now(); await bgOf() !== 'rgb(245, 245, 245)' && Date.now() - t0 < 5000;) await page.waitForTimeout(50);
        await expectPaint(tokenNow, '.pg', {bg: 'rgb(245, 245, 245)', fg: 'rgb(17, 17, 17)'}, 'the token page follows the app to light');
        await expectPaint(tokenNow, 'html', {bg: 'rgb(255, 255, 255)', scheme: 'light'}, 'and its sheet with it');
      } else {
        // In the light app: the light-only Spec is the same white sheet; a page of tokens takes the light palette.
        const light = await show('Light spec');
        await expectPaint(light, 'html', {bg: 'rgb(255, 255, 255)', fg: 'rgb(17, 17, 17)', scheme: 'light'}, 'a light-only page in the light app');
        const token = await show('Token spec');
        await expectPaint(token, 'html', {bg: 'rgb(255, 255, 255)', fg: 'rgb(17, 17, 17)', scheme: 'light'}, 'a page of tokens in the light app');
        await expectPaint(token, '.pg', {bg: 'rgb(245, 245, 245)', fg: 'rgb(17, 17, 17)'}, 'its surface and ink, light');
        await expectPaint(token, '.meta', {fg: 'rgb(94, 94, 94)'}, 'its muted text, light');
        await expectPaint(token, '#lead', {edge: 'rgb(197, 48, 48)'}, 'its fail colour, light');
        const dark = await show('Dark spec');
        await expectPaint(dark, 'html', {bg: 'rgb(255, 255, 255)', scheme: 'light'}, 'a page that handles dark keeps the light app\'s look');
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: a task document: a page of its own colours and no dark is a light sheet; a page of template tokens follows the app in both themes and re-themes; a page that handles dark keeps the app\'s look.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
