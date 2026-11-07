// Types for the globals the classic scripts under ui/ share (ui/app/core.js, api.ts, state.js, ...). A declaration here is
// read by the JSDoc on the JavaScript that defines the global, and by the TypeScript files that use it. No file here
// is a module, so every name below is global, as the scripts' own names are.
// A field the server sends but the page does not read is left out. A field read on the page is optional when the
// server or the page may leave it out.

// ---------------------------------------------------------------- the API client's errors (ui/app/api.ts)

/** The JSON body of a failed answer: a Problem (`error.code`), a task cap's 409 (`message` beside a code), or FastAPI's validation list. */
interface ApiFailureBody {
  error?: string | { code?: string; detail?: string; retryable?: boolean; sign_in?: string; cloud?: unknown; [key: string]: unknown };
  message?: string;
  detail?: string | { loc?: (string | number)[]; msg: string }[];
  [key: string]: unknown;
}

/** What get, post, put and patch throw for a failed answer (apiFailure in ui/app/api.ts): an Error with the body and the HTTP status. */
interface ApiFailure extends Error {
  body: ApiFailureBody;
  status: number;
}

// ---------------------------------------------------------------- toast (ui/app/api.ts)

interface ToastAction {
  label: string;
  run: () => unknown;
}

interface ToastOptions {
  /** Show an error even while signed out (the signed-out banner is the usual place). */
  keepWhenSignedOut?: boolean;
  /** An open modal dialog the toast sits in, so its button can be pressed. */
  host?: Element | null;
}

// ---------------------------------------------------------------- the signed-in person (S.me, GET /api/me)

/** The signed-in person as GET /api/me answers (backend/views.py): a roster person plus the sign-in and access facts. */
interface SelfPerson extends Omit<Partial<import('./api').Person>, 'id'> {
  id: string;
  email?: string;
  /** owner or viewer from /api/me; a bot's own session (/api/v2/me, account_routes.py) says bot, and a human says human. */
  role: 'owner' | 'viewer' | 'human' | 'bot';
  cloud: boolean;
  local: boolean;
  registered?: boolean;
  bot_admin?: boolean;
  credential_access?: boolean;
  credential_admin?: boolean;
  mail_access?: boolean;
  mover?: boolean;
  owner_id?: string;
  company_role?: string;
  can_create_bots?: boolean;
  can_add_people?: boolean;
  can_create_tokens?: boolean;
  can_see_sql?: boolean;
  has_tokens?: boolean;
  developer?: boolean;
  company_domains?: string[];
  proxy_session?: boolean;
  sign_in_name?: string;
  member_bot_limit?: number;
  chat_bots?: string[];
  slug?: string;
  config?: Partial<AppConfig>;
}

// ---------------------------------------------------------------- the server's config (S.config, state.js)

/** An available new version, as the config carries it (`update`), or null when none is known. */
interface ConfigUpdate {
  available?: boolean;
  latest?: string;
  current?: string;
  [key: string]: unknown;
}

/** The environment's names and switches: state.js's CONFIG_DEFAULTS, with whatever the server sends over them. */
interface AppConfig {
  environment_id: string;
  company_name: string;
  app_name: string;
  assistant_name: string;
  assistant_bot: string;
  public_url: string;
  runner_url: string;
  visual_origin: string;
  visual_files?: string;
  github_owner: string;
  local: boolean;
  release: string;
  onboarding_needed: boolean;
  version: string;
  update: ConfigUpdate | null;
  s3_view_urls: Record<string, string>;
  demo?: boolean;
  demo_public?: boolean;
  rehearsal?: boolean;
  support_on?: boolean;
  owner_name?: string;
  owner_email?: string;
  administration_url?: string;
  hosted?: boolean;
  visuals?: 'full' | 'hosted' | 'media';
  ui_build?: string;
  beta?: { id: string; templates?: string[]; harnesses?: string[] }[];
  providers_configured?: boolean;
  default_runtime?: string;
  in_docker?: boolean;
  feedback_question?: string;
  usage_count_notice?: unknown;
  changelog?: unknown;
  [key: string]: unknown;
}

// ---------------------------------------------------------------- the roster and the team (S.emps, S.orgGroups, S.people)

/** What a bot row says about one access level the signed-in person has (`my_access`, backend/views.py). */
interface AccessLevel {
  see?: boolean;
  read?: boolean;
  write?: boolean;
  manage?: boolean;
}

/** A person as a bot row lists them (`users`, `bot_owners`): P.brief in backend/people.py. */
interface BriefPerson {
  id: string;
  name?: string;
  email?: string | null;
  photo?: string;
  photo_url?: string;
}

/** A bot's computer as its row shows it (`machine`): the runner the bot last reported from. */
interface BotMachine {
  runner_id?: string;
  generation?: number;
  label?: string;
  owner_id?: string | null;
  last_seen_at?: string | null;
  platform?: string;
  version?: string;
  [key: string]: unknown;
}

/** An external agent's sync state (`agent`, a Grok Bot or Dots, backend/agents.py). */
interface BotAgent {
  label?: string;
  synced?: boolean;
  last_seen_at?: string | null;
  credential?: unknown;
  [key: string]: unknown;
}

/** One bot on the roster (GET /api/employees). A person is a Person; a bot is one of these. */
interface Employee {
  id: string;
  name?: string;
  slug?: string;
  display_name?: string;
  status?: string;
  template?: string;
  harness?: string;
  team?: string;
  reports_to?: string | null;
  owner_id?: string | null;
  is_branch?: boolean;
  can_chat?: boolean;
  my_access?: AccessLevel;
  machine?: BotMachine | null;
  agent?: BotAgent | null;
  users?: BriefPerson[];
  bot_owners?: BriefPerson[];
  /** Read by overview.js; no server path sends it on a bot row today, so it is optional and not relied on. */
  owners?: BriefPerson[];
  [key: string]: unknown;
}

/** A team (group) on the org chart. */
interface OrgGroup {
  id: string;
  name?: string;
  order?: number;
  parent?: string;
  org_parent?: string;
  root?: string;
}

// ---------------------------------------------------------------- the server's status (S.status, GET /api/status)

/** What a bot is doing right now (`S.status.active`). */
interface ActiveRun {
  employee: string;
  [key: string]: unknown;
}

/** A health issue the Health page lists (`S.status.health_issues`). */
interface HealthIssue {
  kind: string;
  bot?: string;
  bots?: string[];
  since?: string;
  can_review?: boolean;
  [key: string]: unknown;
}

interface ServerStatus {
  active?: ActiveRun[];
  recent_runs?: { employee: string; [key: string]: unknown }[];
  queued?: { bot?: string; employee?: string; [key: string]: unknown }[];
  health_issues?: HealthIssue[];
  company_entity?: string;
  cloud_access?: unknown;
  [key: string]: unknown;
}

// ---------------------------------------------------------------- the live hub state (S.v2, hub-v2.js)

/** Why a bot is quarantined and whether it lifts by itself (`quarantine` on a live bot state, backend/events.py). */
interface Quarantine {
  auto?: boolean;
  resumes_at?: string | null;
  since?: string | null;
  why?: string;
  review?: { found?: string; [key: string]: unknown } | null;
  builder_notice?: unknown;
  [key: string]: unknown;
}

/** A bot's live state from the hub (`S.v2.status[slug]`). */
interface V2BotState {
  state: string;
  bot_state?: string;
  focus?: string;
  changed_at?: string;
  stopped?: string;
  backoff?: string;
  quarantine?: Quarantine | null;
  last_stop?: { text?: string; [key: string]: unknown } | null;
  open_tasks?: number;
  [key: string]: unknown;
}

/** What the person's Needs you went away on its own this week (`S.v2.wentAway`, backend/views.py went_away). */
interface WentAway {
  expired: number;
  withdrawn: number;
  assistant: number;
  total: number;
  days?: number;
}

/** One row of Needs you (`S.v2.needs`). */
interface NeedsItem {
  kind: string;
  id?: string;
  requested_by_id?: string;
  requester_id?: string;
  [key: string]: unknown;
}

interface V2State {
  on: boolean;
  status: Record<string, V2BotState>;
  needs: NeedsItem[];
  needsVersion?: string | null;
  needsCount?: number;
  needsNext?: unknown;
  needsBots?: Record<string, number> | null;
  needsPlateCount?: number | null;
  needsByKind?: Record<string, number> | null;
  needsFoldedIds?: string[] | null;
  wentAway?: WentAway | null;
}

/** The copies each page keeps for its next visit (S.lastRead). Pages not listed here keep their own shape. */
interface LastRead {
  automations?: { list?: unknown[]; groups?: unknown; stats?: unknown; range?: string; maint?: unknown; heart?: unknown };
  /** The Dashboard's places, keyed by the place's key. */
  places?: Record<string, unknown>;
  [page: string]: unknown;
}

// ---------------------------------------------------------------- S, the page's shared state (ui/app/state.js)

interface AppState {
  emps: Employee[];
  people: (import('./api').Person & { hidden?: boolean })[];
  orgGroups: OrgGroup[];
  status: ServerStatus | null;
  statusPending: boolean;
  me: SelfPerson | null;
  route: string;
  config: AppConfig;
  v2: V2State;
  /** What each main page last read, so a return visit paints it at once (keyed by page). */
  lastRead: LastRead;
  seats?: import('./api').Seat[];
  updUnread?: number;
  notifyBrowser?: boolean;
  overviewRosterFresh?: boolean;
}
