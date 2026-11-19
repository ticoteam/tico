// Growth and Brand on a real hub. The Growth page is the one read (backend/growth_core.py, ui/app/growth-page.ts): with
// nothing loaded, every figure says why it has none and nothing calls a removed Growth route; then a channel and a
// month of customers pushed through the API show on it for the month the address names. Old addresses land where they
// moved. Then Strategy has How we sell, Brand's Edit publishes, and Goals switches its KPIs between Tree, Loops and
// Journey. Phone only; ui/tests/growth-page.cts covers the page's three company shapes on desktop and phone.
import type {Page} from 'playwright';
import type * as R from './support/redesign-e2e.cts';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {run, open, go, ok, shot, t} = require('./support/redesign-e2e.cts') as R.Api;

// What the pages the one read replaced called (the cards, the Growth line, Settings, Sources and Get started).
const REMOVED = /\/api\/v2\/(gtm|places\/growth|growth\/(ideas|evidence|working|week|numbers|setup|sources|cohorts|computed|line))\b/;
// The test hub's clock stands still here (evals/factory/world.py).
const HUB_NOW = Date.UTC(2026, 9, 20, 9);
const monthsAgo = (n: number) => { const d = new Date(HUB_NOW); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - n); return d.toISOString().slice(0, 7); };

const flat = async (page: Page, what: string) => {
  const info = await page.evaluate(() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, hash: location.hash,
    all: [...document.querySelectorAll('*')].map(el => [el, el.getBoundingClientRect()] as const).filter(([, r]) => r.right > document.documentElement.clientWidth + 1)
      .slice(0, 8).map(([el, r]) => `${el.tagName}.${el.className}:${Math.round(r.left)}-${Math.round(r.right)}`)}));
  assert.ok(info.sw <= info.cw + 1, `${what} no sideways scroll (${JSON.stringify(info)})`);
};
// With TICO_UI_SHOTS set: the whole page, light and dark.
async function themed(page: Page, name: string) {
  if (!process.env.TICO_UI_SHOTS) return;
  const size = page.viewportSize()!;
  for (const theme of ['light', 'dark']) {
    await page.evaluate((v: string) => { document.documentElement.dataset.theme = v; }, theme);
    const tall = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.querySelector('#main')?.scrollHeight || 0));
    await page.setViewportSize({width: size.width, height: Math.max(size.height, tall + 40)});
    await page.waitForTimeout(150);
    await page.evaluate(() => document.getAnimations().filter(a => a.effect?.getTiming?.().iterations === Infinity).forEach(a => a.cancel()));
    await shot(page, `${name}-${theme}`);
  }
  await page.setViewportSize(size);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}
async function seedKpis(page: Page) {
  const goal = await ok<{goal?: {id: string}; id?: string}>(page, 'POST', 'goals', {title: 'More active homes', owner: 'human:dana'});
  const goalId = (goal.goal?.id || goal.id)!;
  const kpi = async (name: string, unit: string, target: number, role: string | null, parent: string | null, stage: string | null, values: number[], goal_id: string | null = null) => {
    const made = await ok<{kpi?: {id: string}; id?: string}>(page, 'POST', 'kpis', {name, unit, kind: 'improve', target, deadline: '2027-12-31', ...(goal_id ? {goal_id} : {})});
    const id = (made.kpi?.id || made.id)!;
    await ok(page, 'POST', `kpis/${id}`, {growth_role: role, parent_kpi_id: parent, stage});
    for (let i = 0; i < values.length; i++) {
      await ok(page, 'POST', `kpis/${id}/readings`, {value: values[i], period_end: `${monthsAgo(values.length - 1 - i)}-10`, quality: 'measured'});
    }
    return id;
  };
  const nsId = await kpi('Active homes', 'homes', 1000, 'north_star', null, null, [312, 334, 341, 368, 402, 431, 448, 490, 533, 566, 612, 648], goalId);
  const inpId = await kpi('New bookings', '', 120, 'input', nsId, 'acquire', [52, 58, 61, 66, 64, 71, 77, 74, 82, 88, 93, 97]);
  await kpi('Repeat rate', '%', 70, 'input', nsId, 'retain', [58, 59, 61, 60, 62, 63, 62, 64, 66, 65, 67, 68]);
  await kpi('Refund rate', '%', 2, 'guardrail', nsId, null, [2.4, 2.2, 2.3, 2.0, 1.9, 2.1, 1.8, 1.7, 1.9, 1.6, 1.7, 1.5]);
  await kpi('Site visits', 'visits', 40000, null, null, 'reach', [14200, 15100, 15800, 17200, 18400, 19900, 21100, 23800, 24600, 26900, 28200, 31000]);
  return {nsId, inpId, goalId};
}
run('growth', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone});
  const width = phone ? 390 : 1440;
  const noErrors = (where: string) => assert.deepEqual(page.errors, [], `${tag}: ${where} without page errors`);
  const removed: string[] = [];
  page.on('request', (r: {url(): string}) => { if (REMOVED.test(r.url())) removed.push(r.url()); });
  const toasts = () => page.locator('.toast').allInnerTexts();
  const arrive = (to: string, why: string) => page.waitForFunction((w: string) => location.hash === w, to, {timeout: t(5000)}).catch(async () =>
    assert.fail(`${tag}: ${why} lands on ${to} (${await page.evaluate(() => location.hash)})`));
  const tiles = () => page.locator('#gr-body.gp .gp-tile').first().waitFor({timeout: t(15000)});

  // Nothing loaded: the page opens from the sidebar and every figure says why it has none.
  if (phone) await page.locator('#mobile-more').click();
  await page.locator('#side .rd-co-toggle').click();   // Company starts folded
  await page.locator('#side a[data-nav="growth"]').click();
  await arrive('#/growth', 'the sidebar');
  await tiles();
  assert.equal(await page.locator('#main header.ph h1').innerText(), 'Growth', `${tag}: the title`);
  assert.equal(await page.locator('#gr-body .gp-tile .gp-v:not(:has(.gp-word))').count(), 0, `${tag}: every figure says why it has none`);
  await flat(page, `${tag}: empty`);
  await themed(page, `growth-${width}-empty`);

  // A channel and a month of customers, pushed as a source would: the page shows them for the month the address names.
  const month = monthsAgo(1);
  await ok(page, 'POST', 'growth/channels', {slug: 'flyers', name: 'Flyers'});
  await ok(page, 'POST', 'growth/rows', {source: 'hand', csv: `anchor,period,channel,as_of,entered\ncustomer,${month},flyers,${monthsAgo(0)}-19,9\n`});
  await go(page, '#/tasks');
  await go(page, `#/growth?m=${month}`);
  await page.locator('#gr-body tr[data-channel="flyers"]').waitFor({timeout: t(15000)}).catch(async () => assert.fail(`${tag}: the channel shows (${await toasts()})`));
  assert.equal(await page.locator('#main select[data-gp-month]').inputValue(), month, `${tag}: the address's month`);
  assert.equal(await page.locator('#gr-body [data-k="new"] .gp-word').count(), 0, `${tag}: new customers counted`);
  await flat(page, `${tag}: a month`);
  await themed(page, `growth-${width}`);
  noErrors('the Growth page');

  const refs = await seedKpis(page);

  // Old addresses land on what replaced them.
  for (const [from, to] of [['#/growth/channels', '#/growth'], ['#/growth/learning', '#/growth'], ['#/growth/week', '#/growth'], ['#/growth/ideas', '#/growth'],
    ['#/growth/buyers', '#/strategy'], ['#/growth/segments', '#/strategy'], ['#/growth/gtm', '#/strategy'], ['#/growth/gtm/model', '#/strategy'], ['#/strategy/gtm', '#/strategy'],
    ['#/strategy/gtm/channels', '#/strategy'], ['#/growth/gtm/sources', '#/growth'], ['#/strategy/gtm/sources', '#/growth'], ['#/growth/sources', '#/growth'],
    ['#/strategy/growth', '#/growth'], ['#/strategy/growth/channels?x=1', '#/growth']] as [string, string][]) {
    await go(page, from);
    await arrive(to, from);
  }

  // Strategy: How we sell reads and edits like its neighbours.
  await go(page, '#/strategy');
  const how = page.locator('[data-section="how"]');
  await how.waitFor({timeout: t(15000)});
  assert.match(await how.locator('.strat-k').innerText(), /How we sell/i, `${tag}: its title`);
  const {draft} = await ok<{draft?: {revision: number}}>(page, 'GET', 'strategy');
  await ok(page, 'PUT', 'strategy/draft', {revision: draft?.revision ?? 0, sections: {how: 'Local search first, then referrals'}});
  await go(page, '#/tasks');
  await go(page, '#/strategy');
  await how.filter({hasText: 'Local search first'}).waitFor({timeout: t(15000)});
  await flat(page, `${tag}: Strategy`);

  // Brand: Book, then Edit saves Positioning and publishes it.
  await ok(page, 'PUT', 'brand/draft', {sections: {positioning: 'Home cleaning you can set and forget',
    voice: [{trait: 'Warm', do: 'We will sort it.', dont: 'Per our policy.'}],
    words: {use: [{word: 'home', not: 'unit'}], avoid: [{word: 'cheap', why: 'We are not the cheapest', level: 'warn'}, {word: 'maid', why: 'Old word', level: 'block'}]},
    visual: {colors: [{name: 'Acme green', hex: '#1f6f5c'}, {name: 'Paper', hex: '#f6f3ea'}, {name: 'Ink', hex: '#172221'}], fonts: ['Inter'], logo_files: [], imagery: 'Real homes, daylight'}}});
  await ok(page, 'POST', 'brand/publish', {note: 'First brand'});
  await go(page, '#/tasks');
  await go(page, '#/strategy');
  await page.locator('.br-strip').waitFor({timeout: t(15000)});
  assert.equal(await page.locator('.br-strip .br-sw').count(), 3, `${tag}: three swatches`);
  await go(page, '#/strategy/brand');
  await page.locator('.br-bento .br-hero').waitFor({timeout: t(10000)});
  await flat(page, `${tag}: Brand book`);
  await go(page, '#/strategy/brand/edit');
  const pos = page.locator('form[data-sec="positioning"] input[name="positioning"]');
  await pos.waitFor({timeout: t(10000)});
  await pos.fill('Home cleaning you never have to think about');
  await page.locator('form[data-sec="positioning"] [type=submit]').click();
  await page.waitForFunction(() => document.querySelector('.br-subs [data-sub="positioning"] .gr-dot'), null, {timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the section saved (${await toasts()} ${JSON.stringify(page.errors)})`));
  await page.locator('[data-sub="voice"]').click();
  await page.locator('form[data-sec="voice"] .gr-rowed').first().waitFor({timeout: t(5000)});
  await page.locator('[data-check] textarea').fill('Our maid team is cheap');
  await page.locator('[data-check] [type=submit]').click();
  await page.locator('[data-hits] .br-hit').first().waitFor({timeout: t(5000)});
  await flat(page, `${tag}: Brand edit`);
  await page.locator('[data-doc-publish]').click();
  await page.locator('[data-doc-publish-form] input[name="note"]').fill('Sharper positioning');
  await page.locator('[data-doc-publish-form] [type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('[data-doc-publish-form]') && document.querySelector('.br-edit'), null, {timeout: t(10000)});
  const brand = await ok<{published: {body: {positioning: string}}}>(page, 'GET', 'brand');
  assert.equal(brand.published.body.positioning, 'Home cleaning you never have to think about', `${tag}: the published section`);

  // Goals: the KPIs by Tree, Loops and Journey; a KPI's dialog places it in a loop, and leaving shuts it.
  await go(page, '#/strategy/goals');
  const kpis = page.locator('#goal-kpis');
  await kpis.locator('.gkv-ns').first().waitFor({timeout: t(15000)});
  assert.equal(await kpis.locator('[data-gr-seg="gkv"] [aria-checked="true"]').getAttribute('data-v'), 'tree', `${tag}: Tree by default`);
  assert.ok(await kpis.locator('.gkv-in').count() >= 1, `${tag}: the inputs under the North Star`);
  await flat(page, `${tag}: Goals, Tree`);
  await kpis.locator(`[data-kpi-place="${refs.inpId}"]`).click();
  const pd = page.locator('dialog.gr-dlg');
  await pd.locator('[name="loop_name"]').fill('Bookings loop');
  await pd.locator('[name="loop_step"]').fill('1');
  await pd.locator('[type=submit]').click();
  await pd.waitFor({state: 'detached', timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the place saved (${await pd.locator('[data-error]').innerText()})`));
  await kpis.locator(`[data-kpi-place="${refs.inpId}"]`).click();
  await pd.waitFor({timeout: t(5000)});
  await go(page, '#/tasks');
  await pd.waitFor({state: 'detached', timeout: t(10000)});
  await go(page, '#/strategy/goals');
  await kpis.locator('[data-gr-seg="gkv"]').waitFor({timeout: t(15000)});
  await kpis.locator('[data-gr-seg="gkv"] [data-v="loops"]').click();
  await kpis.locator('.gkv-loop .gkv-k').first().waitFor({timeout: t(10000)});
  await flat(page, `${tag}: Goals, Loops`);
  await kpis.locator('[data-gr-seg="gkv"] [data-v="journey"]').click();
  await kpis.locator('.gkv-col .gkv-k').first().waitFor({timeout: t(5000)});
  assert.equal(await kpis.locator('.gkv-col').count(), 5, `${tag}: a column per stage`);
  await flat(page, `${tag}: Goals, Journey`);

  assert.deepEqual(removed, [], `${tag}: nothing calls a removed route`);
  noErrors('every page');
}, {only: 'phone'});   // the sideways-scroll checks (flat) are the phone's (fix 14c16db52, growth.css's phone rules); growth-page.cts covers the desktop
