// Setup's first step, What are you building (ui/setup-strategy.ts, ui/app/welcome.ts), against a stand-in for the setup
// record and the research job (POST /api/v2/strategy/draft-from; fixtures only, the Acme example). Next saves the
// website and the line and starts the research once in the background (Back and Next again starts no second one), then
// moves on to Your team; a start the page never heard back from is asked again on the next Next; an internal address
// is the one refusal that keeps the owner on the step. The strategy draft itself is the Strategy page's.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const uiDir = path.join(__dirname, '..');

type Page = import('playwright').Page;
type Body = {names?: {website?: string; [k: string]: unknown}; answers?: {building?: string}; url?: string; text?: string; [k: string]: unknown};
type Sent = [method: string, path: string, body: Body];

const CONFIG = {environment_id: 'acme', company_name: 'Acme', app_name: 'Acme Hub', assistant_name: 'Assistant', assistant_bot: 'assistant',
  public_url: 'https://acme.example', runner_url: 'https://acme.example', local: false, release: '', onboarding_needed: true,
  version: '0.4.0', runner_compat: {version: '0.4.0', min_runner: '0.1.0'}};
const SITE = 'Acme cleans homes and small offices in Austin at about half the usual price.';

// mode: model (the research runs), drop_once (the first start gets no answer), refuse (an internal address).
async function open(browser: import('playwright').Browser, {mode = 'model'}: {mode?: string} = {}) {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  const errors: string[] = [], sent: Sent[] = [];
  page.on('pageerror', e => errors.push(e.message));
  let record: {names: {website: string; [k: string]: unknown}; needed: boolean; [k: string]: unknown} = {names: {company_name: 'Acme', app_name: 'Acme Hub', assistant_name: 'Assistant', website: ''},
    answers: {building: '', what_we_do: '', customers: '', team_size: '', work_arrives: [], software_product: '', departments: [], briefings: {}},
    selected: {}, completed: null, bots: [], machine: {runners: [], enrolled: false}, needed: true,
    default_chart: [{team: 'Operations', on: true, roles: [{id: 'o1', title: 'Operations Lead', live: true, reports_to: null}]}]};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
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
        record = {...record, ...body, names: {...record.names, ...body.names}};
      }
      return json({...record, home: 'human:ana'});
    }
    if (p === '/api/v2/strategy/draft-from/latest') return json(null);
    if (p === '/api/v2/strategy/draft-from' && method === 'POST') {
      const body = req.postDataJSON();
      sent.push(['POST', '/draft-from', body]);
      if (mode === 'drop_once' && posts(sent, '/draft-from').length === 1) return json({error: {code: 'internal', detail: 'Something went wrong'}}, 500);
      if (mode === 'refuse') return json({error: {code: 'validation', detail: "That address is this computer or an internal one; give the team's public website"}}, 422);
      return json({id: 'job-1', state: 'running', step: 'reading', url: body.url, text: body.text, pages: [], found: null, written: null, error: null});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/welcome');
  await page.locator('#onb-website').waitFor();
  return {page, errors, sent};
}
const title = (page: Page) => page.locator('.onb-step-title').innerText();
const posts = (sent: Sent[], what: string) => sent.filter(([, p]) => p === what);
async function building(page: Page, website: string, text: string) {
  await page.fill('#onb-website', website);
  await page.fill('#onb-building', text);
  await page.click('#onb-next');
}
const onTeam = (page: Page) => page.locator('[data-onb-team]').first().waitFor();
const back = async (page: Page) => { await page.click('#onb-back'); await page.locator('#onb-website').waitFor(); };

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Next saves the website and the line, starts the research once, and moves on; Back and Next again start nothing new.
    {
      const {page, errors, sent} = await open(browser);
      await building(page, 'acme.example', SITE);
      await onTeam(page);
      const put = posts(sent, '/setup')[0]![2];
      assert.equal(put.names?.website, 'acme.example');
      assert.equal(put.answers?.building, SITE);
      assert.deepEqual(posts(sent, '/draft-from').map(([, , body]) => body), [{url: 'acme.example', text: SITE}]);
      await back(page);
      assert.equal(await page.locator('#onb-website').inputValue(), 'acme.example');
      await page.click('#onb-next');
      await onTeam(page);
      assert.equal(posts(sent, '/draft-from').length, 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A start the page never heard back from: the owner still moves on, and the next Next asks again.
    {
      const {page, errors, sent} = await open(browser, {mode: 'drop_once'});
      await building(page, 'acme.example', SITE);
      await onTeam(page);
      await back(page);
      await page.click('#onb-next');
      await onTeam(page);
      assert.equal(posts(sent, '/draft-from').length, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // An internal address: the server refuses it and the owner stays to fix it.
    {
      const {page, errors} = await open(browser, {mode: 'refuse'});
      const first = await title(page);
      await building(page, 'intranet.local', '');
      await page.locator('#onb-status .err').waitFor();
      assert.ok((await page.locator('#onb-status').innerText()).length);
      assert.equal(await title(page), first);
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'onb-website');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('setup-strategy: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
