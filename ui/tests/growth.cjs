// Growth and Brand on a real hub (the growth spec and the brand-and-gtm spec). First, with nothing set up: the Strategy
// page's setup row, Ideas with no ideas (+ Idea with a prediction) and Channels with no month of numbers (+ Month in a
// channel's detail enters the first). Then Dana, Acme's owner, has a published brand and go-to-market, two channels, two
// sources for revenue (a clash), a North Star with an input, a month of channel numbers with an Unknown row, three ideas
// and four buyers, all seeded through the API. The Strategy page shows the Brand strip; Brand's Edit saves a section and
// publishes it; Sources flags the clash; old addresses land on what replaced them; Ideas draws the grid (a list on a
// phone); Channels shows Unknown and a CAC, opens a channel's detail, edits and retires one; Buyers adds and edits one,
// which Where we play follows, and its go-to-market block publishes; an Idea task picks a buyer, starts a test and takes
// a verdict; the brain opens Learning; Goals switches its KPIs between Tree, Loops and Journey. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, ok, shot, t} = require('./support/redesign-e2e.cjs');

const MONTH = new Date().toISOString().slice(0, 7);

const flat = async (page, what) => {
  const info = await page.evaluate(() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth,
    hash: location.hash, all: [...document.querySelectorAll('*')].map(el => [el, el.getBoundingClientRect()]).filter(([, r]) => r.right > document.documentElement.clientWidth + 1)
      .slice(0, 8).map(([el, r]) => `${el.tagName}.${el.className}:${Math.round(r.left)}-${Math.round(r.right)}`)}));
  assert.ok(info.sw <= info.cw + 1, `${what} no sideways scroll (${JSON.stringify(info)})`);
};

// A screenshot once the page settles: a loop that never ends (a loading shimmer elsewhere on the page) is named and
// stopped first, so the shot does not wait on it.
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

// The KPI tree, the sources and the channels, with no ideas and no month of numbers yet.
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
    source_id: stripe.source?.id || stripe.id, owner_ref: 'bot:engineering-lead', why: 'People search for cleaners'});
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
    buyer: {who_buys: 'Homeowners in Austin', who_uses: '', deal_size: '$90 a month', cycle: '1 week', objections: ['Trust in the home']}}});
  await ok(page, 'POST', 'gtm/publish', {note: 'First go-to-market'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'google-ads', month: MONTH, spend: 1200, bot_cost: 60, new_customers: 12,
    kept_d30: 90, kept_d90: 75, kept_d180: 60, quality: 'actual'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'unknown', month: MONTH, new_customers: 3, quality: 'estimate'});
  const bet = async statement => { const r = await ok(page, 'POST', 'strategy/bets', {statement}); return r.bet?.id || r.id; };
  const [word, season] = [await bet('Happy homes tell their neighbours'), await bet('Move-outs bring first-time customers')];
  const idea = await ok(page, 'POST', 'growth/ideas', {title: 'Referral credit $100 to $200', hypothesis: 'A bigger credit doubles referrals.',
    kpi_id: inpId, bet_id: word, channels: ['referrals'], ice_i: 8, ice_c: 6, ice_e: 7});
  const ads = await ok(page, 'POST', 'growth/ideas', {title: 'Search ads for move-out cleans', kpi_id: nsId, bet_id: season, channels: ['google-ads'], ice_i: 6, ice_c: 5, ice_e: 8});
  await ok(page, 'POST', `growth/ideas/${encodeURIComponent(ads.task_id || ads.idea?.task_id || ads.id)}/start`,
    {test_to: '2026-12-31', change: 'Ads on move-out searches', stop_if: 'CAC over $300'});
  await ok(page, 'POST', 'growth/ideas', {title: 'Door hangers in Mueller', channels: []});
  return {ideaId: idea.task_id || idea.idea?.task_id || idea.id};
}

// Four buyers (two served, one tested, one next), and a month of Google Ads split out for property managers, which
// Channels leaves out of the channel's own numbers.
async function seedBuyers(page) {
  const id = r => r.segment?.id || r.id;
  const homes = id(await ok(page, 'POST', 'growth/segments', {name: 'Homeowners', status: 'serving', motion: 'local', who_buys: 'Homeowners in Austin',
    deal_size: '$90 a month', why: 'Most of our bookings'}));
  const pms = id(await ok(page, 'POST', 'growth/segments', {name: 'Property managers', status: 'serving', motion: 'sales_led', who_buys: 'Operations lead',
    deal_size: '$1,200 a month'}));
  const hosts = id(await ok(page, 'POST', 'growth/segments', {name: 'Rental hosts', status: 'testing', motion: 'self_serve', who_buys: 'Hosts with 1-3 homes'}));
  await ok(page, 'POST', 'growth/segments', {name: 'Offices', status: 'next', motion: 'sales_led'});
  await ok(page, 'PUT', 'growth/cohorts', {channel: 'google-ads', month: MONTH, segment_id: pms, spend: 400, new_customers: 2, kept_d30: 100, quality: 'actual'});
  return {homes, pms, hosts};
}

run('growth', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone});
  const noErrors = where => assert.deepEqual(page.errors, [], `${tag}: ${where} without page errors`);
  // Ideas is a grid on a desktop and a list by bet on a phone: the one that shows.
  const IDEA = phone ? '.gr-prow' : '.gr-ic';
  const toasts = () => page.locator('.toast').allInnerTexts();

  // Nothing set up: one setup button, and Competitors keeps its place.
  await go(page, '#/strategy');
  await page.locator('#strat-setup a.gr-btn').first().waitFor({timeout: t(15000)});
  assert.equal(await page.locator('#strat-gtm, .gt-card').count(), 0, `${tag}: no go-to-market card`);
  assert.equal(await page.locator('#strat-brand a').count(), 0, `${tag}: no setup button in the brand slot`);
  await page.locator('#strat-rivals .strat-card-head').waitFor({timeout: t(5000)});
  await flat(page, `${tag}: Strategy, nothing set up`);
  await snap(page, `${tag}-00a-strategy-empty`);

  // Growth is its own section: Strategy's tabs no longer hold it, and the sidebar's Growth opens it.
  assert.deepEqual(await page.locator('.strat-tabs a').allInnerTexts(), ['Market', 'Strategy', 'Org', 'Goals', 'Budget'], `${tag}: the Strategy tabs`);
  const refs = await seedBase(page);
  if (phone) await page.locator('#mobile-more').click();
  await page.locator('#side a[data-nav="growth"]').click();
  await page.waitForFunction(() => location.hash === '#/growth', null, {timeout: t(5000)});
  await page.waitForFunction(() => document.querySelector('#side a[data-nav="growth"]')?.getAttribute('aria-current') === 'page', null, {timeout: t(5000)})
    .catch(() => assert.fail(`${tag}: Growth is current`));
  assert.deepEqual(await page.locator('.strat-tabs a').allInnerTexts(), ['Ideas', 'Buyers', 'Channels', 'Sources'], `${tag}: the Growth tabs`);

  // Ideas with no ideas still draws the grid, and + Idea takes a prediction.
  await page.locator('#gr-body .gr-grid').waitFor({state: 'attached', timeout: t(10000)});
  assert.equal(await page.locator('[data-gr-seg="fw"]').count(), 0, `${tag}: no framework picker`);
  await flat(page, `${tag}: Ideas, no ideas`);
  await snap(page, `${tag}-00b-ideas-empty`);
  await page.locator('#strat-actions [data-gr-new]').click();
  const nd = page.locator('dialog.gr-dlg');
  await nd.locator('[name="title"]').fill('Before-and-after photos on the site');
  await nd.locator('[name="hypothesis"]').fill('If we show real homes, more visitors book.');
  await nd.locator('[name="prediction"]').fill('New bookings 54 to 70 by 2026-12-31');
  await nd.locator('[type=submit]').click();
  await page.locator(`#gr-body ${IDEA}`).first().waitFor({timeout: t(10000)});
  const filed = (await ok(page, 'GET', 'growth/ideas')).ideas.find(i => i.title === 'Before-and-after photos on the site');
  assert.equal(filed?.prediction, 'New bookings 54 to 70 by 2026-12-31', `${tag}: the prediction saved`);

  // Channels with no month of numbers: every channel and Unknown, blank, and + Month in a channel enters the first.
  const stat = (slug, m) => page.locator(`[data-gc-row="${slug}"] [data-m="${m}"] b`);
  await go(page, '#/growth/channels');
  await page.locator('[data-gc-row="unknown"]').waitFor({timeout: t(10000)});
  assert.equal((await stat('google-ads', 'cac').innerText()).trim(), '—', `${tag}: a blank CAC`);
  await flat(page, `${tag}: Channels, no numbers`);
  await snap(page, `${tag}-00c-channels-empty`);
  await page.locator('[data-gc-row="google-ads"]').click();
  await page.locator('[data-gc-month-add]').click();
  const md = page.locator('dialog.gr-dlg');
  assert.equal(await md.locator('[name="month"]').inputValue(), MONTH, `${tag}: this month by default`);
  await md.locator('[name="spend"]').fill('1200');
  await md.locator('[name="new_customers"]').fill('12');
  await md.locator('[type=submit]').click();
  await page.waitForFunction(() => /\$\d/.test(document.querySelector('[data-gc-row="google-ads"] [data-m="cac"] b')?.textContent || ''), null, {timeout: t(10000)});

  const {ideaId} = await seed(page, refs);
  const buyers = await seedBuyers(page);

  // Strategy: the Brand strip, and no Go-to-market card.
  await go(page, '#/strategy');
  await page.locator('.br-strip').waitFor({timeout: t(15000)});
  assert.equal(await page.locator('.gt-card').count(), 0, `${tag}: no go-to-market card`);
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
  await page.locator('form[data-sec="positioning"] input[name="name"]').fill('Acme Clean');
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
  assert.equal(brand.published.body.name, 'Acme Clean', `${tag}: the brand name saves with Positioning`);

  // Sources: the revenue clash, every source a row that opens its form.
  await go(page, '#/growth/sources');
  await page.locator('[data-clash]').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('[data-src-edit]').count(), 2, `${tag}: a row per source`);
  await flat(page, `${tag}: Sources`);
  await snap(page, `${tag}-04-sources`);
  await page.locator('[data-src-edit]').first().click();
  assert.equal(await page.locator('dialog.gr-dlg [name="truth_for"]').inputValue(), 'Revenue', `${tag}: the source's form`);
  await page.locator('dialog.gr-dlg [data-close]').first().click();

  // Old addresses land on what replaced them, query and all.
  for (const [from, to] of [['#/strategy/growth', '#/growth'], ['#/strategy/growth/channels?x=1', '#/growth/channels?x=1'],
    ['#/strategy/gtm', '#/growth/buyers'], ['#/strategy/gtm/sources', '#/growth/sources'], ['#/growth/gtm/channels', '#/growth/channels'],
    ['#/growth/gtm/model', '#/growth/buyers'], ['#/growth/week', '#/growth'], ['#/growth/segments', '#/growth/buyers']]) {
    await page.evaluate(h => { location.hash = h; }, from);
    await page.waitForFunction(w => location.hash === w, to, {timeout: t(5000)}).catch(async () =>
      assert.fail(`${tag}: ${from} lands on ${to} (${await page.evaluate(() => location.hash)})`));
  }
  await page.waitForFunction(() => document.querySelector('.strat-tabs a.cur')?.dataset.grTab === 'buyers', null, {timeout: t(5000)})
    .catch(() => assert.fail(`${tag}: Buyers is the current tab`));

  // A late Ideas answer never draws over Sources, which sits under #/growth.
  let release;
  const held = new Promise(r => { release = r; });
  await page.route('**/api/v2/growth/ideas*', async route => { await held; await route.continue(); });
  await page.evaluate(() => { location.hash = '#/growth'; });
  await go(page, '#/growth/sources');
  await page.locator('[data-clash]').waitFor({timeout: t(10000)});
  release();
  await page.waitForTimeout(500);
  await page.unroute('**/api/v2/growth/ideas*');
  assert.ok(await page.locator('[data-clash]').count(), `${tag}: Sources stays after a late Ideas answer`);

  // Ideas: every idea shows with its status marker.
  await go(page, '#/growth');
  await page.locator(`#gr-body ${IDEA}`).first().waitFor({timeout: t(10000)});
  await page.waitForFunction(sel => document.querySelectorAll(`#gr-body ${sel} .gr-mk`).length >= 4, IDEA, {timeout: t(5000)})
    .catch(() => assert.fail(`${tag}: each idea has a marker`));
  assert.equal(await page.locator(`#gr-body ${phone ? '.gr-pbet' : '.gr-grid tbody tr'}`).count(), 3, `${tag}: two bets and No bet`);
  await flat(page, `${tag}: Ideas`);
  await snap(page, `${tag}-05-ideas`);
  // With a marketing lead, + Idea can ask them to propose ideas instead: a task for them, and the dialog closes.
  await page.evaluate(() => { S.emps.find(e => e.slug === 'engineering-lead').template = 'marketing-lead'; });
  await page.locator('#strat-actions [data-gr-new]').click();
  const asked = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/v2/tasks'), {timeout: t(10000)});
  await page.locator('dialog.gr-dlg [data-gr-propose]').click();
  const ask = (await asked).request().postDataJSON();
  assert.deepEqual([ask.title, ask.owner], ['Propose growth ideas', 'engineering-lead'], `${tag}: the ask goes to the lead`);
  await page.locator('dialog.gr-dlg').waitFor({state: 'detached', timeout: t(5000)});
  await page.evaluate(() => { delete S.emps.find(e => e.slug === 'engineering-lead').template; });

  // Channels: Unknown is its own row, Google Ads has a CAC ($1,260 / 12) and its whole month, not it plus the part split
  // out by buyer, and no row or detail shows buyers.
  await go(page, '#/growth/channels');
  await page.locator('[data-gc-row="unknown"]').waitFor({timeout: t(10000)});
  assert.match(await stat('google-ads', 'cac').innerText(), /\$\d/, `${tag}: a CAC`);
  assert.equal((await stat('google-ads', 'new').innerText()).trim(), '12', `${tag}: the channel's whole month`);
  assert.equal(await page.locator('.gc-all').count(), 1, `${tag}: the All row`);
  assert.notEqual((await page.locator('[data-gc-row="google-ads"] .gc-own').innerText()).trim(), '', `${tag}: the channel's owner`);
  await flat(page, `${tag}: Channels`);
  await snap(page, `${tag}-06-channels`);
  await page.locator('[data-gc-row="google-ads"]').click();
  await page.locator('.gc-detail .gc-coh').first().waitFor({timeout: t(10000)});
  assert.equal(await page.locator('.gc-detail .gc-coh').count(), 1, `${tag}: one whole month in the detail`);
  assert.equal(await page.locator('[data-gc-seg], #gr-body .gs-tag').count(), 0, `${tag}: no buyers on Channels`);
  await flat(page, `${tag}: a channel's detail`);
  await snap(page, `${tag}-07-channel-detail`);
  // Edit changes the channel, with no buyers in its form.
  await page.locator('[data-ch-edit]').click();
  const ce = page.locator('dialog.gr-dlg');
  assert.equal(await ce.locator('[data-gs-pick]').count(), 0, `${tag}: no buyers in the channel's form`);
  await ce.locator('[name="stage"]').selectOption('paused');
  await ce.locator('[type=submit]').click();
  await page.locator('[data-gc-row="google-ads"] .pill.waiting').waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the stage saved (${await toasts()})`));
  // + Channel adds one, and its Edit retires it after asking once more on the same button.
  await page.locator('#strat-actions [data-ch-add]').click();
  const cn = page.locator('dialog.gr-dlg');
  await cn.locator('[name="name"]').fill('Door hangers');
  assert.equal(await cn.locator('[name="slug"]').inputValue(), 'door-hangers', `${tag}: the slug follows the name`);
  await cn.locator('[type=submit]').click();
  await page.locator('[data-gc-row="door-hangers"]').waitFor({timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the new channel shows (${await toasts()})`));
  await page.locator('[data-gc-row="door-hangers"]').click();
  await page.locator('[data-ch-edit]').click();
  const retire = page.locator('dialog.gr-dlg [data-retire]');
  await retire.click();
  assert.ok(await page.locator('[data-gc-row="door-hangers"]').count(), `${tag}: the first press only asks`);
  await retire.click();
  await page.locator('[data-gc-row="door-hangers"]').waitFor({state: 'detached', timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the channel retires (${await toasts()})`));

  // Buyers: a plain list, the name and who buys.
  await go(page, '#/growth/buyers');
  const rows = page.locator('[data-gb-row]');
  await rows.nth(3).waitFor({timeout: t(10000)});
  assert.equal(await rows.count(), 4, `${tag}: four buyers`);
  assert.equal(await page.locator('[data-gb-row] :is(.pill, .gr-tag, select, input)').count(), 0, `${tag}: only words on a row`);
  assert.match(await page.locator(`[data-gb-row="${buyers.homes}"]`).innerText(), /Homeowners[\s\S]*Homeowners in Austin/, `${tag}: the name and who buys`);
  await flat(page, `${tag}: Buyers`);
  await snap(page, `${tag}-08-buyers`);
  // + Buyer adds a new one, testing by default.
  await page.locator('#strat-actions [data-gs-new]').click();
  const bd = page.locator('dialog.gr-dlg');
  await bd.locator('[name="name"]').fill('Move-out cleans');
  assert.equal(await bd.locator('[name="status"]').inputValue(), 'testing', `${tag}: a new buyer is tested first`);
  await bd.locator('[name="who_buys"]').fill('Renters moving out');
  await bd.locator('[type=submit]').click();
  await rows.nth(4).waitFor({timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the new buyer shows (${await toasts()})`));
  const made = (await ok(page, 'GET', 'growth/segments')).segments.find(x => x.name === 'Move-out cleans');
  assert.deepEqual([made?.status, made?.who_buys], ['testing', 'Renters moving out'], `${tag}: the new buyer saved`);
  // Its dialog moves Rental hosts to Serving.
  const editBuyer = async (id, status) => {
    await page.locator(`[data-gb-edit="${id}"]`).click();
    const d = page.locator('dialog.gr-dlg');
    await d.locator('[name="status"]').selectOption(status);
    await d.locator('[type=submit]').click();
    await d.waitFor({state: 'detached', timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the buyer saved (${await d.locator('[data-error]').innerText()})`));
  };
  await editBuyer(buyers.hosts, 'serving');
  assert.equal((await ok(page, 'GET', 'growth/segments')).segments.find(x => x.id === buyers.hosts)?.status, 'serving', `${tag}: the status saved`);
  // Dropped folds behind its toggle.
  await editBuyer(made.id, 'dropped');
  const tog = page.locator('[data-gb-dropped]');
  await tog.waitFor({timeout: t(10000)});
  assert.equal(await page.locator(`.gb-dropped [data-gb-row="${made.id}"]`).isVisible(), false, `${tag}: dropped is folded`);
  await tog.click();
  await page.locator(`.gb-dropped [data-gb-row="${made.id}"]`).waitFor({timeout: t(5000)});
  await flat(page, `${tag}: Buyers with Dropped open`);
  // The go-to-market block edits the draft and publishes it; who buys stays as it was.
  assert.match(await page.locator('.gb-gtm').innerText(), /Local services \+ Self-serve/, `${tag}: the motion reads`);
  await page.locator('[data-gtm-edit]').click();
  const gd = page.locator('dialog.gr-dlg');
  assert.equal(await gd.locator('[name="cycle"]').inputValue(), '1 week', `${tag}: the cycle in the form`);
  await gd.locator('[name="cycle"]').fill('2 weeks');
  await gd.locator('[name="note"]').fill('Longer cycle');
  await snap(page, `${tag}-09-gtm-edit`);
  await gd.locator('[type=submit]').click();
  await page.waitForFunction(() => /2 weeks/.test(document.querySelector('.gb-gtm')?.textContent || ''), null, {timeout: t(10000)})
    .catch(async () => assert.fail(`${tag}: the block shows the new cycle (${await toasts()})`));
  const gtm = await ok(page, 'GET', 'gtm');
  assert.deepEqual([gtm.published.n, gtm.published.body.buyer.cycle, gtm.published.body.buyer.who_buys], [2, '2 weeks', 'Homeowners in Austin'], `${tag}: published v2`);
  await page.locator('.gb-gtm').scrollIntoViewIfNeeded();
  await snap(page, `${tag}-10-buyers-gtm`);
  // Where we play follows: three Us, one Next.
  await go(page, '#/strategy');
  await page.waitForFunction(() => document.querySelectorAll('#strat-where .strat-tm.ours').length === 3, null, {timeout: t(15000)})
    .catch(async () => assert.fail(`${tag}: three Us (${await page.locator('#strat-where').innerText()})`));
  assert.equal(await page.locator('#strat-where .strat-tm.next').count(), 1, `${tag}: one Next`);
  await page.locator('#strat-where').scrollIntoViewIfNeeded();
  await flat(page, `${tag}: Where we play`);
  await snap(page, `${tag}-11-where-we-play`);

  // The Idea panel: pick a buyer, start a test, then a verdict.
  await go(page, '#/task/' + encodeURIComponent(ideaId));
  const panel = page.locator('[data-growth-idea]:not([hidden])');
  await panel.locator('.gi-head').waitFor({timeout: t(15000)});
  assert.equal(await page.locator('.tmodal-main > .tdesc.md:not([hidden])').count(), 0, `${tag}: the hypothesis shows once`);
  await panel.locator('[data-gi-edit]').click();
  const ed = page.locator('dialog.gr-dlg');
  await ed.locator(`[data-gs-pick] input[value="${buyers.homes}"]`).check();
  await ed.locator('[type=submit]').click();
  await panel.locator('.gs-tag').waitFor({timeout: t(10000)});
  assert.equal(await panel.locator('.gs-tag').innerText(), 'Homeowners', `${tag}: the panel shows the buyer`);
  const picked = await ok(page, 'GET', 'growth/ideas/' + encodeURIComponent(ideaId));
  assert.deepEqual((picked.segments || picked.idea?.segments || []).map(x => x === buyers.homes || x === 'segment/homeowners'), [true], `${tag}: the buyer saved`);
  await panel.locator('[data-gi-start]').click();
  const start = page.locator('dialog.gr-dlg');
  await start.locator('[name="change"]').fill('Credit goes from $100 to $200');
  await start.locator('[name="stop_if"]').fill('Fewer than 5 referrals in a week');
  await start.locator('[type=submit]').click();
  await panel.locator('[data-gi-verdict]').waitFor({timeout: t(10000)});
  await snap(page, `${tag}-12-idea`);
  await panel.locator('[data-gi-verdict]').click();
  const v = page.locator('dialog.gr-dlg');
  await v.locator('[name="status"]').selectOption('won');
  await v.locator('[name="result"]').fill('19 referrals against 8');
  await v.locator('[name="learning"]').fill('Credit size matters for referrals');
  await v.locator('[type=submit]').click();
  await page.waitForFunction(() => /won/i.test(document.querySelector('[data-growth-idea] .gi-head .pill')?.textContent || ''), null, {timeout: t(10000)});
  const judged = await ok(page, 'GET', 'growth/ideas/' + encodeURIComponent(ideaId));
  assert.equal(judged.status || judged.idea?.status, 'won', `${tag}: the verdict saved`);

  // Ideas again: a result is greyed and sits after a live idea beside it.
  await ok(page, 'POST', 'growth/ideas', {title: 'Referral flyers', bet_id: judged.bet_id || judged.idea?.bet_id, channels: ['referrals']});
  await go(page, '#/growth');
  const wonIdea = page.locator(`#gr-body ${IDEA}[data-idea="${ideaId}"]`);
  await wonIdea.waitFor({timeout: t(10000)});
  assert.match(await wonIdea.getAttribute('class'), /gr-is-won/, `${tag}: the result's class`);
  assert.ok(await wonIdea.evaluate((el, sel) => el.previousElementSibling?.matches(sel) && !el.nextElementSibling, IDEA), `${tag}: the result comes last`);
  await snap(page, `${tag}-13-ideas-result`);

  // The brain opens Learning, with the go-to-market's versions.
  await page.locator('a.gr-brain').click();
  await page.waitForFunction(() => location.hash === '#/growth/learning', null, {timeout: t(5000)});
  await page.locator('.gl-node').first().waitFor({timeout: t(10000)});
  assert.equal(await page.locator('[data-gtm-ver]').count(), 2, `${tag}: two go-to-market versions`);
  await flat(page, `${tag}: Learning`);
  await snap(page, `${tag}-14-learning`);
  await page.locator('[data-gr-seg="gl"] [data-v="log"]').click();
  await page.locator('.gl-log .gr-row').first().waitFor({timeout: t(5000)});
  await flat(page, `${tag}: Learning log`);
  await snap(page, `${tag}-15-learning-log`);
  await page.locator('[data-gtm-ver="1"]').click();
  await page.locator('dialog.gr-vdlg .br-dl').waitFor({timeout: t(10000)});
  await page.locator('dialog.gr-vdlg [data-close]').first().click();

  // Goals: the KPIs by Tree (the default), Loops and Journey; a KPI's dialog places it in a loop.
  await go(page, '#/strategy/goals');
  const kpis = page.locator('#goal-kpis');
  await kpis.locator('.gkv-ns').waitFor({timeout: t(15000)});
  assert.equal(await kpis.locator('[data-gr-seg="gkv"] [aria-checked="true"]').getAttribute('data-v'), 'tree', `${tag}: Tree by default`);
  assert.equal(await kpis.locator('.gkv-in').count(), 1, `${tag}: the input under the North Star`);
  await kpis.scrollIntoViewIfNeeded();
  await flat(page, `${tag}: Goals, Tree`);
  await snap(page, `${tag}-16-goals-tree`);
  await kpis.locator(`[data-kpi-place="${refs.inpId}"]`).click();
  const pd = page.locator('dialog.gr-dlg');
  await pd.locator('[name="loop_name"]').fill('Bookings loop');
  await pd.locator('[name="loop_step"]').fill('1');
  await pd.locator('[type=submit]').click();
  await pd.waitFor({state: 'detached', timeout: t(10000)}).catch(async () => assert.fail(`${tag}: the place saved (${await pd.locator('[data-error]').innerText()})`));
  // Leaving Goals shuts an open KPI dialog, so it can't save over another page.
  await kpis.locator(`[data-kpi-place="${refs.inpId}"]`).click();
  await pd.waitFor({timeout: t(5000)});
  await page.evaluate(() => { location.hash = '#/tasks'; });
  await pd.waitFor({state: 'detached', timeout: t(10000)});
  await page.evaluate(() => { location.hash = '#/strategy/goals'; });
  await kpis.locator('[data-gr-seg="gkv"]').waitFor({timeout: t(15000)});
  await kpis.locator('[data-gr-seg="gkv"] [data-v="loops"]').click();
  await kpis.locator('.gkv-loop .gkv-k').waitFor({timeout: t(10000)});
  await flat(page, `${tag}: Goals, Loops`);
  await snap(page, `${tag}-17-goals-loops`);
  await kpis.locator('[data-gr-seg="gkv"] [data-v="journey"]').click();
  await kpis.locator('.gkv-col .gkv-k').waitFor({timeout: t(5000)});
  assert.equal(await kpis.locator('.gkv-col').count(), 5, `${tag}: a column per stage`);
  await flat(page, `${tag}: Goals, Journey`);
  await snap(page, `${tag}-18-goals-journey`);
  noErrors('every page');
});
