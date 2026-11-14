// Settings > Health's Checks (Amendment v3): what Check health still has open, from GET /api/v2/health/findings. A setup
// problem is fixed by BotOps. A Tico bug that has a ticket says so; one nobody reported gets Report to Tico, which POSTs
// the finding and then shows it as reported. Owners also see the team rule. Failed runs lists the newest five failed jobs
// under its count, each with its bot, when and why (backend/health.py _failed). Fixtures only.
import type {Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const errors: string[] = [];
    const seen = '2026-11-04T09:00:00Z';
    const finding = (over: Record<string, unknown>) => ({occurrences: 3, last_seen_at: seen, ...over});
    let doc: {botops: boolean; reports_without_asking: boolean; findings: Record<string, unknown>[]} = {botops: true, reports_without_asking: false, findings: [
      finding({key: 'setup:node', kind: 'setup', title: 'Node is missing on this computer', task_id: 'T-4'}),
      finding({key: 'tico:ticket', kind: 'tico', title: 'Upload fails for files over 2 GB', ticket_id: 'T-7'}),
      finding({key: 'disk_full:server', kind: 'tico', title: 'Disk is full on the server'})]};
    const failed = (bot: string, minutes: number, why?: string) => ({bot, state: 'failed', at: new Date(Date.now() - minutes * 60000).toISOString(), ...(why ? {why} : {})});
    const failures = [failed('ops', 5, "couldn't start on Studio Mac after 10 tries"), failed('ops', 20, 'Timeout: max_run_minutes (60 minutes)'),
      failed('ops', 30), failed('ops', 40, 'a'), failed('ops', 50, 'b'), failed('ops', 60, 'the sixth is not shown')];
    const requests: {path: string; body: unknown}[] = [];
    const serve = async (route: Route) => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = (body: unknown, code = 200) => route.fulfill({status: code, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
          : name.endsWith('.css') ? 'text/css' : /\.(?:js|ts)$/.test(name) ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/me') return json({id: 'chris', email: 'chris@tico.team', role: 'owner', cloud: true, config: {company_name: 'Tico'}});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'chris', name: 'Chris'}], org_groups: []});
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: []});
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/health') return json({audience: 'owner', stuck: [], attention: 0, computers: [{id: 'r1', label: 'Studio Mac', online: true, runtimes: []}], failures,
        checks: [{id: 'computers', label: 'Computers', status: 'ok', summary: '1 computer online.', fixes: []},
          {id: 'failed', label: 'Failed runs', status: 'warn', summary: '52 runs failed in the last day.', fixes: [{label: 'Open failed runs', href: '#/settings/history?view=runs&failed=1'}]},
          {id: 'never_stuck', label: 'Tasks', status: 'ok', summary: 'not this text', fixes: [], at: '2026-11-10T00:00:00Z',
            counts: {waiting_on_nothing: 0, retry: 2, manager: 1, person: 3, cant_run: 1, broken: 0}}]});
      if (p === '/api/v2/health/findings') return json(doc);
      if (p === '/api/v2/health/findings/disk_full%3Aserver/report' && route.request().method() === 'POST') {
        requests.push({path: p, body: route.request().postDataJSON()});
        doc = {...doc, findings: doc.findings.map(f => f.key === 'disk_full:server' ? {...f, ticket_id: 'T-9'} : f)};
        return json({ticket_id: 'T-9'});
      }
      if (p === '/api/v2/access/rules' && route.request().method() === 'PUT') {
        requests.push({path: p, body: route.request().postDataJSON()});
        doc = {...doc, reports_without_asking: route.request().postDataJSON().health_monitor_reports};
        return json({});
      }
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/status') return json({bots: []});
      return json(unrouted(p));
    };
    const open = async (target: Page) => {
      await target.goto('http://tico-ui.test/#/settings');
      await target.locator('#settings-health-line [data-hl-details]').click();   // Health has no tab: General's Health line opens it
      await target.locator('#hl-page [data-hl-findings]').waitFor();
    };

    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    page.on('pageerror', (e: Error) => errors.push(e.message));
    await page.route('**/*', serve);
    await open(page);
    // Health · Tasks (S15): the never-stuck check's counts, one line each, not its summary.
    assert.deepEqual(await page.locator('#hl-page [data-hl-check="never_stuck"] .hl-ladder li').allInnerTexts(),
      ['0 waiting on nothing', '2 on a retry', '1 with a manager', '3 reached a person', "1 can't run", '0 broken']);
    assert.equal(await page.locator('#hl-page [data-hl-check="never_stuck"]', {hasText: 'not this text'}).count(), 0);
    // Failed runs: under the count, the newest five failed jobs, each with its bot, when, and why when Health says.
    const failedRows = page.locator('#hl-page [data-hl-check="failed"] [data-hl-failures] li');
    assert.equal(await failedRows.count(), 5);
    assert.match(await failedRows.nth(0).innerText(), /^ops\b.*\bago\s+couldn't start on Studio Mac after 10 tries$/s);
    assert.equal(await failedRows.nth(1).locator('.hl-why').innerText(), 'Timeout: max_run_minutes (60 minutes)');
    assert.equal(await failedRows.nth(2).locator('.hl-why').count(), 0, 'no why line when Health sends none');
    assert.equal(await page.locator('#hl-page', {hasText: 'the sixth is not shown'}).count(), 0);
    assert.equal(await page.locator('#hl-page h2', {hasText: 'Failed in the last day'}).count(), 0, 'listed once, under its check');
    const section = page.locator('#hl-page [data-hl-findings]');
    assert.equal(await section.locator('h2').innerText(), 'Checks');
    const row = (key: string) => section.locator(`[data-hl-finding="${key}"]`);

    // A setup problem is BotOps's, with its task linked. A Tico bug with a ticket says it was reported; no button.
    assert.match(await row('setup:node').locator('.muted').innerText(), /^being fixed by BotOps · seen 3 times/);
    assert.equal(await row('setup:node').locator('[data-hl-report]').count(), 0, 'a setup problem gets no report button');
    assert.match(await row('tico:ticket').locator('.muted').innerText(), /^Tico bug, reported to the Tico team/);
    assert.equal(await row('tico:ticket').locator('[data-hl-report]').count(), 0);
    // A Tico bug nobody reported gets Report to Tico, and only that one.
    assert.equal(await section.locator('[data-hl-report]').count(), 1);
    assert.equal(await row('disk_full:server').locator('[data-hl-report]').innerText(), 'Report to Tico');
    assert.match(await row('disk_full:server').locator('.muted').innerText(), /^Tico bug, not reported/);

    // The team rule is a switch an owner can change; it saves and shows back as on.
    const rule = section.locator('label.hl-monitor-row');
    assert.match(await rule.innerText(), /^Report Tico bugs to the Tico team without asking/);
    assert.equal(await section.locator('[data-hl-report-rule]').isChecked(), false);
    await section.locator('[data-hl-report-rule]').check();
    await page.waitForFunction(() => (document.querySelector('[data-hl-report-rule]') as HTMLInputElement | null)?.checked === true);
    assert.deepEqual(requests.at(-1), {path: '/api/v2/access/rules', body: {health_monitor_reports: true}});

    // Report to Tico POSTs the finding with an empty body, then shows it reported.
    await row('disk_full:server').locator('[data-hl-report]').click();
    await page.waitForFunction(() => document.querySelector('[data-hl-finding="disk_full:server"]')?.textContent.includes('reported to the Tico team'));
    assert.deepEqual(requests.at(-1), {path: '/api/v2/health/findings/disk_full%3Aserver/report', body: {}});
    assert.equal(await row('disk_full:server').locator('[data-hl-report]').count(), 0);

    // Phone: the checks fit the width with no horizontal page scroll.
    const phone = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true, serviceWorkers: 'block'});
    phone.on('pageerror', e => errors.push(e.message));
    await phone.route('**/*', serve);
    await open(phone);
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no horizontal page scroll');
    assert.equal(await phone.locator('#hl-page [data-hl-findings]').evaluate(el => el.getBoundingClientRect().right <= 390), true);
    await phone.close();
    assert.deepEqual(errors, []);
    console.log('health-findings: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
