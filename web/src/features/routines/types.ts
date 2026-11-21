import type { Automation, AutomationRun, HeartbeatView } from '@/api/schema';

/* What the server sends for Routines, Rules and Listening, narrowed where the generated schema says only "an object".
   The fields come from backend/automations.py view(), triggers.py summary(), org_rules.py view(), stage_rules.py and
   reach.py listing(). */

// ---------------------------------------------------------------- routines (the old Automations)

export interface ListenSource {
  id: string;
  kind: 'search' | 'page' | 'github' | 'push' | string;
  site?: string;
  target?: string;
  /** github: what it follows. */
  follow?: 'prs' | 'releases' | string;
}
export interface ListenRule {
  do: 'keep' | 'drop' | string;
  tag?: string;
  text: string;
}

/** `type` says which of the others it carries (backend/automation_flow.py). */
export interface Trigger {
  type: string;
  cron?: string;
  tz?: string;
  /** event: `task.created`, `custom.<x>`. */
  name?: string;
  /** webhook: the incoming webhook's id. */
  id?: string;
  first_due?: string;
  repeats?: string;
  open_days?: number;
  inbox?: string;
  to?: string;
  sources?: ListenSource[];
  rules?: ListenRule[];
  limits?: { count: number; per: string }[];
  at?: string;
  /** Conditions on what the trigger hands a run; a run starts only when they hold. Kept as they are by every edit. */
  filters?: unknown[];
  /** due: the rule's due date it was made for. */
  rule_due_id?: string;
}

export interface Step {
  id: string;
  type: string;
  /** bot_turn: `bot:<id>` (`bot:<slug>` in older steps). */
  bot?: string;
  prompt?: string;
  [k: string]: unknown;
}

export interface LastRun {
  id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export type Routine = Omit<Automation, 'trigger' | 'steps' | 'last_run' | 'spend_cap'> & {
  trigger: Trigger;
  steps?: Step[];
  last_run?: LastRun | null;
  spend_cap?: { per_30m?: number } | null;
  builtin?: boolean;
  readonly?: boolean;
  updated_at?: string;
  listening?: { dot?: string; sources?: { id: string }[] } | null;
  /** A "What wakes" routine's scope: `bot`, `department` (a name), `pipeline`, `task` or `team` (every agent). */
  wakes?: { scope_kind: string; scope_id: string | null } | null;
  /** due: the next due day, YYYY-MM-DD. */
  next_due_at?: string | null;
};

export interface RunStep {
  id?: string;
  type?: string;
  status?: string;
  outputs?: { ref?: unknown; thread?: unknown; turn?: { task?: unknown } } | null;
}
export type Run = Omit<AutomationRun, 'steps'> & { steps: RunStep[] | null; error?: string | null };

// ---------------------------------------------------------------- built in: Maintenance, Heartbeat, System jobs

export interface MaintenanceStep {
  key: string;
  name: string;
  enabled: boolean;
  frequency?: string;
  last_ran_at?: string | null;
  fixed?: boolean;
}
export interface Maintenance {
  settings: { schedule: string; paused: boolean };
  steps: MaintenanceStep[];
  last_run?: { started_at?: string } | null;
  can_change: boolean;
}
export interface MaintenanceRun {
  id: string;
  started_at: string;
  finished_at?: string | null;
  ok?: boolean | null;
  result?: string | null;
  steps?: unknown[];
}
export type Heartbeat = HeartbeatView;
export interface SystemJob {
  key: string;
  name: string;
  what?: string;
  schedule?: string;
  on: boolean;
  /** It has a switch at all. */
  switch?: boolean;
  can_change?: boolean;
  /** It has a Run now (POST /system-routines/{key}/run). */
  controllable?: boolean;
  /** Its last run (backend/system_routines.py row_of). */
  last?: { started_at?: string | null; finished_at?: string | null; ok?: boolean; result?: string | null } | null;
  /** The schedule presets it offers (none: a switch only), the one picked and its daily time "HH:MM". */
  presets?: { key: JobPreset; label: string }[];
  preset?: JobPreset | null;
  at?: string | null;
  /** When it runs next. */
  next?: string | null;
}

/** backend/models.py SystemRoutineUpdate.preset */
export type JobPreset = '15m' | 'hourly' | '4h' | 'daily' | 'business';

// ---------------------------------------------------------------- incoming webhooks (/v2/triggers)

export interface Hook {
  id: string;
  name: string;
  /** The address without its secret; '' for a built-in one. */
  url: string;
  builtin?: boolean;
  created_by?: string;
  last_received?: string | null;
  received_24h?: number;
  refused_24h?: number;
  signed?: boolean;
  preset?: string;
  automations?: { id: string; name: string; owner_id?: string }[];
  /** Where a call's type is read: a header or a /json/path; '' for the preset's own. */
  type_from?: string;
  /** Calls taken an hour, at most. */
  hourly?: number;
  /** Days a call's body is kept. */
  keep_days?: number;
  /** A call nothing listens for: `log` it, or `note` it to whoever made the webhook. */
  unheard?: 'log' | 'note' | string;
}
export interface Delivery {
  delivery: string;
  received_at: string;
  outcome: 'accepted' | 'refused' | 'rate_limited' | 'replayed' | 'duplicate' | string;
  reason?: string | null;
  type?: string | null;
  tasks?: number;
  /** Successful rule and automation results, as counted by the delivery log. */
  rules?: number;
  /** Its body is still kept, so it can be read. */
  stored?: boolean;
}
/** One kept call (triggers.py read): redacted headers and, while kept, the body as sent. */
export interface Payload {
  received_at: string;
  type?: string | null;
  bytes?: number;
  expired: boolean;
  headers?: Record<string, string>;
  body?: string;
}
/** A routine's saved version (`active`) or an agent's proposal (`pending`). */
export interface Version {
  n: number;
  actor: string;
  created_at: string;
  status: 'active' | 'pending';
  summary?: string;
  /** Its trigger and steps as saved (a proposal's, to compare). */
  flow?: { trigger?: Trigger; steps?: Step[] };
}
export interface ShownOnce {
  id: string;
  url: string;
}

// ---------------------------------------------------------------- rules

export interface Scope {
  everyone?: boolean;
  all_bots?: boolean;
  groups?: { id: string; name?: string }[];
  bots?: { id: string; name?: string }[];
  people?: { id: string; name?: string }[];
}
/** A company rule (org_rules.py view): free text, who follows it, and what happens when it is broken. */
export interface OrgRule {
  id: string;
  kind: 'internal' | 'external';
  text: string;
  status: string;
  version: number;
  can_edit: boolean;
  who?: Scope | null;
  /** Internal: whom it is about (a rule the Who follows toward these teammates). */
  with?: Scope | null;
  description?: string | null;
  /** The part of a document it rests on; `quote` only where the reader may read the file. */
  cite?: { file_id?: string; version?: number | null; section?: string | null; quote?: string | null; hidden?: boolean; removed?: boolean } | null;
  /** Its due dates (the first twenty) and how many more. */
  due?: { id: string; due_on: string; status: string; repeats?: string; task_days?: number | null }[];
  due_more?: number;
  source?: { id: string; name: string; kind?: string } | null;
  if_broken?: 'block' | 'escalate' | 'warn' | null;
  reviewer?: { id: string; name?: string } | null;
  updated_at?: string;
  suggested_by?: string | null;
  created_at?: string;
  /** Checked exactly (backend/org_rules.py exact_check_in): an allow list or a dollar cap on one action. */
  exact_check?: ExactCheck | null;
}
export type ExactCheck =
  | { kind: 'allow_list'; action: string; field: string; values: string[] }
  | { kind: 'max_usd'; action: string; field: string; max_usd: number };
/** One version of a rule as the audit log keeps it: the rule before and after a change. */
export interface RuleVersion {
  id: string;
  created_at: string;
  actor: string;
  before: Partial<OrgRule> | null;
  after: Partial<OrgRule> | null;
}
/** A rule's history (GET /api/v2/rules/{id}/history): its versions, newest first, and its holds from the last 30 days,
    each a page with its own cursor. */
export interface RuleHistory {
  versions: RuleVersion[];
  versions_next?: string | null;
  holds: RuleHold[];
  holds_next?: string | null;
}
/** A stage rule: When (event, timer), If (cond, jev: the decision model's question), Then. */
export interface StageRule {
  id: string;
  name: string;
  pipeline_id: string;
  pipeline_name?: string;
  stages: string[];
  stage_names?: string[];
  scope: 'stage' | 'pipeline' | 'company';
  event: string;
  timer?: { kind?: string; hours?: number } | null;
  cond?: Record<string, unknown> | null;
  jev?: string | null;
  then: string;
  then_params?: Record<string, unknown> | null;
  reason?: string | null;
  status: string;
  revision: number;
  created_by?: string;
  updated_at?: string;
  can_edit?: boolean;
  not_acting?: string | null;
  suggests?: string | null;
}
export interface BuiltInRule {
  name: string;
  what?: string;
}
export interface RuleGroups {
  wakes: { id: string; name: string; enabled: boolean }[];
  automations: { id: string; name: string; enabled: boolean }[];
  built_in: BuiltInRule[];
}
export interface DueDate {
  id: string;
  due_on: string;
  repeats?: string;
  status: 'overdue' | 'due_soon' | 'upcoming' | 'satisfied' | string;
  task_days?: number;
  owner?: string | null;
  rule: { id: string; text: string };
}
export type Repeats = 'once' | 'weekly' | 'monthly' | 'quarterly' | 'yearly';
/** A marked occurrence (rule_due.py detail): who or what satisfied it. */
export interface DueMark {
  due_on: string;
  satisfied_by?: string | null;
  via?: string | null;
  satisfied_at?: string | null;
  outcome?: string | null;
}
/** One due date in full, for its panel (GET /rule-due/{id}). */
export interface DueDetail extends Omit<DueDate, 'rule' | 'repeats' | 'task_days'> {
  repeats: Repeats;
  rule: { id: string; text: string; kind?: string; status?: string };
  can_edit?: boolean;
  can_satisfy?: boolean;
  open_days?: string[];
  ended_on?: string | null;
  replaced_by?: string | null;
  retired_at?: string | null;
  routine_id?: string | null;
  /** A task opens this many days before each due day; null is no routine. */
  task_days?: number | null;
  chain?: { id: string; satisfied?: DueMark[] }[];
}
/** Where outside rules come from (From). */
export interface RuleSource {
  id: string;
  name: string;
  kind?: string;
}
/** A file in Files, as the document picker reads it. */
export interface DocFile {
  id: string;
  title?: string | null;
  name?: string | null;
  locator?: string | null;
  url?: string | null;
  library_kind?: string | null;
  archived?: boolean;
}
/** What POST /rules/read answers: the reading task, and whether screening held the document. */
export interface DocRead {
  task_id: string;
  existing?: boolean;
  screening?: string | null;
  truncated?: boolean;
  file?: { title?: string | null } | null;
}
export interface RuleHold {
  id: string;
  state: string;
  task_id?: string | null;
  author?: string;
  author_name?: string;
  created_at: string;
  rules: { id: string }[];
  outcome?: string;
  /** Who the held message or task was for. */
  to?: string[];
  operation?: string;
}
export interface Group {
  id: string;
  name: string;
  parent?: string;
  bots: string[];
  people: string[];
}

export type ReachMode = 'open' | 'nearby' | 'closed';
export type ReachBook = 'anyone' | 'nearby' | 'none';
export interface ReachValues {
  mode?: ReachMode | null;
  book?: ReachBook | null;
  chats_per_day?: number | null;
  tasks_per_day?: number | null;
}
export interface Reach {
  defaults: Required<ReachValues>;
  settings: (ReachValues & { party: string })[];
  effective: Record<string, ReachValues & { from?: Record<string, string | null> }>;
  may_edit: string[];
}

// ---------------------------------------------------------------- listening

export interface Match {
  id: string;
  source?: string | null;
  site?: string | null;
  url?: string | null;
  author?: string | null;
  title?: string | null;
  text?: string | null;
  posted_at?: string | null;
  created_at: string;
  status: string;
  tag?: string | null;
  /** The send that took it, once sending or sent: that run's task holds it. */
  run_id?: string | null;
}
export interface WatchSource {
  id: string;
  key?: string;
  kind: string;
  site?: string;
  target?: string;
  status?: string;
  last_read_at?: string | null;
}
