// Offline browser regression for first run: a company whose config says onboarding_needed lands on #/welcome, which
// is a modal over the app (the sidebar and pages stay behind it, whole) with "Set up <company>", "Step N of 3" and one
// question per step. What are you building saves a draft (PUT /api/v2/setup) and starts the strategy draft; Your team
// lays out the default team by group, each group a switch, and Create team posts /api/v2/setup/complete exactly once.
// Change opens the org builder: departments, one question each, "Recruiting bots…", the suggested bots and the chart
// that grows; the finished chart can rename or re-point a bot. What your bots run on comes after Create: the computer
// in one line, the subscription line, and Done lands on Tasks with no tour. Then the paths with a company file: one
// imported from the team step, and one that failed on server start (its reason in one line, the default team picked).
// The bot page's onboarding banner, Settings -> Bots' catalog cards and a phone's full-screen setup round it out.
// Fixtures only - no server, no network.
import type {Locator, Page} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as ThreadsFake from './support/threads-fake.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFake.Api;
const {t}: {t: (ms: number) => number} = (() => { try { return require('./support/load.cts'); } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
// Shapes of the fixtures and of the page's own globals (declared, not defined: the page supplies them).
type Config = {environment_id: string; company_name: string; app_name: string; assistant_name: string; assistant_bot: string;
  public_url: string; runner_url: string; github_owner: string; local: boolean; release: string; onboarding_needed: boolean;
  version: string; runner_compat: {version: string; min_runner: string}; providers_configured?: boolean; company_name_default?: boolean;
  in_docker?: boolean; compose_project?: string; server_network?: string};
type Me = {id: string; name?: string; role?: string; company_domains?: string[]; sign_in_name?: string; [key: string]: unknown};
type Machine = {runners: {id: string; label: string; online: boolean}[]; enrolled: boolean};
type Card = {template: string; slug: string; name: string; required: boolean; bootstrap: boolean; department?: string; icon?: string;
  suggest?: string; summary: string; tags?: string[]; owns: string[]; never: string[]; runtime: string; model: string;
  reasoning_effort: string; recommend_when: string[]; instructions: string; lead?: boolean; default?: boolean; when?: string;
  starter?: boolean; pack?: string; kind?: string; pains?: string[]; prerequisites?: {tool: string; why: string; required: boolean}[];
  first_routine?: {title: string; cadence: string}};
type Dept = [string, string, string, string, string, string, string];
type Selected = {display_name?: string; reports_to?: string; instructions?: string; [key: string]: unknown};
type Put = {names: Record<string, string>; answers: Record<string, unknown>; selected: Record<string, Selected>};
type BotRow = {id: string; slug: string; display_name: string; status: string; template: string; onboarding_state: string;
  setup_task_id: string | null; repository_present: boolean};
type Setup = {names: Record<string, string>; answers: Record<string, unknown>; selected: Record<string, unknown>;
  completed: string | null; bots: unknown[]; machine: Machine; needed: boolean; default_chart?: typeof DEFAULT_CHART;
  company_import_failed?: {at: string; reason: string} | null};
type Created = {slug: string; template: string; display_name: string; instructions: string; model: string; effort: string; owners: string[]};
type Opened = {conversation?: unknown; messages?: ThreadsFake.ThreadsMessage[]; home?: ThreadsFake.ThreadsMessage[]};
// Page globals the in-page code reads, and the helpers the page defines.
declare const S: {me: Me; config: Config};
declare const ONB: Record<string, unknown>;
declare function refresh(force: boolean): void;
declare function onbOwnerGuess(): string;
declare function onbAsksDomain(): boolean;
declare function onbDefaultKind(): string;
declare function onbComputerHTML(state: Record<string, unknown>): string;
declare function onbCommands(state: Record<string, unknown>): [string, string, boolean?][];
declare function onbMachineLabel(machine: {kind: string}): string;
declare function dockerRunnerCommands(code: string, label: string, runtime: string): [string[], string[], string[]];
interface PageWin { copied: string[]; ticoTheme: {set(theme: string): void} }
// TICO_SCREENSHOT_DIR=<dir> saves screenshots of the first-run screens.
// TICO_SHOT_SCHEME=dark takes them in the dark theme.
const shots = process.env.TICO_SCREENSHOT_DIR, scheme = process.env.TICO_SHOT_SCHEME === 'dark' ? 'dark' : 'light';
// The step's title is well above body text.
const bigTitle = (page: Page) => page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.onb-step-title')!).fontSize)
  >= 1.25 * parseFloat(getComputedStyle(document.querySelector('#onb-step')!).fontSize));
const count = (page: Page) => page.locator('#onb-count').textContent();
// Every finite animation ended first, so a screen that animates in is photographed at rest.
const shot = async (page: Page, name: string) => { if (!shots) return; await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity), null, {timeout: t(5000)}); await page.screenshot({path: path.join(shots, `${name}-${scheme}.png`)}); };

const CONFIG: Config = {environment_id: 'initech', company_name: 'Initech', app_name: 'Initech Hub',
  assistant_name: 'Ace', assistant_bot: 'coo', public_url: 'https://initech.test',
  runner_url: 'https://initech.test', github_owner: 'initech-inc', local: false, release: '',
  onboarding_needed: true, version: '0.2.0', runner_compat: {version: '0.2.0', min_runner: '0.1.0'}};

// One card of the org builder's catalog, in the shape the server renders it.
function bot(template: string, name: string, department: string, icon: string, suggest: string, summary: string, extra: Partial<Card> = {}): Card {
  return {template, slug: template, name, required: false, bootstrap: false, department, icon, suggest, summary, tags: [],
          owns: ['its own work'], never: ['sends anything on its own'], runtime: 'codex', model: 'gpt-6-sol',
          reasoning_effort: 'medium', recommend_when: [], instructions: `# ${name}\n\nDraft, never send.\n`, ...extra};
}
const CATALOG: Card[] = [
  // The server renders every card with the company's names, so the assistant's reads as Ace.
  {template: 'assistant', slug: 'coo', name: 'Ace', required: true, default: true, bootstrap: true,
   when: 'Recommended if you use Slack or send meetings to bots: it routes work nobody was named for.',
   summary: 'Your point of contact. Answers from what the other bots wrote down.',
   owns: ['the chat thread', 'routing questions'], never: ['sends mail on its own'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'xhigh', recommend_when: [],
   instructions: '# Assistant\n\nYou are the point of contact for Initech.\n'},
  {template: 'botops', slug: 'botops', name: 'BotOps', required: true, bootstrap: true,
   summary: 'Builds and repairs the other bots.',
   owns: ['bot repositories', 'setup tasks'], never: ['spends money'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'high', recommend_when: [],
   instructions: '# BotOps\n\nYou set up every other bot.\n'},
  // The org builder's cards, named as the real catalog names them: each with a department, a Material Symbols icon and
  // how it is suggested (the department head, `default` pre-checked, `common` shown, `niche` under More until an answer names it).
  bot('sales-lead', 'Sales Manager', 'sales', 'leaderboard', 'default', 'Runs the pipeline review and coaches the team.', {lead: true}),
  bot('sdr-research', 'Sales Development Representative', 'sales', 'person_search', 'default', 'Researches accounts and writes first-touch emails.', {tags: ['outbound', 'cold email']}),
  bot('account-manager', 'Account Manager', 'sales', 'manage_accounts', 'common', 'Keeps existing accounts growing and renewing.', {tags: ['renewals']}),
  bot('partnerships', 'Partnerships Manager', 'sales', 'partner_exchange', 'niche', 'Finds resellers and keeps them selling.', {tags: ['resellers', 'partners']}),
  bot('marketing-lead', 'Head of Marketing', 'marketing', 'campaign', 'default', 'Owns the plan, the calendar and the numbers.', {lead: true}),
  bot('content', 'Content Marketer', 'marketing', 'edit_note', 'default', 'Turns what you know into posts and guides.', {tags: ['linkedin', 'content']}),
  bot('listening', 'Social Media Manager', 'marketing', 'tag', 'common', 'Drafts posts and watches mentions.'),
  bot('support-lead', 'Head of Customer Support', 'support', 'headset_mic', 'default', 'Keeps the queue healthy and the answers consistent.', {lead: true}),
  {template: 'support', slug: 'support', name: 'Support Agent', required: false, bootstrap: false, starter: true, pack: 'support',
   department: 'support', icon: 'support_agent', suggest: 'default', tags: ['inbox', 'tickets'],
   pains: ['support inbox is overflowing', 'customers wait too long for an answer'],
   prerequisites: [{tool: 'mail', why: 'Support mail is the intake.', required: true}, {tool: 'docs', why: 'The help centre.', required: false}],
   first_routine: {title: 'Daily support triage', cadence: 'Weekdays at 09:00'},
   summary: 'Answers customers and keeps the queue short.',
   owns: ['the ticket queue'], never: ['refunds without a person'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'high',
   recommend_when: ['has_support_inbox', 'uses_tickets', 'sells_to_consumers', 'uses_email'],
   instructions: '# Support Agent\n\nAnswer the queue in the company voice.\n'},
  bot('returns', 'Returns Specialist', 'support', 'assignment_return', 'niche', 'Checks each return against the written policy.'),
  bot('finance-lead', 'Head of Finance', 'finance', 'account_balance_wallet', 'default', 'Keeps cash visible and the month closed.', {lead: true}),
  {template: 'bookkeeping', slug: 'bookkeeping', name: 'Bookkeeper', required: false, bootstrap: false, pack: 'basics',
   department: 'finance', icon: 'menu_book', suggest: 'default',
   summary: 'Chases invoices and reconciles the ledger.',
   owns: ['the invoice ledger'], never: ['pays anything without a person'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'high', recommend_when: ['sells_to_businesses', 'has_pipeline'],
   instructions: '# Bookkeeper\n\nChase, never pay.\n'},
  bot('ops-manager', 'Operations Manager', 'operations', 'assignment_turned_in', 'default', 'Runs the weekly operating rhythm.', {lead: true}),
  {template: 'meeting-notes', slug: 'meeting-notes', name: 'Project Coordinator', required: false, bootstrap: false, starter: true,
   pack: 'operations', department: 'operations', icon: 'event_note', suggest: 'common',
   pains: ['meetings end without owners'], prerequisites: [{tool: 'meetings', why: 'Meeting notes.', required: true}],
   first_routine: {title: 'Weekly project check-in', cadence: 'Fridays at 15:00'},
   summary: 'Turns meetings into owners, dates and a weekly check-in. Internal only.',
   owns: ['the project check-in'], never: ['messages anyone outside the company'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'medium', recommend_when: ['uses_meetings'],
   instructions: '# Project Coordinator\n\nFollow up every meeting.\n'},
  // A message bot: it serves one person, so it is in no department and is offered on its own, off by default.
  {template: 'inbox-manager', slug: 'inbox', name: 'Inbox Manager', kind: 'helper', required: false, bootstrap: false, starter: true,
   icon: 'inbox', pains: ['too much email'], prerequisites: [{tool: 'mail', why: 'A mailbox to read.', required: true}],
   first_routine: {title: 'Morning mail brief', cadence: 'Weekdays at 07:30'},
   summary: 'Reads one person mailbox on a cadence.',
   owns: ['the inbox pass'], never: ['sends mail on its own'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'medium', recommend_when: ['has_personal_inbox'],
   instructions: '# Inbox Manager\n\nRead, never send.\n'},
  bot('general-counsel', 'General Counsel', 'legal', 'balance', 'default', 'Reads contracts and flags the risky clauses.', {lead: true}),
  bot('people-lead', 'Head of People', 'hr', 'supervisor_account', 'default', 'Hires, onboards and answers policy questions.', {lead: true}),
  bot('product-lead', 'Head of Product', 'product', 'lightbulb', 'default', 'Turns feedback into a ranked roadmap.', {lead: true}),
  bot('engineering-lead', 'Head of Engineering', 'engineering', 'developer_board', 'default', 'Plans the sprint and keeps releases moving.', {lead: true}),
  {template: 'issue-triage', slug: 'issue-triage', name: 'QA Engineer', required: false, bootstrap: false, starter: true, pack: 'engineering',
   department: 'engineering', icon: 'bug_report', suggest: 'default', tags: ['github', 'issues'],
   pains: ['issues pile up untriaged'], prerequisites: [{tool: 'github', why: 'The issues.', required: true}],
   first_routine: {title: 'Daily triage', cadence: 'Weekdays at 08:00'},
   summary: 'Labels and routes new issues.',
   owns: ['issue labels'], never: ['closes issues without a person'],
   runtime: 'codex', model: 'gpt-6-sol', reasoning_effort: 'medium', recommend_when: ['uses_github'],
   instructions: '# QA Engineer\n\nLabel, never close.\n'},
  bot('pr-reviewer', 'Senior Software Engineer', 'engineering', 'code_blocks', 'common', 'Reviews every pull request before a person does.'),
];

// The default team (GET /api/v2/setup `default_chart`, backend/company_import.py default_teams): each group, on or off,
// and the roles in it that start working at once.
// The chart's shape, not its names (the server's chart formula owns those): a top seat with reports, one of them
// still to be recruited.
const DEFAULT_CHART = [
  {team: 'Product', on: true, roles: [{id: 'p1', title: 'Alpha Lead', live: true, reports_to: null},
    {id: 'p2', title: 'Alpha Owner', live: true, reports_to: 'p1'}]},
  {team: 'Engineering', on: true, roles: [{id: 'e1', title: 'Beta Lead', live: true, reports_to: null},
    {id: 'e2', title: 'Beta Maker', live: true, reports_to: 'e1'}, {id: 'e3', title: 'Beta Checker', live: true, reports_to: 'e2'}]},
  {team: 'Growth', on: true, roles: [{id: 'g1', title: 'Gamma Lead', live: true, reports_to: null},
    {id: 'g2', title: 'Gamma Analyst', live: false, reports_to: 'g1'}]},
];
// What are you building, left empty (so no research): Next, to Your team.
async function pastBuilding(page: Page) {
  await page.locator('#onb-website').waitFor();
  await page.locator('#onb-next').click();
  await page.locator('.onb-step-title', {hasText: 'Your team'}).waitFor();
}

// What Create leaves: the two starters are parked, one with its repository, one still being set up; the Content
// Marketer is not a starter, so BotOps builds it. A new bot's id is not its slug (the Content Marketer's here).
const CONTENT_ID = '01990000-0000-7000-8000-00000000c0de';
const BOTS_AFTER: BotRow[] = [
  {slug: 'coo', display_name: 'Ace', status: 'active', template: 'assistant', onboarding_state: '',
   setup_task_id: null, repository_present: false},
  {slug: 'botops', display_name: 'BotOps', status: 'active', template: 'botops', onboarding_state: '',
   setup_task_id: null, repository_present: true},
  {slug: 'support', display_name: 'Support desk', status: 'active', template: 'support', onboarding_state: 'needs_setup',
   setup_task_id: null, repository_present: true},
  {slug: 'meeting-notes', display_name: 'Project Coordinator', status: 'planned', template: 'meeting-notes', onboarding_state: 'needs_setup',
   setup_task_id: null, repository_present: false},
  {slug: 'content', display_name: 'Content Marketer', status: 'planned', template: 'content', onboarding_state: '',
   setup_task_id: 'task-content', repository_present: false},
].map((bot): BotRow => ({id: bot.slug === 'content' ? CONTENT_ID : bot.slug, ...bot}));
// The chart Create leaves: Operations before Customer Support, not the order the bots were created in.
const CHART_GROUPS = [{id: 'support', name: 'Customer Support', parent: '', order: 2}, {id: 'operations', name: 'Operations', parent: '', order: 1}];
const CHART_TEAMS: Record<string, string> = {support: 'support', 'meeting-notes': 'operations'};
// The org builder's departments (templates/departments.yaml) and cards (GET /api/v2/setup/groups).
const DEPT_ROWS: Dept[] = [
  ["sales", "Sales", "handshake", "Finds buyers, works every deal to a signature and keeps existing accounts renewing and growing.", "Turn interest into revenue", "What kind of sales do you do today?", "Outbound to studio owners, a two-call demo, annual contracts; two sellers and a spreadsheet pipeline"],
  ["marketing", "Marketing", "campaign", "Makes the right people aware of the company and gives sales a steady flow of interested buyers.", "Be known by the customers you want", "How do customers find you today?", "Mostly word of mouth and search; a monthly newsletter, some paid social, two trade shows a year"],
  ["support", "Customer Support", "support_agent", "Answers customers quickly and correctly, and keeps them successful after they buy.", "Every customer helped and kept", "How do customers reach you for help today, and what do they ask most?", "A shared support inbox and chat on the site; mostly billing questions, setup help and a few bug reports"],
  ["finance", "Finance", "account_balance", "Keeps the books current, cash visible and bills paid and collected, with every number ready for a person to check.", "Know where the money is", "How do you keep the books and handle bills and invoices today?", "Online accounting software and an outside accountant; we invoice monthly and chase late payers by hand"],
  ["operations", "Operations", "settings_suggest", "Keeps the company running: vendors, purchases, projects, IT, the office and the checklists nobody should forget.", "Run smoothly, nothing dropped", "What keeps the company running day to day that someone has to remember?", "Vendor renewals, buying laptops, the office lease, weekly project check-ins and our field crew schedule"],
  ["legal", "Legal", "gavel", "Reviews contracts, keeps the company's filings and policies current and flags risk early, as summaries for a person and never legal advice.", "Sign with eyes open", "What legal work comes up for you today, and who handles it?", "Customer contracts and NDAs every week, a privacy question now and then; an outside lawyer for anything big"],
  ["hr", "HR", "groups", "Hires the right people, brings them in well and looks after them while they are here.", "Hire well, keep good people", "How do you hire and look after your people today?", "Hiring three engineers this quarter; job posts on our site, a shared handbook, reviews twice a year"],
  ["product", "Product", "category", "Decides what to build next from what customers say and do, and writes it down clearly enough to build.", "Build what customers need", "How do you decide what to build next?", "Feature requests from support and sales, a roadmap in a doc, the founders decide each quarter"],
  ["engineering", "Engineering", "code", "Builds, reviews, ships and runs the product's software, with every change reviewed and every incident learned from.", "Ship reliable software, steadily", "How does your team build and ship software today?", "Six engineers on GitHub, pull requests reviewed by one other person, weekly releases, alerts in Slack"],
];
const DEPARTMENTS = DEPT_ROWS.map(([id, name, icon, description, goal, question, placeholder]) => ({id, name, icon, description, goal, question, placeholder,
  head: CATALOG.find(card => card.department === id && card.lead)?.template || '', software_only: ['product', 'engineering'].includes(id)}));
const deptCards = (id: string) => CATALOG.filter(card => card.department === id);
// POST /api/v2/setup/recruit, as the server's local recommender would answer (backend/recruit_rank.py, tested there):
// the head, then the defaults and commons, and a niche card only when the answer names one of its tags. Only a tag of
// two or more words is a why; a niche card brought in by one word has none.
function recruitFor({department, briefing, share}: {department: string; briefing?: string; share?: boolean}) {
  const dept = DEPARTMENTS.find(row => row.id === department)!, words = String(briefing || '').toLowerCase();
  const rows = [];
  for (const card of deptCards(department).sort((a, b) => (b.lead ? 1 : 0) - (a.lead ? 1 : 0))) {
    const tag = (card.tags || []).find(word => words.includes(word));
    if (card.suggest === 'niche' && !tag) continue;
    rows.push({template_id: card.template, why: card.lead ? `Heads ${dept.name} and reports to you` : tag?.includes(' ') ? `Matches “${tag}”`
      : card.suggest === 'default' ? `A starting point for ${dept.name}` : card.suggest === 'common' ? `Common in ${dept.name}` : ''});
  }
  return {bots: rows, suggested_default: deptCards(department).filter(card => card.lead).map(card => card.template),
          source: share ? 'hq' : 'local', shared: !!share, off_by: ''};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    const context = await browser.newContext({viewport: {width: 1100, height: 800}, serviceWorkers: 'block', acceptDownloads: true, colorScheme: scheme});
    // The app is dark unless the browser saved another theme (ui/theme.ts): screenshots take the one asked for.
    if (shots) await context.addInitScript(theme => localStorage.setItem('tico.theme', theme), scheme);
    const puts: Put[] = [], tourPosts: unknown[] = [], completes: unknown[] = [], definitions: {path: string; body: unknown}[] = [], created: Created[] = [], enrollments: unknown[] = [], chats: [string, unknown][] = [], invites: [string, unknown][] = [], owners: [string, unknown][] = [];
    let refuseSetup = false;   // the next PUT /api/v2/setup is refused once (set on the team chart)
    const recruits: Record<string, unknown>[] = [], opened: Record<string, Opened | undefined> = {};
    // Each bot's DM reads what Set up wrote to it (the chat the first message opened).
    const dms = ['support', 'meeting-notes'].map(bot => threadsFake({bot, messages: () => opened[bot]?.messages || [], home: () => opened[bot]?.home || []}));
    let hq = {available: true, off_by: ''}, recruitHeld: Promise<unknown> | null = null, releaseRecruit: (value?: unknown) => void = () => {};   // a recruit answer the test holds
    let record: Setup = {
      names: {company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace'},
      answers: {what_we_do: '', customers: '', team_size: '', work_arrives: [], software_product: '',
                repetitive_work: '', never_without_person: ['send', 'spend', 'publish', 'hire'], pains: [], pains_text: '', tools: []},
      selected: {}, completed: null, bots: [],
      machine: {runners: [], enrolled: false}, needed: true, default_chart: DEFAULT_CHART.map(team => ({...team})),
    };
    const drafts: unknown[] = [], previews: unknown[] = [], imports: unknown[] = [];
    let subscription = {id: 'subscription', done: false, skipped: false};
    let machine: Machine = {runners: [], enrolled: false};
    let enrollCode = 'enroll-code';
    const me = {id: 'ana', name: 'Ana Rivera', role: 'owner', cloud: true,
                email: 'ana@acme.example', credential_access: false, config: CONFIG};
    const v2bots = () => BOTS_AFTER.map((bot, i) => ({id: bot.id, slug: bot.slug, display_name: bot.display_name, state: bot.status,
      status: bot.status, revision: i + 1, team: null, owner_id: 'ana', repo: 'emp-' + bot.slug, onboarding_state: bot.onboarding_state}));

    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      // The page's own modules come from disk: the page shares state with them (docs-page.ts
      // declares DOC_POLL, which route() clears on every navigation), so an empty stub would
      // break every route. Everything else that ends in .js still stubs out.
      const module = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (module) {
        const file = uiFile(module[1]!);
        if (fs.existsSync(file)) return route.fulfill({contentType: module[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')});
      }
      // The icon font, so the screenshots show icons and not their names.
      if (p.startsWith('/vendor/')) {
        const file = path.join(__dirname, '..', p);
        if (fs.existsSync(file)) return route.fulfill({contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript', body: fs.readFileSync(file)});
      }
      // Polls must report the setup state that the completion endpoint already saved.
      // A new install has product updates unread until Create marks them read for the owner, as the server does.
      const config = {...CONFIG, ...record.names, onboarding_needed: me.role === 'owner' && record.needed,
                      changelog: {unread_count: record.completed ? 0 : 105}};
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/providers') return json({configured: true, preset: 'subscriptions-gateway', enabled: ['anthropic', 'openai'], providers: []});
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], recent_runs: []});
      if (p === '/api/employees') return json([{name: 'coo', display_name: 'Tico', status: 'active',
        reports_to: '', description: 'the point of contact', revision: 1, users: [{id: 'ana', name: 'Ana Rivera', email: 'ana@acme.example'}]},
        // After Create the two starters are on the chart, parked, each in its group (the Content Marketer is not on it yet).
        ...(record.completed ? BOTS_AFTER.filter(bot => bot.onboarding_state).map(bot => ({id: bot.id, slug: bot.slug, name: bot.slug, display_name: bot.display_name,
          status: bot.status, reports_to: '', description: '', revision: 5, can_chat: true, can_manage: true, onboarding_state: bot.onboarding_state,
          host: 'keeper', thread_mode: 'personal', users: [], team: CHART_TEAMS[bot.slug]})) : [])]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/templates') return json({cards: CATALOG});
      if (p === '/api/v2/setup') {
        if (request.method() === 'PUT') {
          // The server refuses a save for its shape once, as it refused BotOps's 24,048-character instructions.
          if (refuseSetup) {
            refuseSetup = false;
            return json({error: {code: 'validation', retryable: false,
                                 detail: 'body.selected.botops.instructions: String should have at most 20000 characters'}}, 422);
          }
          const body = request.postDataJSON();
          puts.push(body);
          record = {...record, ...body, machine};
          return json({...record, home: 'human:ana'});
        }
        return json({...record, machine, home: 'human:ana'});
      }
      // What are you building starts the strategy draft (backend/strategy_setup.py); nothing has run before.
      if (p === '/api/v2/strategy/draft-from/latest') return json(null);
      if (p === '/api/v2/strategy/draft-from') { drafts.push(request.postDataJSON()); return json({id: 'run-1', state: 'running', ...request.postDataJSON()}); }
      if (p === '/api/v2/setup/getting-started') return json({items: [{id: 'first_bot', done: false}, subscription], can_build: true, tour_seen: false});
      // A company file: read, then applied with the fingerprint it was read with.
      if (p === '/api/v2/setup/company/preview') {
        previews.push(request.postDataJSON());
        return json({company_name: 'Acme', fingerprint: 'fp-1', employees: [{slug: 'assistant', title: 'Assistant'}, {slug: 'builder', title: 'Builder'}],
                     seats: [{title: 'Bookkeeper'}], deferred: ['goals']});
      }
      if (p === '/api/v2/setup/company/import') {
        imports.push(request.postDataJSON());
        record = {...record, completed: '2026-09-16T10:00:00Z', needed: false, company_import_failed: null, bots: BOTS_AFTER.map(bot => ({...bot})), machine};
        return json({...record, home: 'human:ana'});
      }
      if (p === '/api/v2/setup/groups') return json({version: 'fixture', departments: DEPARTMENTS, cards: CATALOG.filter(card => card.department), hq});
      if (p === '/api/v2/setup/recruit') {
        const body = request.postDataJSON();
        recruits.push(body);
        await recruitHeld;
        return json(recruitFor(body));
      }
      if (p === '/api/v2/setup/complete') {
        completes.push(request.postDataJSON());
        record = {...record, completed: '2026-09-16T10:00:00Z', needed: false, company_import_failed: null,
                  bots: BOTS_AFTER.map(bot => ({...bot})), machine};
        return json({...record, home: 'human:ana'});
      }
      if (p === '/api/v2/setup/getting-started/state') { tourPosts.push(request.postDataJSON()); return json({tour: true, checklist: false, cards: [], skipped: []}); }
      if (p === '/api/v2/enrollments') { enrollments.push(request.postDataJSON()); return json({code: enrollCode, expires_at: '2026-09-16T10:15:00Z'}); }
      if (p === '/api/v2/bots' && request.method() === 'POST') { created.push(request.postDataJSON()); return json({bot: {slug: request.postDataJSON().slug}}); }
      if (p === '/api/v2/bots') return json(v2bots());
      // Start onboarding writes in the bot's Home (the team sees it), and the bot page's chat shows it from then on.
      if (/^\/api\/v2\/threads\/t-home-[^/]+\/messages$/.test(p) && request.method() === 'POST') {
        const bot = p.split('/')[4]!.slice('t-home-'.length), text = request.postDataJSON().text;
        chats.push([bot, request.postDataJSON()]);
        const message = {id: 'msg-' + bot, seq: 1, depth: 0, thread_id: 't-home-' + bot, sender_id: 'human:ana', body: text,
                         created_at: '2026-09-16T10:05:00Z'};
        opened[bot] = {...opened[bot], home: [message]};
        return json({message});
      }
      // A person's chat with a bot: the first message opens it, and the bot page's DM tab shows it from then on.
      if (/^\/api\/v2\/chat\/[^/]+$/.test(p)) {
        const bot = p.split('/')[4]!, text = request.postDataJSON().text;
        chats.push([bot, request.postDataJSON()]);
        const conversation = {id: 'chat-' + bot, kind: 'chat', scope: 'personal', task_id: null, closed_at: null,
                              participants: ['human:ana', 'bot:' + bot]};
        const message = {id: 'msg-' + bot, conversation_id: conversation.id, sender_id: 'human:ana', recipient_id: 'bot:' + bot,
                         kind: 'say', body: text, refs: {}, created_at: '2026-09-16T10:05:00Z'};
        opened[bot] = {conversation, messages: [message]};
        return json({message, conversation});
      }
      for (const dm of dms) { const answer = dm(p, url, request.method()); if (answer !== undefined) return json(answer); }
      if (p === '/api/v2/access/humans') { invites.push(['add', request.postDataJSON()]); return json({person: 'sam', email: 'sam@initech.test', name: 'Sam Ortiz'}); }
      if (p === '/api/v2/access/humans/sam') { invites.push(['role', request.postDataJSON()]); return json({person: 'sam'}); }
      if (/^\/api\/v2\/bots\/[^/]+\/co-owners$/.test(p)) { owners.push([p.split('/')[4]!, request.postDataJSON()]); return json({bot_owners: [{id: 'ana', name: 'Ana Rivera'}, {id: 'ben', name: 'Ben Cole'}], revision: 4}); }
      if (/^\/api\/v2\/bots\/[^/]+\/definition$/.test(p)) {
        definitions.push({path: p, body: request.postDataJSON()});
        return json({slug: p.split('/')[4]!, status: 'active', revision: 9});
      }
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (/^\/api\/v2\/tasks\/[^/]+\/chat$/.test(p)) return json({recipient: {slug: 'botops', name: 'BotOps', can_chat: true}, messages: [], conversation: null});
      if (p === '/api/v2/tasks' || p === '/api/v2/tasks/task-content') {
        const task = {id: 'task-content', title: 'Set up Content Marketer',
          owner_id: 'bot:botops', status: 'doing', version: 1, updated_at: '2026-09-16T10:01:00Z',
          created_at: '2026-09-16T10:00:30Z', requester_id: 'human:ana',
          body: 'Create the repository and the first routine.'};
        return json(p === '/api/v2/tasks' ? {tasks: [task]} : {task, events: [], children: []});
      }
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Rivera', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben Cole', email: 'ben@acme.example'}],
        org_groups: record.completed ? CHART_GROUPS : []});
      if (p === '/api/v2/operations') return json({machines: [], services: [], issues: [], scheduler_enabled: true});
      if (p === '/api/v2/models') return json({models: [{id: 'gpt-6-sol', label: 'Sol', runtime: 'codex', efforts: ['high', 'xhigh'], default_effort: 'high'}], harnesses});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });

    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', (e: Error) => errors.push(e.message));
    page.on('download', download => download.cancel().catch(() => {}));

    // ---- boot: a company that has never been set up opens on its first run: a modal over the app, not a page of its own.
    await page.goto('https://tico-ui.test/');
    await page.waitForFunction(() => location.hash === '#/welcome');
    await page.locator('#onb-website').waitFor();
    assert.equal(await page.locator('dialog#onb').evaluate((el: HTMLDialogElement) => el.matches(':modal')), true);
    assert.equal(await page.locator('#nav-welcome').evaluate((el: HTMLElement) => el.hidden), false);
    assert.match((await page.locator('#nav-welcome').textContent())!, /Finish setup/);
    // The app stays whole behind it: its sidebar keeps every page.
    assert.equal(await page.locator('#side .side-scroll > [data-nav="tasks"]').isVisible(), true);
    assert.equal((await page.locator('#onb-title').textContent())!, 'Set up Initech');
    assert.equal(await count(page), 'Step 1 of 3');
    assert.equal((await page.locator('.onb-step-title').textContent())!, 'What are you building?');
    assert.equal(await bigTitle(page), true);
    // One primary button, no Back on the first step, no way out before the team exists, and no sample company.
    assert.deepEqual(await page.locator('#onb button.primary').evaluateAll(els => els.map(el => el.id)), ['onb-next']);
    assert.equal(await page.locator('#onb-back').count(), 0);
    assert.equal(await page.locator('#onb-close').isVisible(), false);
    assert.equal(await page.locator('#onb-sample, [data-onb-sample], textarea#onb-company-yaml').count(), 0);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog#onb').evaluate((el: HTMLDialogElement) => el.open), true);
    // The company is named and so is the owner (the roster has a name): neither is asked.
    assert.equal(await page.locator('#onb-company').count() + await page.locator('#onb-owner').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.locator('#mobile-nav').isVisible(), false, 'no phone navigation during setup');
    await shot(page, 'phone-0-building');
    await page.setViewportSize({width: 1100, height: 800});
    await shot(page, 'desktop-0-building');

    // ---- What are you building: the website and one line, saved, and the strategy draft started from them.
    // With only an address on the roster, the sign-in's display name is offered, and an address never is.
    assert.equal(await page.evaluate(() => { const was = S.me; S.me = {...was, name: 'ana@acme.example', sign_in_name: 'Ana R.'};
      const proxied = onbOwnerGuess(); S.me = {...was, name: 'ana@acme.example'}; const bare = onbOwnerGuess(); S.me = was; return [proxied, bare].join('|'); }), 'Ana R.|');
    // No company address on the roster (a Gmail owner): the team's email domain is asked once, optionally; with one, it is not.
    assert.equal((await page.locator('#onb-domain').getAttribute('placeholder'))!, 'Optional, such as acme.example');
    assert.equal(await page.evaluate(() => { const was = S.me.company_domains!; S.me.company_domains = ['acme.example'];
      const asked = onbAsksDomain(); S.me.company_domains = was; return asked; }), false);
    await page.locator('#onb-website').fill('initech.test');
    await page.locator('#onb-building').fill('Studio software, and the live audio app behind it.');
    await page.locator('#onb-domain').fill('initech.test');
    await page.locator('#onb-next').click();
    await page.locator('[data-onb-team]').first().waitFor();
    assert.equal(puts.length, 1);
    assert.deepEqual(puts[0]!.names, {company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace', owner_name: 'Ana Rivera', website: 'initech.test', team_domain: 'initech.test'});
    assert.equal(puts[0]!.answers.building, 'Studio software, and the live audio app behind it.');
    assert.deepEqual(puts[0]!.selected, {});
    assert.deepEqual(drafts, [{url: 'initech.test', text: 'Studio software, and the live audio app behind it.'}]);

    // ---- Your team: the server's default chart by team, each a switch; a team's top seat first, everyone under it after.
    assert.equal(await count(page), 'Step 2 of 3');
    assert.equal((await page.locator('.onb-step-title').textContent())!, 'Your team');
    assert.deepEqual(await page.locator('[data-onb-team-row]').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.onbTeamRow)),
      ['Product', 'Engineering', 'Growth']);
    assert.deepEqual(await page.locator('[data-onb-team-row=Engineering] .onb-team-roles').allTextContents(),
      ['Beta Lead', 'Beta Maker · Beta Checker']);
    assert.equal(await page.locator('[data-onb-team-row=Growth] .onb-team-open').count(), 1, 'a seat to recruit is marked');
    assert.equal(await page.locator('#onb-import-failed').count(), 0);
    assert.deepEqual(await page.locator('#onb button.primary').evaluateAll(els => els.map(el => el.id)), ['onb-next']);
    await shot(page, 'desktop-1-team');
    await page.locator('[data-onb-team=Growth]').uncheck();
    // Back keeps what was typed, and Next with the same words starts nothing new.
    await page.locator('#onb-back').click();
    assert.equal(await page.locator('#onb-website').inputValue(), 'initech.test');
    await page.locator('#onb-next').click();
    await page.locator('[data-onb-team]').first().waitFor();
    assert.equal(drafts.length, 1);
    assert.equal(await page.locator('[data-onb-team=Growth]').isChecked(), false);

    // ---- Change: the org builder, starting from the groups that were on (where it has a group of that name).
    await page.locator('#onb-change').click();
    await page.locator('[data-ob-tile]').first().waitFor();
    if (shots) await page.setViewportSize({width: 1440, height: 900});      // the org chart photographed on a laptop screen
    assert.equal(await count(page), 'Step 2 of 3');
    assert.equal((await page.locator('.ob-title').textContent())!, 'What groups do you want?');
    const tilesOn = () => page.locator('[data-ob-tile][aria-pressed=true]').evaluateAll(els => els.map(el => el.dataset.obTile));
    assert.deepEqual(await tilesOn(), ['product', 'engineering']);
    assert.equal(await page.locator('[data-ob-tile]').count(), 9);
    // Back is the default team again.
    await page.locator('#onb-back').click();
    await page.locator('[data-onb-team]').first().waitFor();
    await page.locator('#onb-change').click();
    await page.locator('[data-ob-tile]').first().waitFor();
    // The chart is there from the start: the owner on top, each chosen department waiting, and the built-ins as one folded
    // Built-in group. Message bots are not on it.
    assert.match((await page.locator('#ob-chart [data-oc-owner]').textContent())!, /Ana Rivera\s*CEO/);
    assert.doesNotMatch((await page.locator('#ob-chart').textContent())!, /Ace|BotOps|Inbox Manager/);
    assert.equal(await page.locator('#ob-chart .oc-dept.oc-pending').count(), 2);
    const builtIn = () => page.locator('#ob-chart [data-oc-builtin-toggle]');
    assert.match((await builtIn().textContent())!, /Built-in\s*2/);
    assert.equal((await builtIn().getAttribute('aria-expanded'))!, 'false');
    await builtIn().click();
    assert.equal((await builtIn().getAttribute('aria-expanded'))!, 'true');
    assert.match((await page.locator('#ob-chart').textContent())!, /Ace[\s\S]*BotOps/);
    assert.equal(await builtIn().evaluate(el => document.activeElement === el), true, 'the toggle keeps focus after the redraw');
    await builtIn().click();
    assert.equal((await builtIn().getAttribute('aria-expanded'))!, 'false');
    await shot(page, 'desktop-3-departments');
    for (const id of ['sales', 'marketing', 'support', 'finance', 'operations', 'product']) await page.locator(`[data-ob-tile=${id}]`).click();
    assert.deepEqual(await tilesOn(), ['sales', 'marketing', 'support', 'finance', 'operations', 'engineering']);
    assert.equal(await page.locator('#ob-chart .oc-dept').count(), 6);
    assert.match((await page.locator('#ob-start').textContent())!, /Start with Sales/);
    await page.locator('#ob-start').click();

    // One department at a time: its name, icon, what it is for, one question, and a place to answer.
    await page.locator('#ob-brief').waitFor();
    const plain = (locator: Locator) => locator.evaluate(el => { const copy = el.cloneNode(true) as HTMLElement; copy.querySelectorAll('.ob-ms').forEach(icon => icon.remove()); return copy.textContent!.trim(); });
    assert.equal((await page.locator('.ob-dept-head h2').textContent())!, 'Sales');
    assert.equal((await page.locator('.ob-kicker').textContent())!, '1 of 6');
    const sales = DEPARTMENTS[0]!;
    assert.equal((await page.locator('.ob-dept-head .ob-dept-icon').textContent())!, 'handshake');
    assert.equal((await page.locator('.ob-desc').textContent())!, sales.description);
    assert.equal(await plain(page.locator('.ob-goal')), 'Turn interest into revenue');
    assert.equal((await page.locator('.ob-question').textContent())!, 'What kind of sales do you do today?');
    assert.equal((await page.locator('#ob-brief').getAttribute('placeholder'))!, sales.placeholder);
    // The toggle says what it sends; it follows the install's setting, which is on here.
    assert.equal(((await page.locator('.ob-hq').textContent())!).trim(), 'Suggestions from Tico HQ');
    assert.equal(await page.locator('#ob-hq').isChecked(), true);
    assert.match((await page.locator('.ob-hq-row').textContent())!, /group, answer, team description, customer type, software choice, catalog version and install ID/);
    assert.match((await page.locator('.ob-hq-row a').getAttribute('href'))!, /PRIVACY.md#suggestions/);
    assert.equal(await page.locator('#ob-skip').isVisible(), true);
    assert.match((await page.locator('#ob-chart [data-oc-dept=sales]').getAttribute('class'))!, /oc-cur/);
    await shot(page, 'desktop-4-department');
    recruitHeld = new Promise(resolve => { releaseRecruit = resolve; });
    await page.locator('#ob-brief').fill('Inbound demos, and a few resellers');
    await page.locator('#ob-brief').press('Enter');
    await page.locator('#ob-recruiting').waitFor();
    assert.equal(((await page.locator('#ob-recruiting').textContent())!).trim(), 'Recruiting bots…');
    assert.equal((await page.locator('[data-ob-said]').textContent())!, 'Inbound demos, and a few resellers');
    assert.match((await page.locator('#ob-chart [data-oc-dept=sales]').textContent())!, /Recruiting…/);
    // The group being recruited holds three places on the chart, so its lines never hang there empty.
    assert.equal(await page.locator('#ob-chart [data-oc-dept=sales] .oc-ph').count(), 3);
    await shot(page, 'desktop-5-recruiting');
    releaseRecruit(); recruitHeld = null;
    await page.locator('#ob-suggested').waitFor();
    assert.deepEqual(recruits, [{department: 'sales', briefing: 'Inbound demos, and a few resellers', share: true}]);

    // The suggestions: the head and the defaults start checked, a common bot waits, and the answer brought in a niche one.
    const picked = () => page.locator('[data-ob-pick]:checked').evaluateAll(els => els.map(el => el.dataset.obPick));
    const suggested = () => page.locator('#ob-suggested [data-ob-bot]').evaluateAll(els => els.map(el => el.dataset.obBot));
    const chartBots = (id: string) => page.locator(`#ob-chart [data-oc-dept=${id}] [data-oc-bot]`).evaluateAll(els => els.map(el => el.dataset.ocBot));
    assert.deepEqual(await suggested(), ['sales-lead', 'sdr-research', 'account-manager', 'partnerships']);
    assert.deepEqual(await picked(), ['sales-lead', 'sdr-research']);
    assert.match((await page.locator('[data-ob-bot=sales-lead] .ob-bot-name').textContent())!, /Sales Manager\s*Head/);
    assert.equal(await plain(page.locator('[data-ob-why=sales-lead]')), 'Heads Sales and reports to you');
    // One word of the answer ("resellers") brings the niche bot in but is no reason to show: no why line on it.
    assert.equal(await page.locator('[data-ob-why=partnerships]').count(), 0);
    assert.equal(await plain(page.locator('[data-ob-why=sdr-research]')), 'A starting point for Sales');
    assert.equal(await page.locator('#ob-chart .oc-ph').count(), 0);
    assert.equal((await page.locator('[data-ob-bot=sdr-research] .ob-bot-sum').textContent())!, 'Researches accounts and writes first-touch emails.');
    // Every bot wears the avatar it will have: its blob with its template's icon, the same on its card and on the chart.
    assert.equal((await page.locator('[data-ob-bot=sdr-research] .av.blob .av-glyph').textContent())!, 'person_search');
    assert.equal((await page.locator('#ob-chart [data-oc-bot=sdr-research] .av.blob .av-glyph').textContent())!, 'person_search');
    assert.deepEqual(await chartBots('sales'), ['sales-lead', 'sdr-research']);
    // A long name is never cut off on the chart, and the full name is its tooltip. It wraps to two whole lines when it
    // cannot fit on one (ui/styles/first-run.css); the type decides which, so the line count is checked only then.
    const sdrName = await page.locator('#ob-chart [data-oc-label=sdr-research]').evaluate((el: HTMLElement) => {
      const probe = el.cloneNode(true) as HTMLElement;
      probe.setAttribute('style', 'position:absolute;visibility:hidden;display:block;white-space:nowrap');
      el.parentNode!.append(probe);
      const oneLine = probe.getBoundingClientRect().width <= el.getBoundingClientRect().width;
      probe.remove();
      return {
        cut: el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1,
        lines: Math.round(el.clientHeight / parseFloat(getComputedStyle(el).lineHeight)), oneLine};
    });
    assert.ok(!sdrName.cut && (sdrName.oneLine || sdrName.lines >= 2), JSON.stringify(sdrName));
    assert.equal((await page.locator('#ob-chart [data-oc-bot=sdr-research] .oc-bnode').getAttribute('title'))!, 'Sales Development Representative');
    await page.locator('[data-ob-bot=partnerships]').click();
    assert.deepEqual(await chartBots('sales'), ['sales-lead', 'sdr-research', 'partnerships']);
    assert.match((await page.locator('#ob-chart [data-oc-bot=partnerships]').getAttribute('class'))!, /ob-new/);
    assert.doesNotMatch((await page.locator('#ob-chart [data-oc-bot=sdr-research]').getAttribute('class'))!, /ob-new/);
    // Frame by frame through its entrance, the new bot's line is never drawn ahead of its card (that read as an empty gap).
    const entrance = await page.evaluate(() => {
      const li = document.querySelector<HTMLElement>('#ob-chart [data-oc-bot=partnerships]')!, node = li.querySelector<HTMLElement>('.oc-bnode')!;
      const animations = document.getAnimations().filter(a => li.contains((a.effect as KeyframeEffect | null)?.target ?? null));
      const out = [];
      for (const ms of [0, 100, 200, 300, 400, 600]) {
        for (const a of animations) { a.pause(); a.currentTime = ms; }
        const line = getComputedStyle(li, '::before').transform, scale = line === 'none' ? 1 : new DOMMatrix(line).d;
        out.push({ms, line: +scale.toFixed(2), card: +getComputedStyle(node).opacity});
      }
      for (const a of animations) a.finish();
      return out;
    });
    assert.ok(entrance.every(f => f.card >= f.line), 'line ahead of the card: ' + JSON.stringify(entrance));
    assert.equal((await page.locator('#ob-chart-stats').textContent())!, '6 groups · 3 bots');
    assert.equal(await page.locator('#ob-more').count(), 0);                       // nothing left in Sales to show under More
    // The modal stays inside the window, and its own body scrolls what does not fit; the page behind stays put.
    const screenshotViewport = page.viewportSize()!;
    await page.setViewportSize({width: 1100, height: 800});
    const fitted = await page.evaluate(() => {
      const dialog = document.querySelector<HTMLElement>('#onb')!, body = dialog.querySelector<HTMLElement>('.tmodal-body')!, box = dialog.getBoundingClientRect();
      return {inside: box.top >= 0 && box.bottom <= innerHeight, scrolls: body.scrollHeight <= body.clientHeight || getComputedStyle(body).overflowY !== 'visible',
        page: document.documentElement.scrollHeight <= innerHeight};
    });
    assert.deepEqual(fitted, {inside: true, scrolls: true, page: true});
    await page.setViewportSize(screenshotViewport);
    await shot(page, 'desktop-6-suggestions');
    assert.match((await page.locator('#ob-next').textContent())!, /Next: Marketing/);
    await page.locator('#ob-next').click();

    // Marketing: the answer so far is saved as a draft, and the person turns the toggle off, so nothing leaves.
    await page.locator('#ob-brief').waitFor();
    assert.equal((await page.locator('.ob-dept-head h2').textContent())!, 'Marketing');
    assert.equal(puts.length, 3);
    assert.deepEqual(puts[2]!.answers.departments, ['sales', 'marketing', 'support', 'finance', 'operations', 'engineering']);
    assert.deepEqual(puts[2]!.answers.briefings, {sales: 'Inbound demos, and a few resellers'});
    assert.deepEqual(Object.keys(puts[2]!.selected), ['coo', 'botops', 'sales-lead', 'sdr-research', 'partnerships']);
    await page.locator('.ob-hq').click();
    assert.equal(await page.locator('#ob-hq').isChecked(), false);
    await page.locator('#ob-brief').fill('LinkedIn posts and word of mouth');
    await page.locator('#ob-go').click();
    await page.locator('#ob-suggested').waitFor();
    assert.deepEqual(recruits.at(-1), {department: 'marketing', briefing: 'LinkedIn posts and word of mouth', share: false});
    assert.deepEqual(await picked(), ['marketing-lead', 'content']);
    // Back is the question again, with the answer kept; the same answer does not ask twice.
    await page.locator('#ob-back').click();
    assert.equal(await page.locator('#ob-brief').inputValue(), 'LinkedIn posts and word of mouth');
    await page.locator('#ob-brief').press('Enter');
    await page.locator('#ob-suggested').waitFor();
    assert.equal(recruits.length, 2);
    await page.locator('#ob-next').click();

    // Customer Support: no answer is an answer, and the toggle comes back on for it.
    await page.locator('#ob-brief').waitFor();
    assert.equal((await page.locator('.ob-dept-head h2').textContent())!, 'Customer Support');
    await page.locator('.ob-hq').click();
    await page.locator('#ob-brief').press('Enter');
    await page.locator('#ob-suggested').waitFor();
    assert.deepEqual(recruits.at(-1), {department: 'support', briefing: '', share: true});
    assert.deepEqual(await picked(), ['support-lead', 'support']);
    // More in Customer Support: the whole row is the button, wherever it is pressed, and by keyboard; the list survives a redraw.
    const more = page.locator('#ob-more-toggle');
    assert.equal(await page.locator('#ob-more-list').isVisible(), false);
    assert.equal((await more.getAttribute('aria-expanded'))!, 'false');
    const box = (await more.boundingBox())!;
    await more.click({position: {x: box.width - 6, y: box.height / 2}});          // far from the icon and the words
    assert.equal(await page.locator('#ob-more-list').isVisible(), true);
    assert.equal((await more.getAttribute('aria-expanded'))!, 'true');
    await page.locator('#ob-more-list [data-ob-bot]').first().waitFor();
    await page.locator('#ob-more-list [data-ob-bot]').first().click();             // checking a bot redraws the card
    assert.equal(await page.locator('#ob-more-list').isVisible(), true);
    await page.locator('#ob-more-list [data-ob-bot]').first().click();
    await more.focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#ob-more-list').isVisible(), false);
    assert.equal((await more.getAttribute('aria-expanded'))!, 'false');
    await page.locator('#ob-next').click();

    // Finance is skipped: no question asked, no bots, and the chart says so.
    await page.locator('#ob-brief').waitFor();
    assert.equal((await page.locator('.ob-dept-head h2').textContent())!, 'Finance');
    await page.locator('#ob-skip').click();
    await page.locator('.ob-dept-head h2', {hasText: 'Operations'}).waitFor();
    assert.match((await page.locator('#ob-chart [data-oc-dept=finance]').textContent())!, /Skipped/);
    assert.equal(recruits.filter(row => row.department === 'finance').length, 0);
    await page.locator('#ob-brief').fill('Vendor emails, and too much email');
    await page.locator('#ob-brief').press('Enter');
    await page.locator('#ob-suggested').waitFor();
    await page.locator('[data-ob-bot=meeting-notes]').click();
    assert.equal(await page.locator('[data-ob-bot=inbox]').count(), 0);        // a message bot is in no group
    await page.locator('#ob-next').click();

    // Engineering, the last one, leads to the finished chart.
    await page.locator('.ob-dept-head h2', {hasText: 'Engineering'}).waitFor();
    await page.locator('#ob-brief').fill('GitHub, and issues pile up');
    await page.locator('#ob-brief').press('Enter');
    await page.locator('#ob-suggested').waitFor();
    assert.deepEqual(await picked(), ['engineering-lead', 'issue-triage']);
    assert.match((await page.locator('#ob-next').textContent())!, /See team chart/);
    await page.locator('#ob-next').click();

    // The finished chart: every department with bots, its head under it and the team under the head.
    await page.locator('#ob-summary').waitFor();
    assert.equal((await page.locator('#ob-summary').textContent())!, '5 groups · 11 bots');
    assert.deepEqual(await page.locator('#ob-chart-big [data-oc-dept]').evaluateAll(els => els.map(el => el.dataset.ocDept)),
      ['sales', 'marketing', 'support', 'operations', 'engineering']);
    assert.equal(await page.locator('#ob-side').isVisible(), false);
    // At desktop widths, including the container breakpoint, the chart fills its card.
    assert.ok(await page.locator('#ob-main').evaluate((el: HTMLElement) => el.clientWidth / el.parentElement!.clientWidth > .98));
    assert.equal((await page.locator('[data-ob-helper-row=inbox] small').textContent())!, 'Reads one person’s mailbox and prepares an email brief.');
    assert.equal((await page.locator('[data-ob-helper=inbox]').getAttribute('aria-describedby'))!, 'ob-helper-desc-inbox');
    // Message bots sit apart from the chart: the Inbox Manager is one switch, off until someone turns it on.
    assert.equal((await page.locator('#ob-helpers h3').textContent())!, 'Message bots');
    assert.equal(await page.locator('[data-ob-helper=inbox]').isChecked(), false);
    assert.doesNotMatch((await page.locator('#ob-chart-big').textContent())!, /Inbox Manager/);
    await page.locator('[data-ob-helper=inbox]').check();
    await page.locator('[data-ob-helper-row=inbox] [data-cat-mailbox=inbox]').waitFor();
    await shot(page, 'desktop-7-chart');
    // The Inbox Manager has no mailbox yet, so the chart cannot be taken further.
    await page.locator('#onb-next').click();
    await page.locator('#team-problem:not([hidden])').waitFor();
    assert.match((await page.locator('#team-problem').textContent())!, /Choose whose mailbox Inbox Manager reads/);
    assert.equal(puts.length, 8);                                           // one draft per department answered or skipped
    await page.locator('[data-ob-helper-row=inbox] [data-cat-mailbox=inbox]').selectOption('ana');
    // Click a bot on the chart: rename it or point it at someone else. A message bot is never offered as a manager.
    await page.locator('#ob-chart-big [data-oc-edit=support]').click();
    assert.equal(await page.locator('[data-oc-reports=support]').inputValue(), 'support-lead');     // a worker reports to its head
    assert.equal(await page.locator('[data-oc-reports=support] option[value=inbox], [data-oc-reports=support] option[value=botops]').count(), 0);
    await page.locator('[data-oc-name=support]').fill('Support desk');
    await page.locator('[data-oc-reports=support]').selectOption('meeting-notes');
    await page.locator('[data-oc-done]').click();
    assert.match((await page.locator('#ob-chart-big [data-oc-bot=support]').textContent())!, /Support desk\s*→ Project Coordinator/);
    // Two bots can never report to each other: a bot's options leave out everything under it.
    await page.locator('#ob-chart-big [data-oc-edit=meeting-notes]').click();
    assert.equal(await page.locator('[data-oc-reports=meeting-notes]').inputValue(), 'ops-manager');
    assert.equal(await page.locator('[data-oc-reports=meeting-notes] option[value=support]').count(), 0);
    await page.locator('[data-oc-done]').click();
    await page.locator('#ob-chart-big [data-oc-edit=content]').click();
    await page.locator('[data-oc-reports=content]').selectOption('human:ben');
    await page.locator('[data-oc-done]').click();
    // A refused save says in plain words what happened and what to do, never the field the API names, and the chart stays.
    refuseSetup = true;
    await page.locator('#onb-next').click();
    await page.locator('#onb-status .err').waitFor();
    const refused = (await page.locator('#onb-status').textContent())!;
    assert.ok(refused.trim().length > 0);
    assert.doesNotMatch(refused, /body\.|instructions|String should|20000/);
    assert.equal(completes.length, 0);
    assert.equal(await page.locator('#onb-next').isDisabled(), false);
    // Two presses in a row create the team once.
    await page.evaluate(() => { const button = document.querySelector<HTMLElement>('#onb-next')!; button.click(); button.click(); });
    await page.locator('#onb-machine-status').waitFor();
    assert.equal(completes.length, 1);
    assert.deepEqual(completes[0], {});
    // The team goes to the server in the order to set it up: the built-ins, each department's head and its team, then message bots.
    const team = puts.at(-1)!;
    assert.deepEqual(Object.keys(team.selected), ['coo', 'botops', 'sales-lead', 'sdr-research', 'partnerships', 'marketing-lead', 'content',
      'support-lead', 'support', 'ops-manager', 'meeting-notes', 'engineering-lead', 'issue-triage', 'inbox']);
    const reports = Object.fromEntries(Object.entries(team.selected).map(([slug, row]) => [slug, row.reports_to]));
    assert.deepEqual(reports, {coo: undefined, botops: undefined, 'sales-lead': 'human:ana', 'sdr-research': 'sales-lead', partnerships: 'sales-lead',
      'marketing-lead': 'human:ana', content: 'human:ben', 'support-lead': 'human:ana', support: 'meeting-notes', 'ops-manager': 'human:ana',
      'meeting-notes': 'ops-manager', 'engineering-lead': 'human:ana', 'issue-triage': 'engineering-lead', inbox: 'human:ana'});
    assert.equal(team.selected.support!.display_name, 'Support desk');
    assert.match(team.selected.inbox!.instructions!, /Mailbox: ana@acme.example/);
    assert.deepEqual(team.answers.departments, ['sales', 'marketing', 'support', 'operations', 'engineering']);
    assert.deepEqual(team.answers.briefings, {sales: 'Inbound demos, and a few resellers', marketing: 'LinkedIn posts and word of mouth',
      operations: 'Vendor emails, and too much email', engineering: 'GitHub, and issues pile up'});
    if (shots) await page.setViewportSize({width: 1100, height: 800});

    // ---- What your bots run on, after Create: no Back, a way out, the computer in one line and the subscription line.
    // Nobody is shown the tour: the owner who set the team up has it marked seen and finds it in the account menu.
    assert.equal(await count(page), 'Step 3 of 3');
    assert.equal((await page.locator('.onb-step-title').textContent())!, 'What your bots run on');
    assert.equal(await page.locator('#onb-back').count(), 0);
    assert.equal(await page.locator('#onb-close').isVisible(), true);
    assert.deepEqual(tourPosts, [{tour: true}]);
    // The models are chosen (providers configured), so they are no line here.
    assert.equal(await page.locator('#onb-models').count(), 0);
    assert.equal((await page.locator('#onb-machine-status .onb-line-text').textContent())!.trim(), 'Add the computer your bots run on');
    assert.equal(await page.locator('#onb-computer').evaluate((el: HTMLDetailsElement) => el.open), false);
    const sub = page.locator('#onb-subscription a');
    assert.equal((await sub.textContent())!, 'Add a Claude or Codex subscription');
    assert.equal((await sub.getAttribute('href'))!, '#/settings');
    assert.equal((await sub.getAttribute('data-gs-tab'))!, 'devices');
    assert.deepEqual(await page.locator('#onb button.primary').evaluateAll(els => els.map(el => el.textContent)), ['Done']);
    await shot(page, 'desktop-5-run');
    // The computer: the enrollment file is the Settings flow; the commands keep <slug> literal.
    await page.locator('#onb-computer > summary').click();
    await page.locator('#onb-enroll').waitFor();
    await shot(page, 'desktop-5b-machine');
    // Not in a container, the Mac is offered first; a server in Docker (the usual install) offers the Linux computer first.
    assert.equal(await page.locator('[data-onb-kind][value=mac]').isChecked(), true);
    assert.equal(await page.evaluate(() => { const was = S.config; S.config = {...was, in_docker: true};
      const kind = onbDefaultKind(), html = onbComputerHTML({...ONB, kind}); S.config = was;
      return kind + '|' + /value="linux" checked/.test(html) + '|' + (onbDefaultKind() === 'mac'); }), 'linux|true|true');
    // Not a Mac-only story: computers can be Macs or Linux/cloud boxes, on a subscription or an API key.
    const blurb = await page.locator('#onb-step').evaluate(el => el.parentElement!.textContent);
    assert.match(blurb, /A Mac/);
    assert.match(blurb, /A Linux or cloud server/);
    assert.doesNotMatch(blurb, /Mac you own/);
    // The saved subscriptions preset offers both sign-ins; a team without providers still omits them.
    assert.match((await page.locator('#onb-step').textContent())!, /profile login default claude/);
    assert.match((await page.locator('#onb-step').textContent())!, /profile login default codex/);
    assert.doesNotMatch(await page.evaluate(() => onbCommands({...ONB, providers: null}).map(row => row[1]).join('\n')),
      /profile add default|profile login|codex login/);
    // One numbered path: make the setup file, then the lines that use it. A Mac has no other way, so no Other ways.
    assert.deepEqual(await page.locator('.onb-steps > li > .k').allTextContents(),
      ["Download this computer's setup file", 'Register this Mac for the team', 'Add your Claude subscription',
        'Add your ChatGPT through Codex subscription', 'Start the bot service']);
    assert.equal(await page.locator('#onb-other').count(), 0);
    // Copy sits inside each command's box, not on a row of its own under it.
    assert.equal(await page.locator('.onb-cmd').count(), await page.locator('.onb-cmd > [data-onb-copy]').count());
    // Once a provider is chosen the sign-in step is there, in order, on a Mac and on a Linux server; on Linux the
    // plain-Docker line is the other way, after the path.
    assert.deepEqual(await page.evaluate(() => {
      const state = {...ONB, providers: {default: {runtime: 'codex'}}};
      const shape = (list: [string, string, boolean?][]) => list.map(([label, , other]) => (other ? 'other:' : '') + label.split(/[ ,(]/)[0]!).join(' ');
      return [shape(onbCommands(state)), shape(onbCommands({...state, kind: 'linux'}))];
    }), ['Register Add Start', 'Set Sign other:With']);
    assert.match(await page.evaluate(() => onbCommands({...ONB, providers: {default: {runtime: 'codex'}}})[1]![1]), /profile login default codex/);
    assert.match(await page.evaluate(() => onbCommands({...ONB, kind: 'linux', providers: {default: {runtime: 'claude'}}})[1]![1]), /docker exec -it -u bot tico-runner-tico-[a-z0-9]+ claude auth login/);
    // Sign-ins follow the company's choice, not an unrelated saved default model.
    const signIns = await page.evaluate(() => {
      const providers = [{id: 'anthropic', runtime: 'claude', label: 'Claude'},
        {id: 'openai', runtime: 'codex', label: 'ChatGPT'}, {id: 'google', runtime: 'gemini', label: 'Google'},
        {id: 'openrouter', runtime: 'pi', label: 'OpenRouter'}, {id: 'tico', runtime: 'pi', gateway: true}];
      return ['mac', 'linux'].flatMap(kind => ['subscriptions-gateway', 'subscriptions', 'gateway', 'own-keys'].map(preset => {
        const state = {...ONB, kind, providers: {preset, providers, enabled: ['anthropic', 'google', 'openrouter', 'tico'], default: {runtime: 'codex'}}};
        return {kind, preset, commands: onbCommands(state).map(row => row[1]).join('\n')};
      }));
    });
    for (const {kind, preset, commands} of signIns) {
      if (preset.startsWith('subscriptions')) {
        assert.match(commands, kind === 'mac' ? /profile login default claude/ : /claude auth login/);
        assert.match(commands, kind === 'mac' ? /profile login default codex/ : /codex login/);
        assert.doesNotMatch(commands, /gemini/);
      } else if (preset === 'gateway') {
        assert.doesNotMatch(commands, /profile login|codex login|claude auth login|gemini/);
      } else {
        assert.match(commands, kind === 'mac' ? /profile login default claude/ : /claude auth login/);
        assert.doesNotMatch(commands, /codex login|profile login default codex|profile login default pi/);
      }
    }
    assert.match((await page.locator('#onb-step').textContent())!, /scripts\/tico-computer -e <slug> install bot/);
    await page.locator('#onb-enroll').click();
    await page.waitForFunction(() => !/<setup-file>/.test(document.querySelector<HTMLElement>('#onb-step')!.textContent));
    assert.deepEqual(enrollments, [{operator: 'ana'}]);
    assert.match((await page.locator('#onb-step').textContent())!, /scripts\/tico-computer -e <slug> enroll --code-file "\$HOME\/Downloads\/tico-enrollment-ana-[a-z0-9]+\.json" --label "Ana's Mac"/);
    // A Linux or cloud server gets the installer line (compose, with the updater) with the real URL and code, and no setup file.
    await page.locator('[data-onb-kind][value=linux]').check();
    const installLine = /curl -fsSL https:\/\/github\.com\/ticoteam\/tico\/releases\/download\/v0\.2\.0\/install\.sh -o install\.sh && sh install\.sh --runner --name tico-code --url https:\/\/initech\.test --code '<code>' --label "Ana's server"/;
    assert.match((await page.locator('#onb-step').textContent())!, installLine);
    // A server that answers on this computer only is not 127.0.0.1 to a container: the runner joins its Docker network.
    assert.equal(await page.evaluate(() => { const was = S.config; S.config = {...was, local: true, runner_url: 'http://127.0.0.1:8877', compose_project: 'acme', server_network: 'acme_default'};
      const [run, , plain] = dockerRunnerCommands('c0de', onbMachineLabel({kind: 'linux'}), 'codex'); S.config = was;
      return [run[1], plain[1]].join('\n'); }),
      'curl -fsSL https://github.com/ticoteam/tico/releases/download/v0.2.0/install.sh -o install.sh && sh install.sh --runner --name acme-c0de --url http://server:8765 --server-network acme_default --code \'c0de\' --label "This computer"\n'
      + 'docker run -d --name tico-runner-acme-c0de --restart unless-stopped --network acme_default -v tico-runner-acme-c0de_runner-home:/home/runner ghcr.io/ticoteam/tico-runner:v0.2.0 join --url http://server:8765 --code \'c0de\' --label "This computer"');
    // One path is on the screen: the code, installer and saved subscriptions. A parenthetical is a hint under its label.
    assert.deepEqual(await page.locator('.onb-steps > li > .k').allTextContents(),
      ['Create a one-time code for this computer', 'Set up the runner on the server',
        'Sign in to Claude', 'Sign in to ChatGPT through Codex']);
    assert.match((await page.locator('.onb-steps .onb-hint').first().textContent())!, /^installs Docker if it is missing; keeps itself on this server's release$/);
    // The bare docker run stays as an alternative under Other ways, closed, pinned to the server's release and said not to update itself.
    assert.equal(await page.locator('#onb-other').evaluate((el: HTMLDetailsElement) => el.open), false);
    assert.equal(await page.locator('#onb-other .onb-cmd').isVisible(), false);
    assert.match((await page.locator('#onb-other').textContent())!, /no updater, so it will not follow the server's releases/);
    assert.match((await page.locator('#onb-other').textContent())!, /docker run -d --name tico-runner-tico-code --restart unless-stopped -v tico-runner-tico-code_runner-home:\/home\/runner ghcr\.io\/ticoteam\/tico-runner:v0\.2\.0 join --url https:\/\/initech\.test --code '<code>'/);
    await page.locator('#onb-other > summary').click();
    assert.equal(await page.locator('#onb-other .onb-cmd').isVisible(), true);
    // Before Add computer there is no code to show.
    assert.equal(await page.locator('#onb-code').count(), 0);
    await page.locator('#onb-enroll').click();
    await page.waitForFunction(() => /--code 'enroll-code' --label "Ana's server"/.test(document.querySelector<HTMLElement>('#onb-step')!.textContent));
    // The one-time code is on the screen in large type, not only in a toast; Other ways stays as it was left.
    assert.equal((await page.locator('#onb-code-value').textContent())!, 'enroll-code');
    assert.ok(await page.locator('#onb-code-value').evaluate(el => parseFloat(getComputedStyle(el).fontSize)) >= 20);
    assert.equal((await page.locator('#onb-enroll').textContent())!, 'New code');
    assert.equal(await page.locator('#onb-other').evaluate((el: HTMLDetailsElement) => el.open), true);
    // No command, and not the code, runs past the card: each wraps inside its box, on a laptop and on a phone.
    const overflow = () => page.evaluate(() => {
      const card = document.querySelector<HTMLElement>('#onb-step')!.getBoundingClientRect();
      return [...document.querySelectorAll('#onb-step .onb-cmd, #onb-step .onb-cmd code, #onb-code-value')]
        .filter(el => el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().right > card.right + 1).length;
    });
    assert.equal(await overflow(), 0);
    await shot(page, 'desktop-5b-machine-code');
    const desk = page.viewportSize()!;
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await overflow(), 0);
    await shot(page, 'phone-5b-machine-code');
    await page.setViewportSize(desk);
    // Each Copy takes exactly what its box shows: the installer line, the line under Other ways and the code, and
    // after New code the new code everywhere.
    await page.evaluate(() => { (window as unknown as PageWin).copied = [];
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async (value: string) => { (window as unknown as PageWin).copied.push(value); }}}); });
    const copies = async () => {
      await page.evaluate(() => { (window as unknown as PageWin).copied = []; });
      const buttons = [...await page.locator('.onb-steps [data-onb-copy]:visible').all(),
        ...await page.locator('#onb-other [data-onb-copy]:visible').all(), page.locator('#onb-code-copy')];
      for (const button of buttons) await button.click();
      await page.waitForFunction((count: number) => (window as unknown as PageWin).copied.length === count, buttons.length);
      const values = await page.evaluate(() => (window as unknown as PageWin).copied);
      assert.deepEqual(values, [...await page.locator('.onb-cmd code:visible').allTextContents(), (await page.locator('#onb-code-value').textContent())!]);
      return [values[0], values.at(-2), values.at(-1)];
    };
    const shown = async () => {
      const commands = await page.locator('.onb-cmd code:visible').allTextContents();
      return [commands[0], commands.at(-1), (await page.locator('#onb-code-value').textContent())!];
    };
    let [installer, other, code] = (await copies()) as [string, string, string];
    assert.deepEqual([installer, other, code], await shown());
    assert.match(installer, /^curl -fsSL .* --code 'enroll-code' /);
    assert.match(other, /^docker run -d .* --code 'enroll-code' /);
    assert.equal(code, 'enroll-code');
    enrollCode = 'enroll-code-2';
    await page.locator('#onb-enroll').click();
    await page.locator('#onb-code-value', {hasText: 'enroll-code-2'}).waitFor();
    [installer, other, code] = (await copies()) as [string, string, string];
    assert.deepEqual([installer, other, code], await shown());
    assert.match(installer, /--code 'enroll-code-2' /);
    assert.match(other, /--code 'enroll-code-2' /);
    assert.equal(code, 'enroll-code-2');
    enrollCode = 'enroll-code';
    await page.locator('#onb-enroll').click();
    await page.locator('#onb-code-value', {hasText: /^enroll-code$/}).waitFor();
    assert.doesNotMatch((await page.locator('#onb-step').textContent())!, /docker exec -it tico-runner/);
    await page.locator('[data-onb-kind][value=mac]').check();
    // The Mac comes online: the line says which computers are, with no instructions left.
    machine = {runners: [{id: 'runner-1', label: "Ana's Mac", online: true}, {id: 'runner-2', label: 'Cloud box', online: true}, {id: 'runner-3', label: 'Old laptop', online: false}], enrolled: true};
    await page.locator('#onb-machine-status[data-onb-machine=online]').waitFor({timeout: t(9000)});
    assert.equal((await page.locator('#onb-machine-status .onb-line-text').textContent())!.trim(), "Ana's Mac, Cloud box");
    assert.equal(await page.locator('#onb-enroll').count() + await page.locator('#onb-kind').count() + await page.locator('.onb-cmd').count(), 0);
    assert.equal(await page.locator('#onb-subscription').count(), 1);
    // Done: the modal goes and the owner is on Tasks.
    await page.locator('#onb-next').click();
    await page.locator('dialog#onb').waitFor({state: 'detached'});
    await page.waitForFunction(() => location.hash === '#/tasks');
    assert.equal(completes.length, 1);
    assert.equal(await page.locator('.gs-tour').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.locator('#mobile-nav').isVisible(), true, 'phone navigation returns after setup');
    await page.setViewportSize({width: 1100, height: 800});
    // The owner starts caught up: no What's new count left over from before Create.
    assert.equal(await page.locator('#changelog-notice').isVisible(), false, "no What's new count after Create");
    assert.equal(await page.locator('#changelog-badge').isHidden(), true, 'nor its badge in the account menu');
    // Finishing takes the setup entry out of the sidebar and puts the names on the app; the chart marks the parked bots.
    await page.evaluate(() => refresh(true));
    assert.equal(await page.locator('#nav-welcome').evaluate((el: HTMLElement) => el.hidden), true);
    await page.locator('#side .side-scroll > [data-nav="tasks"]').waitFor();
    assert.equal(await page.title(), 'Initech Hub');
    await page.locator('#tree .node[href="#/bot/support"] .tree-setup').waitFor();
    assert.equal(await page.locator('#tree .node[href="#/bot/coo"] .tree-setup').count(), 0);
    // The bot page carries the mark, and Start onboarding: one line from the person begins its onboarding conversation.
    await page.goto('https://tico-ui.test/#/bot/support');
    await page.locator('#bot-onboard').waitFor();
    await shot(page, 'desktop-7-bot-page');
    assert.match((await page.locator('#bot-onboard').textContent())!, /Onboarding/);
    assert.equal(await page.locator('#bot-onboard p.bot-onboard-tip').count(), 1);                     // the tip is the only paragraph
    assert.equal(await page.locator('#bot-onboard p:not(.bot-onboard-tip)').count(), 0);              // the pill and the button, no other paragraph
    assert.equal((await page.locator('#bot-start-setup').textContent())!, 'Start onboarding');
    assert.equal((await page.locator('#bot-onboard [data-setup-help="bot"]').textContent())!, 'Want help onboarding it?');
    await page.locator('#bot-start-setup').click();
    await page.locator('#bot-start-setup', {hasText: 'Onboarding started'}).waitFor();
    assert.deepEqual(chats, [['support', {text: "Let's set you up."}]]);
    assert.deepEqual(definitions, []);                                    // it was already active: nothing to activate first
    // Home shows the setup conversation: the person's line, in the chat Set up wrote to (not the private DM).
    await page.goto('https://tico-ui.test/#/bot/support/home');
    await page.locator('#bot-thread .th-msg.th-mine', {hasText: "Let's set you up."}).waitFor();
    // A planned starter is activated first (an owner's call), then told the same line.
    await page.goto('https://tico-ui.test/#/bot/meeting-notes');
    await page.locator('#bot-start-setup').click();
    await page.locator('#bot-start-setup', {hasText: 'Onboarding started'}).waitFor();
    // Start onboarding opens Home (Incognito stays off), which shows the line at once, not after a reload.
    await page.locator('#bot-thread .th-msg.th-mine', {hasText: "Let's set you up."}).waitFor({timeout: t(3000)});
    assert.equal(await page.locator('#bot-thread .th-empty').count(), 0);
    assert.deepEqual(definitions, [{path: '/api/v2/bots/meeting-notes/definition', body: {status: 'active', expected_revision: 4}}]);
    assert.deepEqual(chats.at(-1), ['meeting-notes', {text: "Let's set you up."}]);
    // The mark clears when the bot says it is set up: the page follows the bot's state.
    BOTS_AFTER.find(bot => bot.slug === 'meeting-notes')!.onboarding_state = 'onboarded';
    await page.evaluate(() => refresh(true));
    await page.locator('#bot-onboard').waitFor({state: 'detached'});
    await page.locator('#tree .node[href="#/bot/meeting-notes"] .tree-setup').waitFor({state: 'detached'});

    // ---- Settings -> Bots: the same cards, adding one bot from the catalog.
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#machine-add-open').click();
    await page.locator('#machine-kind').selectOption('linux');
    assert.equal(await page.locator('#machine-label').inputValue(), "Ana Rivera's server");
    // The first-run enrollment step already checks the Linux server command; keep this distinct Windows installer path.
    await page.locator('#machine-kind').selectOption('windows');
    await page.locator('#register-machine').click();
    await page.waitForFunction(() => /install-wsl\.ps1/.test(document.querySelector<HTMLElement>('#machine-enroll-status')!.textContent));
    assert.match((await page.locator('#machine-enroll-status').textContent())!, /irm https:\/\/github\.com\/ticoteam\/tico\/releases\/download\/v0\.2\.0\/install-wsl\.ps1\)\)\) -Url https:\/\/initech\.test -Code 'enroll-code' -Label 'Ana Rivera''s PC' -Name tico-enrollco/);
    await page.locator('#machine-kind').selectOption('mac');
    await page.keyboard.press('Escape');
    await page.locator('[data-settings-tab=bots]').click();
    await page.locator('#settings-add-catalog:not([disabled])').click();
    await page.locator('#catalog-picker-grid').waitFor();
    // The bots that already exist are not offered again, and nothing is forced on here.
    const offered = await page.locator('#catalog-picker-grid .cat-card').evaluateAll(els => els.map(el => el.dataset.catCard));
    assert.equal(offered[0], 'botops');
    assert.ok(['bookkeeping', 'inbox', 'issue-triage', 'content'].every(slug => offered.includes(slug)), offered.join());
    assert.ok(!['coo', 'support', 'meeting-notes'].some(slug => offered.includes(slug)), offered.join());
    assert.equal(await page.locator('#catalog-picker-grid [data-cat-toggle=botops]').isDisabled(), false);
    assert.equal(await page.locator('#catalog-picker-grid [data-cat-toggle=bookkeeping]').isChecked(), false);
    await page.locator('#catalog-picker-grid [data-cat-toggle=bookkeeping]').check();
    await page.locator('#catalog-picker-grid [data-cat-name=bookkeeping]').fill('Ledger');
    await page.locator('#catalog-picker-grid [data-cat-card=bookkeeping] .cat-instructions summary').click();
    await page.locator('#catalog-picker-grid [data-cat-instructions=bookkeeping]').fill('# Ledger\n\nChase, never pay. Ask Ana.\n');
    await page.getByRole('button', {name: 'Add selected', exact: true}).click();
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#catalog-picker')!.open);
    assert.equal(created.length, 1);
    assert.equal(created[0]!.slug, 'bookkeeping');
    assert.equal(created[0]!.template, 'bookkeeping');
    assert.equal(created[0]!.display_name, 'Ledger');
    assert.equal(created[0]!.instructions, '# Ledger\n\nChase, never pay. Ask Ana.\n');
    assert.equal(created[0]!.model, 'gpt-6-sol');
    assert.equal(created[0]!.effort, 'high');
    assert.deepEqual(created[0]!.owners, ['ana']);

    // Inbox bots ask whose mailbox, then append Mailbox: so BotOps can fill {{mailbox}}.
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('[data-settings-tab=bots]').click();
    await page.locator('#settings-add-catalog:not([disabled])').click();
    await page.locator('#catalog-picker-grid').waitFor();
    assert.equal(await page.locator('#catalog-picker-grid [data-cat-mailbox=inbox]').count(), 1);
    await page.locator('#catalog-picker-grid [data-cat-toggle=inbox]').check();
    await page.getByRole('button', {name: 'Add selected', exact: true}).click();
    assert.match((await page.locator('#catalog-picker-status').textContent())!, /whose mailbox/);
    await page.locator('#catalog-picker-grid [data-cat-mailbox=inbox]').selectOption('ana');
    await page.getByRole('button', {name: 'Add selected', exact: true}).click();
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#catalog-picker')!.open);
    assert.equal(created.length, 2);
    assert.equal(created[1]!.slug, 'ana-inbox');
    assert.equal(created[1]!.template, 'inbox-manager');
    assert.equal(created[1]!.display_name, 'Ana Rivera message bot');
    assert.match(created[1]!.instructions, /Mailbox: ana@acme.example/);

    // ---- phone width: setup is the whole screen, one column, never sideways; a saved draft opens on its chart.
    record = {...record, completed: null, needed: true};
    const phone = await context.newPage();
    phone.on('pageerror', e => errors.push(e.message));
    await phone.setViewportSize({width: 390, height: 844});
    await phone.goto('https://tico-ui.test/#/welcome');
    await phone.locator('#onb-website').waitFor();
    const fits = (on: Page) => on.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth
      && document.querySelector<HTMLElement>('#onb .tmodal-body')!.scrollWidth <= document.querySelector<HTMLElement>('#onb .tmodal-body')!.clientWidth);
    const screen = (await phone.locator('dialog#onb').boundingBox())!;
    assert.deepEqual([screen.x, screen.y, screen.width, screen.height].map(Math.round), [0, 0, 390, 844]);
    assert.equal(await fits(phone), true);
    await phone.locator('#onb-next').click();
    await phone.locator('#ob-summary').waitFor();
    assert.equal((await phone.locator('#ob-summary').textContent())!, '5 groups · 11 bots');
    assert.equal(await fits(phone), true);
    await shot(phone, 'phone-7-chart');
    await phone.locator('#ob-departments').click();
    await phone.locator('[data-ob-tile]').first().waitFor();
    assert.equal(await phone.locator('#ob-strip').isVisible(), true);
    assert.equal(await phone.locator('#ob-chart').isVisible(), false);
    assert.match((await phone.locator('#ob-strip').textContent())!, /5 groups · 11 bots/);   // the skipped Finance is not counted
    assert.equal(await fits(phone), true);
    await shot(phone, 'phone-3-departments');
    await phone.locator('#ob-strip').click();
    assert.equal(await phone.locator('#ob-chart').isVisible(), true);
    assert.equal(await fits(phone), true);
    await shot(phone, 'phone-3b-strip-open');
    await phone.locator('#ob-strip').click();
    assert.equal(await phone.locator('#ob-chart').isVisible(), false);
    await phone.locator('#ob-start').click();
    await phone.locator('#ob-brief').waitFor();
    assert.equal(await phone.locator('#ob-brief').inputValue(), 'Inbound demos, and a few resellers');     // the saved answer
    assert.equal(await fits(phone), true);
    await shot(phone, 'phone-4-department');
    recruitHeld = new Promise(resolve => { releaseRecruit = resolve; });
    await phone.locator('#ob-go').click();
    await phone.locator('#ob-recruiting').waitFor();
    assert.equal(await fits(phone), true);
    await shot(phone, 'phone-5-recruiting');
    releaseRecruit(); recruitHeld = null;
    await phone.locator('#ob-suggested').waitFor();
    assert.equal(await fits(phone), true);
    await shot(phone, 'phone-6-suggestions');

    // An install that may not ask Tico HQ (here DO_NOT_TRACK) shows the toggle off, and says why.
    hq = {available: false, off_by: 'DO_NOT_TRACK'};
    await phone.goto('https://tico-ui.test/#/tasks');
    await phone.goto('https://tico-ui.test/#/welcome');
    await phone.locator('#onb-website').waitFor();
    await phone.locator('#onb-next').click();
    await phone.locator('#ob-departments').click();
    await phone.locator('#ob-start').click();
    await phone.locator('#ob-brief').waitFor();
    assert.equal(await phone.locator('#ob-hq').isChecked(), false);
    assert.equal(await phone.locator('#ob-hq').isDisabled(), true);
    assert.equal((await phone.locator('[data-ob-hq-off]').textContent())!, 'Off: DO_NOT_TRACK is set');
    await phone.locator('#ob-go').click();
    await phone.locator('#ob-suggested').waitFor();
    assert.equal(recruits.at(-1)!.share, false);
    hq = {available: true, off_by: ''};
    await phone.close();

    // After Create, on a phone, for a team that has not chosen its models: the recommended choice is one line, and the
    // others are under it. Done saves the choice.
    BOTS_AFTER.find(bot => bot.slug === 'meeting-notes')!.onboarding_state = 'needs_setup';
    record = {...record, completed: '2026-09-16T10:00:00Z', needed: false, bots: BOTS_AFTER.map(bot => ({...bot})), machine};
    const run = await context.newPage();
    run.on('pageerror', e => errors.push(e.message));
    await run.setViewportSize({width: 390, height: 844});
    const providerSaves: unknown[] = [];
    await run.route('**/v2/providers', r => {
      if (r.request().method() !== 'GET') { providerSaves.push(r.request().postDataJSON()); return r.fulfill({json: {configured: true, preset: 'subscriptions-gateway'}}); }
      return r.fulfill({json: {configured: false, preset: 'subscriptions-gateway', enabled: ['anthropic', 'openai'], default: {runtime: 'codex'}, providers: [
        {id: 'anthropic', label: 'Anthropic', runtime: 'claude'}, {id: 'openai', label: 'OpenAI', runtime: 'codex'},
        {id: 'openrouter', label: 'OpenRouter', runtime: 'pi'}]}});
    });
    await run.goto('https://tico-ui.test/#/welcome');
    await run.locator('#onb-models').waitFor();
    assert.equal(await count(run), 'Step 3 of 3');
    assert.equal((await run.locator('#onb-models-name').textContent())!, 'Your Claude and ChatGPT subscriptions, plus Tico Gateway');
    assert.equal(await run.locator('#onb-models').evaluate((el: HTMLDetailsElement) => el.open), false);
    assert.equal(await fits(run), true);
    await shot(run, 'phone-5-run');
    await run.locator('#onb-models > summary').click();
    const details = run.locator('[data-provider-details]');
    assert.equal(await details.isVisible(), false);
    assert.equal(await run.locator('[data-provider]:visible, [data-provider-model]:visible, .prov-routed:visible').count(), 0);
    assert.ok(await run.locator('.prov-preset').evaluateAll(rows => rows.every(row => {
      const radio = row.querySelector('input')!.getBoundingClientRect();
      const title = row.querySelector('strong')!.getClientRects()[0]!;
      return radio.top < title.bottom && radio.bottom > title.top && radio.right < title.left;
    })), 'each radio is beside the first title line');
    await run.locator('[data-provider-preset][value=own-keys]').check();
    assert.equal(await details.isVisible(), true);
    assert.equal(await run.locator('[data-provider]:visible').count(), 3);
    assert.equal((await run.locator('#onb-models-name').textContent())!, "Your company's plans or API keys");
    for (const preset of ['subscriptions', 'gateway', 'subscriptions-gateway']) {
      await run.locator(`[data-provider-preset][value=${preset}]`).check();
      assert.equal(await details.isVisible(), false);
    }
    assert.equal(await fits(run), true);
    await shot(run, 'phone-5b-run-models');
    await run.locator('#onb-next').click();
    await run.locator('dialog#onb').waitFor({state: 'detached'});
    assert.equal(providerSaves.length > 0, true);
    await run.goto('https://tico-ui.test/#/bot/support');
    await run.locator('#bot-onboard').waitFor();
    assert.equal(await run.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await shot(run, 'phone-7-bot-page');
    await run.close();

    // ---- a company file that failed on server start: its reason is one line over the default team, which is picked;
    // there is no Dismiss and nothing to paste. Create clears it.
    const fresh = (): Setup => ({...record, names: {company_name: 'Initech', app_name: 'Initech Hub', assistant_name: 'Ace'},
      answers: {}, selected: {}, completed: null, needed: true, bots: [], default_chart: DEFAULT_CHART.map(team => ({...team}))});
    record = {...fresh(), company_import_failed: {at: '2026-09-16T09:00:00Z', reason: 'teams.yaml line 4: Growth Lead reports to cmo, which is not on the team'}};
    const failed = await context.newPage();
    failed.on('pageerror', e => errors.push(e.message));
    await failed.goto('https://tico-ui.test/#/welcome');
    await failed.locator('#onb-website').waitFor();
    assert.equal(await failed.locator('#onb-import-failed').count(), 0, 'the failure is not the first thing read');
    await pastBuilding(failed);
    assert.equal((await failed.locator('#onb-import-failed').textContent())!.trim(),
      "Your company file wasn't imported: teams.yaml line 4: Growth Lead reports to cmo, which is not on the team.");
    assert.equal(await failed.locator('#onb-import-failed button, #onb textarea').count(), 0);
    assert.deepEqual(await failed.locator('[data-onb-team]:checked').evaluateAll(els => els.map(el => el.dataset.onbTeam)), ['Product', 'Engineering', 'Growth']);
    await shot(failed, 'desktop-2-team-failed');
    const before = completes.length;
    await failed.locator('#onb-next').click();
    await failed.locator('#onb-machine-status').waitFor();
    assert.equal(completes.length, before + 1);
    assert.equal(await failed.locator('#onb-import-failed').count(), 0);
    await failed.close();

    // ---- a company file chosen on the team step: read, shown, and imported by Create team.
    record = fresh();
    const imported = await context.newPage();
    imported.on('pageerror', e => errors.push(e.message));
    await imported.goto('https://tico-ui.test/#/welcome');
    await pastBuilding(imported);
    const yaml = 'version: 1\ncompany:\n  name: Acme\n';
    await imported.locator('#onb-company-file').setInputFiles({name: 'acme.tico.company.yaml', mimeType: 'application/yaml', buffer: Buffer.from(yaml)});
    await imported.locator('#onb-company-file-box').waitFor();
    assert.deepEqual(previews.at(-1), {company_yaml: yaml});
    assert.match(await imported.locator('#onb-company-file-box').innerText(), /Acme[\s\S]*Assistant[\s\S]*Builder[\s\S]*Bookkeeper\s*Recruiting/);
    assert.equal(await count(imported), 'Step 2 of 3');
    await shot(imported, 'desktop-2-team-file');
    // Back is the default team again.
    await imported.locator('#onb-back').click();
    await imported.locator('[data-onb-team]').first().waitFor();
    await imported.locator('#onb-company-file').setInputFiles({name: 'acme.tico.company.yaml', mimeType: 'application/yaml', buffer: Buffer.from(yaml)});
    await imported.locator('#onb-company-file-box').waitFor();
    const completed = completes.length;
    await imported.locator('#onb-next').click();
    await imported.locator('#onb-machine-status').waitFor();
    assert.deepEqual(imports, [{company_yaml: yaml, fingerprint: 'fp-1'}]);
    assert.equal(completes.length, completed);
    await imported.close();

    // A fresh install (no TICO_TEAM_NAME: the server's stand-in "Company") asks the name, with an empty field and a
    // placeholder, and Next asks for it instead of quietly naming the team "Company".
    Object.assign(CONFIG, {company_name: 'Company', company_name_default: true});
    record = {...fresh(), names: {company_name: '', app_name: '', assistant_name: ''}};
    const named = await context.newPage();
    named.on('pageerror', e => errors.push(e.message));
    await named.goto('https://tico-ui.test/#/welcome');
    await named.locator('#onb-company').waitFor();
    const mark2 = puts.length;
    assert.equal(await named.locator('#onb-company').inputValue(), '');
    assert.equal((await named.locator('#onb-company').getAttribute('placeholder'))!, 'Acme');
    assert.equal((await named.locator('label.onb-field:has(#onb-company) .k').textContent())!, 'Company name');
    await named.locator('#onb-next').click();
    await named.locator('#onb-status .err', {hasText: "Add your company's name."}).waitFor();
    assert.equal(await named.evaluate(() => (document.activeElement as HTMLElement).id), 'onb-company');
    assert.equal(puts.length, mark2);
    await named.locator('#onb-company').fill('Globex');
    await named.locator('#onb-next').click();
    await named.locator('[data-onb-team]').first().waitFor();
    assert.equal(puts.at(-1)!.names.company_name, 'Globex');
    assert.equal((await named.locator('#onb-title').textContent())!, 'Set up Globex');
    await named.close();
    Object.assign(CONFIG, {company_name: 'Initech'}); delete CONFIG.company_name_default;

    // A viewer has no setup entry and lands on Tasks if they type the address.
    me.role = 'human';
    const viewer = await context.newPage();
    viewer.on('pageerror', e => errors.push(e.message));
    await viewer.goto('https://tico-ui.test/#/welcome');
    await viewer.waitForFunction(() => location.hash === '#/tasks');
    assert.equal(await viewer.locator('#onb-company').count(), 0);

    assert.deepEqual(errors, []);
    console.log('PASS: first run is a three-step modal over the app: building, the default team or the org builder or a company file, created once, then what the bots run on.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
