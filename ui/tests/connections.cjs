// Library › Connections (ui/app/connections.js) against a fake GET /api/v2/connections: one row per connection with its
// status, scope, readers, bots and a Configure link only where the reader may change it; `next` pages the list; the
// rail marks Connections under Library; a phone stacks each row with no sideways scroll; a tool under the old address
// still opens Library › Tools. TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const OWNER = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, bot_admin: true};
const row = (kind, ident, name, status, scope, readers, configure, extra = {}) => ({id: ident ? kind + ':' + ident : kind, kind, name,
  detail: '', status, scope, readers, bots: [], more_bots: 0, configure, can_configure: true, ...extra});
const ROWS = [
  row('slack', '', 'Slack', 'on', 'company', 'Everyone', '#/library/tools/slack', {bots: ['eng-lead', 'builder']}),
  row('github', '', 'GitHub', 'waiting', 'company', 'Bots with repository access', '#/library/tools/github'),
  row('mailbox', 'ops@acme.example', 'ops@acme.example', 'error', 'person', 'Ana Silva', '#/settings/inbox', {detail: 'Sign-in expired'}),
  row('inbox', 'ibx_a', 'Account logins', 'on', 'team', 'Engineering', '#/settings/inbound', {can_configure: false}),
  row('meetings', 'zoom', 'Zoom', 'on', 'company', 'Meeting attendees', '#/meetings'),
  row('webhook', 'wh-1', 'Order alerts', 'paused', 'person', 'Ana Silva', '#/settings/webhooks/outgoing'),
  row('listening', '', 'Listening', 'off', 'company', 'Everyone', '#/strategy/market/listening'),
];

async function open(browser, {viewport = DESKTOP, hash = '#/library/connections'} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [], reads = [];
  page.on('pageerror', e => errors.push(e.message));
  const config = {company_name: 'Acme'};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...OWNER, config});
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/connections') {
      const start = Number(url.searchParams.get('cursor') || 0), end = start ? ROWS.length : 5;
      reads.push(url.search);
      return json({connections: ROWS.slice(start, end), next: end < ROWS.length ? String(end) : null});
    }
    if (p === '/api/v2/tools') return json({integrations: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, reads};
}
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const viewport of [DESKTOP, PHONE]) {
      const tag = viewport === PHONE ? 'phone' : 'desktop';
      const {page, errors, reads} = await open(browser, {viewport});
      const rows = page.locator('.cn-row');
      await rows.nth(4).waitFor();
      assert.equal(await rows.count(), 5, `${tag}: the first page`);
      await page.locator('.cn-more button').click();
      await rows.nth(6).waitFor();
      assert.deepEqual(reads, ['?limit=100', '?limit=100&cursor=5'], `${tag}: follows next`);
      assert.equal(await page.locator('.cn-more').isHidden(), true, `${tag}: nothing more`);
      const slack = page.locator('.cn-row[data-id="slack"]');
      assert.equal(await slack.locator('.cn-go a').getAttribute('href'), '#/library/tools/slack', `${tag}: Slack's cards stay on Tools`);
      assert.equal(await slack.locator('.cn-bots a').count(), 2, `${tag}: its bots`);
      assert.equal(await page.locator('.cn-row[data-id="inbox:ibx_a"] .cn-go a').count(), 0, `${tag}: no link where it cannot be changed`);
      assert.equal(await page.locator('.cn-row[data-id="mailbox:ops@acme.example"] .cn-fail').count(), 1, `${tag}: an error shows`);
      assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
      if (viewport === DESKTOP) {
        await page.locator('#side a[data-nav="connections"].cur').waitFor({state: 'attached'});
      }
      await shot(page, tag + '-connections');
      assert.deepEqual(errors, [], `${tag}: no page errors`);
      await page.close();
    }
    {
      // A tool under the old address still opens Library › Tools.
      const {page, errors} = await open(browser, {hash: '#/library/connections/slack'});
      await page.waitForFunction(() => location.hash === '#/library/tools/slack');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Connections: rows, paging, configure links, rail, phone, old tool addresses');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
