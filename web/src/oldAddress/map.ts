/* Old app addresses (`/#/task/12`, from mail, pushes, chat text, bookmarks) at their page in this app. One row per
   address the old router (ui/app/router.ts route(), and redesignRoute and toolsRoute it asks first) understands, in the
   order it tries them, so the first row that matches wins as it did there. A row whose page the new design dropped
   points at the nearest page holding its content and says so; a row with no such page returns null (the caller opens
   the home page). Plain functions: no router, no React. */

/** One old address. `path` is tried on the address without its query; `when` narrows it by the query. */
export interface Row {
  /** The old page, as the old app called it. */
  page: string;
  path: RegExp;
  when?: (q: URLSearchParams) => boolean;
  /** The new address (path and search, without the `/next` base), or null when the new app has no page for it. */
  to: (m: readonly string[], q: URLSearchParams) => string | null;
}

/* ---------------------------------------------------------------- building addresses */

const dec = (s: string | undefined): string => {
  try {
    return decodeURIComponent(s ?? '');
  } catch {
    return s ?? ''; // typed by hand: used as it is
  }
};
/** A path segment from an old one: decoded, then encoded once. */
const seg = (s: string | undefined): string => encodeURIComponent(dec(s));

// The router's own search format (TanStack's default): a value its parser would read as JSON (a number, true) is
// written quoted, so `?open=123` arrives as the text "123", not the number.
const JSONISH = /^(?:\s|["[{\d-]|fa|nu|tr)/;
const searchValue = (v: string): string => {
  if (!JSONISH.test(v)) return v;
  try {
    JSON.parse(v);
    return JSON.stringify(v);
  } catch {
    return v;
  }
};

/** `path` with the given search; empty and missing values are left out. A number is written as one. */
function at(path: string, search: Record<string, string | number | null | undefined> = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(search)) if (v) q.set(k, typeof v === 'number' ? String(v) : searchValue(v));
  // `:` is legal in a query and reads the same either way; kept plain so `?tool=api:<id>` reads as written
  const s = q.toString().replace(/%3A/gi, ':');
  return s ? `${path}?${s}` : path;
}
/** A file version from the old query (a whole number above 0), or nothing. */
const version = (q: URLSearchParams): number | null => (/^[1-9]\d{0,8}$/.test(q.get('v') ?? '') ? Number(q.get('v')) : null);

/* A sign-in's return carries only the names and shapes its page reads; anything else in the old query (a stray `code`,
   a forged value) is dropped here, before any page sees it. */
/** GitHub's return words (backend/github_app.py). */
const GITHUB_WORDS = new Set(['connected', 'installed', 'authorized', 'pending', 'declined', 'finish', 'again', 'expired']);
/** A QuickBooks or Xero return (backend/finance_routes.py): `books`, and on an error a reason code. */
const booksReturn = (q: URLSearchParams) => {
  const books = q.get('books');
  const reason = q.get('reason') ?? '';
  return {
    books: books === 'connected' || books === 'error' ? books : null,
    reason: books === 'error' && /^[a-z_]{1,40}$/.test(reason) ? reason : null,
  };
};
/** The skills helper's code (backend/skill_devices.py: eight letters and digits, shown as two fours). */
const SKILL_CODE = /^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/;

/* ---------------------------------------------------------------- old names at new ones */

/** Old Settings tabs (and their old names, ui/app/pages-shell.ts SETTINGS_ALIAS) at a new Settings section. */
const SETTINGS_SECTION: Record<string, string> = {
  general: 'general',
  repos: 'general',
  repositories: 'general',
  storage: 'general',
  people: 'people',
  humans: 'people',
  bots: 'agents',
  // Subscriptions fold into Agents in the new design (owner, Nov 19).
  subscriptions: 'agents',
  devices: 'computers',
  computers: 'computers',
  models: 'models',
  chat: 'models',
  providers: 'models',
  limits: 'models',
  'turn-levels': 'models',
  gateway: 'models',
  tasks: 'tasks',
  contacts: 'contacts',
  security: 'security',
  screening: 'security',
  billing: 'billing',
  history: 'history',
  runs: 'history',
  health: 'health',
  changelog: 'changelog',
  account: 'account',
};
/** Cards that moved out of their tab (ui/app/pages-shell.ts SETTINGS_MOVED_TO_MODELS; tokens are Account's now). */
const SETTINGS_CARD_SECTION: Record<string, string> = {
  'settings-models': 'models',
  'set-providers': 'models',
  'set-gateway': 'models',
  'set-turn-levels': 'models',
  'settings-subs': 'agents',
  'settings-tokens': 'account',
};
/** System's cards that are a built-in agent's settings now (ui/app/pages-shell.ts SETTINGS_SYSTEM_BOTS). */
const SYSTEM_AGENTS: Record<string, string> = { 'set-general-manager-card': 'general-manager', 'set-strategy-bot-card': 'strategy-owner' };
const SYSTEM_JOBS = '/company/routines?panel=builtin:system';

function settingsAddress(tab: string, card: string, q: URLSearchParams): string {
  if (tab === 'strategy-bot') card = 'set-strategy-bot-card';
  if (['system', 'rules', 'routines', 'recurring'].includes(tab) || tab === 'strategy-bot') {
    if (card === 'set-screening-card') return '/settings/security';
    const agent = SYSTEM_AGENTS[card];
    return agent ? at(`/team/${agent}`, { tab: 'settings' }) : SYSTEM_JOBS;
  }
  // Releases was a card of System: the release agent's settings, which an address cannot name. Agents lists it.
  if (tab === 'releases') return '/settings/agents';
  const section = SETTINGS_CARD_SECTION[card] ?? SETTINGS_SECTION[tab];
  if (!section) return '/settings'; // the old app opened General for a name it did not know
  const who = q.get('who');
  return at(`/settings/${section}`, section === 'history' && who && /^(bot|human):\S+$/.test(who) ? { who } : {});
}

/** Old Rules tabs (ui/app/pages-shell.ts RU_TABS) at the new page's (features/routines RULE_TABS). */
const RULES_TAB: Record<string, string> = {
  internal: '',
  external: 'outside',
  stages: 'stages',
  due: 'due',
  wakes: 'wakes',
  automations: 'on-tasks',
  builtin: 'built-in',
  suggested: 'suggested',
};
/** Finance tabs both apps have; the old My budget is the overview now. */
const FINANCE_TABS = new Set(['pnl', 'transactions', 'budgets', 'payables', 'receivables', 'balance', 'cards', 'tax', 'attribution', 'sources', 'access']);
/** Automations' built-in rows (ui/app/automations-*.ts) at the Routines panel's. */
const BUILTIN: Record<string, string> = { heartbeat: 'heartbeat', maintenance: 'maintenance', 'system-jobs': 'system' };

/** The old Tools list's filters (and the Keys list's, ui/app/pages-shell.ts toolsKeysQuery) the new list keeps. */
const toolsList = (q: URLSearchParams): string =>
  at('/library/tools', { q: q.get('q'), needs: q.get('needs') === '1' || q.get('issues') === '1' ? 1 : null, kind: q.get('kind') });
const tool = (id: string): string => at('/library/tools', { tool: id });
const MAIL_TOOLS = '/library/tools?kind=mailbox';

/** One part of Tools (what follows `#/tools/`), as ui/app/pages-shell.ts toolsRoute reads it. */
function toolsAddress(rest: string, q: URLSearchParams): string {
  const [head = '', id] = rest.split('/').filter(Boolean);
  const name = dec(id);
  if (!head) return toolsList(q);
  if (head === 'vendor' && id) return tool(`vendor:${name}`);
  if (head === 'api' && id) return tool(`api:${name}`);
  if (head === 'website' && id) return tool(`website:${name}`);
  // Keys was its own page; a key has no page of its own now: the tool when named, else the list.
  if (head === 'keys') return id && id !== 'credentials' ? tool(name) : toolsList(q);
  if (head === 'key') return id ? tool(`key:${name}`) : '/library/tools';
  if (head === 'webhooks') return id ? tool(`webhook:${name}`) : at('/library/tools', { kind: 'webhook' });
  if (head === 'mailbox' && id) return tool(`mailbox:${name}`);
  // A Tico inbox's address is the same for a number and an email inbox (backend/api_tools.py); the page draws both.
  if (head === 'number' && id) return tool(`number:${name}`);
  if (head === 'address' && id) return tool(`address:${name}`);
  // Tools › Settings' sections (ui/app/pages-shell.ts INBOX_SECTIONS) are settings on their tools now: Email's page,
  // Phone numbers' requests and the mailing address. No section was Email.
  if (head === 'settings') return tool(id === 'texts' ? 'number' : id === 'letters-in' || id === 'letters-out' ? 'address' : 'mailbox');
  if (head === 'messages') return '/threads/messaging';
  // GitHub's install and sign-in return here with ?github=… (backend/github_app.py): a known word is carried for the
  // page to read; nothing else is.
  if (head === 'github') {
    const word = q.get('github') ?? '';
    return at('/library/tools', { tool: 'github', github: GITHUB_WORDS.has(word) ? word : null });
  }
  return tool(dec(head));
}

/** Settings › Inboxes (what follows `#/settings/inboxes/`, ui/app/pages-shell.ts toolsFromInboxes). */
function inboxesAddress(rest: string): string {
  const [head = '', id] = rest.split('/').filter(Boolean);
  if (head === 'messages') return '/threads/messaging';
  if (id && ['mailbox', 'number', 'address'].includes(head)) return tool(`${head}:${dec(id)}`);
  return MAIL_TOOLS;
}

/* ---------------------------------------------------------------- the table, in the old router's order */

const tail = '(?:/(.*))?'; // the rest of the path after a page, optional

export const ROWS: readonly Row[] = [
  // A file's review view (ui/app/file-review-route.ts), over the task or agent it came from. The new app opens a file
  // in Library › Files; the page under it is not opened.
  { page: 'File review', path: /^#\/file\/([^/]+)$/, to: ([id], q) => at('/library/files', { file: dec(id), v: version(q) }) },
  // Settings with a recruit handoff (a title and brief from outside): Add agent is in Agents; the handoff is not carried.
  { page: 'Settings, recruit handoff', path: /^#\/settings$/, when: (q) => q.has('recruit'), to: () => '/settings/agents' },
  // Decide, then Needs you: Flow now. A link naming a task opens it inside Flow.
  { page: 'Decide', path: /^#\/decide$/, to: (_m, q) => (q.get('task') ? `/flow/${seg(q.get('task') ?? '')}` : '/flow') },

  /* redesignRoute (ui/app/redesign.ts), asked before the router's own list */
  // Goals moved under Strategy; one goal opens in its panel. Same for #/strategy/goals below.
  { page: 'Goals (old address)', path: /^#\/goals(?:\/([^/]+))?$/, to: ([id]) => at('/company/strategy/goals', { panel: id ? `goal:${dec(id)}` : null }) },
  { page: 'Listening (old Settings tab and Market view)', path: /^#\/(?:settings\/listening|strategy\/market\/listening)$/, to: () => '/company/listening' },
  { page: 'Library', path: /^#\/library$/, to: () => '/library/docs' },
  { page: 'Growth (old Strategy tab)', path: new RegExp(`^#/strategy/growth${tail}$`), to: () => '/company/growth' },
  { page: 'Go-to-market sources', path: new RegExp(`^#/strategy/gtm/sources${tail}$`), to: () => '/company/growth' },
  { page: 'Go-to-market', path: new RegExp(`^#/strategy/gtm${tail}$`), to: () => '/company/strategy' },
  { page: 'Routines (old Tasks view)', path: /^#\/(?:recurring|tasks\/routines)$/, to: () => '/company/routines' },
  // Automations is Routines. One automation opens in its panel; Webhooks is a tab; the built-in jobs are panels.
  {
    page: 'Automations',
    path: /^#\/automations(?:\/([^/]+)(?:\/([^/]+))?(?:\/.*)?)?$/,
    to: ([id, sub]) => {
      const name = dec(id);
      if (!name || name === 'bot') return '/company/routines';
      if (name === 'webhooks') return at('/company/routines', { tab: 'webhooks', panel: sub ? `hook:${dec(sub)}` : null });
      const builtin = BUILTIN[name];
      return at('/company/routines', { panel: builtin ? `builtin:${builtin}` : `routine:${name}` });
    },
  },
  // Listening; one listening automation is its watch (the same id).
  { page: 'Listening', path: /^#\/listening(?:\/([^/]+)(?:\/.*)?)?$/, to: ([id]) => at('/company/listening', { watch: id && id !== 'bot' ? dec(id) : null }) },

  /* toolsRoute (ui/app/pages-shell.ts): Tools and every old address of what it holds */
  { page: 'Library › Tools (old address)', path: new RegExp(`^#/library/tools${tail}$`), to: ([rest], q) => (rest?.startsWith('credentials/') ? '/library/tools' : toolsAddress(rest ?? '', q)) },
  { page: 'Integrations (old Tools)', path: new RegExp(`^#/integrations${tail}$`), to: ([rest], q) => (rest?.startsWith('credentials/') ? '/library/tools' : toolsAddress(rest ?? '', q)) },
  { page: 'Library › Connections (old Tools)', path: new RegExp(`^#/library/connections${tail}$`), to: ([rest], q) => toolsAddress(rest ?? '', q) },
  // Credentials was the keys list; keys live on each tool now.
  { page: 'Credentials', path: new RegExp(`^#/credentials${tail}$`), to: () => '/library/tools' },
  { page: 'Settings › Inboxes', path: new RegExp(`^#/settings/inboxes${tail}$`), to: ([rest]) => inboxesAddress(rest ?? '') },
  {
    page: 'Settings › Mailboxes, Email & Phone, Mail, Google sign-in',
    path: /^#\/settings\/(mailboxes|inbound|company-inbox|email-phone|mail|inbox|google)(?:\/([^/]+))?\/?$/,
    to: ([tab, id]) => (tab === 'mailboxes' && id ? tool(`mailbox:${dec(id)}`) : MAIL_TOOLS),
  },
  { page: 'Tools', path: new RegExp(`^#/tools${tail}$`), to: ([rest], q) => toolsAddress(rest ?? '', q) },

  { page: 'Strategy › Brand', path: new RegExp(`^#/strategy/brand${tail}$`), to: () => '/company/strategy/brand' },
  { page: 'Growth', path: new RegExp(`^#/growth${tail}$`), to: () => '/company/growth' },
  { page: 'Risk › Disputes', path: /^#\/risk\/disputes\/?$/, to: () => '/company/risk/disputes' },
  { page: 'Risk, one risk', path: /^#\/risk\/r\/([^/]+)$/, to: ([slug]) => at('/company/risk', { risk: dec(slug) }) },
  // Obligations was a tab; the new design shows them as each matter's dates on Risk.
  { page: 'Risk', path: new RegExp(`^#/risk${tail}$`), to: () => '/company/risk' },
  { page: 'Rules', path: /^#\/rules(?:\/([^/]+))?\/?$/, to: ([tab]) => at('/company/rules', { tab: RULES_TAB[dec(tab)] || null }) },
  { page: 'Products', path: /^#\/products(?:\/(.+))?$/, to: ([key]) => at('/company/products', { panel: key ? dec(key) : null }) },
  {
    page: 'Library › Skills',
    path: new RegExp(`^#/library/skills${tail}$`),
    to: ([rest], q) => {
      const parts = (rest ?? '').split('/').filter(Boolean).map(dec);
      if (parts[0] === 'proposals' && parts[1]) return at('/library/skills', { proposal: parts[1] });
      return at('/library/skills', { skill: parts.join('/') || null, q: q.get('q') });
    },
  },
  {
    page: 'Library › Contacts (and its old Directory address)',
    path: /^#\/library\/(?:contacts|directory)(?:\/(.+))?$/,
    to: ([id], q) => at('/library/contacts', { type: q.get('type'), q: q.get('q'), panel: id ? dec(id) : null }),
  },
  { page: 'Library › Files', path: new RegExp(`^#/library/files${tail}$`), to: (_m, q) => at('/library/files', { kind: q.get('kind'), q: q.get('q') }) },
  // A page (a file of kind page) full view: opened in Library › Files.
  { page: 'Page full view', path: /^#\/page\/([^/]+)$/, to: ([id], q) => at('/library/files', { file: dec(id), v: version(q) }) },
  { page: 'Strategy › Goals', path: /^#\/strategy\/goals(?:\/([^/]+))?$/, to: ([id]) => at('/company/strategy/goals', { panel: id ? `goal:${dec(id)}` : null }) },
  { page: 'Strategy › Org', path: new RegExp(`^#/strategy/org${tail}$`), to: () => '/company/strategy/org' },
  // Budget is not a Strategy tab in the new design: budgets are Finance's Budgets tab.
  { page: 'Strategy › Budget', path: new RegExp(`^#/strategy/budget${tail}$`), to: () => at('/company/finance', { tab: 'budgets' }) },
  // Finance; QuickBooks and Xero sign-ins come back to #/finance?books=connected (or =error&reason=…,
  // backend/finance_routes.py): they land on Sources, where the connection shows, with those two words carried.
  {
    page: 'Finance',
    path: /^#\/finance(?:\/([^/]+))?(?:\/.*)?$/,
    to: ([tab], q) => {
      if (q.has('books')) return at('/company/finance', { tab: 'sources', ...booksReturn(q) });
      const t = dec(tab);
      return at('/company/finance', { tab: FINANCE_TABS.has(t) ? t : null });
    },
  },
  // The public company page (Share) has no page in the new design: Company.
  { page: 'Company page (Share)', path: new RegExp(`^#/company-page${tail}$`), to: () => '/company' },

  /* the router's own list (ui/app/router.ts route()) */
  // Notes and Recordings, then Meetings: the Calendar holds meetings now; one meeting opens in its panel.
  { page: 'Notes, Recordings', path: /^#\/(?:notes|recordings)$/, to: (_m, q) => meetingsAddress(q.get('recording')) },
  { page: 'Call window', path: /^#\/call\/([^/]+)$/, to: ([id]) => `/call/${seg(id)}` },
  { page: 'Inbox (old Mail)', path: /^#\/inbox$/, to: () => '/threads/mail' },
  // Home was the Company page in the old app.
  { page: 'Home', path: /^#\/home$/, to: () => '/company' },
  { page: 'Overview (Dashboard, Floor, Campus, Graph, Factories)', path: /^#\/overview(?:\/(?:floor|campus|graph|factories))?$/, to: () => '/company' },
  // One factory's floor has no page in the new design: Company.
  { page: "A factory's floor", path: /^#\/overview\/floor\/(.+)$/, to: () => '/company' },
  { page: 'Company page (Share, in Overview)', path: /^#\/overview\/company-page$/, to: () => '/company' },
  { page: 'Team', path: /^#\/team\/([^/]+)(?:\/.*)?$/, to: ([name]) => `/team/${seg(name)}` },
  { page: 'Welcome (first run)', path: /^#\/welcome$/, to: () => at('/', { setup: 'path' }) },
  // The Roadmap ranked the Product pipeline's tasks; that pipeline is on Tasks.
  { page: 'Roadmap', path: /^#\/roadmap$/, to: () => '/roadmap' },
  { page: 'Tags', path: /^#\/tags$/, to: () => '/tags' },
  { page: 'Tag', path: /^#\/tag\/(.+)$/, to: ([tag]) => `/tags/${seg(tag)}` },
  // A task by number: the new app finds its pipeline and replaces the address.
  { page: 'Task', path: /^#\/task\/([^/]+)(?:\/.*)?$/, to: ([id]) => `/task/${seg(id)}` },
  { page: 'Task (#/tasks/<id>)', path: /^#\/tasks\/([^/]+)(?:\/.*)?$/, to: ([id]) => `/task/${seg(id)}` },
  { page: 'Chat (retired)', path: /^#\/chat$/, to: () => '/tasks' },
  // A live meeting's page has no counterpart: the Calendar holds meetings.
  { page: 'Live meeting', path: /^#\/meetings\/live\/(.+)$/, to: () => '/calendar' },
  { page: 'Meetings', path: /^#\/meetings$/, to: (_m, q) => (q.get('live') ? '/calendar' : meetingsAddress(q.get('meeting'))) },
  // An agent's page; a person's or agent's Settings and Instructions are tabs there. Other old tabs open the page.
  {
    page: 'Agent page',
    path: /^#\/bot\/([^/]+)(?:\/([^/]+))?(?:\/.*)?$/,
    to: ([ref, tab]) => at(`/team/${seg(ref)}`, { tab: tab === 'settings' || tab === 'instructions' ? tab : null }),
  },
  { page: 'Threads', path: /^#\/threads$/, to: () => '/threads' },
  {
    page: 'Calendar',
    path: /^#\/calendar(?:\/.*)?$/,
    to: (_m, q) => at('/calendar', { view: q.get('view'), date: q.get('date'), q: q.get('q') }),
  },
  // New message is the menu's Message picker now: the old address opens Threads.
  { page: 'New message', path: /^#\/threads\/new$/, to: () => '/threads' },
  { page: 'Thread', path: /^#\/thread\/([^/]+)(?:\/.*)?$/, to: ([id], q) => at(`/threads/${seg(id)}`, { m: q.get('m') }) },
  // A recruiting seat's Applicants: adding an agent is in Settings › Agents in the new design.
  { page: 'Recruiting seat', path: /^#\/seat\/(.+)$/, to: () => '/settings/agents' },
  // The Assistant's private chat is Tico's sheet, on every page: the home page.
  { page: 'Assistant', path: /^#\/assistant$/, to: () => '/' },
  { page: 'What the Assistant learned', path: /^#\/assistant\/learned$/, to: () => '/learned' },
  // The skills helper's approve page: `code` is what the helper on a computer shows (backend/skill_devices.py). Only a
  // code's shape is carried; the Skills page takes it out of the address as soon as it reads it.
  {
    page: 'Skills connect',
    path: /^#\/skills\/connect$/,
    to: (_m, q) => at('/library/skills', { connect: SKILL_CODE.test(q.get('code') ?? '') ? q.get('code') : 'setup' }),
  },
  { page: 'Person', path: /^#\/person\/([^/]+)(?:\/([^/]+))?(?:\/.*)?$/, to: ([id, tab]) => at(`/team/${seg(id)}`, { tab: tab === 'settings' ? tab : null }) },
  // Tasks; Needs you (view=foryou) is Flow. The old filters were across pipelines and Tasks is one pipeline now: dropped.
  { page: 'Tasks', path: /^#\/tasks$/, to: (_m, q) => (q.get('view') === 'foryou' ? '/flow' : '/tasks') },
  {
    page: 'Updates',
    path: /^#\/updates$/,
    to: (_m, q) => {
      const tab = q.get('view') === 'archive' ? 'archive' : q.get('kind') === 'weekly' ? 'weekly' : q.get('kind') === 'daily' ? 'daily' : '';
      return at(tab ? `/updates/${tab}` : '/updates', { open: q.get('update') });
    },
  },
  { page: 'Settings', path: /^#\/settings$/, to: () => '/settings' },
  // Help was the Support pop-up over Company: the Support chat beside Company.
  { page: 'Help', path: /^#\/help$/, to: () => at('/company', { chat: 'support' }) },
  { page: 'Getting started (retired)', path: /^#\/getting-started$/, to: () => '/tasks' },
  { page: 'Repositories', path: /^#\/repositories$/, to: () => '/settings/general' },
  // Webhooks (it was Triggers): incoming ones are Routines' Webhooks tab, outgoing ones tools, the Gateway's on Models.
  {
    page: 'Webhooks, Triggers',
    path: /^#\/(?:triggers|webhooks|settings\/triggers|settings\/webhooks)(?:\/([^/]+))?(?:\/.*)?$/,
    to: ([part]) => (part === 'outgoing' ? at('/library/tools', { kind: 'webhook' }) : part === 'gateway' ? '/settings/models' : at('/company/routines', { tab: 'webhooks' })),
  },
  { page: 'Health', path: /^#\/health$/, to: () => '/settings/health' },
  { page: 'Settings › roadmap suggestions', path: /^#\/settings\/roadmap$/, to: () => '/settings/tasks' },
  { page: 'Settings › storage', path: /^#\/settings\/storage$/, to: () => '/settings/general' },
  { page: 'Settings › System (Rules, Routines)', path: /^#\/settings\/(?:rules|routines)$/, to: () => SYSTEM_JOBS },
  { page: 'Settings › People', path: /^#\/settings\/people$/, to: () => '/settings/people' },
  { page: 'Settings › Computers', path: /^#\/settings\/computers$/, to: () => '/settings/computers' },
  { page: 'Subscriptions', path: /^#\/(?:settings\/)?subscriptions$/, to: () => '/settings/agents' },
  { page: "Strategy Owner's settings", path: /^#\/settings\/ceo$/, to: () => at('/team/strategy-owner', { tab: 'settings' }) },
  // Every other Settings tab, and a card in it (backend/settings_entries.py writes these).
  {
    page: 'Settings tab or card',
    path: /^#\/settings\/([a-z-]+)(?:\/([a-z0-9-]+))?\/?$/,
    to: ([tab, card], q) => settingsAddress(tab ?? '', card ?? '', q),
  },
  // The owner's read-only query page is not in the new design.
  { page: 'SQL', path: /^#\/sql$/, to: () => null },
  {
    page: 'Mail',
    path: /^#\/mail$/,
    to: (_m, q) => at('/threads/mail', { box: q.get('mailbox'), open: q.get('thread'), view: q.get('view') === 'agent' ? 'instructions' : null }),
  },
  { page: 'Messaging', path: /^#\/messaging$/, to: () => '/threads/messaging' },
  { page: 'Changelog', path: /^#\/changelog$/, to: () => '/settings/changelog' },
  { page: 'Market (old address)', path: new RegExp(`^#/market${tail}$`), to: () => '/company/strategy/market' },
  { page: 'Strategy › Market', path: new RegExp(`^#/strategy/market${tail}$`), to: () => '/company/strategy/market' },
  // Lenses is not a tab in the new design: Market holds what it read.
  { page: 'Strategy › Lenses', path: new RegExp(`^#/strategy/lenses${tail}$`), to: () => '/company/strategy/market' },
  { page: 'Strategy', path: /^#\/strategy$/, to: () => '/company/strategy' },
  { page: 'Docs, market collection', path: /^#\/docs(?:\/[^/]+)?$/, when: (q) => q.get('collection') === 'market', to: () => '/company/strategy/market' },
  { page: 'Docs', path: /^#\/docs(?:\/([^/]+))?$/, to: ([id], q) => (id ? `/library/${seg(id)}` : at('/library/docs', { q: q.get('q') })) },
  { page: 'Runs', path: /^#\/runs$/, to: () => '/settings/history' },
  // The nightly learning runs: what Tico learned is the nearest page.
  { page: 'Learnings', path: /^#\/learnings$/, to: () => '/learned' },
  { page: 'Usage', path: /^#\/usage$/, to: () => '/company/usage' },
  // Replay (play back a range) is not in the new design.
  { page: 'Replay', path: /^#\/replay$/, to: () => null },
  // A computer's screen and performance: the computer, open in Settings › Computers.
  { page: "A computer's screen", path: /^#\/computers\/([^/]+)\/screen$/, to: ([id]) => at('/settings/computers', { open: dec(id) }) },
  { page: "A computer's performance", path: /^#\/computers\/([^/]+)\/performance$/, to: ([id]) => at('/settings/computers', { open: dec(id) }) },
  // The style guide was out of the navigation; the new app's galleries are dev-only.
  { page: 'Style guide', path: /^#\/style-guide$/, to: () => '/settings/style-guide' },
  // Empty: the old app's landing.
  { page: 'Home (empty address)', path: /^#\/?$/, to: () => '/' },
];

function meetingsAddress(meeting: string | null): string {
  return meeting ? at('/calendar', { panel: `meeting:${meeting}` }) : at('/calendar', { view: 'list' });
}

/* ---------------------------------------------------------------- lookup */

/** The old address split: `#/a/b?x=1` is ['#/a/b', 'x=1']. A trailing slash is not part of the page. */
function split(oldHash: string): [string, string] {
  const bare = oldHash.replace(/^\/(?=#)/, ''); // the server's form, `/#/task/12`
  const hash = bare.startsWith('#') ? bare : bare.startsWith('/') ? '#' + bare : '#/' + bare;
  const i = hash.indexOf('?');
  const path = i < 0 ? hash : hash.slice(0, i);
  return [path.length > 2 ? path.replace(/\/+$/, '') : path, i < 0 ? '' : hash.slice(i + 1)];
}

/** The row an old address matches, with its captures, or null. */
export function rowFor(oldHash: string): { row: Row; m: string[]; q: URLSearchParams } | null {
  const [path, raw] = split(oldHash);
  const q = new URLSearchParams(raw);
  for (const row of ROWS) {
    const hit = row.path.exec(path);
    if (hit && (!row.when || row.when(q))) return { row, m: hit.slice(1).map((x) => x ?? ''), q };
  }
  return null;
}

/**
 * The new app's address (path and search, no `/next` base) for an old hash address, with or without its `#`, or null
 * when the old app had no such page or the new app has none for it (the caller then opens the home page).
 */
export function newAddressFor(oldHash: string): string | null {
  const hit = rowFor(oldHash);
  return hit ? hit.row.to(hit.m, hit.q) : null;
}
