import type { IconName } from '@/kit/Icon';
import { condWords, type Cond, type FlowRule } from './board';
import type { Routine, Step, Trigger } from './types';
import { browserZone, cap, eventWords } from './words';

/* An automation's flow as the old Automations page drew it (ui/app/automations-flow.ts, automations-overview.ts): what
   starts it, its steps top to bottom, and a Paths step that forks into columns that never rejoin. Pure: the tables the
   list and the flow read, finding and placing a step anywhere in the tree, and a fresh step of each type. */

// ---------------------------------------------------------------- the list

/** What starts it, as the list groups it; Tico's own jobs are `system`. */
export const KINDS = ['message', 'task_received', 'schedule', 'webhook', 'event', 'due', 'system'] as const;
export const KIND_WORDS: Record<string, string> = {
  message: 'Message',
  task_received: 'Task received',
  schedule: 'Schedule',
  webhook: 'Webhook',
  event: 'Event',
  due: 'Due date',
  system: 'System job',
  listening: 'Listening',
};
export const KIND_ICONS: Record<string, IconName> = {
  message: 'mail',
  task_received: 'tasks',
  schedule: 'clock',
  webhook: 'link',
  event: 'spark',
  due: 'calendar',
  system: 'gear',
  listening: 'search',
};
export const kindOf = (r: Pick<Routine, 'kind'> & { trigger?: Trigger }) => (r.kind === 'system' ? 'system' : (r.trigger?.type ?? ''));

/** Its state, as the filters count it (automations-overview.ts auState). */
export type AutoState = 'on' | 'failed' | 'hold' | 'off';
export const STATE_WORDS: Record<AutoState, string> = { on: 'On', failed: 'Last run failed', hold: 'On hold', off: 'Off' };
export const stateOf = (r: Pick<Routine, 'enabled' | 'paused' | 'hold'> & { last_run?: { status?: string } | null }): AutoState =>
  r.enabled === false || r.paused ? 'off' : r.hold ? 'hold' : r.last_run?.status === 'failed' ? 'failed' : 'on';

/** The time ranges the counts and the chart cover, and the bucket each bar is. */
export const RANGES = ['5m', '1h', '24h', '7d'] as const;
export type Range = (typeof RANGES)[number];
export const RANGE_BUCKET: Record<Range, string> = { '5m': '30 s', '1h': '5 min', '24h': 'hour', '7d': '6 hours' };
export const RANGE_WORDS: Record<Range, string> = { '5m': 'Last 5 minutes', '1h': 'Last hour', '24h': 'Last 24 hours', '7d': 'Last 7 days' };
export const RANGE_SECONDS: Record<Range, number> = { '5m': 300, '1h': 3600, '24h': 86400, '7d': 604800 };

export const GROUPS = [
  ['trigger', 'Trigger'],
  ['owner', 'Owner'],
  ['none', 'None'],
] as const;
export type GroupBy = (typeof GROUPS)[number][0];
export const SORTS = [
  ['last', 'Last run'],
  ['fail', 'Most failures'],
  ['runs', 'Most runs'],
  ['cost', 'Cost'],
  ['name', 'Name'],
] as const;
export type SortBy = (typeof SORTS)[number][0];

/** Money as the old page wrote it: cents, and "<$0.01" for less. */
export const money = (n: number | null | undefined) => {
  const v = Number(n) || 0;
  return v > 0 && v < 0.005 ? '<$0.01' : `$${v.toFixed(2)}`;
};
/** How long a run took: "4s", "2m", "1h". */
export function took(from?: string | null, to?: string | null): string {
  if (!from || !to) return '';
  const s = (Date.parse(to) - Date.parse(from)) / 1000;
  if (!(s >= 0)) return '';
  return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
}
/** When it next runs: "in 19m", "in 11h", then the day. */
export function nextWords(at: string | null | undefined, now = Date.now()): string {
  if (!at) return '';
  const ms = Date.parse(at) - now;
  if (!(ms > 0)) return 'now';
  const m = Math.round(ms / 60000);
  if (m < 60) return `in ${Math.max(1, m)}m`;
  if (m < 36 * 60) return `in ${Math.round(m / 60)}h`;
  return new Date(at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// ---------------------------------------------------------------- steps

export const STEP_WORDS: Record<string, string> = {
  filter: 'Filter',
  decide: 'Decide',
  llm: 'LLM',
  find: 'Find',
  bot_turn: 'Agent turn',
  paths: 'Paths',
  action: 'Action',
};
export const STEP_TYPES = ['filter', 'decide', 'llm', 'find', 'bot_turn', 'paths', 'action'] as const;
export const ACTIONS: Record<string, string> = {
  archive: 'Archive',
  leave_for_you: 'Leave for you',
  draft_reply: 'Draft reply',
  send_reply: 'Send reply',
  create_task: 'Create task',
  label: 'Label',
  forward: 'Forward',
  wake_bot: 'Wake an agent',
  note: 'Note',
  post_update: 'Post update',
  call_webhook: 'Call a webhook',
  assign: 'Assign',
  unsubscribe: 'Unsubscribe',
};
export const FIND_SOURCES: Record<string, string> = { mail: 'Mail', tasks: 'Tasks', prs: 'Pull requests', docs: 'Docs' };
export const QUESTION_TYPES: Record<string, string> = { noul: 'Yes or no', choice: 'Choice', score: 'Score' };
/** Paths a fork holds, and how deep forks may nest (CONTRACT §2). */
export const MAX_PATHS = 10;
export const MAX_DEPTH = 3;

export interface Path {
  name?: string;
  when?: Cond[];
  steps?: Step[];
}
export interface Question {
  type?: string;
  instructions?: unknown;
  criteria?: string[] | Record<string, string>;
  [k: string]: unknown;
}
/** A step with the fields each type carries. */
export interface FlowStep extends Step {
  enabled?: boolean;
  rules?: FlowRule[];
  questions?: Record<string, Question>;
  set?: string;
  out?: string;
  as?: string;
  source?: string;
  query?: string;
  since?: string;
  limit?: number;
  do?: string;
  args?: Record<string, unknown>;
  paths?: Path[];
  otherwise?: { steps?: Step[] };
}

const forks = (s: FlowStep): { steps?: Step[] }[] => [...(s.paths ?? []), s.otherwise ?? { steps: [] }];

/** A step by id anywhere in the tree: the list it is in, where, and how deep in Paths that list is. */
export function findStep(steps: Step[] | undefined, id: string, depth = 0): { list: FlowStep[]; at: number; step: FlowStep; depth: number } | null {
  const list = (steps ?? []) as FlowStep[];
  for (let at = 0; at < list.length; at++) {
    const s = list[at]!;
    if (s.id === id) return { list, at, step: s, depth };
    if (s.type === 'paths') for (const p of forks(s)) {
      const hit = findStep(p.steps, id, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

/** A list of steps by its key: `root`, `<paths id>:<i>`, or `<paths id>:other`; made where it is missing. */
export function listAt(steps: Step[], key: string): { list: FlowStep[]; depth: number } | null {
  if (key === 'root') return { list: steps as FlowStep[], depth: 0 };
  const [sid = '', which = ''] = key.split(':');
  const hit = findStep(steps, sid);
  if (hit?.step.type !== 'paths') return null;
  const p = which === 'other' ? (hit.step.otherwise ??= { steps: [] }) : hit.step.paths?.[Number(which)];
  if (!p) return null;
  p.steps ??= [];
  return { list: p.steps as FlowStep[], depth: hit.depth + 1 };
}

/** The step types the "+" at this place offers: Paths only at the end of a list that has none, under the nesting limit. */
export function typesAt(steps: Step[], key: string, at: number): string[] {
  const c = listAt(structuredClone(steps), key);
  const paths = !!c && at === c.list.length && !c.list.some((s) => s.type === 'paths') && c.depth < MAX_DEPTH;
  return STEP_TYPES.filter((t) => t !== 'paths' || paths);
}

const newId = () => `s_${Math.random().toString(36).slice(2, 10)}`;
/** The next free output name for a step kind: d1, d2… for Decide, l1… for LLM. */
function nextOut(steps: Step[], prefix: string): string {
  const used = new Set<string>();
  const walk = (list: Step[] | undefined) =>
    ((list ?? []) as FlowStep[]).forEach((s) => {
      if (s.out) used.add(s.out);
      if (s.type === 'paths') forks(s).forEach((p) => walk(p.steps));
    });
  walk(steps);
  let n = 1;
  while (used.has(prefix + n)) n++;
  return prefix + n;
}

/** A fresh step of a type, as the old "+" made it. `me` is the person's id, `agent` the first agent they may pick. */
export function newStep(type: string, steps: Step[], me: string, agent: string): FlowStep {
  const s: FlowStep = { id: newId(), type, enabled: true };
  if (type === 'filter') s.rules = [{ when: [{ field: 'message.from', op: 'contains', value: '' }], match: 'all', then: [{ do: 'archive', args: {} }], stop: true }];
  if (type === 'decide') Object.assign(s, { questions: { question: { type: 'noul', instructions: '' } }, out: nextOut(steps, 'd') });
  if (type === 'llm') Object.assign(s, { as: `assistant:${me}`, prompt: '', out: nextOut(steps, 'l') });
  if (type === 'find') Object.assign(s, { source: 'mail', query: '', since: 'last_run', limit: 20 });
  if (type === 'bot_turn') Object.assign(s, { bot: agent, prompt: '' });
  if (type === 'paths') Object.assign(s, { paths: [{ name: 'Path 1', when: [], steps: [] }], otherwise: { steps: [] } });
  if (type === 'action') Object.assign(s, { do: 'archive', args: {} });
  return s;
}

/** Steps with one step put in place: added at `insert`, else replacing the step with its id. */
export function putStep(steps: Step[], step: FlowStep, insert?: { key: string; at: number }): Step[] {
  const next = structuredClone(steps);
  if (insert) listAt(next, insert.key)?.list.splice(insert.at, 0, step);
  else {
    const hit = findStep(next, step.id);
    if (hit) hit.list[hit.at] = step;
  }
  return clean(next);
}
export function dropStep(steps: Step[], id: string): Step[] {
  const next = structuredClone(steps);
  const hit = findStep(next, id);
  if (hit) hit.list.splice(hit.at, 1);
  return next;
}
export function stepOn(steps: Step[], id: string, on: boolean): Step[] {
  const next = structuredClone(steps);
  const hit = findStep(next, id);
  if (hit) hit.step.enabled = on;
  return next;
}
/** What is written back: a Decide step on a saved set sends the set only (its questions came along read-only); a rule's
    stop follows its own next. */
function clean(steps: Step[]): Step[] {
  for (const s of steps as FlowStep[]) {
    if (s.type === 'decide' && s.set) delete s.questions;
    if (s.type === 'filter') for (const r of s.rules ?? []) if ('next' in r && 'stop' in r) r.stop = r.next === 'end';
    if (s.type === 'paths') forks(s).forEach((p) => clean(p.steps ?? []));
  }
  return steps;
}

// ---------------------------------------------------------------- words

/** "d1.bucket" reads "bucket"; a trigger's "message.from" reads "from". */
export const fieldWords = (f: unknown) => (String(f ?? '').includes('.') ? String(f).split('.').slice(1).join('.') : String(f ?? ''));
const pct = (p: unknown) => `${Math.round(Number(p) * 100)}%`;
const SHORT_OPS: Record<string, string> = { is: '=', is_not: '≠', '>=': '≥', '<=': '≤', contains: 'has', matches: 'matches' };
/** A condition as a chip: "amount ≥ 80", "damaged = yes", "subject has “invoice”". */
export function condChip(c: Cond | null | undefined): string {
  if (!c) return '';
  const f = fieldWords(c.field);
  const v = c.value;
  const min = c.min != null && c.min !== '' ? ` ≥ ${pct(c.min)}` : '';
  if (c.op === 'contains') return `${f} has “${String(v ?? '')}”`;
  if (c.op === 'matches') return `${f} matches /${String(v ?? '')}/`;
  if ((c.field === 'event' || c.field === 'event.name') && typeof v === 'string' && v && (!c.op || c.op === 'is' || c.op === 'is_not'))
    return (c.op === 'is_not' ? '≠ ' : '') + eventWords(v);
  const shown = v === true ? 'yes' : v === false ? 'no' : String(v ?? '');
  return `${f} ${SHORT_OPS[c.op ?? 'is'] ?? c.op ?? '='} ${shown}${min}`.trim();
}
export const condsChip = (r: FlowRule) => ((r.when ?? []).length ? (r.when ?? []).map(condChip).join(r.match === 'any' ? ' or ' : ' and ') : 'Always');
export { condWords };

/** A step's title: its card's first line. `name` turns an owner id into a name. */
export function stepTitle(s: FlowStep, name: (id: string) => string): string {
  if (s.type === 'filter') {
    const n = (s.rules ?? []).length;
    return `${n} ${n === 1 ? 'rule' : 'rules'}`;
  }
  if (s.type === 'decide') return s.set ? String(s.set).replace(/@(\d+)$/, ' v$1') : 'Custom questions';
  if (s.type === 'llm') return name(s.as ?? '') || 'LLM';
  if (s.type === 'bot_turn') return name(s.bot ? (s.bot.includes(':') ? s.bot : `bot:${s.bot}`) : '') || 'An agent';
  if (s.type === 'find') return FIND_SOURCES[s.source ?? ''] ?? s.source ?? 'Find';
  if (s.type === 'action') return ACTIONS[s.do ?? ''] ?? s.do ?? 'Action';
  return STEP_WORDS[s.type] ?? cap(s.type);
}

/** A Decide step's question ids, inline or its set's. */
export const questionIds = (s: FlowStep) => Object.keys(s.questions ?? {});

/** An action step's who or what, for its card: "to Ada", a label, a webhook. */
export function actionWho(s: FlowStep, name: (id: string) => string): string {
  const a = s.args ?? {};
  const who = (a.owner ?? a.to ?? a.bot) as string | undefined;
  if (who) return `to ${name(String(who).includes(':') ? String(who) : `bot:${String(who)}`) || String(who)}`;
  if (s.do === 'label') return String(a.name ?? a.label ?? a.tag ?? '');
  if (s.do === 'call_webhook') return String(a.webhook ?? '');
  return String(a.title ?? a.name ?? '');
}

/** A Filter rule's actions in words. */
export const thenWords = (then: FlowRule['then']) => (then ?? []).map((x) => (typeof x === 'string' ? x : (x.do ?? ''))).map((d) => ACTIONS[d] ?? d);

// ---------------------------------------------------------------- triggers

/** The triggers a new automation and the trigger editor offer; the others are made where they come from. */
export const WHEN = [
  ['schedule', 'On a schedule'],
  ['event', 'When something happens'],
  ['due', 'Before a due date'],
] as const;
export type When = (typeof WHEN)[number][0];
export const REPEATS = [
  ['once', 'Once'],
  ['weekly', 'Weekly'],
  ['monthly', 'Monthly'],
  ['quarterly', 'Quarterly'],
  ['yearly', 'Yearly'],
] as const;

/** A local day `n` days from today, YYYY-MM-DD. */
function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
/** A fresh trigger of each kind: weekdays at 9 in the person's zone, a task created, or a due date a month out. */
export function newTrigger(type: string): Trigger {
  if (type === 'event') return { type, name: 'task.created' };
  if (type === 'due') return { type, first_due: inDays(30), repeats: 'yearly', open_days: 14, tz: browserZone() };
  return { type: 'schedule', cron: '0 9 * * 1-5', tz: browserZone() };
}

/** An event in a few words, the dotted name only ever a tooltip (old AU_EVENTS). */
export const EVENT_SHORT: Record<string, string> = {
  'task.created': 'Task created',
  'task.assigned': 'Task assigned',
  'task.step_changed': 'Task stage changed',
  'task.moved': 'Task changed pipeline',
  'task.commented': 'Task comment',
  'task.done': 'Task finished',
  'task.closed': 'Task closed',
  'task.reopened': 'Task reopened',
  'thread.created': 'Thread started',
  'message.created': 'Message sent',
  'message.held': 'Message held',
  'bot.run_failed': 'Agent run failed',
  'bot.held': 'Agent held',
  'bot.released': 'Agent released',
  'org_rule.retired': 'Rule retired',
  'rules.blocked': 'Rule blocked',
  'rules.escalated': 'Rule escalated',
  'rules.warned': 'Rule warned',
  'meeting.ready': 'Meeting ready',
  'strategy.published': 'Strategy published',
  'brand.published': 'Brand published',
  'inbound.received': 'Message received',
  'webhook.received': 'Webhook received',
  'tico.builtin.wakes': 'Every event',
  'tico.builtin.health': 'Health check',
};
export function eventShort(name: string | undefined): string {
  const n = name ?? '';
  if (EVENT_SHORT[n]) return EVENT_SHORT[n];
  if (n.startsWith('tico.builtin.job.')) return 'System job';
  return cap(n.replace(/^(tico\.builtin\.|tico\.|custom\.)/, '').replace(/[._*-]+/g, ' ').trim()) || 'Event';
}

/** Where its work comes from, in a few words: the list's trigger cell and the trigger card's chip (auSource). */
export function sourceWords(t: Trigger | undefined, names: { who?: (id: string) => string; hook?: (id: string) => string | undefined; schedule?: (cron: string) => string } = {}): string {
  if (!t) return '';
  if (t.type === 'message') return t.inbox ?? '';
  if (t.type === 'task_received') return t.to ? `Tasks to ${names.who?.(t.to) || t.to.replace(/^\w+:/, '')}` : 'Tasks';
  if (t.type === 'schedule') return t.cron ? (names.schedule?.(t.cron) ?? t.cron) : '';
  if (t.type === 'webhook') return (t.id && names.hook?.(t.id)) || t.name || t.id || '';
  if (t.type === 'event') return t.name ? eventShort(t.name) : '';
  if (t.type === 'due') {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t.first_due ?? '');
    const day = m ? new Date(+m[1]!, +m[2]! - 1, +m[3]!).toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(t.repeats === 'once' ? { year: 'numeric' } : {}) }) : '';
    return [`Due ${day}`, t.repeats, `${t.open_days ?? 14}d before`].filter(Boolean).join(' · ');
  }
  return '';
}
