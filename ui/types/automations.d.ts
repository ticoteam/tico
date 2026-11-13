// The page state of the Automations screen: AU, the `let` in ui/app/automations.ts. It is set to a new object
// when the page is entered (pageAutomations, literal plus auOverviewState() in automations-overview.js) and to null when
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
}


/** The overview's filters (automations-overview.js): the trigger kinds picked, and the words. */
interface AuFilters {
  trig: Set<string>;
  status: string;
  team: string;
  owner: string;
  q: string;
}

/** The overview's stats for one range (automations-overview.js auStatsLoad). */
interface AuStats {
  range: AuRange;
  at: number;
  error: string;
  n: number;
  per: Map<string, import('./api').AutomationStats['automations'][number]>;
}

/** The overview's Runs tab: every run across the range, read a page at a time (automations-overview.js auAllLoad). */
interface AuAll {
  key: string;
  rows: import('./api').AutomationOverviewRuns['runs'];
  next: string | null;
  loading: boolean;
  error: string;
  done?: boolean;
}

/* An automation's flow as the page reads and writes it (CONTRACT §2; automations-flow.ts draws it, automations-editors.js
   edits it). api.d.ts types steps, trigger and destinations as plain records; these are the fields the page uses. */

/** An action's arguments as the hub keeps them: text mostly, but a post_update's `to` is a list and a webhook's `data` an object. */
type AuArgs = Record<string, unknown>;
/** A rule's or a destination's action: {do, args}; older copies said just the name. */
type AuThen = string | { do?: string; args?: AuArgs };
/** A condition: a field, a check and a value; a yes/no or a choice can carry a minimum (0..1; '' or none for no minimum). */
interface AuCond { field?: string; op?: string; value?: unknown; min?: number | string | null | undefined }
/** A Filter rule: its conditions, its actions, the destination it goes to, and what runs after it. */
interface AuRule { when?: AuCond[]; match?: string; then?: AuThen[]; to?: string | undefined; stop?: boolean; next?: string }
/** A Decide question: yes/no ('noul') or a choice over criteria (a list of ids, or ids to words). */
interface AuQuestion { type?: string; instructions?: unknown; criteria?: string[] | Record<string, string> }
/** A saved question set (GET /v2/automations/question-sets), whose questions a Decide step on it reads. */
type AuQuestionSet = import('./api').QuestionSets['sets'][number];
/** One column of a Paths step, or its Otherwise. */
interface AuPath { name?: string; when?: AuCond[]; steps?: AuStep[] }
/** An llm step's choice of destinations: a draft or a reply turn that picks one. */
interface AuChoose { mode?: string; to?: string[]; default?: string }
/** A step: its id, kind and switch, and each kind's own fields (filter, decide, llm, find, bot_turn, paths, action). */
interface AuStep {
  id: string;
  type: string;
  enabled?: boolean;
  out?: string;
  rules?: AuRule[];
  questions?: Record<string, AuQuestion>;
  set?: string;
  level?: string;
  as?: string;
  prompt?: string;
  choose?: AuChoose;
  bot?: string;
  source?: string;
  query?: string;
  since?: string;
  limit?: number;
  paths?: AuPath[];
  otherwise?: AuPath;
  /** A Paths step in a listening: what each part of the send is, a tag's items together or one item. */
  by?: string;
  do?: string;
  args?: AuArgs;
}
/** A destination of a board flow: where a rule, a question or the LLM sends a message, and what it does there. */
interface AuDest { id: string; name?: string; color?: string | undefined; then?: AuThen[] }
/** What starts an automation: its kind and that kind's fields. */
interface AuTrigger { type?: string; filters?: AuCond[]; name?: string; inbox?: string; [field: string]: unknown }
/** A flow: its steps, its destinations (a board's), and its trigger. */
interface AuFlow { steps?: AuStep[]; destinations?: AuDest[] | null; trigger?: (AuTrigger & Partial<AuFlowTrigger>) | undefined }
/** An automation as the page holds it (AU.cur): a row (AuRow) with steps and trigger always present (automations.ts
    auNormal), and the fields a built-in or a system job carries. */
type AuAutomation = AuRow & {
  steps: AuStep[];
  trigger: AuTrigger & Partial<AuFlowTrigger>;
  description?: string;
  owner?: string;
  controls?: { schedule?: string };
};
/** An automation as the page keeps it (automations.ts auNormal): a list row or the open one. Its trigger is a flow's
 *  (AuFlowTrigger, automations-editors.ts), each field present on its own kind; a built-in or read-only row says so. */
type AuRow = Omit<import('./api').Automation, 'trigger' | 'hold' | 'last_run' | 'steps' | 'destinations'> & {
  trigger?: Partial<AuFlowTrigger>; builtin?: boolean; readonly?: boolean;
  /** The flow (AuStep, AuDest: automations-editors.ts); api.d.ts has each as a bare record. */
  steps?: AuStep[]; destinations?: AuDest[] | null;
  /** The last run as the list reads it: its status and when it started (api.d.ts has it as a bare record). */
  last_run?: { status?: string | null; started_at?: string | null; [field: string]: unknown } | null;
  /** The run guard's hold; null once a write says it is gone (automations.ts auHoldAct). */
  hold?: import('./api').Automation['hold'] | null;
  /** How many runs Release or Drop moved (POST .../held); the page drops it before keeping the row. */
  moved?: number;
};

interface AutomationsState {
  /** True on the Listening page (#/listening, ui/app/listening.ts): the list is the listenings and nothing else. */
  lst?: boolean;
  /** Every listening's sources and their health (GET /v2/listening/sources), by automation id; null until read. */
  lstSources?: Map<string, LstSourceRow[]> | null;
  /** A push source's address, shown once after the save that made it (source id to address). */
  lstAddr?: Record<string, string>;
  // The list and its teams. automations.js auLoadList sets list, groups and listError; a row is an automation, or one
  // of the pseudo-rows the page adds (Maintenance, the Heartbeat, Sys jobs, Incoming webhooks), built to the same shape.
  /** Every automation the viewer may see, pseudo-rows first. null until the first read. */
  list: AuRow[] | null;
  /** The message of a failed list read; '' when the list is good (automations.js auLoadList). */
  listError?: string;
  /** Teams from /v2/groups (automations.js auLoadList). */
  groups: Array<import('./api').Group>;
  /** Question sets for the decide step (automations-editors.ts); [] until read. */
  sets: AuSet[] | null;
  /** Templates for Apply template (automations-new.js). */
  templates?: import('./api').AutomationTemplates['templates'];

  // The visit: the route's automation and view (automations.js pageAutomations, auRouteParts).
  /** The open automation's id; '' on the list. */
  id: string;
  /** The open automation's tab: Flow or Runs (automations.js auRouteParts). */
  view: AuView;
  /** The open automation as the server sent it, with steps and trigger present (automations.js auNormal). null until read. */
  cur: AuAutomation | null;
  /** The editor open in the side panel (automations-editors.ts auEdOpen): AuEd, one type per kind. */
  ed: AuEd | null;
  /** The ⋯ or + menu open over the page (automations.js auMenuOpen). */
  menu: { kind: 'add' | 'more'; at: string } | null;
  /** #au-root, set once in automations.js auShell. */
  root: HTMLElement | null;
  /** The open automation's run log (automations-runs.js auRunsLoad); null until the Runs tab is loaded. */
  runs: AuRunList<import('./api').AutomationRun> | null;
  /** The last run, for the flow's bars; set undefined on a new visit (automations.js), null or a run (automations-editors.ts, runs). */
  lastRun?: import('./api').AutomationRun | null | undefined;
  /** A run to open once its list is read (automations-overview.js, automations-editors.ts; read in automations-runs.js). */
  openRun?: string | null;
  /** The flow's branch resize watcher (automations-flow.js); automations.js disconnects it on the Runs tab. */
  branchRO?: ResizeObserver;

  // Maintenance (automations-maintenance.js), its own screen.
  /** /v2/maintenance (automations.js auLoadList). */
  maint: MaintView | null;
  /** The message of a failed maintenance read or save ('' when none). */
  maintError: string;
  /** Bumped on each save or read; a read that started before a save keeps the old values. */
  mtGen: number;
  /** Maintenance runs, read a page at a time (automations-maintenance.js). */
  mtRuns: MaintRunList | null;
  /** Maintenance run rows the viewer opened. */
  mtOpen: Set<string>;
  /** Maintenance is saving (automations-maintenance.js). */
  mtBusy?: boolean;
  /** Run now was sent and has not answered yet (automations-maintenance.js). */
  mtStarting?: boolean;
  /** The timer that follows a live Maintenance run (automations-maintenance.js). */
  mtFollow?: number | null;
  /** A run to open once the Maintenance runs load (automations-overview.js sets it from a mnt_ row). */
  mtOpenRun?: string | null;
  /** The view the Maintenance pane was last drawn for (automations-maintenance.js). */
  mtPaneView?: AuView;

  // The Heartbeat (automations-heartbeat.js), its own screen.
  /** /v2/heartbeat?limit=1 (automations.js auLoadList). */
  heart: import('./api').HeartbeatView | null;
  /** The message of a failed Heartbeat read or save ('' when none). */
  heartError: string;
  /** Bumped on each save or read (automations-heartbeat.js). */
  hbGen: number;
  /** The beats log, read a page at a time (automations-heartbeat.js). */
  hbLog: AuRunList<import('./api').HeartbeatBeat> | null;
  /** The once-a-minute refresh's timer (automations-heartbeat.js); null when it is not running. */
  hbPoll: number | null;
  /** The Heartbeat is saving (automations-heartbeat.js). */
  hbBusy?: boolean;
  /** The view the Heartbeat pane was last drawn for (automations-heartbeat.js). */
  hbPaneView?: AuView;

  // The overview (automations-overview.js auOverviewState and the list's controls).
  /** The time range, kept in localStorage (auRangeSaved). */
  range: string;
  /** The overview's tab: the list or the Runs tab. */
  tab: string;
  /** The filters (AuFilters). */
  f: AuFilters;
  /** The grouping the table shows, e.g. 'trigger' (automations-overview.js). */
  group: string;
  /** The sort the table shows (automations-overview.js). */
  sort: string;
  /** The run-status filter on the Runs tab ('' for all). */
  rstatus: string;
  /** Groups the table has closed (automations-overview.js). */
  closed: Set<string>;
  /** Filter sections open on a phone (automations-overview.js auFacetsDraw). */
  fopen: Set<string>;
  /** The overview's stats for `range`; null until read (automations-overview.js auStatsLoad). */
  stats: AuStats | null;
  /** The Runs tab's rows for the current filter; null until read (automations-overview.js auAllLoad). */
  all: AuAll | null;
  /** The scroll position each tab was left at (automations-overview.js). */
  scroll: Record<string, number>;
  /** The optional columns the table keeps, decided once the range's stats arrive; null until then: all (auTableHTML). */
  cols: AuColKey[] | null;
  /** The timer that re-reads the Runs tab after a change (automations-overview.js). */
  allSoon?: number;
}
