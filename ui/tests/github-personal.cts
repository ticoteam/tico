// Tools > GitHub with a personal account (ui/github-connect.ts). Until the owner authorizes Tico for the account, the card
// says Let Tico create repositories, with one Authorize button, and names the callback URL only when the App was set up
// without it. Once authorized it says Tico can create them. Either way it names the waiting bot's repository and links to the App's
// settings and the uninstall page on github.com/settings. Without a connection, the form starts on My GitHub account with
// nothing to type, and asks for a name only when An organization is chosen (task #255). Desktop and phone, light and dark: TICO_UI_SHOTS=<dir> saves the card in each.
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
type AppState = typeof PERSONAL | {connected: false; app_name: string};
// What the form asked of GET /api/v2/github/app/manifest, and where it then sent the browser.
interface Asked {queries: URLSearchParams[]; github: string[]}
const MANIFEST = {name: 'Acme Tico', url: 'https://tico.acme.example'};

// The Tico page's theme switch (ui/app/theme.ts), run in the page.
interface PageWin {ticoTheme: {set(value: string): void}}

// The routes the Tools page needs; the GitHub App's answer is read from `state.app` so one page can change it.
async function serve(page: Page, state: {app: AppState}, asked: Asked = {queries: [], github: []}) {
  await page.route('**/*', (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.hostname === 'github.com') {
      asked.github.push(`${req.method()} ${url.origin}${p}`);
      return route.fulfill({contentType: 'text/html', body: '<title>GitHub</title>'});
    }
    if (p === '/api/v2/github/app/manifest') {
      asked.queries.push(url.searchParams);
      const org = url.searchParams.get('org');
      if (org === 'nobody-here') return json({error: {code: 'github_account_missing', detail: 'No GitHub account or organization named nobody-here'}}, 404);
      const where = org ? `organizations/${org}/settings` : 'settings';
      return json({action: `https://github.com/${where}/apps/new?state=s1`, manifest: MANIFEST, state: 's1'});
    }
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

// Not connected (task #255): the form opens on My GitHub account with no field to fill, and Connect GitHub goes straight
// to GitHub's personal-account page. An organization asks for its name only once chosen.
async function connectForm(viewport: {width: number; height: number}, tag: string) {
  const page = await browser().newPage({viewport, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  const asked: Asked = {queries: [], github: []};
  await serve(page, {app: {connected: false, app_name: 'Acme Tico'}}, asked);
  await page.goto('https://tico-ui.test/#/library/tools/github');
  const card = page.locator('section:has(#set-github)'), form = page.locator('[data-gh-form]');
  await form.waitFor();
  const choice = form.getByRole('radiogroup', {name: 'Create the app in'});
  const mine = choice.getByRole('radio', {name: 'My GitHub account'}), theirs = choice.getByRole('radio', {name: 'An organization'});
  // AC-1: My GitHub account is chosen, nothing to type, no App name, and Connect GitHub is enabled.
  assert.equal(await mine.getAttribute('aria-checked'), 'true');
  assert.equal(await theirs.getAttribute('aria-checked'), 'false');
  assert.equal(await form.locator('input[type=text]:visible').count(), 0, 'no text field to fill');
  assert.equal(await form.getByLabel('App name').count(), 0, 'no App name field');
  assert.equal(await form.getByLabel('GitHub account or organization').count(), 0, 'the old account field is gone');
  assert.ok(await form.getByRole('button', {name: 'Connect GitHub'}).isEnabled());
  assert.match(await words(form), /use a separate account or organization/);
  // AC-7: the name GitHub will propose, and that it can be changed there.
  assert.equal(await words(form.locator('[data-gh-name]')), 'GitHub will name the app "Acme Tico". You can rename it on GitHub\'s page.');
  await themed(page, card, `github-connect-personal-${tag}`);

  // AC-4: An organization with no name stays in Tico and says what is missing.
  await theirs.click();
  assert.equal(await theirs.getAttribute('aria-checked'), 'true');
  const org = form.getByLabel('Organization name');
  assert.ok(await org.isVisible(), 'the organization name field shows once chosen');
  await form.getByRole('button', {name: 'Connect GitHub'}).click();
  assert.equal(await words(form.locator('[data-gh-status]')), 'Enter the organization\'s name');
  assert.equal(asked.queries.length, 0, 'nothing asked of the server');
  // AC-9: neither choice scrolls sideways on a phone.
  if (tag === 'phone') {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 0, `no sideways scroll with An organization chosen (${overflow}px)`);
  }
  await themed(page, card, `github-connect-organization-${tag}`);

  // AC-6: a name GitHub does not know keeps today's message, and the page stays.
  await org.fill('nobody-here');
  await form.getByRole('button', {name: 'Connect GitHub'}).click();
  await form.locator('[data-gh-status]', {hasText: 'No GitHub account or organization named nobody-here'}).waitFor();
  assert.equal(asked.github.length, 0, 'still in Tico');

  // AC-5: an organization's name goes into GitHub's organization address.
  await org.fill('acme-co');
  await Promise.all([page.waitForURL(/github\.com/), form.getByRole('button', {name: 'Connect GitHub'}).click()]);
  assert.equal(asked.queries.at(-1)!.get('org'), 'acme-co');
  assert.deepEqual(asked.github, ['POST https://github.com/organizations/acme-co/settings/apps/new']);

  // AC-2: back on Tools, My GitHub account sends no account at all and goes to GitHub's personal-account page.
  await page.goto('https://tico-ui.test/#/library/tools/github');
  await form.waitFor();
  assert.equal(await theirs.getAttribute('aria-checked'), 'false', 'a fresh form starts on My GitHub account again');
  if (tag === 'phone') {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 0, `no sideways scroll with My GitHub account chosen (${overflow}px)`);
  }
  await Promise.all([page.waitForURL(/github\.com/), form.getByRole('button', {name: 'Connect GitHub'}).click()]);
  const personal = asked.queries.at(-1)!;
  assert.equal(personal.has('org'), false, 'no account is sent for a personal account');
  assert.equal(personal.get('administration'), 'true');
  assert.deepEqual(asked.github.at(-1), 'POST https://github.com/settings/apps/new');

  // A team with no name yet: no made-up app name is offered.
  await serve(page, {app: {connected: false, app_name: ''}}, asked);
  await page.goto('https://tico-ui.test/#/library/tools/github');
  await form.waitFor();
  assert.equal(await words(form.locator('[data-gh-name]')), 'Pick the app\'s name on GitHub\'s page.');
  await page.close();
  assert.deepEqual(errors, [], 'the connect form raised no errors');
}

let shared: import('playwright').Browser | null = null;
const browser = () => shared!;

(async () => {
  if (SHOTS) fs.mkdirSync(SHOTS, {recursive: true});
  const browser = shared = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
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

      await page.close();
      await connectForm(viewport, tag);
    }
    assert.deepEqual(errors, [], 'the page raised no errors');
    console.log('github-personal: unauthorized, authorized, and connect form for an account and an organization ok');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
