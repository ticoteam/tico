// Growth and Brand on a real hub. The Growth page is a grid of cards (place `growth`, ui/app/dashboard.js): with
// nothing set up, each default card says it has nothing yet and Settings links to Sources; with one month of only new
// customers, the New customers and Channels cards show it; then Dana, Acme's owner, has a North Star, stage KPIs and a
// guardrail with readings, eight channels and Unknown, months with gaps and marketing tasks, all seeded through the
// API. The Funnel, Numbers, Channels, Retention and Growth board cards draw from it; a stage opens its KPI on Goals; a
// channel's open task is a chip on its row and a card on the board; the month picker reads the cards again and the
// address keeps it; Settings, + Channel, Edit, Retire and a month's correction save; a Number is added for one channel;
// old addresses land where they moved. Then Sources flags a clash, Strategy has How we sell, Brand's Edit publishes,
// and Goals switches its KPIs between Tree, Loops and Journey. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, ok, shot, t} = require('./support/redesign-e2e.cjs');

const REMOVED = /\/api\/v2\/(gtm|growth\/(ideas|segments|evidence|working|week|numbers))/;
// The test hub's clock stands still here (evals/factory/world.py).
const HUB_NOW = Date.UTC(2026, 9, 20, 9);
const monthsAgo = n => { const d = new Date(HUB_NOW); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - n); return d.toISOString().slice(0, 7); };
const MONTH = monthsAgo(0);
// The month with no row at all, and the one where spend went unrecorded: gaps stay gaps.
const NO_ROW = 6, NO_SPEND = 3;
const CARDS = ['Funnel', 'New customers', 'CAC', 'Spend', 'LTV:CAC', 'Payback', 'Kept 30 days', 'Channels', 'Retention', 'Growth board'];
const CHANNELS = [['google-ads', 'Google Ads', 'scaling', 'bot:engineering-lead', 1.4], ['referrals', 'Referrals', 'scaling', 'human:dana', .9], ['seo', 'SEO', 'testing', null, .6],
  ['local-pages', 'Local pages', 'testing', 'bot:engineering-lead', .5], ['door-hangers', 'Door hangers in Mueller', 'testing', null, .3], ['partners', 'Property manager partners', 'idea', 'human:dana', .25],
  ['newsletter', 'Newsletter', 'paused', null, .2], ['meta-ads', 'Meta Ads', 'paused', 'bot:engineering-lead', .15]];
const short = m => new Date(`${m}-15`).toLocaleString('en-US', {month: 'short', timeZone: 'UTC'});
const long = m => `${short(m)} ${m.slice(0, 4)}`;

const flat = async (page, what) => {
  const info = await page.evaluate(() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, hash: location.hash,
    all: [...document.querySelectorAll('*')].map(el => [el, el.getBoundingClientRect()]).filter(([, r]) => r.right > document.documentElement.clientWidth + 1)
      .slice(0, 8).map(([el, r]) => `${el.tagName}.${el.className}:${Math.round(r.left)}-${Math.round(r.right)}`)}));
  assert.ok(info.sw <= info.cw + 1, `${what} no sideways scroll (${JSON.stringify(info)})`);
};
// With TICO_UI_SHOTS set: the whole page, light and dark.
async function themed(page, name) {
  if (!process.env.TICO_UI_SHOTS) return;
  const size = page.viewportSize();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(v => { document.documentElement.dataset.theme = v; }, theme);
    const tall = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.querySelector('#main')?.scrollHeight || 0));
    await page.setViewportSize({width: size.width, height: Math.max(size.height, tall + 40)});
    await page.waitForTimeout(150);
    await page.evaluate(() => document.getAnimations().filter(a => a.effect?.getTiming?.().iterations === Infinity).forEach(a => a.cancel()));
    await shot(page, `${name}-${theme}`);
  }
  await page.setViewportSize(size);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}
async function seedChannels(page, stripe) {
  for (const [slug, name, stage, owner_ref] of CHANNELS) {
    await ok(page, 'POST', 'growth/channels', {slug, name, track: slug === 'referrals' ? 'brand' : 'performance', stage, owner_ref,
      source_id: slug === 'google-ads' ? stripe : null});
  }
}
async function seedMonths(page) {
  await ok(page, 'PUT', 'growth/settings', {revenue_month: 92, margin_pct: 58});
  for (let n = 11; n >= 0; n--) {
    if (n === NO_ROW) continue;
    const growth = 1 + (11 - n) * .07 + (n % 3 === 0 ? -.06 : .03);
    for (const [slug, , , , weight] of CHANNELS) {
      if (slug === 'meta-ads' && n < 5) continue;
      const fresh = Math.round(40 * weight * growth), cac = 62 + (slug.length * 9) + n * 2;
      await ok(page, 'PUT', 'growth/cohorts', {channel: slug, month: monthsAgo(n), spend: n === NO_SPEND ? null : fresh * cac, bot_cost: n === NO_SPEND ? null : Math.round(fresh * 4),
        new_customers: fresh, kept_d30: n < 1 ? null : 84 + (n % 4), kept_d90: n < 3 ? null : 70 + (n % 5), kept_d180: n < 6 ? null : 58 + (n % 3), quality: n % 5 ? 'actual' : 'estimate',
        sources: {spend: slug === 'google-ads' ? 'Google Ads console' : ''}});
    }
    await ok(page, 'PUT', 'growth/cohorts', {channel: 'unknown', month: monthsAgo(n), new_customers: 3 + n % 4, quality: 'estimate'});
  }
}
async function seedKpis(page) {
  const goal = await ok(page, 'POST', 'goals', {title: 'More active homes', owner: 'human:dana'});
  const goalId = goal.goal?.id || goal.id;
  const kpi = async (name, unit, target, role, parent, stage, values, goal_id = null) => {
    const made = await ok(page, 'POST', 'kpis', {name, unit, kind: 'improve', target, deadline: '2027-12-31', ...(goal_id ? {goal_id} : {})});
    const id = made.kpi?.id || made.id;
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
// Marketing tasks moved through their steps; one left open, in Testing, on Google Ads.
async function seedTasks(page, goalId) {
  const task = async (title, channel, steps, goal = null) => {
    const made = await ok(page, 'POST', 'tasks', {title, body: title, owner: 'bot:engineering-lead', type: 'idea', labels: channel ? [`channel:${channel}`] : [], ...(goal ? {goal_id: goal} : {})});
    const id = made.task?.id || made.id;
    for (const step of steps) {
      const read = await ok(page, 'GET', `tasks/${id}`);
      await ok(page, 'POST', `tasks/${id}`, {version: (read.task || read).version, step});
    }
    return id;
  };
  const won = await task('Referral credit $100 to $200', 'referrals', ['idea-doing', 'idea-won'], goalId);
  await task('Search ads for move-out cleans', 'google-ads', ['idea-doing', 'idea-lost'], goalId);
  await task('Door hangers near new builds', 'door-hangers', ['idea-doing', 'idea-inconclusive']);
  await task('Before-and-after photos on the site', null, ['idea-doing', 'idea-won'], goalId);
  await task('Post in neighbourhood groups', 'referrals', ['idea-doing', 'idea-dropped']);
  const open = await task('Landing page for office cleans', 'google-ads', ['idea-doing'], goalId);
  return {won, open};
}

run('growth', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone});
  const width = phone ? 390 : 1440;
  const noErrors = where => assert.deepEqual(page.errors, [], `${tag}: ${where} without page errors`);
  const removed = [];
  page.on('request', r => { if (REMOVED.test(r.url())) removed.push(r.url()); });
  const headings = () => page.locator('#dash-grid .dash-pin-head h2').allInnerTexts();
  const card = name => page.locator('#dash-grid .dash-pin').filter({has: page.locator('.dash-pin-head h2', {hasText: new RegExp(`^${name}$`)})});
  const row = slug => page.locator(`#dash-grid .grc-ch tr[data-channel="${slug}"]`);
  const num = name => card(name).locator('.grc-num-v');
  const toasts = () => page.locator('.toast').allInnerTexts();
  const arrive = (to, why) => page.waitForFunction(w => location.hash === w, to, {timeout: t(5000)}).catch(async () =>
    assert.fail(`${tag}: ${why} lands on ${to} (${await page.evaluate(() => location.hash)})`));
  const dialog = () => page.locator('dialog.gr-dlg[open]');
  const settings = async () => {
    await page.locator('[data-gr-settings]').click();
    await dialog().locator('[data-gs-channel="unknown"]').waitFor({timeout: t(10000)});
    return dialog();
  };
  // The Growth grid drawn (not the page it came from).
  const grid = () => page.waitForFunction(() => document.querySelector('.dash-head h1')?.textContent === 'Growth' && document.querySelector('#dash-grid .dash-pin'), null, {timeout: t(15000)});
  const reload = async () => { await go(page, '#/growth/sources'); await page.locator('[data-gr-back]').waitFor({timeout: t(10000)}); await go(page, '#/growth'); await grid(); };

  // Nothing at all: every default card says it has nothing yet; Settings links to Sources, which links back.
  if (phone) await page.locator('#mobile-more').click();
  await page.locator('#side a[data-nav="growth"]').click();
  await arrive('#/growth', 'the sidebar');
  await grid();
  assert.deepEqual(await headings(), CARDS, `${tag}: the default cards`);
  assert.equal(await page.locator('.dash-head h1').innerText(), 'Growth', `${tag}: the title`);
  assert.equal(await page.locator('#dash-grid .dash-none').count(), CARDS.length, `${tag}: each says it has nothing yet`);
  assert.equal(await page.locator('.dash-head [data-gr-ns]').isVisible(), false, `${tag}: no North Star line`);
  assert.equal(await page.locator('.dash-head .grh-month').isVisible(), false, `${tag}: no month picker`);
  assert.equal(await page.locator('.dash-head [data-gr-board]').getAttribute('href'), '#/tasks?view=board&type=idea&who=all', `${tag}: the Growth board link`);
  assert.equal(await page.locator('.dash-actions').isVisible(), true, `${tag}: the owner arranges`);
  await flat(page, `${tag}: empty`);
  await themed(page, `growth-${width}-empty`);
  await (await settings()).locator('a[data-gr-sources]').click();
  await arrive('#/growth/sources', 'Settings\' Sources link');
  await dialog().waitFor({state: 'detached', timeout: t(5000)}).catch(() => assert.fail(`${tag}: Settings closes on the way`));
  await page.locator('[data-gr-back]').click();
  await arrive('#/growth', 'the back link');

  // One month of only new customers: New customers and Channels show it; an unknown CAC is a dash.
  await ok(page, 'POST', 'growth/channels', {slug: 'flyers', name: 'Flyers', track: 'performance', stage: 'idea'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'flyers', month: monthsAgo(1), new_customers: 9, quality: 'actual'});
  await reload();
  await row('flyers').waitFor({timeout: t(10000)});
  assert.equal(await num('New customers').innerText(), '9', `${tag}: the month's new customers`);
  assert.match(await card('CAC').innerText(), new RegExp(`None for ${long(monthsAgo(1))}`), `${tag}: no CAC that month`);
  assert.deepEqual(await row('flyers').locator('td.grc-n').allInnerTexts(), ['9', '—', '—'], `${tag}: the row's numbers`);
  assert.equal(await page.locator('.dash-head .grh-month').isVisible(), false, `${tag}: no month picker with one month`);
  // Settings: the channel's Edit retires it after asking once more on the same button.
  await (await settings()).locator('[data-gs-edit="flyers"]').click();
  const retire = dialog().locator('[data-retire]');
  await retire.click();
  assert.equal(await dialog().count(), 1, `${tag}: the first press only asks`);
  await retire.click();
  await row('flyers').waitFor({state: 'detached', timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the channel retires (${await toasts()})`));

  const refs = await seedKpis(page);
  const stripe = await ok(page, 'POST', 'growth/sources', {truth_for: 'Revenue', system: 'Stripe', reach_kind: 'credential', reach_ref: 'stripe', may: 'read'});
  await ok(page, 'POST', 'growth/sources', {truth_for: 'Revenue', system: 'QuickBooks', reach_kind: 'mcp', reach_ref: 'quickbooks', may: 'read'});
  await seedChannels(page, stripe.source?.id || stripe.id);
  await seedMonths(page);
  const tasks = await seedTasks(page, refs.goalId);

  // The page: the North Star line, then every card with what it reads.
  await reload();
  await page.locator('#dash-grid .grc-funnel').waitFor({timeout: t(15000)});
  assert.deepEqual(await headings(), CARDS, `${tag}: the order`);
  const ns = page.locator('.dash-head [data-gr-ns]');
  assert.match(await ns.innerText(), /North Star[\s\S]*Active homes[\s\S]*648/, `${tag}: the North Star line`);
  assert.equal(await ns.getAttribute('href'), `#/strategy/goals?kpi=${encodeURIComponent(refs.nsId)}`, `${tag}: the line opens its KPI`);
  // The Funnel: five stages, each its first KPI; a stage with none says so; Refer loops back.
  const stages = page.locator('#dash-grid .grc-funnel > li');
  assert.deepEqual(await stages.evaluateAll(els => els.map(el => el.dataset.stage)), ['reach', 'acquire', 'activate', 'retain', 'refer'], `${tag}: five stages`);
  assert.match(await stages.nth(0).innerText(), /Site visits[\s\S]*31,000/, `${tag}: Reach's KPI`);
  assert.match(await stages.nth(1).innerText(), /New bookings[\s\S]*97/, `${tag}: Acquire's KPI`);
  assert.match(await stages.nth(2).innerText(), /No KPI/, `${tag}: Activate has none`);
  assert.match(await stages.nth(4).innerText(), /↺ loops back to Reach/, `${tag}: the loop`);
  // Numbers: a value, a change with an arrow, a trend; 30 days has no reading for this month's cohort yet, so last
  // month's shows, saying which.
  assert.match(await num('New customers').innerText(), /^\d[\d,]*$/, `${tag}: New customers`);
  assert.match(await card('New customers').locator('.gr-d').getAttribute('aria-label'), /^(Up|Down|Flat) /, `${tag}: a change with an arrow`);
  assert.ok(await card('New customers').locator('svg.gr-spark').count(), `${tag}: a trend`);
  assert.match(await card('Kept 30 days').innerText(), new RegExp(`\\d+%[\\s\\S]*${short(monthsAgo(1))}`), `${tag}: last month's retention`);
  // Channels: eight and Unknown, names as stored, Unknown last; the open task a chip on its channel's row.
  const slugs = await page.locator('#dash-grid .grc-ch tbody tr').evaluateAll(els => els.map(el => el.dataset.channel));
  assert.deepEqual(slugs.slice().sort(), [...CHANNELS.map(c => c[0]), 'unknown'].sort(), `${tag}: eight channels and Unknown`);
  assert.equal(slugs.at(-1), 'unknown', `${tag}: Unknown last`);
  assert.equal(await row('door-hangers').locator('th').innerText(), 'Door hangers in Mueller', `${tag}: a name as stored`);
  assert.equal(await row('google-ads').locator('a.grc-chip').getAttribute('href'), `#/task/${encodeURIComponent(tasks.open)}`, `${tag}: the open task on its row`);
  assert.equal(await row('google-ads').locator('a.grc-chip .grc-sq').getAttribute('data-stage'), 'testing', `${tag}: in Testing`);
  assert.equal(await row('referrals').locator('a.grc-chip').count(), 0, `${tag}: closed tasks are not worked on`);
  // Retention: shaded cells; the Growth board: the open task in Testing with its channel.
  assert.ok(await page.locator('#dash-grid .grc-ret-t td.grc-k:not(.grc-k-none)').count() >= 3, `${tag}: retention cells`);
  const testing = page.locator('#dash-grid .grc-lane[data-lane="testing"]');
  assert.match(await testing.locator(`a.grc-card[href="#/task/${encodeURIComponent(tasks.open)}"]`).innerText(), /Landing page for office cleans[\s\S]*Google Ads/, `${tag}: the board's card`);
  await flat(page, `${tag}: the page`);
  await themed(page, `growth-${width}`);

  // The month picker reads every card again: the month with no spend has no CAC, a month with no row is not offered,
  // and the address keeps the month.
  const picker = page.locator('[data-gr-month]');
  assert.equal(await picker.inputValue(), MONTH, `${tag}: the newest month`);
  assert.deepEqual((await picker.locator('option').evaluateAll(o => o.map(x => x.value))).includes(monthsAgo(NO_ROW)), false, `${tag}: no empty month`);
  const before = await num('New customers').innerText();
  await picker.selectOption(monthsAgo(NO_SPEND));
  await card('CAC').locator('.dash-none').waitFor({timeout: t(10000)});
  assert.notEqual(await num('New customers').innerText(), before, `${tag}: the picked month's value`);
  await arrive(`#/growth?m=${monthsAgo(NO_SPEND)}`, 'the month picked');
  await go(page, '#/tasks');
  await go(page, `#/growth?m=${monthsAgo(NO_SPEND)}`);
  await card('CAC').locator('.dash-none').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('[data-gr-month]').inputValue(), monthsAgo(NO_SPEND), `${tag}: the address opens on its month`);
  await page.locator('[data-gr-month]').selectOption(MONTH);
  await num('CAC').waitFor({timeout: t(10000)});

  // A stage opens its KPI on Goals.
  await stages.nth(1).locator('a').click();
  await page.waitForFunction(id => location.hash.startsWith('#/strategy/goals') && location.hash.includes(id), refs.inpId, {timeout: t(5000)});
  await page.locator(`#goal-kpis .gkv-hit[data-kpi="${refs.inpId}"]`).waitFor({timeout: t(15000)});
  await go(page, '#/growth');
  await page.locator('#dash-grid .grc-funnel').waitFor({timeout: t(15000)});

  // Settings: a channel's months, newest first; correct one and the Channels card shows it.
  let sd = await settings();
  assert.equal(await sd.locator('[data-gs-channel]').count(), CHANNELS.length + 1, `${tag}: every channel and Unknown`);
  await sd.locator('[data-gs-months="google-ads"]').click();
  await dialog().locator('.gc-coh').first().waitFor({timeout: t(10000)});
  assert.match(await dialog().locator('.gc-coh').first().innerText(), new RegExp(long(MONTH)), `${tag}: newest first`);
  await flat(page, `${tag}: a channel's months`);
  await themed(page, `growth-${width}-months`);
  await dialog().locator('[data-gc-edit]').first().click();
  await dialog().locator('[name="new_customers"]').fill('99');
  await dialog().locator('[type=submit]').click();
  await row('google-ads').locator('td.grc-n').first().filter({hasText: /^99$/}).waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the correction shows (${await toasts()})`));
  // Edit changes the channel, and + Channel adds one.
  await (await settings()).locator('[data-gs-edit="google-ads"]').click();
  await dialog().locator('[name="stage"]').selectOption('paused');
  await dialog().locator('[type=submit]').click();
  await dialog().waitFor({state: 'detached', timeout: t(10000)});
  const google = (await ok(page, 'GET', 'growth/channels')).channels.find(c => c.slug === 'google-ads');
  assert.equal(google.stage, 'paused', `${tag}: the stage saved`);
  await (await settings()).locator('[data-ch-add]').click();
  await dialog().locator('[name="name"]').fill('Yard signs');
  assert.equal(await dialog().locator('[name="slug"]').inputValue(), 'yard-signs', `${tag}: the slug follows the name`);
  await dialog().locator('[type=submit]').click();
  await row('yard-signs').waitFor({timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the new channel shows (${await toasts()})`));
  // The numbers save.
  sd = await settings();
  assert.equal(await sd.locator('[name="revenue_month"]').inputValue(), '92', `${tag}: the saved revenue`);
  await sd.locator('[name="revenue_month"]').fill('95');
  await sd.locator('[type=submit]').click();
  await sd.waitFor({state: 'detached', timeout: t(10000)});
  assert.equal((await ok(page, 'GET', 'growth/settings')).revenue_month, 95, `${tag}: the setting saved`);

  // A Number for one channel, added from the grid's form.
  await page.locator('[data-dash-add="card"]').click();
  const form = page.locator('#dash-dialog[open]');
  assert.equal(await form.locator('[name="template"]').inputValue(), 'growth_number', `${tag}: a Number first`);
  await form.locator('[name="metric"]').selectOption('new_customers');
  await form.locator('[name="channel"]').selectOption('referrals');
  await form.locator('[type="submit"]').click();
  await card('New customers · Referrals').locator('.grc-num-v').waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the Number shows (${await headings()})`));
  assert.equal(await card('New customers · Referrals').getAttribute('data-size'), 'small', `${tag}: a tile`);

  // Sources: the link, the back link, the revenue clash, every source a row that opens its form.
  await (await settings()).locator('a[data-gr-sources]').click();
  await arrive('#/growth/sources', 'the Sources link');
  await page.locator('[data-clash]').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('[data-src-edit]').count(), 2, `${tag}: a row per source`);
  assert.equal(await page.locator('[data-gr-back]').getAttribute('href'), '#/growth', `${tag}: back to Growth`);
  await flat(page, `${tag}: Sources`);
  await themed(page, `growth-${width}-sources`);
  await page.locator('[data-src-edit]').first().click();
  assert.equal(await page.locator('dialog.gr-dlg [name="truth_for"]').inputValue(), 'Revenue', `${tag}: the source's form`);
  await page.locator('dialog.gr-dlg [data-close]').first().click();

  // Old addresses land on what replaced them.
  for (const [from, to] of [['#/growth/channels', '#/growth'], ['#/growth/learning', '#/growth'], ['#/growth/week', '#/growth'], ['#/growth/ideas', '#/growth'],
    ['#/growth/buyers', '#/strategy'], ['#/growth/segments', '#/strategy'], ['#/growth/gtm', '#/strategy'], ['#/growth/gtm/model', '#/strategy'], ['#/strategy/gtm', '#/strategy'],
    ['#/strategy/gtm/channels', '#/strategy'], ['#/growth/gtm/sources', '#/growth/sources'], ['#/strategy/gtm/sources', '#/growth/sources'],
    ['#/strategy/growth', '#/growth'], ['#/strategy/growth/channels?x=1', '#/growth']]) {
    await go(page, from);
    await arrive(to, from);
  }

  // A late answer never draws over Sources, which sits under #/growth.
  let release;
  const held = new Promise(r => { release = r; });
  await page.route('**/api/v2/places/growth*', async route => { await held; await route.fallback(); });
  await go(page, '#/growth');
  await go(page, '#/growth/sources');
  await page.locator('[data-clash]').waitFor({timeout: t(10000)});
  release();
  await page.waitForTimeout(500);
  assert.ok(await page.locator('[data-clash]').count(), `${tag}: Sources stays after a late answer`);

  // Strategy: How we sell reads and edits like its neighbours.
  await go(page, '#/strategy');
  const how = page.locator('[data-section="how"]');
  await how.waitFor({timeout: t(15000)});
  assert.match(await how.locator('.strat-k').innerText(), /How we sell/i, `${tag}: its title`);
  const {draft} = await ok(page, 'GET', 'strategy');
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
  const brand = await ok(page, 'GET', 'brand');
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
});
