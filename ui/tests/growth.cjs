// Growth and Brand on a real hub. The Growth page: with nothing set up, one line and a link to Sources; with one month of
// only new customers, only that part shows; then Dana, Acme's owner, has a North Star, inputs and a guardrail with
// readings, eight channels and Unknown, months with gaps and marketing tasks, all seeded through the API. The parts draw
// in order with their stats; a stat opens its KPI on Goals; a channel opens in place with its chart, retention, source
// and open tasks; a Tried row opens its task; the month picker moves the stats; Settings, + Channel, Edit, Retire and a
// month's correction save; old addresses land where they moved. Then Sources flags a clash, Strategy has How we sell,
// Brand's Edit publishes, and Goals switches its KPIs between Tree, Loops and Journey. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, ok, shot, t} = require('./support/redesign-e2e.cjs');

const REMOVED = /\/api\/v2\/(gtm|growth\/(ideas|segments|evidence|working|week))/;
// The test hub's clock stands still here (evals/factory/world.py).
const HUB_NOW = Date.UTC(2026, 9, 20, 9);
const monthsAgo = n => { const d = new Date(HUB_NOW); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - n); return d.toISOString().slice(0, 7); };
const MONTH = monthsAgo(0);
// The month with no row at all, and the one where spend went unrecorded: gaps stay gaps.
const NO_ROW = 6, NO_SPEND = 3;
const PARTS = ['north-star', 'growth', 'cost', 'retention', 'value', 'channels', 'tried'];
const CHANNELS = [['google-ads', 'Google Ads', 'scaling', 'bot:engineering-lead', 1.4], ['referrals', 'Referrals', 'scaling', 'human:dana', .9], ['seo', 'SEO', 'testing', null, .6],
  ['local-pages', 'Local pages', 'testing', 'bot:engineering-lead', .5], ['door-hangers', 'Door hangers in Mueller', 'testing', null, .3], ['partners', 'Property manager partners', 'idea', 'human:dana', .25],
  ['newsletter', 'Newsletter', 'paused', null, .2], ['meta-ads', 'Meta Ads', 'paused', 'bot:engineering-lead', .15]];

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
// The moves spread over the window, as a real year's would be: the task routes date a move when it happens, so on a
// seeded hub they all fall on one day and merge into one tick.
async function spreadMarkers(page) {
  await page.route('**/api/v2/growth/numbers*', async route => {
    const res = await route.fetch(), body = await res.json();
    body.markers.forEach((m, i) => { m.at = new Date(HUB_NOW - (5 + i * 23) * 864e5).toISOString(); });
    await route.fulfill({response: res, json: body});
  });
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
// Marketing tasks moved through their steps, each move a marker; one left open on Google Ads.
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
  const parts = () => page.locator('#gr-body [data-part]').evaluateAll(els => els.map(el => el.dataset.part));
  const stat = key => page.locator(`#gr-body [data-stat="${key}"]`);
  const toasts = () => page.locator('.toast').allInnerTexts();
  const arrive = (to, why) => page.waitForFunction(w => location.hash === w, to, {timeout: t(5000)}).catch(async () =>
    assert.fail(`${tag}: ${why} lands on ${to} (${await page.evaluate(() => location.hash)})`));

  // Nothing at all: one line and a link to Sources, which links back.
  if (phone) await page.locator('#mobile-more').click();
  await page.locator('#side a[data-nav="growth"]').click();
  await arrive('#/growth', 'the sidebar');
  await page.locator('#gr-body .gr-empty').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('.strat-tabs a').count(), 0, `${tag}: no tabs`);
  assert.equal(await parts().then(p => p.length), 0, `${tag}: no parts`);
  await flat(page, `${tag}: empty`);
  await themed(page, `growth-${width}-empty`);
  await page.locator('#gr-body [data-gr-sources]').click();
  await arrive('#/growth/sources', 'the empty page\'s link');
  await page.locator('[data-gr-back]').click();
  await arrive('#/growth', 'the back link');

  // One month of only new customers: Growth and Channels show, and no stat or part with no data is drawn.
  await ok(page, 'POST', 'growth/channels', {slug: 'flyers', name: 'Flyers', track: 'performance', stage: 'idea'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'flyers', month: monthsAgo(1), new_customers: 9, quality: 'actual'});
  await go(page, '#/growth/sources');
  await go(page, '#/growth');
  await stat('new_customers').waitFor({timeout: t(10000)});
  assert.deepEqual(await parts(), ['growth', 'channels'], `${tag}: only what has data`);
  assert.equal(await page.locator('#gr-body [data-gr-month]').count(), 0, `${tag}: no month picker with one month`);
  assert.equal(await page.locator('#gr-body').evaluate(el => /—/.test(el.innerText)), false, `${tag}: never a dash`);
  assert.equal(await page.locator('[data-gc-row="flyers"] [data-m="cac"]').innerText(), '', `${tag}: an unknown CAC is blank`);
  const flyers = page.locator('[data-gc-row="flyers"]');
  // + Month in a channel's detail, and the channel's edit retires it after asking once more on the same button.
  await flyers.click();
  await page.locator('[data-ch-edit]').click();
  const retire = page.locator('dialog.gr-dlg [data-retire]');
  await retire.click();
  assert.ok(await flyers.count(), `${tag}: the first press only asks`);
  await retire.click();
  await flyers.waitFor({state: 'detached', timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the channel retires (${await toasts()})`));

  const refs = await seedKpis(page);
  const stripe = await ok(page, 'POST', 'growth/sources', {truth_for: 'Revenue', system: 'Stripe', reach_kind: 'credential', reach_ref: 'stripe', may: 'read'});
  await ok(page, 'POST', 'growth/sources', {truth_for: 'Revenue', system: 'QuickBooks', reach_kind: 'mcp', reach_ref: 'quickbooks', may: 'read'});
  await seedChannels(page, stripe.source?.id || stripe.id);
  await seedMonths(page);
  const tasks = await seedTasks(page, refs.goalId);
  await spreadMarkers(page);

  // The page: every part in order, the North Star first with its chart and ticks.
  await go(page, '#/growth/sources');
  await go(page, '#/growth');
  await page.locator('#gr-body [data-part="tried"]').waitFor({timeout: t(15000)});
  assert.deepEqual(await parts(), PARTS, `${tag}: the order`);
  assert.equal(await page.locator('#strat-actions button').allInnerTexts().then(b => b.join()), '+ Channel', `${tag}: one action`);
  assert.match(await page.locator('.gr-hero').innerText(), /Active homes[\s\S]*648[\s\S]*homes/, `${tag}: the North Star's value`);
  await page.locator('[data-gr-ns] .gr-line').first().waitFor({timeout: t(5000)});
  assert.ok(await page.locator('[data-gr-ns] [data-gr-tick]').count() >= 1, `${tag}: task ticks on the North Star`);
  assert.deepEqual(await page.locator('[data-part="value"] .gr-stat-l').allInnerTexts(), ['Revenue / customer', 'Margin', 'LTV', 'LTV:CAC', 'Payback'], `${tag}: Value's stats`);
  assert.deepEqual(await page.locator('[data-part="growth"] .gr-stat-l').allInnerTexts(), ['New customers', 'Site visits'], `${tag}: an acquisition KPI in Growth`);
  assert.equal(await page.locator('[data-part="channels"] [data-gc-row]').count(), 9, `${tag}: eight channels and Unknown`);
  assert.equal(await page.locator('[data-gc-row]').last().getAttribute('data-gc-row'), 'unknown', `${tag}: Unknown last`);
  assert.match(await stat('new_customers').locator('.gr-d').getAttribute('aria-label'), /^(Up|Down|Flat) /, `${tag}: a change with an arrow`);
  assert.ok(await stat('new_customers').locator('svg.gr-spark').count(), `${tag}: a trend`);
  // 30 days has no reading for this month's cohort yet: last month's shows, saying which.
  assert.match(await page.locator('[data-part="retention"] [data-stat="kept_d30"]').innerText(), new RegExp(`\\d+%[\\s\\S]*${new Date(`${monthsAgo(1)}-15`).toLocaleString('en-US', {month: 'short'})}`), `${tag}: last month's retention`);
  await flat(page, `${tag}: the page`);
  await themed(page, `growth-${width}`);

  // Twenty-four months and thirty channels hold up: axis labels clear of each other, the list folded, no sideways scroll.
  const stretch = async route => {
    const res = await route.fetch(), body = await res.json();
    const earlier = Array.from({length: 12}, (_, i) => monthsAgo(23 - i));
    body.months = [...earlier, ...body.months];
    body.series = [...earlier.map((month, i) => ({...body.series[0], month, new_customers: 80 + i * 9})), ...body.series];
    body.channels = [...Array.from({length: 30}, (_, i) => ({slug: `extra-${i}`, name: `Neighbourhood channel number ${i}`, stage: 'testing', owner_ref: null, series: [{month: MONTH, new_customers: i, cac: 100 + i, ltv_cac: 2}]})), ...body.channels];
    await route.fulfill({response: res, json: body});
  };
  await page.route('**/api/v2/growth/numbers*', stretch);
  await go(page, '#/growth/sources');
  await go(page, '#/growth');
  await page.locator('[data-gc-all]').waitFor({timeout: t(15000)});
  assert.equal(await page.locator('[data-gc-row]').count(), 10, `${tag}: a long list folds`);
  await page.locator('[data-gc-all]').click();
  assert.equal(await page.locator('[data-gc-row]').count(), 39, `${tag}: All shows every channel`);
  await page.locator('[data-gc-row="extra-3"]').click();
  await page.locator('#gc-detail').waitFor({timeout: t(5000)});
  await flat(page, `${tag}: a long list`);
  await page.locator('[data-gc-row="extra-3"]').click();
  const axis = await page.locator('[data-gr-ns] text.gr-ax[text-anchor]').evaluateAll(els => els.map(el => { const r = el.getBoundingClientRect(); return [r.left, r.right]; }));
  axis.forEach((box, i) => { if (i) assert.ok(box[0] >= axis[i - 1][1], `${tag}: axis labels clear of each other (${JSON.stringify(axis)})`); });
  await themed(page, `growth-${width}-long`);
  await page.unroute('**/api/v2/growth/numbers*', stretch);
  await go(page, '#/growth/sources');
  await go(page, '#/growth');
  await page.locator('#gr-body [data-part="tried"]').waitFor({timeout: t(15000)});

  // A tap on a tick gives its title; tapping it again, the chart beside it or anywhere else clears it. So does the mouse moving off.
  const tip = page.locator('[data-gr-ns] .gr-chart-tip'), tick = page.locator('[data-gr-ns] [data-gr-tick]').first();
  const press = phone ? l => l.tap() : l => l.click();
  const off = why => tip.waitFor({state: 'hidden', timeout: t(3000)}).catch(() => assert.fail(`${tag}: ${why} clears the title`));
  const titled = async () => { await tip.waitFor({timeout: t(3000)}); assert.ok((await tip.innerText()).length > 3, `${tag}: a tick's title`); };
  await press(tick);
  await titled();
  await press(tick);
  await off('the same tick');
  await press(tick);
  await titled();
  await press(page.locator('[data-part="cost"] h2'));
  await off('a tap elsewhere');
  await press(tick);
  await titled();
  await press(page.locator('[data-gr-ns] [data-gr-tick]').nth(1));
  assert.match(await tip.innerText(), /\S/, `${tag}: another tick takes over`);
  const box = await page.locator('[data-gr-ns]').boundingBox();
  if (phone) await page.touchscreen.tap(box.x + 12, box.y + 40); else await page.mouse.click(box.x + 12, box.y + 40);
  await off('a tap on the chart');
  if (!phone) {
    await tick.hover();
    await titled();
    await page.locator('[data-part="cost"] h2').hover();
    await off('the mouse moving off');
  }

  // The month picker moves the stats; the month with no spend hides Spend, and a window month with no row is not offered.
  const picker = page.locator('[data-gr-month]');
  assert.deepEqual((await picker.locator('option').evaluateAll(o => o.map(x => x.value))).includes(monthsAgo(NO_ROW)), false, `${tag}: no empty month`);
  const before = await stat('new_customers').locator('.gr-stat-v').innerText();
  await picker.selectOption(monthsAgo(NO_SPEND));
  await stat('spend').waitFor({state: 'detached', timeout: t(10000)});
  assert.notEqual(await stat('new_customers').locator('.gr-stat-v').innerText(), before, `${tag}: the picked month's value`);
  assert.equal(await stat('cac').count(), 0, `${tag}: no CAC with no spend`);
  await page.locator('[data-gr-month]').selectOption(MONTH);
  await stat('spend').waitFor({timeout: t(10000)});

  // A channel opens in place: chart, retention, source and open tasks, with no ideas or buyers.
  await page.locator('[data-gc-row="google-ads"]').click();
  await page.locator('[data-gc-chart] .gr-line').first().waitFor({timeout: t(10000)});
  assert.ok(await page.locator('#gc-detail [data-gc-kept] .gr-stat').count() >= 1, `${tag}: its retention`);
  assert.match(await page.locator('#gc-detail [data-gc-from]').innerText(), /Stripe[\s\S]*Google Ads console/, `${tag}: where its numbers come from`);
  assert.match(await page.locator('#gc-detail [data-gc-tasks]').innerText(), /Landing page for office cleans/, `${tag}: its open tasks`);
  assert.equal(await page.locator('#gc-detail [data-gc-tasks] a').first().getAttribute('href'), `#/task/${encodeURIComponent(tasks.open)}`, `${tag}: an open task's link`);
  assert.equal(await page.locator('#gc-detail').evaluate(el => /\b(Ideas|Buyers?|Segments?)\b/.test(el.innerText)), false, `${tag}: no ideas or buyers`);
  await flat(page, `${tag}: a channel's detail`);
  await themed(page, `growth-${width}-channel`);
  // Correct a month.
  await page.locator('#gc-detail .gc-months summary').click();
  await page.locator('[data-gc-edit]').first().click();
  const md = page.locator('dialog.gr-dlg');
  await md.locator('[name="new_customers"]').fill('99');
  await md.locator('[type=submit]').click();
  await page.locator('[data-gc-row="google-ads"] [data-m="new"]').filter({hasText: '99'}).waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the correction shows (${await toasts()})`));
  // Edit changes the channel, and + Channel adds one.
  await page.locator('[data-ch-edit]').waitFor({timeout: t(10000)});
  await page.locator('[data-ch-edit]').click();
  await page.locator('dialog.gr-dlg [name="stage"]').selectOption('paused');
  await page.locator('dialog.gr-dlg [type=submit]').click();
  await page.locator('[data-gc-row="google-ads"] .gc-meta').filter({hasText: 'Paused'}).waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the stage saved (${await toasts()})`));
  await page.locator('#strat-actions [data-ch-add]').click();
  const cn = page.locator('dialog.gr-dlg');
  await cn.locator('[name="name"]').fill('Yard signs');
  assert.equal(await cn.locator('[name="slug"]').inputValue(), 'yard-signs', `${tag}: the slug follows the name`);
  await cn.locator('[type=submit]').click();
  await page.locator('[data-gc-row="yard-signs"]').waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the new channel shows (${await toasts()})`));

  // Settings save.
  await page.locator('[data-gr-settings]').click();
  const sd = page.locator('dialog.gr-dlg');
  await sd.locator('[name="revenue_month"]').waitFor({timeout: t(5000)});
  assert.equal(await sd.locator('[name="revenue_month"]').inputValue(), '92', `${tag}: the saved revenue`);
  await sd.locator('[name="revenue_month"]').fill('95');
  await sd.locator('[type=submit]').click();
  await sd.waitFor({state: 'detached', timeout: t(10000)});
  assert.equal((await ok(page, 'GET', 'growth/settings')).revenue_month, 95, `${tag}: the setting saved`);

  // Tried: five, then All; a row opens its task.
  const tried = page.locator('[data-part="tried"] [data-marker]');
  await tried.first().waitFor({timeout: t(10000)});
  assert.equal(await tried.count(), 5, `${tag}: the newest five`);
  await page.locator('[data-gr-tried-all]').click();
  assert.ok(await tried.count() > 5, `${tag}: All shows the rest`);
  assert.match(await page.locator(`[data-part="tried"] [data-marker="${tasks.won}"]`).first().innerText(), /Won/, `${tag}: the outcome`);
  await page.locator(`[data-part="tried"] [data-marker="${tasks.won}"]`).first().click();
  await arrive(`#/task/${encodeURIComponent(tasks.won)}`, 'a Tried row');
  await page.locator('.tmodal-title').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('[data-growth-idea]').count(), 0, `${tag}: no idea panel on the task`);

  // A stat opens its KPI on Goals, in Tree.
  await go(page, '#/growth');
  await stat(refs.inpId).click();
  await page.waitForFunction(id => location.hash.startsWith('#/strategy/goals') && location.hash.includes(id), refs.inpId, {timeout: t(5000)});
  await page.locator(`#goal-kpis .gkv-hit[data-kpi="${refs.inpId}"]`).waitFor({timeout: t(15000)});

  // Sources: the link, the back link, the revenue clash, every source a row that opens its form.
  await go(page, '#/growth');
  await page.locator('#gr-body [data-gr-sources]').click();
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
  await page.route('**/api/v2/growth/numbers*', async route => { await held; await route.fallback(); });
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
