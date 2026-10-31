// Build a team (ui/app/team-build.js): the dialog asks the server for the roles a briefing needs, shows them as cards,
// keeps what the person picked when the briefing changes, and hires the picks in one request. Run at a desktop and a
// phone size in light and dark. Fixtures only, no network. TB_SHOTS=<folder> also saves the screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const SHOTS = process.env.TB_SHOTS || '';
const group = {id: 'engineering', name: 'Engineering', parent: '', org_parent: '', order: 0};
const item = (template, name, match = '') => ({template, name, icon: '', department: 'engineering', line: 'Works on the team.',
  tags: [], owns: [], prerequisites: [], match, source: 'template'});
const cloud = {id: 'devin', name: 'Devin', maker: 'Cognition', site: 'devin.ai', line: 'A cloud agent.', supported: false,
  connect_id: null, source: 'cloud', template: '', icon: '', department: '', tags: [], owns: [], prerequisites: [], match: ''};
const suggestion = () => ({team: 'engineering', department: 'engineering', roles: [
  {role: 'Head of Engineering', pick: item('engineering-lead', 'Engineering Lead'), alternatives: [], on_team: true, checked: false,
    bot: {slug: 'ana-eng-lead', name: 'Rhea', icon: ''}},
  {role: 'Security engineer', pick: item('security-engineer', 'Security Engineer', 'strong'),
    alternatives: [item('software-architect', 'Software Architect', 'good'), item('pr-reviewer', 'PR Reviewer')], on_team: false, checked: true},
  {role: 'DevOps engineer', pick: item('devops-engineer', 'DevOps Engineer'), alternatives: [], on_team: false, checked: true},
  {role: 'Developer advocate', pick: item('developer-advocate', 'Developer Advocate'), alternatives: [item('docs-writer', 'Docs Writer')],
    on_team: false, checked: false},
  {role: 'Second pair of hands', pick: cloud, alternatives: [], on_team: false, checked: false},
]});
const until = async (test, ms = 4000) => {
  const end = Date.now() + ms;
  while (!test()) { if (Date.now() > end) throw new Error('timed out waiting'); await new Promise(r => setTimeout(r, 25)); }
};

async function scenario(browser, {name, viewport, theme}) {
  const tag = `${name}-${theme}`, phone = name === 'phone';
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(saved => localStorage.setItem('tico.theme', saved), theme);
  await page.addInitScript(() => localStorage.setItem('hub.collapsed.v3.ana', '[]'));
  const calls = [];
  let release = () => {}, suggests = 0;
  const gate = new Promise(resolve => { release = resolve; });     // holds the first answer so the loading state can be seen
  const shot = async suffix => { if (SHOTS) await page.screenshot({path: path.join(SHOTS, `team-build-${tag}${suffix}.png`)}); };
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana', org_parent: ''}], seats: [], org_groups: [group]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/team-build/suggest') {
      calls.push(['suggest', route.request().postDataJSON()]);
      if (++suggests === 1) await gate;
      return json(suggestion());
    }
    if (p === '/api/v2/team-build/hire') {
      calls.push(['hire', route.request().postDataJSON()]);
      return json({team: 'engineering', bots: [{slug: 'security-engineer', name: 'Security Engineer', template: 'security-engineer', role: 'Security engineer', stage: 'onboarding'}]});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks');
  await page.waitForFunction(() => typeof teamBuildOpen === 'function');
  await page.evaluate(() => teamBuildOpen('engineering'));
  const dialog = page.locator('#team-build-dialog'), cards = page.locator('#team-build-dialog .tb-card');
  await dialog.waitFor();
  // Focus starts on the brief on the desktop, and on the dialog on the phone so the keyboard stays down until a tap.
  const focused = await page.evaluate(() => document.activeElement.id === 'team-build-dialog' ? 'dialog'
    : document.activeElement.matches('.tb-brief input') ? 'brief' : 'other');
  assert.equal(focused, phone ? 'dialog' : 'brief');
  // While the answer is out, the header dot is working; the cards arrive after it.
  await until(() => calls.length === 1);
  assert.equal(await page.locator('#team-build-dialog .tb-dot').getAttribute('data-state'), 'thinking');
  assert.deepEqual(calls[0][1], {team: 'engineering', briefing: ''});
  release();
  await cards.nth(4).waitFor();
  assert.equal(await cards.count(), 5);
  assert.equal(await page.locator('#team-build-dialog .tb-dot').getAttribute('data-state'), 'done');
  // The cards fade in (transform and opacity) on the first answer.
  assert.equal(await cards.nth(0).evaluate(el => getComputedStyle(el).animationName), 'tb-in');
  // Layout: three columns on the desktop, one with a sticky footer on the phone, and no sideways scroll.
  const columns = await page.locator('#team-build-dialog [data-tb-grid]').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
  assert.equal(columns, phone ? 1 : 3);
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'the dialog does not scroll sideways');
  if (phone) assert.equal(await page.locator('#team-build-dialog .tb-foot').evaluate(el => getComputedStyle(el).position), 'sticky');
  // The header dot keeps its round shape in the flex header (390px and desktop alike). Measured once the done hop has
  // settled, on the visible blob, so the transform is counted.
  await page.waitForTimeout(1000);
  const dotRatio = await page.locator('#team-build-dialog .tb-dot .body').evaluate(el => {
    const box = el.getBoundingClientRect();
    return box.width / box.height;
  });
  assert.ok(dotRatio >= 0.8 && dotRatio <= 1.25, `the dot is ${dotRatio.toFixed(2)} times as wide as tall`);
  await page.waitForTimeout(400);
  await shot('');
  // A team bot already in the team is greyed and has no checkbox to take; a cloud agent is not picked (it cannot be hired here yet).
  assert.equal(await page.locator('#team-build-dialog .tb-card.is-team').count(), 1);
  assert.equal(await page.locator('#team-build-dialog .tb-card.is-team .tb-pick-name').innerText(), 'Rhea');
  assert.ok(await page.locator('#team-build-dialog .tb-card.is-team .tb-check').isDisabled());
  assert.equal(await page.locator('#team-build-dialog .tb-card.is-team .tb-change').count(), 0);
  assert.ok(await page.locator('#team-build-dialog [data-card="4"] .tb-check').isDisabled());
  assert.equal(await page.locator('#team-build-dialog [data-card="4"] [data-tb-request]').count(), 1);
  const count = () => page.locator('#team-build-dialog .tb-count').innerText().then(t => parseInt(t, 10));
  const hireButton = page.locator('#team-build-dialog .tb-hire');
  assert.equal(await count(), 2);                                                    // the two checked defaults
  assert.equal(await hireButton.isDisabled(), false);
  // Request hands the cloud agent to the Cloud agents page with this team, so its seat lands here.
  await page.evaluate(() => { window.cloudAgentRequest = (pick, options) => { window.__cloudRequest = {name: pick.name, team: options.team}; }; });
  await page.locator('#team-build-dialog [data-card="4"] [data-tb-request]').click();
  assert.deepEqual(await page.evaluate(() => window.__cloudRequest), {name: 'Devin', team: 'engineering'});
  // Change opens that role's alternatives; picking one swaps it in and picks the role.
  await page.locator('#team-build-dialog [data-card="1"] [data-tb-change]').click();
  assert.equal(await page.locator('#team-build-dialog [data-card="1"] [data-tb-change]').getAttribute('aria-expanded'), 'true');
  assert.ok(await page.locator('#tb-alts-1').isVisible());
  await shot('-change');
  await page.locator('#team-build-dialog [data-tb-alt="1:0"]').click();
  assert.equal(await page.locator('#team-build-dialog [data-card="1"] .tb-pick-name').innerText(), 'Software Architect');
  assert.equal(await page.locator('#team-build-dialog [data-card="1"] .tb-alt').count(), 2);
  assert.equal(await count(), 2);
  assert.equal(await cards.count(), 5);                                              // replaced in place, not added
  await page.locator('#team-build-dialog [data-tb-check="3"]').click();
  assert.equal(await count(), 3);
  // A new briefing goes to the server once, after a pause, and keeps what the person picked.
  await page.locator('#team-build-dialog .tb-brief input').fill('A Rust API and a React app on AWS');
  await until(() => calls.filter(c => c[0] === 'suggest').length === 2);
  assert.equal(calls.filter(c => c[0] === 'suggest').at(-1)[1].briefing, 'A Rust API and a React app on AWS');
  await page.waitForFunction(() => document.querySelector('#team-build-dialog .tb-dot')?.dataset.state === 'done');
  assert.equal(await page.locator('#team-build-dialog [data-card="1"] .tb-pick-name').innerText(), 'Software Architect');
  assert.equal(await count(), 3);
  // Hire sends each checked role with its chosen template, in order; the dialog closes and a toast says it.
  await hireButton.click();
  await until(() => calls.at(-1)[0] === 'hire');
  assert.deepEqual(calls.at(-1)[1], {team: 'engineering', reports_to: '', picks: [
    {role: 'Security engineer', template: 'software-architect'},
    {role: 'DevOps engineer', template: 'devops-engineer'},
    {role: 'Developer advocate', template: 'developer-advocate'},
  ]});
  await dialog.waitFor({state: 'detached'});
  assert.ok(await page.locator('.toast').count() >= 1);
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  if (SHOTS) fs.mkdirSync(SHOTS, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const run of [
      {name: 'desktop', viewport: {width: 1300, height: 900}, theme: 'dark'},
      {name: 'desktop', viewport: {width: 1300, height: 900}, theme: 'light'},
      {name: 'phone', viewport: {width: 390, height: 844}, theme: 'light'},
      {name: 'phone', viewport: {width: 390, height: 844}, theme: 'dark'},
    ]) await scenario(browser, run);
    console.log('team-build: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
