import type {Route} from 'playwright';
// First run: the welcome's first screen offers "Start with a sample company" (backend/sample_company.py). One click
// posts /api/v2/setup/sample once and lands on the finished first run, named as the sample, with no wizard step
// saved; the offer is gone from the second screen on. Fixtures only - no server, no network.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

// u5: browser globals the page's own scripts define (see ui/app); declared once per name, same type everywhere.
declare var S: {config: {update: {current: string; latest: string; available: boolean; managed?: boolean}; company_name: string; onboarding_needed: boolean; providers_configured: boolean}; emps: {id: string; name: string}[]; me: {role: string; bot_admin?: boolean}; route: string};


const CONFIG = {environment_id: 'initech', company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace',
  assistant_bot: 'assistant', public_url: 'https://initech.test', runner_url: 'https://initech.test', local: false, release: '',
  onboarding_needed: true, providers_configured: false, version: '0.2.0', runner_compat: {version: '0.2.0', min_runner: '0.1.0'}};
const SAMPLE_BOTS = ([['assistant', 'Assistant'], ['botops', 'BotOps'], ['head-of-product', 'Product Lead'], ['builder', 'Builder'],
  ['support-agent', 'Support Agent'], ['content-marketer', 'Content Owner'], ['strategy-owner', 'Strategy Owner'],
  ['librarian', 'Librarian']] as [string, string][]).map(([slug, display_name]) => ({id: slug, slug, display_name,
  status: 'active', template: slug, onboarding_state: ['assistant', 'botops'].includes(slug) ? '' : 'onboarded', setup_task_id: null,
  repository_present: false}));

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    const context = await browser.newContext({viewport: {width: 1100, height: 800}, serviceWorkers: 'block'});
    const puts: Record<string, unknown>[] = [], samples: Record<string, unknown>[] = [], previews: Record<string, unknown>[] = [], imports: Record<string, unknown>[] = [];
    let record: {names: {company_name: string; app_name: string; assistant_name: string}; answers: Record<string, string>; selected: Record<string, unknown>; completed: string | null; bots: unknown[]; machine: {runners: unknown[]; enrolled: boolean}; needed: boolean; sample: {loaded: string; by: string; bots: string[]; tasks: string[]} | null; [k: string]: unknown} = {names: {company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace'},
      answers: {what_we_do: '', customers: '', team_size: '', software_product: ''}, selected: {}, completed: null, bots: [],
      machine: {runners: [], enrolled: false}, needed: true, sample: null};
    let samplesHeld: Promise<unknown> | null = null, releaseSample: (value?: unknown) => void = () => {};
    await context.route('**/*', async (route: Route) => {
      const request = route.request(), p = new URL(request.url()).pathname;
      const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      // index.html loads marked from /vendor: a stub, so the page's own script errors do not hold the recovery card up.
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (isUiPath(p)) return serveUi(route, p);
      const config = {...CONFIG, ...record.names, onboarding_needed: record.needed, providers_configured: !!record.sample};
      if (p === '/api/me') return json({id: 'owner', name: 'Owner Person', role: 'owner', cloud: true, email: 'owner@initech.test', config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], recent_runs: []});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: []});
      if (p === '/api/v2/templates') return json({cards: []});
      if (p === '/api/v2/setup') {
        if (request.method() === 'PUT') { puts.push(request.postDataJSON() as Record<string, unknown>); return json({...record, home: 'human:owner'}); }
        return json({...record, home: 'human:owner'});
      }
      if (p === '/api/v2/setup/sample') {
        samples.push(request.postDataJSON() as Record<string, unknown>);
        await samplesHeld;
        record = {...record, names: {...record.names, company_name: 'Sample Co'}, completed: '2026-10-18T10:00:00Z', needed: false,
                  bots: SAMPLE_BOTS, sample: {loaded: '2026-10-18T10:00:00Z', by: 'human:owner', bots: SAMPLE_BOTS.map(b => b.slug), tasks: ['t1', 't2', 't3', 't4', 't5']}};
        return json({...record, home: 'human:owner'});
      }
      if (p === '/api/v2/setup/company/preview') {
        previews.push(request.postDataJSON() as Record<string, unknown>);
        return json({fingerprint: 'a'.repeat(64), company_name: 'Acme Finance', builtins: ['assistant', 'botops'],
          employees: [{id: 'assistant', title: 'Assistant', start: 'now', reports_to: 'human:owner'}],
          seats: [{id: 'bookkeeping', title: 'Acme <Bookkeeper>', template: 'finance-owner', reports_to: 'finance-owner'}],
          deferred: ['tool_connections']});
      }
      if (p === '/api/v2/setup/company/import') {
        imports.push(request.postDataJSON() as Record<string, unknown>);
        record = {...record, names: {...record.names, company_name: 'Acme Finance'},
          completed: '2026-10-20T10:00:00Z', needed: false,
          company_import: {fingerprint: 'a'.repeat(64), deferred: ['tool_connections'], seats: 1,
                           people: [{id: 'dana', name: 'Dana', title: 'Controller', seats: [], invited_at: null}]},
          company_import_failed: null, seats: {bookkeeping: 's-1'},
          bots: [{...SAMPLE_BOTS[0], id: 'assistant', slug: 'assistant', display_name: 'Assistant', status: 'active'}]};
        return json({...record, home: 'human:owner'});
      }
      if (p === '/api/v2/setup/getting-started/state') return json({tour: true, checklist: false, cards: [], skipped: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p.startsWith('/api/')) return json(unrouted(p));
      return route.fulfill({status: 404, body: ''});
    });

    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('https://tico-ui.test/');
    await page.waitForFunction(() => location.hash === '#/welcome');
    await page.locator('#onb-website').waitFor();
    // The first screen offers it, in plain words.
    assert.equal(await page.locator('#onb-sample').textContent(), 'Start with a sample company');
    assert.match(await page.locator('#onb-sample-box').textContent(), /five bots plus the built-in helpers.*approve it\. Nothing is sent outside\./s);
    // One click: the Next buttons wait, a second click does nothing, and the post is made once with no draft saved.
    samplesHeld = new Promise(resolve => { releaseSample = resolve; });
    await page.locator('#onb-sample').click();
    await page.locator('#onb-status', {hasText: 'Starting the sample company'}).waitFor();
    assert.equal(await page.locator('#onb-next').isDisabled(), true);
    await page.evaluate(() => (document.querySelector('#onb-sample') as HTMLElement | null)?.click());
    releaseSample();
    await page.locator('#onb-tasks').waitFor();
    assert.deepEqual(samples, [{}]);
    assert.deepEqual(puts, []);
    assert.equal(await page.locator('#onb-sample').count(), 0);
    assert.equal(await page.evaluate(() => [S.config.company_name, S.config.onboarding_needed, S.config.providers_configured].join('|')),
                 'Sample Co|false|true');
    assert.equal(await page.locator('[data-onb-bot=builder]').count(), 1);
    assert.match(await page.locator('.fr-sample-created').textContent(), /Five bots plus the built-in helpers/);
    assert.equal(await page.locator('.fr-starter-tasks summary').textContent(), '5 starter tasks created');
    assert.deepEqual(await page.locator('.fr-starter-tasks a').evaluateAll(links => links.map(link => link.getAttribute('href'))),
      ['#/task/t1', '#/task/t2', '#/task/t3', '#/task/t4', '#/task/t5']);
    assert.equal(await page.locator('#fr-owners summary').textContent().then(text => text!.replace(/\s+/g, ' ').trim()), 'You own all 8 bots. Change');

    // A company that has begun the wizard is past the offer: it shows on the first screen only.
    const later = await context.newPage();
    record = {...record, completed: null, needed: true, sample: null, bots: []};
    await later.goto('https://tico-ui.test/');
    later.on('pageerror', e => errors.push(e.message));
    await later.locator('#onb-website').waitFor();
    await later.locator('#onb-next').click();
    await later.locator('#ss').waitFor();
    assert.equal(await later.locator('#onb-sample').count(), 0);

    // A picker file is previewed first. Applying it uses its own endpoint once,
    // preserves its title as text and never substitutes the sample-company path.
    const imported = await context.newPage();
    await imported.goto('https://tico-ui.test/');
    await imported.locator('#onb-company-file').setInputFiles({name: 'acme.tico.company.yaml',
      mimeType: 'text/yaml', buffer: Buffer.from('version: 0.2\ncompany:\n  name: Acme Finance\n')});
    await imported.locator('#onb-company-file-use').waitFor();
    assert.equal(imports.length, 0);
    // The built-ins work from day one; every other role is a Recruiting seat.
    assert.match(await imported.locator('#onb-company-file-box').innerText(), /Assistant — works from day one.*Acme <Bookkeeper> — Recruiting/s);
    assert.equal(await imported.locator('#onb-company-file-box bookkeeper').count(), 0);
    await imported.locator('#onb-company-file-use').click();
    await imported.locator('#onb-tasks').waitFor();
    assert.equal(imports.length, 1);
    assert.deepEqual(imports[0], {...previews[0], fingerprint: 'a'.repeat(64)});
    assert.equal(samples.length, 1);
    assert.match(await imported.locator('#onb').innerText(), /Your chosen org chart is laid out: 1 role marked Recruiting/);
    assert.match(await imported.locator('#onb').innerText(), /Invite the people it names from Settings › Humans/);
    assert.match(await imported.locator('#onb').innerText(), /Saved for later setup: tool connections/);
    assert.deepEqual(errors, []);
    console.log('sample-company: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
