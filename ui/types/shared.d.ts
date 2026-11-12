// Types for the globals the classic scripts under ui/ share (ui/app/core.ts, api.ts, state.ts, ...). A declaration here is
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
  host?: Element | null | undefined;
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
  /** A bot's read-only view of the app (backend/app_view.py): no email, nothing offered that writes. */
  read_only_view?: boolean;
  sign_in_name?: string;
  member_bot_limit?: number;
  chat_bots?: string[];
  slug?: string;
  config?: Partial<AppConfig>;
}

// ---------------------------------------------------------------- the server's config (S.config, state.ts)

/** One known issue the update check lists (`update.known_issues`). */
interface ConfigKnownIssue {
  title: string;
  url?: string | null;
  fixed_in?: string | null;
}

/** An available new version, as the config carries it (`update`), or null when none is known. */
interface ConfigUpdate {
  available?: boolean;
  latest?: string;
  current?: string;
  /** Known issues in the running version, from the update check (backend/releases.py); `known_issues_checked` is false when GitHub was not asked. */
  known_issues?: ConfigKnownIssue[];
  known_issues_checked?: boolean;
  /** The release Tico support set for this hosted server (backend/hosted_updates.py), until it is on it. */
  scheduled?: string;
  /** Tico runs this server's updates (a cloud company, backend/releases.py). */
  managed?: boolean;
  /** The release's changelog link. */
  url?: string;
  /** Notices from Tico for this install (backend/hosted_updates.py). */
  messages?: UpdateMessage[];
  /** The message Tico HQ has for this version (backend/releases.py). */
  message?: UpdateMessage | null;
  [key: string]: unknown;
}

/** One notice Tico sent: its tone (`kind`), its text (`body`) and an optional https link (`url`). */
interface UpdateMessage {
  id?: string | number | null;
  body?: string;
  kind?: string;
  url?: string;
}

/** The "How is Tico working for you?" question the server asks at a set moment (backend/support_asks.py). */
interface FeedbackAsk {
  moment: string;
  question: string;
}

/** The environment's names and switches: state.ts's CONFIG_DEFAULTS, with whatever the server sends over them. */
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
  beta?: { id: string; name?: string; docs?: string[]; templates?: string[]; harnesses?: string[] }[];
  providers_configured?: boolean;
  default_runtime?: string;
  /** The release the server runs, as runners compare it (settings-devices.ts serverReleaseTag). */
  runner_compat?: { version?: string };
  in_docker?: boolean;
  feedback_question?: FeedbackAsk | null;
  usage_count_notice?: unknown;
  changelog?: { unread_count?: number; [key: string]: unknown } | null;
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
  /** The computer's screen: off, view or control, and whether the computer offers it (computer-screen.ts). */
  screen?: string | null;
  screen_available?: boolean;
  [key: string]: unknown;
}

/** A computer's readiness report (SettingsMachine.readiness): what each runtime and harness can do, as the computer last reported it. */
interface MachineReadiness {
  runtimes?: Record<string, { authenticated?: string; goals?: boolean | null } | undefined>;
  harnesses?: Record<string, MachineHarness | undefined>;
}
/** One harness in a computer's readiness report: the runtime it runs on, and whether goals work there. */
interface MachineHarness {
  runtime?: string;
  goals?: boolean | null;
}

/** An external agent's sync state (`agent`, a Grok Bot or Dots, backend/agents.py). */
interface BotAgent {
  label?: string;
  /** The harness the agent runs (settings-devices.ts agentKind). */
  harness?: string;
  synced?: boolean;
  last_seen_at?: string | null;
  credential?: unknown;
  [key: string]: unknown;
}

/** One row of a bot's access list (`access` on a roster row): a service the bot can reach, as which identity, with what verbs. */
interface BotAccess {
  service: string;
  identity?: string | null;
  can: string[];
  connected?: boolean;
  note?: string | null;
}

/** One bot on the roster (GET /api/employees). A person is a Person; a bot is one of these. */
interface Employee {
  id: string;
  name?: string;
  slug?: string;
  display_name?: string;
  status?: string;
  /** The state a bot row may carry besides status (overview.ts reads it). */
  state?: string;
  icon?: string;
  template?: string;
  harness?: string;
  /** The bot's own model choice (an older row says runtime and effort for harness and reasoning_effort). */
  model?: string;
  runtime?: string;
  reasoning_effort?: string;
  effort?: string;
  team?: string;
  reports_to?: string | null;
  owner_id?: string | null;
  /** The bot a branch follows (a slug); absent on a bot that is not a branch. */
  shared_from?: string | null;
  is_branch?: boolean;
  can_chat?: boolean;
  my_access?: AccessLevel;
  /** The bot's access policy (settings-access.ts reads it): see, read and write; null or absent when it is not set. */
  access_policy?: {see: import('./api').AccessAudience; read: import('./api').AccessAudience; write: import('./api').AccessAudience} | null;
  access?: BotAccess[] | null;
  machine?: BotMachine | null;
  /** The computer a bot is placed on (`assignments` row), when the server sends it. */
  assignment?: { runner_id?: string } | null;
  agent?: BotAgent | null;
  /** The model it falls back to (the bot page's Info card). */
  fallback?: SettingsBotChoice | null;
  /** Whether the bot's computer or agent is up, and whether its setup is done (the status tooltip, ui/app/tooltip.ts, reads them). */
  online?: boolean;
  ready?: boolean;
  readiness?: { gateway?: string | null; goals?: boolean | null | undefined } | null;
  users?: BriefPerson[];
  bot_owners?: BriefPerson[];
  /** Read by overview.js; no server path sends it on a bot row today, so it is optional and not relied on. */
  owners?: BriefPerson[];
  [key: string]: unknown;
}

/** A team (group) on the org chart. */
interface OrgGroup {
  id: string;
  name?: string | undefined;
  lead?: string;   // the lead's ref (person:<id> or bot:<slug>), "" for none (backend/groups.py)
  order?: number;
  parent?: string;
  org_parent?: string;
  root?: string;
  bots?: string[];   // the group's members (GET /v2/groups `bots`)
}

/** A person on the chart: the roster row plus the fields the server sends that the generated type leaves out. */
type OrgPerson = AppState['people'][number] & {role?: string; inbox_bot?: string; report_position?: number};
// A node of the team chart, as the sidebar builds it (orgTreeByParent, orgTreeWithHelpers in ui/app/sidebar.ts) and
// other pages draw it (Company > Graph, Budget, goals). A bot node is its roster row; a seat node is its seat row (its
// own `kind` is the node's). `kind` tells them apart, so a check on it narrows the node.
type OrgGroupNode = { kind: 'group'; helpers?: boolean; id: string; name?: string | undefined; order: number };
type OrgPersonNode = { kind: 'person'; id: string; person: OrgPerson };
type OrgBotNode = Employee & {kind: 'bot'};
type OrgSeatRow = Omit<import('./api').Seat, 'kind'>;
type OrgSeatNode = OrgSeatRow & {kind: 'seat'; seat: OrgSeatRow};
type OrgNode = OrgGroupNode | OrgPersonNode | OrgBotNode | OrgSeatNode;
/** A teammate on the chart (a group, a person or a bot): a node without the recruiting seats. */
type OrgChartRow = Exclude<OrgNode, OrgSeatNode>;
/** The chart's nodes by their parent's key ('' for the top, `g:`/`p:`/`b:` + id). */
type OrgByParent = Record<string, OrgNode[]>;

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
  title: string;
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

// ---------------------------------------------------------------- the live hub state (S.v2, hub-v2.ts)

/** Why a bot is quarantined and whether it lifts by itself (`quarantine` on a live bot state, backend/events.py). */
interface Quarantine {
  auto?: boolean;
  resumes_at?: string | null;
  since?: string | null;
  why?: string;
  /** What it refused to write and the task it was on (the bot page's Quarantined card, ui/app/bot-page.ts). */
  review?: { found?: string; what?: string; preview?: string; task?: { id: string; title?: string } | null; [key: string]: unknown } | null;
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
  /** Why its last run ended without finishing (backend/execution.py last_stop): one line, its tone and the action it offers. */
  last_stop?: { text?: string; tone?: string; action?: string; reason?: string; at?: string; needs_person?: boolean; [key: string]: unknown } | null;
  /** The spend limit it is over, when it is over one (the bot page's limit banner). */
  limit?: { limit?: number | null; spent?: number; scope?: string; name?: string; period?: string; [key: string]: unknown } | null;
  open_tasks?: number;
  /** The status tooltip's Needs humans row and Next run row (ui/app/tooltip.ts). */
  needs_human?: number;
  next_due_at?: string | null;
  /** The spend limit holding its new work (backend/usage_limits.py `over`). */
  limit?: SpendLimit | null;
  [key: string]: unknown;
}

/** A spend limit a bot is over: its own, or its team's or a group's (`scope`). */
interface SpendLimit {
  scope?: string;
  name?: string;
  period?: string;
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

/** One row of Needs you (`S.v2.needs`): GET /v2/needs-you's item (NeedsEntry, ui/app/task-api.ts). */
type NeedsItem = NeedsEntry;

interface V2State {
  on: boolean;
  status: Record<string, V2BotState>;
  needs: NeedsItem[];
  needsVersion?: string | null;
  needsCount?: number;
  needsNext?: string | null;
  needsBots?: Record<string, number> | null;
  /** The server's count of asks, tasks and mentions per stage id over the whole list (a page's `stages`). */
  needsStages?: Record<string, number> | null;
  needsPlateCount?: number | null;
  needsByKind?: Record<string, number> | null;
  needsFoldedIds?: string[] | null;
  wentAway?: WentAway | null;
}

/** One week of GET /api/v2/overview/bot-hours (backend/bot_hours.py), the Overview's bot-hours card. */
interface BotHoursWeek { week: string; bot_hours: number; people: number; human_hours: number; ratio: number | null; so_far: boolean }
/** A week the card can plot: one with a ratio. */
type BotHoursPoint = BotHoursWeek & { ratio: number };
interface BotHours {
  zone: string; now: string; first_run: string | null; people: number; hours_per_person: number; gap_minutes: number;
  last_7_days: { bot_hours: number; human_hours: number; ratio: number | null; full: boolean };
  weeks: BotHoursWeek[];
}

/** The copies each page keeps for its next visit (S.lastRead). Pages not listed here keep their own shape. */
interface LastRead {
  /** The Automations list, its teams and its counts (automations.ts automationsStop, auLoadList). */
  automations?: {
    list: AuRow[]; groups: Array<import('./api').Group>; stats?: AuStats | null; range?: string;
    maint: MaintView | null; heart: import('./api').HeartbeatView | null;
  };
  /** The Overview's bot-hours answer (overview-hours.ts). */
  hours?: BotHours | null;
  /** The Dashboard's places, keyed by the place's key. */
  places?: Record<string, DashView>;
  /** The Tasks page's last read, of the view it names (tasks-page.ts tasksLoad). */
  tasks?: {view: string; needs: NeedsEntry[]; tasks: TaskBriefV3[]} | null;
  [page: string]: unknown;
}

// ---------------------------------------------------------------- S, the page's shared state (ui/app/state.ts)

/** A roster person as S.people holds them: the Person row plus the profile, Slack and notice fields the human page reads. */
type RosterPerson = import('./api').Person & {
  hidden?: boolean;
  left?: boolean;
  about?: string | null;
  goals?: string;
  notes?: string | null;
  github?: string | null;
  phone?: string | null;
  slack?: string | null;
  /** The bot a "Notes go to" line names (a slug). */
  bot?: string | null;
  bots?: string[];
  my_access?: AccessLevel;
  slack_muted_bots?: string[] | null;
  notify_slack_task_done?: boolean;
  notify_slack_bot_messages?: boolean;
  notify_browser?: boolean;
  email_digest?: boolean;
  update_draft?: boolean;
};

interface AppState {
  emps: Employee[];
  people: RosterPerson[];
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

// ---------------------------------------------------------------- the anchor a script-placed popover carries

/** The element a menu or popover placed by script opens from (settings-bots.js, subscriptions.js, task-props.js set it;
 *  motion.ts reads it to grow the menu from that side). Absent on everything else. */
interface HTMLElement {
  anchor?: Element | null;
}

// ---------------------------------------------------------------- moving a node without taking it out of the page

/** Element.moveBefore (Chromium 133+), shaped as lib.dom declares it from TypeScript 6. Older engines lack it: callers
 *  check for it first (visual-cards.ts visualSwap, overview.ts). */
interface ParentNode {
  moveBefore(node: Node, child: Node | null): void;
}

/** The open Growth page (ui/app/growth-page.ts GR): its route's load, sub-page and month; `data` is Sources' own. */
interface GrowthState {
  load: number;
  sub: string;
  month: string;
  data: unknown;
}

// ---------------------------------------------------------------- the health answer (HL, ui/health.ts) and live task drafts (task-live.ts)

/** A bot the health answer lists as stuck (`HL.stuck`). */
interface HealthStuckRow {
  bot: string;
  why: string;
  [key: string]: unknown;
}

/** A bot on a fallback model since a time (`HL.on_fallback`). */
interface HealthFallbackRow {
  changed_at?: string | null;
  [key: string]: unknown;
}

/** The health answer the app polls (`HL`, ui/health.ts): the parts the other pages read. */
interface HealthSnapshot {
  checks?: unknown[];
  audience?: string;
  stuck?: HealthStuckRow[];
  on_fallback?: HealthFallbackRow[];
  limited?: unknown[];
  [key: string]: unknown;
}

/** A task open in its dialog, live (`dialog.taskLive`; TaskLive is task-live.ts's). */
interface HTMLDialogElement {
  taskLive?: TaskLive | null;
}
