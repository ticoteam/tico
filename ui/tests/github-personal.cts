// Tools > GitHub with a personal account (ui/github-connect.ts). Until the owner authorizes Tico for the account, the card
// says Let Tico create repositories, with one Authorize button, and names the callback URL only when the App was set up
// without it. Once authorized it says Tico can create them. Either way it names the waiting bot's repository and links to the App's
// settings and the uninstall page on github.com/settings. Without a connection, the form asks for one account or organization
// name. Desktop and phone, light and dark: TICO_UI_SHOTS=<dir> saves the card in each.
import type {Locator, LaunchOptions, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;

const SHOTS = process.env.TICO_UI_SHOTS || '';
const VIEWPORTS = {desktop: {width: 1440, height: 900}, phone: {width: 390, height: 844}};
const CALLBACK = 'https://tico.acme.example/api/v2/github/app/authorized';
const PERSONAL = {
  connected: true, slug: 'acme-tico', org: 'ana-dev', account_type: 'User', app_id: 4242, administration: true, installed: true,
  authorized: false, callback_url: CALLBACK,
  install_url: '/api/v2/github/app/install',
  settings_url: 'https://github.com/settings/apps/acme-tico',
  permissions_url: 'https://github.com/settings/apps/acme-tico/permissions',
  uninstall_url: 'https://github.com/settings/installations',
  create_url: 'https://github.com/new?owner=ana-dev&visibility=private',
  waiting: [{bot: 'newbie', repository: 'ana-dev/bot-newbie', task_id: 'task-1',
    create_url: 'https://github.com/new?owner=ana-dev&visibility=private&name=bot-newbie'}],
};
const AUTHORIZED = {...PERSONAL, authorized: true};
// What GET /api/v2/github/app answers: the App, or the not-connected answer.
type AppState = typeof PERSONAL | {connected: false};

// The Tico page's theme switch (ui/app/theme.ts), run in the page.
interface PageWin {ticoTheme: {set(value: string): void}}

// The routes the Tools page needs; the GitHub App's answer is read from `state.app` so one page can change it.
async function serve(page: Page, state: {app: AppState}) {
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

// The card in both themes, saved as <name>-dark.png and <name>-light.png when TICO_UI_SHOTS is set.
async function themed(page: Page, locator: Locator, name: string) {
  if (!SHOTS) return;
  for (const theme of ['dark', 'light']) {
    await page.evaluate((v: string) => (window as unknown as PageWin).ticoTheme.set(v), theme);
    await locator.screenshot({path: path.join(SHOTS, `${name}-${theme}.png`), animations: 'disabled'});
  }
  await page.evaluate(() => (window as unknown as PageWin).ticoTheme.set('dark'));
}

(async () => {
  if (SHOTS) fs.mkdirSync(SHOTS, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    const errors: string[] = [];
    for (const [tag, viewport] of Object.entries(VIEWPORTS)) {
      const page = await browser.newPage({viewport, serviceWorkers: 'block'});
      page.on('pageerror', (e: Error) => errors.push(e.message));
      const state: {app: AppState} = {app: PERSONAL};
      await serve(page, state);
      await page.goto('https://tico-ui.test/#/library/tools/github');
      const host = page.locator('#set-github'), card = page.locator('section:has(#set-github)');
      await host.locator('[data-gh-install]').waitFor();

      // Connected as a personal account, not yet authorized: one line asks for the authorization, with one button, and the
      // callback URL is named only when the App was set up without it.
      const text = await words(host);
      assert.match(text, /Connected to ana-dev through the app acme-tico\./);
      assert.match(text, /Installed on the account\./);
      assert.match(text, /Let Tico create repositories\./);
      assert.doesNotMatch(text, /signs in as you|Authorize it once/, 'no sign-in explanation on the card');
      assert.doesNotMatch(text, /New bots wait for you to create their repository on GitHub\./, 'the old wait-only line is gone');
      assert.match(text, /Waiting for a repository: newbie \(create ana-dev\/bot-newbie yourself\)\./);
      assert.doesNotMatch(text, /organi[sz]ation/i, 'a personal account is never called an organization');
      const authorize = host.locator('[data-gh-authorize]');
      assert.equal(await authorize.getByRole('button', {name: 'Authorize', exact: true}).count(), 1, 'one Authorize button');
      assert.equal(await authorize.locator('code').textContent(), CALLBACK, 'the callback hint, for an App set up before authorization');
      assert.equal(await host.locator('[data-gh-permissions], [data-gh-repo-fix]').count(), 0, 'no Administration fix when it is on');
      assert.equal(await host.locator('[data-gh-events]').getAttribute('href'), PERSONAL.permissions_url);
      assert.equal(await host.locator('[data-gh-create="0"]').getAttribute('href'), PERSONAL.waiting[0]!.create_url);
      assert.equal(await host.getByRole('link', {name: 'Uninstall on GitHub'}).getAttribute('href'), PERSONAL.uninstall_url);
      await themed(page, card, `github-personal-unauthorized-${tag}`);
      if (tag === 'phone') {
        const box = (await card.boundingBox())!;
        assert.ok(box.x + box.width <= viewport.width, 'the card fits a phone without a sideways scroll');
      }

      // An App set up with the callback in its manifest: the same line and button, and no callback hint.
      state.app = {...PERSONAL, callback_url: ''};
      await page.reload();
      await host.locator('[data-gh-install]').waitFor();
      assert.equal(await host.getByRole('button', {name: 'Authorize', exact: true}).count(), 1, 'the same one button');
      assert.equal(await host.locator('[data-gh-authorize] code').count(), 0, 'no callback hint once the App has it');

      // Not installed yet: the Install link is Tico's own route, which makes the state GitHub's install return must carry.
      state.app = {...PERSONAL, callback_url: '', installed: false};
      await page.reload();
      await host.locator('[data-gh-install]').waitFor();
      assert.equal(await host.locator('[data-gh-install-link]').getAttribute('href'), PERSONAL.install_url, 'the install link goes through Tico');

      // Authorized: the card says Tico can create them, with no Authorize button.
      state.app = AUTHORIZED;
      await page.reload();
      await host.locator('[data-gh-install]').waitFor();
      const done = await words(host);
      assert.match(done, /Tico can create bot repositories on ana-dev\./);
      assert.doesNotMatch(done, /Let Tico create repositories/, 'no authorize line once authorized');
      assert.equal(await host.getByRole('button', {name: 'Authorize', exact: true}).count(), 0, 'no Authorize button once authorized');
      assert.equal(await host.locator('[data-gh-authorize]').count(), 0);
      await themed(page, card, `github-personal-connected-${tag}`);

      // Not connected: one field for the account or organization, and the App name and Administration choice beside it.
      state.app = {connected: false};
      await page.reload();
      const form = page.locator('[data-gh-form]');
      await form.waitFor();
      const org = form.getByLabel('GitHub account or organization');
      assert.equal(await org.getAttribute('placeholder'), 'your-name or your-org');
      assert.equal(await form.getByLabel('Organization', {exact: true}).count(), 0, 'the old single-organization label is gone');
      assert.match(await words(form), /use a separate account or organization/);
      assert.equal(await form.getByLabel('App name').count(), 1);
      await themed(page, card, `github-personal-connect-${tag}`);
      await page.close();
    }
    assert.deepEqual(errors, [], 'the page raised no errors');
    console.log('github-personal: unauthorized, authorized and connect form ok');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
