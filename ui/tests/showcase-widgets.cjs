// Showcase, not a test: page widgets on a real hub (seed `pages`), with a page that has its own widget view. The hub
// has no visual address, so the page's frame address is faked and served with the server's real treatment (cleaned,
// the height reporter added: backend/visuals.py). Prints each screenshot as "SHOT <name> <base64 jpeg>".
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {run, open, go, ok, api, t} = require('./support/redesign-e2e.cjs');

const ROOT = path.resolve(__dirname, '..', '..');
const FRAME = 'https://visuals.acme.example/visuals/weekly.sig';
const RAW = `<!doctype html><html><head><title>Engineering weekly</title><style>
:root{color-scheme:light dark;--ink:#1d2230;--muted:#6b7280;--line:#e5e7eb;--bar:#4f7cff;--bg:#fff}
@media (prefers-color-scheme:dark){:root{--ink:#e8eaf0;--muted:#9aa1ad;--line:#2a2e37;--bar:#7c9bff;--bg:#16181d}}
body{margin:0;font:14px/1.45 system-ui,sans-serif;color:var(--ink);background:var(--bg);padding:20px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:18px 0 6px}.sub{color:var(--muted);margin:0 0 16px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}
.kpi{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:12px}.up{color:#16a34a;font-size:12px}
.chart{display:flex;align-items:flex-end;gap:6px;height:90px;border-bottom:1px solid var(--line)}
.chart i{flex:1;background:var(--bar);border-radius:4px 4px 0 0}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
/* Widget view: a compact card in the space the card gives it */
[data-tico-view=card] body{padding:12px}
[data-tico-view=card] .full-only{display:none}
[data-tico-view=card] h1{font-size:14px;margin-bottom:8px}
[data-tico-view=card] .kpis{gap:8px;margin-bottom:10px}
[data-tico-view=card] .kpi{padding:6px 8px}
[data-tico-view=card] .kpi b{font-size:18px}
[data-tico-view=card] .chart{height:56px}
[data-tico-size=small] .chart{display:none}
[data-tico-size=small] .kpis{grid-template-columns:repeat(2,1fr)}
[data-tico-size=small] .kpi:nth-child(3){display:none}
</style></head><body>
<h1>Engineering weekly</h1><p class="sub full-only">Week of Oct 27 · from the Engineering Lead</p>
<div class="kpis"><div class="kpi"><b>14</b><span>changes shipped</span> <span class="up">+4</span></div>
<div class="kpi"><b>6 h</b><span>review time</span> <span class="up">−3 h</span></div><div class="kpi"><b>2</b><span>incidents</span></div></div>
<div class="chart"><i style="height:40%"></i><i style="height:55%"></i><i style="height:35%"></i><i style="height:70%"></i><i style="height:60%"></i><i style="height:85%"></i><i style="height:100%"></i></div>
<section class="full-only"><h2>What shipped</h2><table><tr><th>Change</th><th>Owner</th><th>Merged</th></tr>
<tr><td>Faster sign-in</td><td>Platform</td><td>Oct 28</td></tr><tr><td>Export to CSV</td><td>Data</td><td>Oct 29</td></tr>
<tr><td>Calendar sync fix</td><td>Apps</td><td>Oct 30</td></tr><tr><td>Billing retries</td><td>Payments</td><td>Oct 31</td></tr></table>
<h2>Next week</h2><ul><li>Ship the launch checklist</li><li>Cut review time under 5 h</li><li>Close both incident follow-ups</li></ul></section>
</body></html>`;
const SERVED = execFileSync('python3', ['-I', '-c', `
import sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
from backend import visuals
data, _ = visuals.clean(sys.stdin.read().encode(), "page")
print(visuals.with_reporter(data).decode())
`], {input: RAW, encoding: 'utf8'});

async function snap(page, name, opts = {}) {
  await page.waitForTimeout(700);
  const b = await page.screenshot({type: 'jpeg', quality: 82, ...opts});
  console.log(`SHOT ${name} ${b.toString('base64')}`);
}

async function framed(page) {
  const swap = async route => {
    const response = await route.fetch();
    const body = await response.json().catch(() => null);
    const fix = node => {
      if (Array.isArray(node)) return node.map(fix);
      if (node && typeof node === 'object') {
        const out = {};
        for (const [k, v] of Object.entries(node)) out[k] = fix(v);
        if (out.file_id && out.title === 'Engineering weekly') out.src = FRAME;
        return out;
      }
      return node;
    };
    await route.fulfill({response, json: fix(body)});
  };
  await page.route('**/api/v2/places/**', swap);
  await page.route('**/api/v2/pages/**', swap);
  await page.route(FRAME, route => route.fulfill({contentType: 'text/html; charset=utf-8', body: SERVED}));
}

run('showcase-widgets', ['org', 'toured', 'groups', 'pages'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone});
  const pages = (await ok(page, 'GET', 'pages?limit=10')).pages;
  const weekly = pages.find(p => p.title === 'Engineering weekly');
  const launch = pages.find(p => p.title === 'Launch plan');
  if (!phone) {
    for (const [place, item, size] of [['dashboard', weekly, 'large'], ['dashboard', launch, 'medium'], ['person:me', weekly, 'small'],
      ['bot:engineering-lead', weekly, 'small']]) {
      const r = await api(page, 'POST', 'pins', {place, item: 'page:' + item.file_id, size, position: 0});
      console.log('pin', place, size, r.status, r.status >= 300 ? JSON.stringify(r.body).slice(0, 200) : '');
    }
  } else {
    const r = await api(page, 'POST', 'pins', {place: 'dashboard', item: 'page:' + weekly.file_id, size: 'medium', position: 0});
    console.log('pin phone', r.status);
  }
  await framed(page);
  const skip = page.locator('#feedback-ask:not([hidden]) [data-skip]');

  // The Company dashboard: the page as a large widget, a page without a widget view as a medium one.
  await go(page, '#/overview');
  await page.locator('.pg-card').first().waitFor({timeout: t(15000)}).catch(() => {});
  if (await skip.count()) await skip.click();
  await page.waitForFunction(() => [...document.querySelectorAll('.pg-card iframe.page-frame')].every(f => f.pageHeight), null, {timeout: t(8000)}).catch(() => {});
  const state = await page.evaluate(() => [...document.querySelectorAll('.pg-card')].map(c => ({cls: c.className,
    frame: c.querySelector('iframe')?.getAttribute('src') || '', h: c.getBoundingClientRect().height, expand: !!c.querySelector('.pg-expand')})));
  console.log('cards', JSON.stringify(state));
  const first = page.locator('.pg-card').first();
  if (await first.count()) await first.scrollIntoViewIfNeeded();
  await snap(page, `${tag}-dashboard`);
  if (!phone) {
    const wk = page.locator(`.pg-card[data-page="${weekly.file_id}"]`).first();
    if (await wk.count()) {
      await wk.hover();
      await snap(page, `${tag}-widget-large`, {clip: await wk.boundingBox()});
    }
    const ln = page.locator(`.pg-card[data-page="${launch.file_id}"]`).first();
    if (await ln.count()) await snap(page, `${tag}-widget-plain`, {clip: await ln.boundingBox()});

    // Mine: the same page as a small widget, its own small layout.
    await go(page, '#/home');
    await page.locator(`.pg-card[data-page="${weekly.file_id}"]`).first().waitFor({timeout: t(10000)}).catch(() => {});
    await page.waitForTimeout(1200);
    const sm = page.locator(`.pg-card[data-page="${weekly.file_id}"]`).first();
    if (await sm.count()) await snap(page, `${tag}-widget-small`, {clip: await sm.boundingBox()});
    await snap(page, `${tag}-mine`);

    // The expand icon opens the full page: the full layout, with the table and next steps.
    await go(page, '#/overview');
    const icon = page.locator(`.pg-card[data-page="${weekly.file_id}"] .pg-expand`).first();
    await icon.waitFor({timeout: t(10000)}).catch(() => {});
    if (await icon.count()) await icon.click();
    else await go(page, '#/page/' + weekly.file_id);
    await page.waitForFunction(() => location.hash.startsWith('#/page/'), null, {timeout: t(5000)}).catch(() => {});
    await page.waitForFunction(() => document.querySelector('#pg-view iframe.page-frame')?.style.height, null, {timeout: t(8000)}).catch(() => {});
    console.log('full', await page.evaluate(() => document.querySelector('#pg-view iframe')?.getAttribute('src')));
    await snap(page, `${tag}-full`);

    // Dark theme.
    await page.emulateMedia({colorScheme: 'dark'});
    await go(page, '#/overview');
    await page.waitForTimeout(1500);
    await snap(page, `${tag}-dashboard-dark`);
    await page.emulateMedia({colorScheme: 'light'});
  }
  console.log('errors', JSON.stringify(page.errors));
});
