// The page state of the Automations screen: AU, the `let` in ui/app/automations.ts. It is set to a new object
// when the page is entered (pageAutomations, literal plus auOverviewState() in automations-overview.ts) and to null when
// it is left (automationsStop). A few fields are added later by the file that first needs them; those are optional.
// AU is reassigned, so a TypeScript reader calls automationsState() (ui/app/automations-state.ts) each time and narrows
// the result; it never keeps the object across an await. Server data takes its api.d.ts type; a view with no type
// there is Record<string, unknown>. No file here is a module, so every name is global.

type AuRange = '5m' | '1h' | '24h' | '7d';
type AuView = 'flow' | 'runs';

/** A run list read a page at a time (runs, Maintenance runs, the Heartbeat's beats). */
interface AuRunList<R> {
  rows: R[];
  next: string | null;
  open: Set<string>;
  loading: boolean;
  error?: string;
  id?: string;
  /** How many extra pages a run tapped on the overview is read through (automations-maintenance.ts auMaintRunsLoad). */
  seek?: number;
}

/** A Decide step's question as its editor row: its name (id), what it asks and its choice options (automations-editors.ts auDecideQs). */
interface AuEdQuestion extends AuQuestion {
  id: string;
  ask: string;
  opts: string;
}
/** A board pop-up's copy of its part: a rule, a question, a destination or the LLM (automations-editors.ts). Each field is the part's own. */
interface AuEdItem {
  id?: string;
  name?: string;
  color?: string | null;
  when?: AuCond[] | null;
  match?: string;
  then?: AuThen[] | null;
  next?: string;
  to?: string | string[] | null;
  text?: string;
  min?: number | string;
  mode?: string;
  prompt?: string | undefined;
  default?: string;
  as?: string;
}
/** A run model the Runs-on field lists (task-modal.js TASK_RUN_ROWS): its id, its name, and the harnesses and efforts it offers. */
interface AuRunModel {
  id: string;
  label?: string;
  harnesses?: string[];
  efforts?: string[];
  default_effort?: string | null;
}
/** A version as the versions list sends it (GET .../versions). */
type AuVersion = import('./api').AutomationVersions['versions'][number];
/** A flow as the page reads one: a version's flow, or the open automation's (the fields the editors and the diff read). */
interface AuFlowData {
  trigger?: AuTrigger | null;
  steps?: AuStep[] | null;
  destinations?: AuDest[] | null;
}
/** A pending version (a bot's proposal) with its diff open or shut (automations-editors.ts auPropsHTML). */
interface AuEdProp extends Omit<AuVersion, 'flow'> {
  flow: AuFlowData;
  diff: boolean;
}

/** The editor open in the side panel (automations-editors.ts auEdOpen): its kind, and the fields each kind uses. */
interface AuEditor {
  kind: 'board' | 'run' | 'spend' | 'step' | 'trigger' | 'versions';
  /** A step: its copy, its id, the path it opened on, and where a new step goes. */
  step?: AuStep;
  id?: string | undefined;
  path?: string | null;
  insert?: {key: string; index: number};
  /** A trigger: its copy, the saved one (kept while another kind is picked), and whether its event is Custom. */
  trigger?: AuTrigger;
  saved?: AuTrigger;
  evCustom?: boolean | undefined;
  /** A Decide step: its questions as rows, and whether they are the step's own (Custom). */
  qs?: AuEdQuestion[];
  custom?: boolean;
  /** The spend limit's field: the server's cap as it sent it, or what the person typed. */
  value?: unknown;
  /** A test run's sample input. */
  text?: string;
  /** A board pop-up: the part (rule, q, dest or llm), its index (-1 for a new one) and its copy. */
  part?: string;
  i?: number;
  item?: AuEdItem;
  /** Versions: the list, the proposals, and the read's error. */
  rows?: AuVersion[] | null;
  props?: AuEdProp[];
  error?: string;
  [field: string]: unknown;
}

/** The overview's filters (automations-overview.ts): the trigger kinds picked, and the words. */
interface AuFilters {
  trig: Set<string>;
  status: string;
  team: string;
  owner: string;
  q: string;
}

/** The overview's stats for one range (automations-overview.ts auStatsLoad). */
interface AuStats {
  range: AuRange;
  at: number;
  error: string;
  n: number;
  per: Map<string, import('./api').AutomationStats['automations'][number]>;
}

/** The overview's Runs tab: every run across the range, read a page at a time (automations-overview.ts auAllLoad). */
interface AuAll {
  key: string;
  rows: import('./api').AutomationOverviewRuns['runs'];
  next: string | null;
  loading: boolean;
  error: string;
  done?: boolean;
}

/** The trigger as the page reads it: the fields of each trigger type (automations-editors.ts, automations.ts auSource). */
type AuTrigger = {
  type?: string;
  name?: string;
  id?: string;
  inbox?: string;
  to?: string;
  cron?: string;
  destination?: string;
  first_due?: string;
  repeats?: string;
  open_days?: number;
  tz?: string;
  /** The trigger's conditions (automations-flow.ts auTriggerCard). */
  filters?: AuCond[] | null;
  /** A due routine's rule, which its sample input names (automations-editors.ts auSample). */
  rule_due_id?: string;
};

/** The last run as the list reads it (automations-overview.ts; automations-heartbeat.ts auHeartRow builds one). */
type AuLastRun = {id?: string | undefined; started_at?: string | undefined; status: string};

/** Learn's beta numbers as GET /api/v2/maintenance sends them (automations-maintenance.ts auMaintStatsHTML; backend/learnings.py beta_stats). */
interface AuLearnStats {
  days?: number | null;
  runs?: number | null;
  items_read?: number | null;
  items_sent?: Record<string, number | null> | null;
  saved?: number | null;
  dropped?: number | null;
  cost_usd?: number | null;
}

/** One Maintenance step as GET /api/v2/maintenance sends it (backend/maintenance.py); api.d.ts types that answer unknown. */
interface AuMaintStep {
  key: string;
  name: string;
  enabled?: boolean;
  fixed?: boolean;
  frequency?: string;
  custom_every?: number | null;
  custom_unit?: string | null;
  max_bots?: number | null;
  last_ran_at?: string | null;
  consent?: boolean;
  stats?: AuLearnStats | null;
}

/** One step's line in a Maintenance run (automations-maintenance.ts auMaintRunsHTML). */
interface AuMaintRunStep {
  step: string;
  status: string;
  reason?: string | null;
  checked?: number | null;
  found?: number | null;
  woke?: number | null;
  cost_usd?: number | string | null;
}

/** One Maintenance run as the run log reads it (GET /api/v2/maintenance/runs). */
interface AuMaintRun {
  id: string;
  started_at?: string;
  finished_at?: string | null;
  ok?: boolean;
  trigger?: string;
  step?: string | null;
  result?: string | null;
  cost_usd?: number | string | null;
  steps?: AuMaintRunStep[] | null;
}

/** Maintenance as GET /api/v2/maintenance sends it (automations-maintenance.ts). */
interface AuMaint {
  settings: {paused?: boolean; schedule?: string; monthly_cap_usd?: number | null; monthly_cap_usd_in_effect?: number | null};
  steps: AuMaintStep[];
  can_change?: boolean;
  live_run_id?: string | null;
  last_run?: AuMaintRun | null;
  next_run_at?: string | null;
}

/** One step's change in a Maintenance save (automations-maintenance.ts auMaintStepSave). */
interface AuMaintStepChange {
  key: string;
  enabled?: boolean;
  frequency?: string;
  custom_every?: number;
  custom_unit?: string;
  max_bots?: number;
}

/** The body of a Maintenance save: the fields the page changes (automations-maintenance.ts auMaintSave). */
interface AuMaintSave {
  paused?: boolean;
  schedule?: string;
  monthly_cap_usd?: number;
  steps?: AuMaintStepChange[];
}

/** The answer to Run now (POST /api/v2/maintenance/run). */
interface AuMaintRunReply {
  started?: boolean;
  run?: AuMaintRun | null;
}

/** A condition: one check on a field, with a minimum for a model's answer (automations-flow.ts auCondText). */
interface AuCond {
  field?: string;
  op?: string;
  value?: unknown;
  min?: number | string | null;
}

/** An action in a chain: a bare name in older copies, or {do, args} (CONTRACT §2; automations-flow.ts auThenDo). */
interface AuAct {
  do?: string | undefined;
  args?: Record<string, unknown> | null;
}
type AuThen = string | AuAct;

/** A Filter rule: its conditions, how they match, its actions, and where it goes next (automations-flow.ts). */
interface AuRule {
  when?: AuCond[] | null;
  match?: string;
  then?: AuThen[] | null;
  to?: string | null;
  next?: string;
  stop?: boolean;
}

/** A Decide question: yes/no (noul) or a choice over criteria (automations-flow.ts auBoardShape). */
interface AuQuestion {
  type?: string;
  /** The question's text: a string, or the structured instructions the server keeps (automations-editors.ts auAskText). */
  instructions?: unknown;
  criteria?: string[] | Record<string, string> | null;
  [field: string]: unknown;
}

/** A Paths column, or the Otherwise column (automations-flow.ts auPathsHTML). */
interface AuPath {
  name?: string;
  match?: string;
  when?: AuCond[] | null;
  steps?: AuStep[] | null;
}

/** An llm step's choice: its mode, the destinations it may pick, and the unsure one (automations-flow.ts auBoardShape). */
interface AuChoose {
  mode?: string;
  to?: string[];
  default?: string;
}

/** A step as the page reads it. Its type says which fields it has (automations-flow.ts auNewStep). */
interface AuStep {
  id: string;
  type: string;
  enabled?: boolean;
  out?: string;
  /** filter */
  rules?: AuRule[] | null;
  /** decide: a saved set, or its own questions */
  set?: string;
  questions?: Record<string, AuQuestion> | null;
  level?: string;
  /** llm and bot_turn */
  as?: string;
  bot?: string;
  prompt?: string;
  choose?: AuChoose | null;
  /** find */
  source?: string;
  query?: string;
  since?: string;
  limit?: number;
  /** paths */
  paths?: AuPath[] | null;
  otherwise?: AuPath | null;
  /** action */
  do?: string;
  args?: Record<string, unknown> | null;
  /** bot_turn: the model, harness and effort the turn runs on (automations-editors.ts auBotRunsFields). */
  model?: string;
  harness?: string;
  effort?: string;
}

/** A destination: where a flow sends a message or task, and the actions it runs there (automations-flow.ts). */
interface AuDest {
  id: string;
  name?: string;
  color?: string | null;
  then?: AuThen[] | null;
}

/** An automation as the page reads it: the generated Automation, plus fields the server sends that the schema does not declare yet. */
type AuAutomation = Omit<import('./api').Automation, 'trigger' | 'hold' | 'last_run' | 'steps' | 'destinations'> & {
  trigger?: AuTrigger;
  hold?: NonNullable<import('./api').Automation['hold']> | null;
  last_run?: AuLastRun | null;
  /** The flow's steps as automations-flow.ts reads them (the generated schema sends plain objects). */
  steps?: AuStep[];
  /** The flow's destinations (automations-flow.ts auBoardShape). */
  destinations?: AuDest[] | null;
  /** The team the row belongs to (automations-overview.ts auTeam). */
  group?: string;
  /** The owner as the server sends it when owner_id is not set (automations-new.ts auApplyTemplate). */
  owner?: string;
  /** A system job's own schedule (automations-overview.ts auTrigLine). */
  controls?: {schedule?: string};
  builtin?: boolean;
  readonly?: boolean;
  /** A built-in row's one-line description (automations-heartbeat.ts auHeartRow). */
  description?: string;
  /** Set on an answer that moved the automation; the page drops it before use (automations.ts auHoldAct). */
  moved?: boolean;
};

interface AutomationsState {
  // The list and its teams. automations.ts auLoadList sets list, groups and listError; a row is an automation, or one
  // of the pseudo-rows the page adds (Maintenance, the Heartbeat, Sys jobs, Incoming webhooks), built to the same shape.
  /** Every automation the viewer may see, pseudo-rows first. null until the first read. */
  /** undefined when a kept copy from S.lastRead has no list (automations.ts pageAutomations). */
  list: AuAutomation[] | null | undefined;
  /** The message of a failed list read; '' when the list is good (automations.ts auLoadList). */
  listError?: string;
  /** Teams from /v2/groups (automations.ts auLoadList). */
  groups: Array<import('./api').Group>;
  /** Question sets for the decide step (automations-editors.ts); [] until read. */
  sets: import('./api').QuestionSets['sets'] | null;
  /** Templates for Apply template (automations-new.ts). */
  templates?: import('./api').AutomationTemplates['templates'];

  // The visit: the route's automation and view (automations.ts pageAutomations, auRouteParts).
  /** The open automation's id; '' on the list. */
  id: string;
  /** The open automation's tab: Flow or Runs (automations.ts auRouteParts). */
  view: AuView;
  /** The open automation as the server sent it, with steps and trigger present (automations.ts auNormal). null until read. */
  cur: AuAutomation | null;
  /** The editor open in the side panel (automations-editors.ts auEdOpen). */
  ed: AuEditor | null;
  /** The ⋯ or + menu open over the page (automations.ts auMenuOpen). */
  menu: { kind: 'add' | 'more'; at: string } | null;
  /** #au-root, set once in automations.ts auShell. */
  root: HTMLElement;
  /** The open automation's run log (automations-runs.ts auRunsLoad); null until the Runs tab is loaded. */
  runs: AuRunList<import('./api').AutomationRun> | null;
  /** The last run, for the flow's bars; set undefined on a new visit (automations.ts), null or a run (automations-editors.ts, runs). */
  lastRun?: import('./api').AutomationRun | null | undefined;
  /** A run to open once its list is read (automations-overview.ts, automations-editors.ts; read in automations-runs.ts). */
  openRun?: string | null;
  /** The flow's branch resize watcher (automations-flow.ts); automations.ts disconnects it on the Runs tab. */
  branchRO?: ResizeObserver;

  // Maintenance (automations-maintenance.ts), its own screen.
  /** /v2/maintenance (automations.ts auLoadList). */
  maint: AuMaint | null;
  /** The message of a failed maintenance read or save ('' when none). */
  maintError: string;
  /** Bumped on each save or read; a read that started before a save keeps the old values. */
  mtGen: number;
  /** Maintenance runs, read a page at a time (automations-maintenance.ts). */
  mtRuns: AuRunList<AuMaintRun> | null;
  /** Maintenance run rows the viewer opened. */
  mtOpen: Set<string>;
  /** Maintenance is saving (automations-maintenance.ts). */
  mtBusy?: boolean;
  /** Run now was sent and has not answered yet (automations-maintenance.ts). */
  mtStarting?: boolean;
  /** The timer that follows a live Maintenance run (automations-maintenance.ts). */
  mtFollow?: number | null;
  /** A run to open once the Maintenance runs load (automations-overview.ts sets it from a mnt_ row). */
  mtOpenRun?: string | null;
  /** The view the Maintenance pane was last drawn for (automations-maintenance.ts). */
  mtPaneView?: AuView;

  // The Heartbeat (automations-heartbeat.ts), its own screen.
  /** /v2/heartbeat?limit=1 (automations.ts auLoadList). */
  heart: import('./api').HeartbeatView | null;
  /** The message of a failed Heartbeat read or save ('' when none). */
  heartError: string;
  /** Bumped on each save or read (automations-heartbeat.ts). */
  hbGen: number;
  /** The beats log, read a page at a time (automations-heartbeat.ts). */
  hbLog: AuRunList<import('./api').HeartbeatBeat> | null;
  /** The once-a-minute refresh's timer (automations-heartbeat.ts); null when it is not running. */
  hbPoll: number | null;
  /** The Heartbeat is saving (automations-heartbeat.ts). */
  hbBusy?: boolean;
  /** The view the Heartbeat pane was last drawn for (automations-heartbeat.ts). */
  hbPaneView?: AuView;

  // The overview (automations-overview.ts auOverviewState and the list's controls).
  /** The time range, kept in localStorage (auRangeSaved). */
  range: AuRange;
  /** The overview's tab: the list or the Runs tab. */
  tab: 'list' | 'runs';
  /** The filters (AuFilters). */
  f: AuFilters;
  /** The grouping the table shows, e.g. 'trigger' (automations-overview.ts). */
  group: string;
  /** The sort the table shows (automations-overview.ts). */
  sort: string;
  /** The run-status filter on the Runs tab ('' for all). */
  rstatus: string;
  /** Groups the table has closed (automations-overview.ts). */
  closed: Set<string>;
  /** Filter sections open on a phone (automations-overview.ts auFacetsDraw). */
  fopen: Set<string>;
  /** The overview's stats for `range`; null until read (automations-overview.ts auStatsLoad). */
  stats: AuStats | null;
  /** The Runs tab's rows for the current filter; null until read (automations-overview.ts auAllLoad). */
  all: AuAll | null;
  /** The scroll position each tab was left at (automations-overview.ts). */
  scroll: { list: number; runs: number };
  /** The timer that re-reads the Runs tab after a change (automations-overview.ts). */
  allSoon?: number;
}
