// Settings > Health's Checks (Amendment v3): what Check health still has open, from GET /api/v2/health/findings. A setup
// problem is fixed by BotOps. A Tico bug that has a ticket says so; one nobody reported gets Report to Tico, which POSTs
// the finding and then shows it as reported. Owners also see the team rule. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const errors = [];
    const seen = '2026-11-04T09:00:00Z';
    const finding = (over) => ({occurrences: 3, last_seen_at: seen, ...over});
    let doc = {botops: true, reports_without_asking: false, findings: [
      finding({key: 'setup:node', kind: 'setup', title: 'Node is missing on this computer', task_id: 'T-4'}),
      finding({key: 'tico:ticket', kind: 'tico', title: 'Upload fails for files over 2 GB', ticket_id: 'T-7'}),
      finding({key: 'disk_full:server', kind: 'tico', title: 'Disk is full on the server'})]};
    const requests = [];
    const serve = async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = (body, code = 200) => route.fulfill({status: code, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
          : name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/me') return json({id: 'chris', email: 'chris@tico.team', role: 'owner', cloud: true, config: {company_name: 'Tico'}});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'chris', name: 'Chris'}], org_groups: []});
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: []});
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/health') return json({audience: 'owner', stuck: [], attention: 0, computers: [{id: 'r1', label: 'Studio Mac', online: true, runtimes: []}], failures: [],
        checks: [{id: 'computers', label: 'Computers', status: 'ok', summary: '1 computer online.', fixes: []}]});
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
    const open = async target => {
      await target.goto('http://tico-ui.test/#/settings');
      await target.locator('#settings-more > summary').click();
      await target.locator('[data-settings-tab=health]').click();
      await target.locator('#hl-page [data-hl-findings]').waitFor();
    };

    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', serve);
    await open(page);
    const section = page.locator('#hl-page [data-hl-findings]');
    assert.equal(await section.locator('h2').innerText(), 'Checks');
    const row = key => section.locator(`[data-hl-finding="${key}"]`);

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
    await page.waitForFunction(() => document.querySelector('[data-hl-report-rule]')?.checked === true);
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
