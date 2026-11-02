// Setup's first two steps (ui/setup-strategy.js, ui/app/welcome.js) against a stand-in for the setup record, the
// research job (POST /api/v2/strategy/draft-from, polled) and the strategy draft (fixtures only, the Acme example).
// What are you building saves the website and the line, then starts the research once (Back and Next again starts no
// second one). Your bets polls it, then shows the proposed bets as claims: Keep makes one active (Rules out and Wrong
// if are optional), Strike retires it, and the draft fills in beside them; Next stays off with its reason until
// one bet is active and What we won't do has a line, then saves that line with the
// draft's revision. Without a model, or when the research fails, the step is a plain form; when it gave up, what it
// found can be added by hand; an internal address keeps the owner on the first step.
// TICO_SCREENSHOT_DIR=<dir> saves review screenshots at desktop and phone width.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');

const shots = process.env.TICO_SCREENSHOT_DIR;
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1280, height: 1000};
const shot = async (page, name) => {
  if (!shots) return;
  const was = page.viewportSize();
  for (const [size, viewport] of [['desktop', DESKTOP], ['phone', PHONE]]) {
    await page.setViewportSize(viewport);
    await page.screenshot({animations: 'disabled', path: path.join(shots, `setup-${name}-${size}.png`)});
  }
  await page.setViewportSize(was);
};

const CONFIG = {environment_id: 'acme', company_name: 'Acme', app_name: 'Acme Hub', assistant_name: 'Assistant', assistant_bot: 'coo',
  public_url: 'https://acme.example', runner_url: 'https://acme.example', local: false, release: '', onboarding_needed: true,
  version: '0.4.0', runner_compat: {version: '0.4.0', min_runner: '0.1.0'}};
const SITE = 'Acme cleans homes and small offices in Austin at about half the usual price.';
const FOUND = {
  serve: 'Busy households and small offices in Austin who now hire a freelancer or CleanCo.',
  sell: 'Weekly cleaning at $79 a visit for a home.',
  bets: [
    {statement: 'Price at half the market, because bots do the office work.', why: 'Your site leads with the price.',
     rules_out: 'Premium add-ons, custom quotes.', wrong_if: 'Margin per job under 25% for a quarter'},
    {statement: 'Sell to offices through property managers.', why: 'Your customers page lists three managers.',
     rules_out: 'Ads to tenants.', wrong_if: 'Fewer than 5 managers signed'},
    {statement: 'Weekly cleans only.', why: 'Every plan on your pricing page is weekly.', rules_out: '', wrong_if: 'One-off requests pass 30%'},
  ],
};
// The rest of what the shared prompt drafts (backend/strategy_prompt.py), offered by hand when a run gave up.
const FOUND_MORE = {summary: 'Half-price weekly cleans for Austin homes and offices.', vision: 'Every home in Austin clean every week.',
  know: 'Bots do the scheduling and invoicing, so a visit costs less.', not: 'No one-off deep cleans.'};
const marked = text => `_Unconfirmed: drafted from acme.example._\n\n${text}`;

// mode: model (runs, then writes the draft), no_model, failed, gave_up, refuse (an internal address).
async function open(browser, {mode = 'model', viewport = DESKTOP} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], sent = [];
  page.on('pageerror', e => errors.push(e.message));
  let record = {names: {company_name: 'Acme', app_name: 'Acme Hub', assistant_name: 'Assistant', website: ''},
    answers: {building: '', what_we_do: '', customers: '', team_size: '', work_arrives: [], software_product: '', departments: [], briefings: {}},
    selected: {}, completed: null, bots: [], machine: {runners: [], enrolled: false}, needed: true};
  const st = {draft: {id: 'v-d', n: null, status: 'draft', guide: 'default', summary: '', vision: '', sections: {extra: {}}, bets: [], revision: 1},
    current: null, bets: []};
  let job = null, polls = 0;
  const strategy = () => ({draft: st.draft, current: st.current, bets: st.bets, label: 'Bets', label_one: 'Bet', may_edit: true,
    website: record.names.website, goals_by_bet: {}, health_by_bet: {}});
  const check_by = '2027-01-23';
  const research = () => {
    // The first poll is still drafting; the second has the answer.
    if (job.state !== 'running' || mode === 'hang' || ++polls < 2) { if (job.state === 'running') job.step = 'thinking'; return; }
    if (mode === 'failed') Object.assign(job, {state: 'failed', step: null, error: {code: 'timeout', message: 'The site did not answer in time.'}});
    else if (mode === 'gave_up') Object.assign(job, {state: 'gave_up', step: null, found: {...FOUND, ...FOUND_MORE, bets: FOUND.bets.slice(0, 1)}, error: {code: 'edited', message: 'The draft changed'}});
    else {
      st.draft = {...st.draft, sections: {...st.draft.sections, serve: marked(FOUND.serve), sell: marked(FOUND.sell)}, revision: st.draft.revision + 4};
      st.bets = FOUND.bets.map((b, i) => ({id: 'b-' + (i + 1), statement: b.statement, why: b.why, rules_out: b.rules_out,
        wrong_if: {kind: 'text', text: b.wrong_if, check_by}, cites: [], status: 'proposed', rank: i}));
      Object.assign(job, {state: 'done', step: null, found: FOUND, written: {sections: ['sell', 'serve'], bets: st.bets.map(b => b.id), revision: st.draft.revision}});
    }
  };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    const config = {...CONFIG, onboarding_needed: record.needed};
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', role: 'owner', cloud: true, email: 'ana@acme.example', config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/v2/providers') return json({configured: true, preset: 'subscriptions', enabled: ['anthropic'], providers: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/templates') return json({cards: []});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', email: 'ana@acme.example'}], org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], recent_runs: []});
    if (p === '/api/employees') return json([]);
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/setup') {
      if (method === 'PUT') {
        const body = req.postDataJSON();
        sent.push(['PUT', '/setup', body]);
        // The server keeps the website as an https:// address.
        const website = body.names?.website && !/^https?:\/\//.test(body.names.website) ? 'https://' + body.names.website : body.names?.website || '';
        record = {...record, ...body, names: {...body.names, website}};
      }
      return json({...record, home: 'human:ana'});
    }
    if (p === '/api/v2/strategy/draft-from' && method === 'POST') {
      const body = req.postDataJSON();
      sent.push(['POST', '/draft-from', body]);
      if (mode === 'drop_once' && posts(sent, '/draft-from').length === 1) return json({error: {code: 'internal', detail: 'Something went wrong'}}, 500);
      if (mode === 'refuse') return json({error: {code: 'validation', detail: "That address is this computer or an internal one; give the team's public website"}}, 422);
      job = {id: 'job-1', state: mode === 'no_model' ? 'no_model' : 'running', step: mode === 'no_model' ? null : body.url ? 'reading' : 'thinking',
        url: body.url, text: body.text, revision: st.draft.revision, pages: [], found: null, written: null, error: null,
        started_at: '2026-10-25T09:00:00Z', finished_at: null};
      return json(job);
    }
    const poll = p.match(/^\/api\/v2\/strategy\/draft-from\/([^/]+)$/);
    if (poll) {
      if (!job) return json({error: {code: 'not_found', detail: 'No research yet'}}, 404);
      research();
      return json(job);
    }
    if (p === '/api/v2/strategy' && method === 'GET') return json(strategy());
    if (p.startsWith('/api/v2/strategy') && method !== 'GET') {
      const body = req.postDataJSON();
      sent.push([method, p.slice('/api/v2/strategy'.length), body]);
      if (p === '/api/v2/strategy/draft') {
        if (body.revision !== st.draft.revision) return json({error: {code: 'conflict', detail: 'The draft changed'}}, 409);
        const {sections = {}, revision: _, ...lines} = body;
        st.draft = {...st.draft, ...lines, sections: {...st.draft.sections, ...sections}, revision: st.draft.revision + 1};
        return json({draft: st.draft});
      }
      // The server's rule (strategy._wrong_if): a sentence wrong-if has a Check by.
      if (body.wrong_if?.kind === 'text' && !body.wrong_if.check_by) return json({error: {code: 'lint', detail: 'a wrong-if in words needs a check_by date: the day to look again'}}, 422);
      const one = p.match(/^\/api\/v2\/strategy\/bets\/([^/]+)$/);
      if (one) { const b = st.bets.find(x => x.id === one[1]); Object.assign(b, body); st.draft.revision++; return json({bet: b, revision: st.draft.revision}); }
      if (p === '/api/v2/strategy/bets') {
        const b = {id: 'b-new' + st.bets.length, why: '', rules_out: '', wrong_if: null, cites: [], status: 'proposed', rank: st.bets.length, ...body};
        st.bets.push(b); st.draft.revision++;
        return json({bet: b, revision: st.draft.revision});
      }
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/welcome');
  await page.locator('#onb-website').waitFor();
  return {page, errors, sent, st};
}
const status = page => page.locator('#onb-status').innerText();
const posts = (sent, what) => sent.filter(([, p]) => p === what);
async function building(page, website, text) {
  await page.fill('#onb-website', website);
  await page.fill('#onb-building', text);
  await page.click('#onb-next');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // The research runs, the owner keeps, strikes and completes the claims, writes What we won't do and moves on.
    {
      const {page, errors, sent, st} = await open(browser);
      assert.equal(await page.locator('.onb-head h1').innerText(), 'What are you building');
      assert.match(await page.locator('.onb-progress').innerText().then(t => t.replace(/\s+/g, ' ').trim()), /^Building Bets Names About Team/);
      await shot(page, '1-building');
      await building(page, 'acme.example', SITE);
      await page.locator('#ss').waitFor();
      assert.equal(await page.locator('.onb-head h1').innerText(), 'Your bets');
      const put = posts(sent, '/setup').at(-1)[2];
      assert.equal(put.names.website, 'acme.example');
      assert.equal(put.answers.building, SITE);
      // The research reads the address as the server stored it.
      assert.deepEqual(posts(sent, '/draft-from').map(x => x[2]), [{url: 'https://acme.example', text: SITE}]);
      assert.match(await page.locator('#ss-job').innerText(), /^(Reading|Drafting from) acme\.example…$/);
      assert.equal(await page.locator('#onb-next').isDisabled(), true);
      await shot(page, '2-bets-reading');
      await page.locator('li[data-ss-bet]').nth(2).waitFor();
      assert.equal(await page.locator('#ss-job').innerText(), 'Drafted from acme.example. Keep, edit or strike each.');
      // The draft beside them: the sections marked unconfirmed, no bet kept yet.
      const side = page.locator('#ss-side');
      assert.match(await side.innerText(), /Who we serve\s+Unconfirmed: drafted from acme\.example\.\s+Busy households/i);
      assert.equal(await side.locator('em').first().innerText(), 'Unconfirmed: drafted from acme.example.');
      assert.match(await side.innerText(), /None kept yet/);
      assert.equal(await status(page), 'Needs an active bet');
      await shot(page, '3-bets');

      await page.click('li[data-ss-bet="b-1"] [data-ss-keep]');
      await page.locator('li[data-ss-bet="b-1"] .pill.ok').waitFor();
      assert.deepEqual(sent.at(-1), ['POST', '/bets/b-1', {status: 'active'}]);
      assert.match(await side.locator('.ss-side-bets').innerText(), /Price at half the market/);
      assert.equal(await status(page), "Needs What we won't do");
      assert.equal(await page.locator('#onb-next').isDisabled(), true);

      await page.click('li[data-ss-bet="b-2"] [data-ss-strike]');
      await page.locator('li[data-ss-bet="b-2"]').waitFor({state: 'detached'});
      assert.deepEqual(sent.at(-1), ['POST', '/bets/b-2', {status: 'retired'}]);

      // Keep without Rules out keeps it too: Rules out and Wrong if are optional.
      await page.click('li[data-ss-bet="b-3"] [data-ss-keep]');
      await page.locator('li[data-ss-bet="b-3"] .pill.ok').waitFor();
      assert.deepEqual(sent.at(-1), ['POST', '/bets/b-3', {status: 'active'}]);
      assert.equal(await side.locator('.ss-side-bets li').count(), 2);

      await page.fill('#ss-not', 'No custom quotes, no phone line.');
      assert.match(await side.innerText(), /What we won't do\s+No custom quotes, no phone line\./i);
      assert.equal(await page.locator('#onb-next').isDisabled(), false);
      assert.equal(await status(page), '');
      await shot(page, '4-bets-ready');
      const revision = st.draft.revision;
      await page.click('#onb-next');
      await page.locator('#onb-company').waitFor();
      assert.deepEqual(posts(sent, '/draft').at(-1)[2], {sections: {not: 'No custom quotes, no phone line.'}, revision});
      assert.equal(st.draft.sections.not, 'No custom quotes, no phone line.');

      // Back to the start and Next again: the same website and words start no second run.
      await page.click('#onb-back');
      await page.locator('#ss li[data-ss-bet]').first().waitFor();
      assert.equal(await page.locator('#ss-not').inputValue(), 'No custom quotes, no phone line.');
      await page.click('#onb-back');
      await page.locator('#onb-website').waitFor();
      assert.equal(await page.locator('#onb-website').inputValue(), 'https://acme.example');
      await page.click('#onb-next');
      await page.locator('#ss li[data-ss-bet]').first().waitFor();
      assert.equal(posts(sent, '/draft-from').length, 1);
      assert.equal(await page.locator('#onb-next').isDisabled(), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // No model: the same fields as a plain form, the new bet's form open; a statement alone is enough to keep it.
    {
      const {page, errors, sent} = await open(browser, {mode: 'no_model'});
      await building(page, '', 'A booking app for cleaners.');
      await page.locator('#ss').waitFor();
      assert.deepEqual(posts(sent, '/draft-from').map(x => x[2]), [{url: '', text: 'A booking app for cleaners.'}]);
      assert.equal(await page.locator('#ss-job').innerText(), 'No AI provider yet, so write them yourself.');
      const form = page.locator('[data-bet-form="new"]');
      await form.waitFor();
      await shot(page, '5-bets-plain');
      await form.locator('[name="statement"]').fill('Cleaners pay for bookings, not seats.');
      await form.locator('[name="rules_out"]').fill('Per-seat pricing.');
      await form.locator('[name="wrong_text"]').fill('Fewer than 20 paying cleaners by March');
      await form.locator('button[type="submit"]').click();
      await page.locator('li[data-ss-bet] .pill.ok').waitFor();
      assert.deepEqual(posts(sent, '/bets')[0][2], {statement: 'Cleaners pay for bookings, not seats.', why: '', rules_out: 'Per-seat pricing.',
        wrong_if: {kind: 'text', text: 'Fewer than 20 paying cleaners by March', check_by: new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10)},
        status: 'active'});
      await page.fill('#ss-not', 'No enterprise sales.');
      assert.equal(await page.locator('#onb-next').isDisabled(), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The research failed: it says why, and the step is the plain form.
    {
      const {page, errors} = await open(browser, {mode: 'failed'});
      await building(page, 'acme.example', SITE);
      await page.locator('[data-bet-form="new"]').waitFor();
      assert.equal(await page.locator('#ss-job').innerText(), 'The site did not answer in time. Write them yourself.');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // It gave up (the draft changed under it): what it found, each part added by hand.
    {
      const {page, errors, sent, st} = await open(browser, {mode: 'gave_up'});
      await building(page, 'acme.example', SITE);
      const found = page.locator('#ss-found');
      await found.waitFor();
      assert.match(await found.innerText(), /^The draft changed while this ran, so nothing was written\. It found:/);
      // Summary, vision, the four sections and the one bet, in that order.
      assert.deepEqual(await found.locator('.ss-found-row .strat-bet-k').allTextContents(), ['Summary', 'Vision', 'Who we serve',
        'What we sell, and for how much', "What we know that others don't", "What we won't do", 'Bet']);
      await shot(page, '6-bets-found');
      await found.locator('[data-ss-take="0"]').click();
      await page.locator('li[data-ss-bet]').first().waitFor();
      const added = posts(sent, '/bets')[0][2];
      assert.deepEqual({...added, wrong_if: {...added.wrong_if, check_by: 'set'}}, {statement: FOUND.bets[0].statement, why: FOUND.bets[0].why,
        rules_out: FOUND.bets[0].rules_out, status: 'proposed', wrong_if: {kind: 'text', text: FOUND.bets[0].wrong_if, check_by: 'set'}});
      assert.match(added.wrong_if.check_by, /^\d{4}-\d{2}-\d{2}$/);
      const revision = st.draft.revision;
      await found.locator('[data-ss-use="serve"]').click();
      await page.locator('#ss-side').getByText(FOUND.serve).waitFor();
      assert.deepEqual(posts(sent, '/draft')[0][2], {sections: {serve: FOUND.serve}, revision});
      await found.locator('[data-ss-use="summary"]').click();
      await page.waitForFunction(() => !document.querySelector('[data-ss-use="summary"]'));
      assert.deepEqual(posts(sent, '/draft')[1][2], {summary: FOUND_MORE.summary, revision: revision + 1});
      await found.locator('[data-ss-use="know"]').click();
      await page.waitForFunction(() => !document.querySelector('[data-ss-use="know"]'));
      assert.deepEqual(posts(sent, '/draft')[2][2], {sections: {know: FOUND_MORE.know}, revision: revision + 2});
      assert.equal(st.draft.summary, FOUND_MORE.summary);
      assert.equal(await found.locator('.ss-found-row').count(), 3);                 // the vision, sell and not are left
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A start the page never heard back from: Next again asks again, and the research runs.
    {
      const {page, errors, sent} = await open(browser, {mode: 'drop_once'});
      await building(page, 'acme.example', SITE);
      await page.locator('[data-bet-form="new"]').waitFor();
      await page.click('#onb-back');
      await page.locator('#onb-website').waitFor();
      await page.click('#onb-next');
      await page.locator('li[data-ss-bet]').first().waitFor();
      assert.equal(posts(sent, '/draft-from').length, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A run still going when the owner clears the website and the line: the step is the plain form, not that run.
    {
      const {page, errors, sent} = await open(browser, {mode: 'hang'});
      await building(page, 'acme.example', SITE);
      await page.locator('#ss-job .ss-job').waitFor();
      await page.click('#onb-back');
      await page.locator('#onb-website').waitFor();
      await building(page, '', '');
      await page.locator('[data-bet-form="new"]').waitFor();
      assert.equal(await page.locator('#ss-job').innerText(), '');
      assert.equal(posts(sent, '/draft-from').length, 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // An internal address: the server refuses it and the owner stays to fix it.
    {
      const {page, errors} = await open(browser, {mode: 'refuse'});
      await building(page, 'intranet.local', '');
      await page.locator('#onb-status .err').waitFor();
      assert.match(await status(page), /internal one/);
      assert.equal(await page.locator('.onb-head h1').innerText(), 'What are you building');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'onb-website');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('setup-strategy: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
