// Tools > GitHub right after the owner connects an organization with All repositories (ui/github-connect.ts). The
// connected card asks at once which repository the team works on, one tap each, and ticks nothing by itself; a lone
// product repository is the main button. While Tico makes the bots' own repositories it says how many are made, and
// why the rest wait when GitHub said. A second return from GitHub says so in one sentence. Desktop and phone.
import type {Locator, LaunchOptions, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;


const VIEWPORTS = {desktop: {width: 1440, height: 900}, phone: {width: 390, height: 844}};
const ORG = {
  connected: true, slug: 'kiln-tico', org: 'ticoteam', account_type: 'Organization', app_id: 4242, administration: true, installed: true,
  authorized: false, callback_url: '', install_url: '/api/v2/github/app/install',
  settings_url: 'https://github.com/organizations/ticoteam/settings/apps/kiln-tico',
  permissions_url: 'https://github.com/organizations/ticoteam/settings/apps/kiln-tico/permissions',
  uninstall_url: 'https://github.com/organizations/ticoteam/settings/installations',
  create_url: 'https://github.com/new?owner=ticoteam&visibility=private',
  bot_repositories: {made: 6, taken: 0, waiting: 15, problem: ''},
};
type AppState = typeof ORG | {connected: false};
interface Repo {full_name: string; enabled: boolean; bot_repo: boolean; archived: boolean | null; reachable: boolean}
interface State {app: AppState; repositories: Repo[]; puts: {name: string; body: unknown}[]}
const repo = (full_name: string, more: Partial<Repo> = {}): Repo => ({full_name, enabled: false, bot_repo: false, archived: false, reachable: true, ...more});

// The routes the Tools page needs; the GitHub App's answer is read from `state.app` so one page can change it.
async function serve(page: Page, state: State) {
  await page.route('**/*', (route: Route) => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (isUiPath(p)) return serveUi(route, p);
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico', local: true, compose_project: 'acme', server_network: 'acme_default'};
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/people') return json({people: [], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: []});
    const github = {service: 'github', title: 'GitHub', kind: 'cli', summary: 'Code', access: 'gh CLI', credentials: ['GH_TOKEN'], declared_as: '',
      writes: 'allowed', owner_id: 'Ana', aliases: [], status: 'not_configured', bots: [], connections: []};
    if (p === '/api/v2/tools') return json({integrations: [github]});
    if (p === '/api/v2/tools/github') return json({...github, queries: [], body: '', learnings: []});
    if (p === '/api/v2/github/app') return json(state.app);
    if (p === '/api/v2/repositories') return json({repositories: state.repositories, github_connected: true, github_installed: true});
    if (p.startsWith('/api/v2/repositories/') && req.method() === 'PUT') {
      const name = decodeURIComponent(p.slice('/api/v2/repositories/'.length));
      state.puts.push({name, body: req.postDataJSON()});
      const row = state.repositories.find(r => r.full_name === name);
      if (row) row.enabled = true;
      return json(row || {});
    }
    if (p === '/api/v2/github/app/authorize-link') return json({url: 'https://github.com/login/oauth/authorize?client_id=Iv1.abc&state=fixture'});
    if (p === '/api/v2/access') return json({people: [{id: 'ana', name: 'Ana', owner: true, role: 'owner', can_sign_in: true}], home_domain: 'example.com', allowed: [], allowed_domains: [], owner_id: {}, rules: {}});
    if (p === '/api/v2/me/tokens') return json({tokens: []});
    if (p === '/api/v2/slack/app') return json({configured: false});
    if (p === '/api/v2/meeting-importers') return json({importers: [], computers: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
}

// The text a person reads, with the layout's whitespace collapsed.
const words = async (locator: Locator) => (await locator.textContent())!.replace(/\s+/g, ' ').trim();


(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    const errors: string[] = [];
    for (const [tag, viewport] of Object.entries(VIEWPORTS)) {
      const page = await browser.newPage({viewport, serviceWorkers: 'block'});
      page.on('pageerror', (e: Error) => errors.push(e.message));
      // One product repository, a bot's own, an archived one and one GitHub no longer lists.
      const state: State = {app: ORG, puts: [], repositories: [
        repo('ticoteam/bot-builder', {bot_repo: true}), repo('ticoteam/tico'), repo('ticoteam/old-site', {archived: true}),
        repo('ticoteam/bot-product-manager', {bot_repo: true, reachable: false, archived: null})]};
      await serve(page, state);
      await page.goto('https://tico-ui.test/#/library/tools/github?github=connected');
      const host = page.locator('#set-github');
      const choose = host.locator('[data-gh-choose]');
      await choose.waitFor();
      assert.match(await words(choose), /^Which repository does your team work on\? ticoteam\/tico Use$/, 'only the product repository is offered');
      const use = choose.getByRole('button', {name: 'Use', exact: true});
      assert.equal(await use.count(), 1);
      assert.match((await use.getAttribute('class'))!, /primary/, 'a lone product repository is the main button');
      assert.deepEqual(state.puts, [], 'nothing is put In use for the person');
      assert.match(await words(host), /Making bot repositories: 6 of 21\./);
      assert.equal(await words(host.locator('[data-gh-status]')), '', 'a first return needs no words');
      if (tag === 'phone') {
        const box = (await use.boundingBox())!;
        assert.ok(box.x + box.width <= viewport.width && box.height >= 32, 'the Use button fits a phone and can be tapped');
      }
      // One tap puts it In use, and the question goes.
      await use.click();
      await choose.waitFor({state: 'detached'});
      assert.deepEqual(state.puts, [{name: 'ticoteam/tico', body: {enabled: true}}]);

      // Several product repositories: each has its own button, none the main one.
      state.repositories = [repo('ticoteam/tico'), repo('ticoteam/site'), repo('ticoteam/bot-builder', {bot_repo: true})];
      await page.reload();
      await choose.waitFor();
      assert.equal(await choose.getByRole('button', {name: 'Use', exact: true}).count(), 2);
      assert.equal(await choose.locator('button.primary').count(), 0);

      // GitHub refused a create: the line says why the rest wait. All made: no line.
      state.app = {...ORG, bot_repositories: {made: 6, taken: 0, waiting: 15, problem: 'GitHub is limiting requests. Nothing is wrong here; try again in a few minutes.'}};
      await page.reload();
      await host.locator('[data-gh-bot-repos]').waitFor();
      assert.match(await words(host.locator('[data-gh-bot-repos]')), /6 of 21\. GitHub is limiting requests\./);
      state.app = {...ORG, bot_repositories: {made: 21, taken: 0, waiting: 0, problem: ''}};
      await page.reload();
      await host.locator('[data-gh-install]').waitFor();
      assert.equal(await host.locator('[data-gh-bot-repos]').count(), 0);

      // A second return from GitHub (Back to its Create page, or the same return twice): one sentence, on the card.
      await page.goto('https://tico-ui.test/#/library/tools/github?github=again');
      await page.reload();
      await host.locator('[data-gh-install]').waitFor();
      assert.equal(await words(host.locator('[data-gh-status]')), 'GitHub is already connected, so nothing changed. If GitHub made a second app just now, delete it there.');
      state.app = {connected: false};
      await page.goto('https://tico-ui.test/#/library/tools/github?github=expired');
      await page.reload();
      await page.locator('[data-gh-form]').waitFor();
      assert.equal(await words(page.locator('[data-gh-form] [data-gh-status]')), 'That GitHub link is used or too old. Connect again.');
      await page.close();
    }
    assert.deepEqual(errors, [], 'the page raised no errors');
    console.log('github-after-connect: repository choice, bot repository progress and second return ok');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
