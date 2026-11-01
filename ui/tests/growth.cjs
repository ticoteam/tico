// Growth, Brand and Go-to-market on a real hub (the growth spec and the brand-and-gtm spec). First, with nothing set up:
// the Strategy page's setup row, the Growth tab with no ideas (+ Idea with a prediction) and Channels with no month of
// numbers (+ Month enters the first). Then Dana, Acme's owner, has a
// published brand and go-to-market, two channels, two sources for revenue (a clash), a North Star with an input, a
// month of channel numbers with an Unknown row, and three ideas, all seeded through the API. The Strategy page shows the
// Brand strip; Brand's Edit saves a section and publishes it; Channels shows Unknown and a CAC; each Growth framework
// draws the ideas; an Idea task shows its panel, starts a test and takes a verdict. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, ok, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const MONTH = new Date().toISOString().slice(0, 7);

// A screenshot once the page settles: a loop that never ends (a loading shimmer elsewhere on the page) is named and
// stopped first, so the shot does not wait on it.
// What sticks out past the right edge, for a failed no-sideways check.
const wide = page => page.evaluate(() => [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1
  && !el.parentElement?.closest('.gr-scroll,.gr-cols,.gr-steps,.gr-seg,.br-subs')).slice(0, 6)
  .map(el => `${el.tagName}.${el.className}#${el.id}:${Math.round(el.getBoundingClientRect().right)}`).join(' '));
const flat = async (page, what) => {
  const info = await page.evaluate(() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth,
    hash: location.hash, all: [...document.querySelectorAll('*')].map(el => [el, el.getBoundingClientRect()]).filter(([, r]) => r.right > document.documentElement.clientWidth + 1)
      .slice(0, 8).map(([el, r]) => `${el.tagName}.${el.className}:${Math.round(r.left)}-${Math.round(r.right)}`)}));
  assert.ok(info.sw <= info.cw + 1, `${what} no sideways scroll (${JSON.stringify(info)})`);
};

async function snap(page, name) {
  const loops = await page.evaluate(() => {
    const inf = document.getAnimations().filter(a => a.effect?.getTiming?.().iterations === Infinity);
    const names = inf.map(a => `${a.effect.target?.className || a.effect.target?.tagName || '?'}:${a.animationName || ''}`);
    inf.forEach(a => a.cancel());
    return names;
  });
  if (loops.length) console.log(`${name}: stopped ${loops.join(', ')}`);
  await shot(page, name);
}

// Before anything is seeded: no brand, no go-to-market, no channels.
// Then the KPI tree, the sources and the channels, with no ideas and no month of numbers yet.
async function seedBase(page) {
  const ns = await ok(page, 'POST', 'kpis', {name: 'Active homes', unit: 'homes', kind: 'improve', target: 500, deadline: '2026-12-31'});
  const nsId = ns.kpi?.id || ns.id;
  await ok(page, 'POST', `kpis/${nsId}`, {growth_role: 'north_star'});
  await ok(page, 'POST', `kpis/${nsId}/readings`, {value: 320});
  const inp = await ok(page, 'POST', 'kpis', {name: 'New bookings', unit: '', kind: 'improve', target: 80, deadline: '2026-12-31'});
  const inpId = inp.kpi?.id || inp.id;
  await ok(page, 'POST', `kpis/${inpId}`, {growth_role: 'input', parent_kpi_id: nsId, stage: 'acquire'});
  await ok(page, 'POST', `kpis/${inpId}/readings`, {value: 54});
  const stripe = await ok(page, 'POST', 'growth/sources', {truth_for: 'Revenue', system: 'Stripe', reach_kind: 'credential', reach_ref: 'stripe', may: 'read'});
  await ok(page, 'POST', 'growth/sources', {truth_for: 'Revenue', system: 'QuickBooks', reach_kind: 'mcp', reach_ref: 'quickbooks', may: 'read'});
  await ok(page, 'POST', 'growth/channels', {slug: 'google-ads', name: 'Google Ads', track: 'performance', stage: 'scaling', budget_month: 1200,
    source_id: stripe.source?.id || stripe.id, why: 'People search for cleaners'});
  await ok(page, 'POST', 'growth/channels', {slug: 'referrals', name: 'Referrals', track: 'brand', stage: 'testing', budget_month: 300});
  return {nsId, inpId};
}

async function seed(page, {nsId, inpId}) {
  await ok(page, 'PUT', 'brand/draft', {sections: {
    positioning: 'Home cleaning you can set and forget',
    voice: [{trait: 'Warm', do: 'We will sort it.', dont: 'Per our policy.'}, {trait: 'Plain', do: 'Clean in 3 hours.', dont: 'Leverage synergies.'}],
    words: {use: [{word: 'home', not: 'unit'}], avoid: [{word: 'cheap', why: 'We are not the cheapest', level: 'warn'}, {word: 'maid', why: 'Old word', level: 'block'}]},
    visual: {colors: [{name: 'Acme green', hex: '#1f6f5c'}, {name: 'Paper', hex: '#f6f3ea'}, {name: 'Ink', hex: '#172221'}], fonts: ['Inter'], logo_files: [], imagery: 'Real homes, daylight'},
    claims: {allowed: [{claim: '4.9 stars from 2,100 reviews', proof: 'Google Business Profile', check: 'monthly', status: 'proven'}], never: ['Guaranteed']},
    tone: [{situation: 'A complaint', guide: 'Say sorry first, then the fix.'}],
  }});
  await ok(page, 'POST', 'brand/publish', {note: 'First brand'});
  await ok(page, 'PUT', 'gtm/draft', {sections: {motion: {primary: 'local', secondary: 'self_serve'},
    buyer: {who_buys: 'Homeowners in Austin', who_uses: '', deal_size: '$90 a month', cycle: '1 week', objections: ['Trust in the home']},
    wishes: [{text: "Don't spam property managers", scope: 'outbound', level: 'warn'}]}});
  await ok(page, 'POST', 'gtm/publish', {note: 'First go-to-market'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'google-ads', month: MONTH, spend: 1200, bot_cost: 60, new_customers: 12,
    kept_d30: 90, kept_d90: 75, kept_d180: 60, quality: 'actual'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'unknown', month: MONTH, new_customers: 3, quality: 'estimate'});
  const idea = await ok(page, 'POST', 'growth/ideas', {title: 'Referral credit $100 to $200', hypothesis: 'A bigger credit doubles referrals.',
    kpi_id: inpId, channels: ['referrals'], ice_i: 8, ice_c: 6, ice_e: 7});
  await ok(page, 'POST', 'growth/ideas', {title: 'Search ads for move-out cleans', kpi_id: nsId, channels: ['google-ads'], ice_i: 6, ice_c: 5, ice_e: 8});
  await ok(page, 'POST', 'growth/ideas', {title: 'Door hangers in Mueller', channels: []});
  return {ideaId: idea.task_id || idea.idea?.task_id || idea.id};
}

run('growth', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone});
  const noErrors = where => assert.deepEqual(page.errors, [], `${tag}: ${where} without page errors`);

  // Nothing set up: one row with both setup buttons, and Competitors keeps its place.
  await go(page, '#/strategy');
  await page.locator('#strat-setup a.gr-btn').nth(1).waitFor({timeout: t(15000)});
  assert.equal(await page.locator('#strat-gtm a, #strat-brand a').count(), 0, `${tag}: no setup button in the card slots`);
  await page.locator('#strat-rivals .strat-card-head').waitFor({timeout: t(5000)});
  await flat(page, `${tag}: Strategy, nothing set up`);
  await snap(page, `${tag}-00a-strategy-empty`);

  const refs = await seedBase(page);

  // Growth with no ideas still draws the framework over the KPI tree.
  await go(page, '#/strategy/growth');
  await page.locator('#gr-view .gr-ns').waitFor({timeout: t(10000)});
  assert.ok(await page.locator('#gr-view .gr-col').count() >= 1, `${tag}: the input column`);
  await page.locator('[data-gr-seg="fw"] [data-v="experiments"]').click();
  await page.waitForFunction(() => document.querySelectorAll('#gr-view .gr-exp .gr-col').length === 3, null, {timeout: t(5000)});
  await page.locator('[data-gr-seg="fw"] [data-v="north_star"]').click();
  await page.locator('#gr-view .gr-ns').waitFor({timeout: t(5000)});
  await flat(page, `${tag}: Growth, no ideas`);
  await snap(page, `${tag}-00b-growth-empty`);

  // + Idea takes a prediction.
  await page.locator('#strat-actions [data-gr-new]').click();
  const nd = page.locator('dialog.gr-dlg');
  await nd.locator('[name="title"]').fill('Before-and-after photos on the site');
  await nd.locator('[name="hypothesis"]').fill('If we show real homes, more visitors book.');
  await nd.locator('[name="prediction"]').fill('New bookings 54 to 70 by 2026-12-31');
  await nd.locator('[type=submit]').click();
  await page.locator('#gr-view .gr-ic').first().waitFor({timeout: t(10000)});
  const filed = (await ok(page, 'GET', 'growth/ideas')).ideas.find(i => i.title === 'Before-and-after photos on the site');
  assert.equal(filed?.prediction, 'New bookings 54 to 70 by 2026-12-31', `${tag}: the prediction saved`);

  // Channels with no month of numbers: every channel and Unknown, blank, and + Month enters the first.
  await go(page, '#/strategy/growth/channels');
  await page.locator('[data-gc-row="unknown"]').waitFor({timeout: t(10000)});
  const blank = page.locator('tr:has([data-gc-row="google-ads"]) td').nth(3);
  assert.equal((await blank.innerText()).trim(), '—', `${tag}: a blank CAC`);
  await flat(page, `${tag}: Channels, no numbers`);
  await snap(page, `${tag}-00c-channels-empty`);
  await page.locator('[data-gc-month-add]').click();
  const md = page.locator('dialog.gr-dlg');
  assert.equal(await md.locator('[name="month"]').inputValue(), MONTH, `${tag}: this month by default`);
  await md.locator('[name="channel"]').selectOption('google-ads');
  await md.locator('[name="spend"]').fill('1200');
  await md.locator('[name="new_customers"]').fill('12');
  await md.locator('[type=submit]').click();
  await page.waitForFunction(() => /\$\d/.test(document.querySelectorAll('tr:has([data-gc-row="google-ads"]) td')[3]?.textContent || ''), null, {timeout: t(10000)});

  const {ideaId} = await seed(page, refs);

  // Strategy: the Brand strip and the Go-to-market card.
  await go(page, '#/strategy');
  await page.locator('.br-strip').waitFor({timeout: t(15000)});
  await page.locator('.gt-card').waitFor({timeout: t(5000)});
  assert.equal(await page.locator('.br-strip .br-sw').count(), 3, `${tag}: three swatches`);
  assert.equal(await page.locator('.br-strip s.br-never').count(), 2, `${tag}: never-say words struck through`);
  await flat(page, `${tag}: Strategy`);
  await snap(page, `${tag}-01-strategy`);

  // Brand: Book, then Edit saves Positioning and publishes it as v2.
  await go(page, '#/strategy/brand');
  await page.locator('.br-bento .br-hero').waitFor({timeout: t(10000)});
  await flat(page, `${tag}: Brand book`);
  await snap(page, `${tag}-02-brand-book`);
  await go(page, '#/strategy/brand/edit');
  const pos = page.locator('form[data-sec="positioning"] input[name="positioning"]');
  await pos.waitFor({timeout: t(10000)});
  await pos.fill('Home cleaning you never have to think about');
  await page.locator('form[data-sec="positioning"] [type=submit]').click();
  await page.waitForFunction(() => document.querySelector('.br-subs [data-sub="positioning"] .gr-dot'), null, {timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the section saved (${await page.locator('.toast').allInnerTexts()} ${JSON.stringify(page.errors)})`));
  await page.locator('[data-sub="voice"]').click();
  await page.locator('form[data-sec="voice"] .gr-rowed').first().waitFor({timeout: t(5000)});
  // Check copy shows its hits and leaves an unsaved edit in the section alone.
  const trait = page.locator('form[data-sec="voice"] .gr-rowed input').first();
  await trait.fill('Warm and quick');
  await page.locator('[data-check] textarea').fill('Our maid team is cheap');
  await page.locator('[data-check] [type=submit]').click();
  await page.locator('[data-hits] .br-hit').first().waitFor({timeout: t(5000)});
  assert.equal(await trait.inputValue(), 'Warm and quick', `${tag}: the unsaved edit stays`);
  await flat(page, `${tag}: Brand edit`);
  await snap(page, `${tag}-03-brand-edit`);
  await page.locator('[data-doc-publish]').click();
  await page.locator('[data-doc-publish-form] input[name="note"]').fill('Sharper positioning');
  await page.locator('[data-doc-publish-form] [type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('[data-doc-publish-form]') && document.querySelector('.br-edit'), null, {timeout: t(10000)});
  const brand = await ok(page, 'GET', 'brand');
  assert.equal(brand.published.n, 2, `${tag}: published v2`);
  assert.equal(brand.published.body.positioning, 'Home cleaning you never have to think about', `${tag}: the saved section`);

  // Go-to-market: Motion, then Sources flags the revenue clash.
  await go(page, '#/strategy/gtm');
  await page.locator('.gt-mo.gt-on').first().waitFor({timeout: t(10000)});
  assert.equal(await page.locator('.gt-mo.gt-on').count(), 2, `${tag}: primary and secondary`);
  await snap(page, `${tag}-04-gtm-motion`);
  await go(page, '#/strategy/gtm/sources');
  await page.locator('[data-clash]').waitFor({timeout: t(10000)});
  await flat(page, `${tag}: Sources`);
  await snap(page, `${tag}-05-gtm-sources`);
  await go(page, '#/strategy/gtm/model');
  await page.locator('.gt-model .gt-nsr').waitFor({timeout: t(10000)});
  await snap(page, `${tag}-06-gtm-model`);
  await go(page, '#/strategy/gtm/channels');
  await page.locator('[data-ch-edit="google-ads"]').waitFor({timeout: t(10000)});
  await snap(page, `${tag}-07-gtm-channels`);

  // Growth: every framework draws the ideas.
  await go(page, '#/strategy/growth');
  await page.locator('#gr-view .gr-ic').first().waitFor({timeout: t(10000)});
  for (const fw of ['north_star', 'loops', 'journey', 'bets_channels', 'experiments']) {
    await page.locator(`[data-gr-seg="fw"] [data-v="${fw}"]`).click();
    await page.waitForFunction(f => document.querySelector(`[data-gr-seg="fw"] [data-v="${f}"]`)?.getAttribute('aria-checked') === 'true'
      && document.querySelectorAll('#gr-view .gr-ic').length >= 3, fw, {timeout: t(5000)})
      .catch(() => assert.fail(`${tag}: ${fw} draws the ideas`));
    await flat(page, `${tag}: ${fw}`);
    if (fw === 'north_star' || fw === 'bets_channels') await snap(page, `${tag}-08-growth-${fw}`);
  }
  await page.locator('[data-gr-seg="fw"] [data-v="north_star"]').click();

  // Channels: Unknown is its own row, and Google Ads has a CAC ($1,260 / 12).
  await go(page, '#/strategy/growth/channels');
  await page.locator('[data-gc-row="unknown"]').waitFor({timeout: t(10000)});
  const cac = await page.locator('tr:has([data-gc-row="google-ads"]) td').nth(3).innerText();
  assert.match(cac, /\$\d/, `${tag}: a CAC (${cac})`);
  await page.locator('[data-gc-row="google-ads"]').click();
  await page.locator('.gc-detail .gc-heat').waitFor({timeout: t(10000)});
  await flat(page, `${tag}: Channels`);
  await snap(page, `${tag}-09-channels`);

  // The Idea panel: start a test, then a verdict.
  await go(page, '#/task/' + encodeURIComponent(ideaId));
  const panel = page.locator('[data-growth-idea]:not([hidden])');
  await panel.locator('.gi-head').waitFor({timeout: t(15000)});
  assert.equal(await page.locator('.tmodal-main > .tdesc.md:not([hidden])').count(), 0, `${tag}: the hypothesis shows once`);
  await panel.locator('[data-gi-start]').click();
  const start = page.locator('dialog.gr-dlg');
  await start.locator('[name="change"]').fill('Credit goes from $100 to $200');
  await start.locator('[name="stop_if"]').fill('Fewer than 5 referrals in a week');
  await start.locator('[type=submit]').click();
  await panel.locator('[data-gi-verdict]').waitFor({timeout: t(10000)});
  await snap(page, `${tag}-10-idea`);
  await panel.locator('[data-gi-verdict]').click();
  const v = page.locator('dialog.gr-dlg');
  await v.locator('[name="status"]').selectOption('won');
  await v.locator('[name="result"]').fill('19 referrals against 8');
  await v.locator('[name="learning"]').fill('Credit size matters for referrals');
  await v.locator('[type=submit]').click();
  await page.waitForFunction(() => /won/i.test(document.querySelector('[data-growth-idea] .gi-head .pill')?.textContent || ''), null, {timeout: t(10000)});
  const judged = await ok(page, 'GET', 'growth/ideas/' + encodeURIComponent(ideaId));
  assert.equal(judged.status || judged.idea?.status, 'won', `${tag}: the verdict saved`);

  // Learning and This week draw.
  await go(page, '#/strategy/growth/learning');
  await page.locator('.gl-node').first().waitFor({timeout: t(10000)});
  await snap(page, `${tag}-11-learning`);
  await go(page, '#/strategy/growth/week');
  await page.locator('.gr-week').waitFor({timeout: t(10000)});
  await flat(page, `${tag}: This week`);
  await snap(page, `${tag}-12-week`);
  noErrors('every page');
});
