import type { Routine, Step, Trigger } from './types';

/* The board (ui/app/automations-flow.ts auBoardShape, auBoardFlow; automations-new.ts auApplyFlow): a flow shaped as
   rules → questions → LLM → destinations reads as one board, the inbox board. It is a view over ordinary steps: an
   optional filter of rules; a Decide step of yes/no questions and the filter that pairs question i with rule i; then the
   LLM, either Sort (a Decide step with one choice over the destination ids and the filter that routes each answer) or
   an llm step with `choose` (draft or reply); and the flow's destinations. `boardOf` reads those parts out of a flow,
   or null for any other flow; `flowOf` writes them back, each step keeping its id and fields. */

export type Act = string | { do?: string; args?: Record<string, unknown> };
export interface Cond {
  field?: string;
  op?: string;
  value?: unknown;
  min?: number | string;
}
export interface FlowRule {
  when?: Cond[];
  match?: string;
  then?: Act[];
  to?: string;
  next?: string;
  stop?: boolean;
  [k: string]: unknown;
}
export interface Dest {
  id: string;
  name?: string;
  color?: string;
  then?: Act[];
  [k: string]: unknown;
}
interface Question {
  type?: string;
  instructions?: string;
  criteria?: string[] | Record<string, string>;
  [k: string]: unknown;
}
interface FlowStep extends Step {
  enabled?: boolean;
  rules?: FlowRule[];
  questions?: Record<string, Question>;
  out?: string;
  set?: unknown;
  as?: string;
  choose?: { mode?: string; to?: string[]; default?: string; [k: string]: unknown };
}
export interface Flow {
  trigger?: Trigger;
  steps?: Step[];
  destinations?: Dest[] | null;
}

/** A board's rule: where it goes after its chain ('end', or 'rule' to move on to the next part). */
export interface BoardRule extends FlowRule {
  next: string;
}
export interface BoardQuestion {
  id: string;
  text: string;
  min?: number | string;
  then: Act[];
  to: string;
}
export interface BoardLlm {
  mode: string;
  prompt: string;
  to: string[];
  default: string;
  as: string;
  min?: number | string | null;
}
interface Src {
  rules?: FlowStep;
  qd?: FlowStep;
  qf?: FlowStep;
  sd?: FlowStep;
  sf?: FlowStep;
  sq?: string;
  ls?: FlowStep;
}
export interface Board {
  rules: BoardRule[];
  questions: BoardQuestion[];
  llm: BoardLlm;
  dests: Dest[];
  src: Src;
  mail: boolean;
}

export const Q_MIN = 0.75;
const SORT_MIN = 0.5;
/** A yes/no condition with no minimum holds at half (the engine's own default). */
export const NOUL_MIN = 0.5;

const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const newId = () => `s_${Math.random().toString(36).slice(2, 10)}`;
const doOf = (x: Act | null | undefined): string => (typeof x === 'string' ? x : (x?.do ?? ''));
const argsOf = (x: Act | null | undefined): Record<string, unknown> => (x && typeof x === 'object' && x.args) || {};

// Where a rule goes after its chain, as the engine reads it: its own next, else end when it has a destination or stops.
const ruleNext = (r: FlowRule | null | undefined) => r?.next || (r?.to || r?.stop === true ? 'end' : 'step');
const ask = (mail: boolean) => (mail ? 'Pick where this message goes.' : 'Pick where this goes.');

export function boardOf(flow: Flow | null | undefined): Board | null {
  const dests = flow?.destinations;
  if (!flow || !Array.isArray(dests) || !dests.length || dests.some((d) => !d || typeof d.id !== 'string' || !d.id)) return null;
  const ids = new Set<string | undefined>(dests.map((d) => d.id));
  const steps = (Array.isArray(flow.steps) ? flow.steps : []) as FlowStep[];
  const toOk = (r: FlowRule) => r.to == null || r.to === '' || ids.has(r.to);
  const inline = (d: FlowStep | undefined): d is FlowStep & { questions: Record<string, Question>; out: string } =>
    d?.type === 'decide' && !d.set && !!d.questions && typeof d.questions === 'object' && !Array.isArray(d.questions) && typeof d.out === 'string' && !!d.out;
  const one = (r: FlowRule, field: string, value: string) => {
    const c = (r.when ?? [])[0];
    return (r.when ?? []).length === 1 && typeof c === 'object' && c.field === field && (c.op || 'is') === 'is' && (c.value === value || (value === 'yes' && c.value === true));
  };
  // A switched-off step stays in the step list: the board has no switch to show it, and a save would turn it on.
  if (steps.some((x) => !x || x.enabled === false)) return null;
  const sortOne = (x: FlowStep | undefined) =>
    x?.type === 'filter' && Array.isArray(x.rules) && x.rules.length === 1 && !(x.rules[0]?.when ?? []).length && !(x.rules[0]?.then ?? []).length && ids.has(x.rules[0]?.to);
  const b: Omit<Board, 'llm'> & { llm: BoardLlm | null } = { rules: [], questions: [], llm: null, dests: copy(dests), src: {}, mail: flow.trigger?.type === 'message' };
  let i = 0;
  const rs = steps[i];
  if (rs?.type === 'filter' && !(steps.length === 1 && sortOne(steps[0]))) {
    const rules = rs.rules ?? [];
    if (!rules.every((r) => r && typeof r === 'object' && toOk(r) && ['end', 'rule'].includes(ruleNext(r)) && !(r.to && ruleNext(r) !== 'end'))) return null;
    b.src.rules = rs;
    b.rules = rules.map((r) => ({ ...copy(r), next: ruleNext(r) }));
    i++;
  }
  // Questions: every one yes/no, and each rule tests one of them (yes at its minimum) and ends; listed in rule order.
  const qd = steps[i];
  const qf = steps[i + 1];
  const qids = inline(qd) ? Object.keys(qd.questions) : [];
  if (qids.length && inline(qd) && qf?.type === 'filter' && qids.every((q) => qd.questions[q]?.type === 'noul')) {
    const rules = qf.rules ?? [];
    const used = new Set<string>();
    if (rules.length !== qids.length) return null;
    for (const r of rules) {
      const qid = qids.find((q) => r && one(r, `${qd.out}.${q}`, 'yes'));
      const q = qid ? qd.questions[qid] : undefined;
      if (!qid || used.has(qid) || !toOk(r) || ruleNext(r) !== 'end' || !q || typeof q.instructions !== 'string') return null;
      used.add(qid);
      const min = r.when?.[0]?.min;
      b.questions.push({ id: qid, text: q.instructions, ...(min == null || min === '' ? {} : { min }), then: copy(r.then ?? []), to: r.to || '' });
    }
    Object.assign(b.src, { qd, qf });
    i += 2;
  }
  // The LLM: Sort (one choice over destination ids, each answer routed, the last rule the unsure default) or a turn.
  const sd = steps[i];
  const sf = steps[i + 1];
  const sq = inline(sd) ? Object.keys(sd.questions) : [];
  const q0 = sq[0];
  if (q0 && sq.length === 1 && inline(sd) && sd.questions[q0]?.type === 'choice' && sf?.type === 'filter') {
    const q = sd.questions[q0];
    const crit = Array.isArray(q.criteria) ? q.criteria.map(String) : Object.keys(q.criteria ?? {});
    const rules = sf.rules ?? [];
    const last = rules[rules.length - 1];
    const field = `${sd.out}.${q0}`;
    const opts: string[] = [];
    if (typeof q.instructions !== 'string' || !crit.length || rules.length !== crit.length + 1 || !crit.every((o) => ids.has(o))) return null;
    for (let k = 0; k < crit.length; k++) {
      const r = rules[k];
      const o = r?.when?.[0]?.value;
      if (!r || typeof o !== 'string' || !crit.includes(o) || opts.includes(o) || !one(r, field, o) || r.to !== o || (r.then ?? []).length || ruleNext(r) !== 'end') return null;
      opts.push(o);
    }
    if (!last || (last.when ?? []).length || !last.to || !ids.has(last.to) || (last.then ?? []).length) return null;
    // The board shows one minimum for Sort, so routes with different ones keep the step list.
    const mins = rules.slice(0, -1).map((r) => r.when?.[0]?.min ?? '');
    if (mins.some((m) => m !== mins[0])) return null;
    b.llm = { mode: 'sort', prompt: q.instructions, to: opts, default: last.to, ...(mins[0] === '' ? {} : { min: mins[0] }), as: '' };
    Object.assign(b.src, { sd, sf, sq: q0 });
    i += 2;
  } else if (i === steps.length - 1 && sd && sortOne(sd)) {
    const to = sd.rules?.[0]?.to ?? '';
    b.llm = { mode: 'sort', prompt: '', to: [to], default: to, as: '' };
    b.src.sf = sd;
    i += 1;
  } else if (sd?.type === 'llm' && sd.choose && typeof sd.choose === 'object') {
    const c = sd.choose;
    // A draft or a reply answers a message: only a message flow has one.
    if (flow.trigger?.type !== 'message' || !['draft', 'reply'].includes(c.mode ?? '') || !Array.isArray(c.to) || !c.to.length || !c.to.every((x) => ids.has(x)) || !c.default || !ids.has(c.default))
      return null;
    b.llm = { mode: c.mode ?? 'draft', prompt: String(sd.prompt || ''), to: [...c.to], default: c.default, as: sd.as || '' };
    b.src.ls = sd;
    i += 1;
  } else return null;
  return i === steps.length ? (b as Board) : null;
}

/** A routine's board, or null when its steps are not shaped as one. */
export const routineBoard = (r: Routine) => boardOf({ trigger: r.trigger, steps: r.steps, destinations: r.destinations as Dest[] | null | undefined });

/** A board back as a flow's steps and destinations. */
export function flowOf(b: Board): { steps: Step[]; destinations: Dest[] } {
  const used = new Set<string | undefined>();
  const steps: FlowStep[] = [];
  const keep = (x: FlowStep | undefined, type: string): FlowStep => (x ? copy(x) : { id: newId(), type, enabled: true });
  const outName = (x: FlowStep, prefix: string) => {
    if (x.out && !used.has(x.out)) {
      used.add(x.out);
      return x.out;
    }
    let n = 1;
    while (used.has(prefix + n)) n++;
    used.add(prefix + n);
    return prefix + n;
  };
  for (const x of [b.src.qd, b.src.sd, b.src.ls]) if (x?.out) used.add(x.out);
  const rule = (r: FlowRule): FlowRule => {
    const x: FlowRule = { ...copy(r), match: r.match || 'all', when: copy(r.when ?? []), then: copy(r.then ?? []) };
    if (!x.to) delete x.to;
    x.next = x.to ? 'end' : x.next === 'rule' ? 'rule' : 'end';
    x.stop = x.next === 'end';
    return x;
  };
  if (b.rules.length) steps.push({ ...keep(b.src.rules, 'filter'), rules: b.rules.map(rule) });
  if (b.questions.length) {
    const d = keep(b.src.qd, 'decide');
    const f = keep(b.src.qf, 'filter');
    const old = b.src.qd?.questions ?? {};
    used.delete(d.out);
    delete d.set;
    d.out = outName(d, 'd');
    d.questions = Object.fromEntries(b.questions.map((q) => [q.id, { ...copy(old[q.id] ?? {}), type: 'noul', instructions: q.text }]));
    f.rules = b.questions.map((q) =>
      rule({ when: [{ field: `${d.out}.${q.id}`, op: 'is', value: 'yes', ...(q.min == null ? {} : { min: q.min }) }], match: 'all', then: q.then, to: q.to, next: 'end' }),
    );
    steps.push(d, f);
  }
  const L = b.llm;
  const only = L.to[0];
  // One destination needs no choice: a single rule sends everything there (a one-option choice is refused).
  if (L.mode === 'sort' && L.to.length === 1 && only) steps.push({ ...keep(b.src.sf, 'filter'), rules: [rule({ when: [], match: 'all', then: [], to: only })] });
  else if (L.mode === 'sort') {
    const d = keep(b.src.sd, 'decide');
    const f = keep(b.src.sf, 'filter');
    const qid = b.src.sq || 'to';
    const old: Question = b.src.sd?.questions?.[qid] ?? {};
    const was: Record<string, string> = old.criteria && !Array.isArray(old.criteria) ? old.criteria : {};
    used.delete(d.out);
    delete d.set;
    d.out = outName(d, 'd');
    d.questions = {
      [qid]: { ...copy(old), type: 'choice', instructions: L.prompt || ask(b.mail), criteria: Object.fromEntries(L.to.map((id) => [id, was[id] ?? destName(id, b.dests)])) },
    };
    // Each route keeps Sort's minimum as stored (none stays none); a new Sort gets the board's.
    const field = `${d.out}.${qid}`;
    const min = b.src.sd ? L.min : (L.min ?? SORT_MIN);
    f.rules = [
      ...L.to.map((id) => rule({ when: [{ field, op: 'is', value: id, ...(min == null ? {} : { min }) }], match: 'all', then: [], to: id })),
      rule({ when: [], match: 'all', then: [], to: L.default }),
    ];
    steps.push(d, f);
  } else {
    const x = keep(b.src.ls, 'llm');
    used.delete(x.out);
    // A choose's default must be one of its own.
    const to = L.to.includes(L.default) ? [...L.to] : [...L.to, L.default];
    Object.assign(x, { as: L.as || x.as, prompt: L.prompt || ask(b.mail), out: outName(x, 'l'), choose: { ...(x.choose ?? {}), to, default: L.default, mode: L.mode } });
    steps.push(x);
  }
  return { steps, destinations: copy(b.dests) };
}

export const destName = (id: string, dests: Dest[]) => dests.find((d) => d.id === id)?.name || id;

/** A new destination's id, from its name: lower case, a letter first, unique on the board. */
export function destId(name: string, dests: Dest[]): string {
  let base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 32);
  if (!/^[a-z]/.test(base)) base = `dest${base ? `_${base}` : ''}`;
  const taken = dests.map((d) => d.id);
  if (!taken.includes(base)) return base;
  let n = 2;
  while (taken.includes(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

const freeId = (taken: string[], prefix: string) => {
  let n = 1;
  while (taken.includes(prefix + n)) n++;
  return prefix + n;
};

/** Who a draft or reply turn runs as: a person's Assistant, or the owning agent; '' for a team (no one to run as). */
export function ownerAs(r: Pick<Routine, 'owner_id'> & { owner?: string }): string {
  const o = String(r.owner_id || r.owner || '');
  if (/^(person|human):/.test(o)) return `assistant:${o.split(':').slice(1).join(':')}`;
  if (o.startsWith('bot:')) return o;
  return '';
}

/** The LLM's modes: Sort, and on a message flow with someone to run as, Sort + draft and Sort + reply. */
export const llmModes = (r: Pick<Routine, 'owner_id'> & { trigger: Trigger }) => [
  { value: 'sort', label: 'Sort' },
  ...(r.trigger.type === 'message' && ownerAs(r)
    ? [
        { value: 'draft', label: 'Sort + draft' },
        { value: 'reply', label: 'Sort + reply' },
      ]
    : []),
];

// ---------------------------------------------------------------- actions in a chain

/** Mail actions queue on the message, the rest run at once; a chain lists them first. */
export const MAIL_ACTS = ['archive', 'label', 'star', 'leave_for_you', 'forward', 'draft_reply', 'send_reply', 'unsubscribe'];
export const ACTS: Record<string, string> = {
  archive: 'Archive',
  label: 'Label',
  star: 'Star',
  leave_for_you: 'Leave in inbox',
  forward: 'Forward',
  draft_reply: 'Draft reply',
  send_reply: 'Reply',
  unsubscribe: 'Unsubscribe',
  create_task: 'Create a task',
  wake_bot: 'Message an agent',
  assign: 'Assign',
  note: 'Note',
  post_update: 'Post update',
  call_webhook: 'Call webhook',
};
export const OPS: Record<string, string> = { is: 'is', is_not: 'is not', contains: 'contains', matches: 'matches', '>=': '≥', '<=': '≤' };
/** The fields a rule's condition starts with, by trigger. */
export const TRIGGER_FIELDS: Record<string, string[]> = {
  message: ['message.from', 'message.to', 'message.subject', 'message.body'],
  task_received: ['task.title', 'task.body', 'task.from', 'task.tag'],
  event: ['event.name'],
  webhook: ['payload.type'],
  due: ['due.due_on', 'due.repeats'],
};

/** Each action's argument the editor shows, under the name the server reads first; the others it also reads. */
export const ARG: Record<string, { key: string; also?: string[] }[]> = {
  create_task: [{ key: 'title' }, { key: 'owner' }],
  assign: [{ key: 'owner' }],
  wake_bot: [{ key: 'bot' }, { key: 'text', also: ['prompt'] }],
  forward: [{ key: 'to', also: ['webhook'] }],
  label: [{ key: 'name', also: ['label', 'tag'] }],
  draft_reply: [{ key: 'body', also: ['text'] }],
  send_reply: [{ key: 'body', also: ['text'] }],
  note: [{ key: 'text', also: ['note'] }],
  post_update: [{ key: 'text', also: ['body'] }],
  call_webhook: [{ key: 'webhook' }],
};

/** Star is a label (STARRED); the board shows it as its own action. */
export function actKey(x: Act | null | undefined): string {
  const d = doOf(x);
  const a = argsOf(x);
  return d === 'label' && /^starred$/i.test(String(a.name ?? a.label ?? a.tag ?? '')) ? 'star' : d;
}

/** An argument as the editor shows it: its own key, else one the server also reads. */
export function argOf(x: Act, key: string): string {
  const a = argsOf(x);
  const spec = ARG[actKey(x)]?.find((f) => f.key === key);
  for (const k of [key, ...(spec?.also ?? [])]) if (a[k] != null && a[k] !== '') return String(a[k]);
  return '';
}

/** An action as a line: "Create a task, Ada", "Label Urgent"; none reads "Do nothing". */
export function actLine(x: Act | undefined, name: (id: string) => string): string {
  if (!x) return 'Do nothing';
  const k = actKey(x);
  const who = (w: string) => name(w.includes(':') ? w : `bot:${w}`);
  const owner = argOf(x, 'owner');
  if (k === 'create_task') return owner ? `Create a task, ${who(owner)}` : 'Create a task';
  if (k === 'assign') return owner ? `Assign to ${who(owner)}` : 'Assign';
  if (k === 'wake_bot') return argOf(x, 'bot') ? `Message ${who(argOf(x, 'bot'))}` : 'Message the agent';
  if (k === 'label') return argOf(x, 'name') ? `Label ${argOf(x, 'name')}` : 'Label';
  if (k === 'forward') return argOf(x, 'to') ? `Forward to ${argOf(x, 'to')}` : 'Forward';
  if (k === 'post_update') return 'Post to Updates';
  return ACTS[k] ?? k;
}

/** A condition in words: "message.from contains @acme.example". */
export function condWords(c: Cond): string {
  const v = c.value === true ? 'yes' : c.value === false ? 'no' : String(c.value ?? '');
  return `${c.field ?? ''} ${OPS[c.op || 'is'] ?? c.op} ${v}`.trim();
}
export const condsWords = (r: FlowRule) => ((r.when ?? []).length ? (r.when ?? []).map(condWords).join(r.match === 'any' ? ' or ' : ' and ') : 'Always');

// ---------------------------------------------------------------- one item put back

export type Part = 'rule' | 'question' | 'destination' | 'llm';
/** An action as the editor sends it: what it does and the arguments it shows, as text. */
export interface ActIn {
  do: string;
  args?: Record<string, string>;
}
export interface ItemIn {
  part: Part;
  /** Which one; none adds a new one (the LLM has just one). */
  at?: number;
  when?: { field: string; op?: string; value: string }[];
  match?: 'all' | 'any';
  then?: ActIn[];
  /** A rule or question's last step: a destination's id, else it ends ('' ) or, for a rule, moves on. */
  goTo?: string;
  moveOn?: boolean;
  text?: string;
  /** A question's minimum confidence, 0 to 1; null for the engine's own. */
  min?: number | null;
  name?: string;
  mode?: 'sort' | 'draft' | 'reply';
  prompt?: string;
  dests?: string[];
  unsure?: string;
}

const refuse = (why: string): never => {
  throw Object.assign(new Error(why), { code: `bad_input: ${why}` });
};

/** The chain as saved: each action keeps what the editor did not show; a blank shown field says nothing. Mail
    actions go first on a message flow. */
function chainOf(given: ActIn[], was: Act[] | undefined, mail: boolean): Act[] {
  const out = given.map((x, j) => {
    const doing = x.do === 'star' ? 'label' : x.do;
    const old = was?.[j];
    const kept = old && actKey(old) === x.do ? { ...argsOf(old) } : {};
    for (const [k, v] of Object.entries(x.do === 'star' ? { name: 'STARRED' } : (x.args ?? {}))) {
      for (const also of ARG[x.do]?.find((f) => f.key === k)?.also ?? []) delete kept[also];
      if (v.trim()) kept[k] = /^(owner|bot)$/.test(k) ? v.replace(/^human:/, 'person:') : v;
      else delete kept[k];
    }
    return { do: doing, args: kept };
  });
  if (!mail) return out;
  const isMail = (x: Act) => MAIL_ACTS.includes(actKey(x));
  return [...out.filter(isMail), ...out.filter((x) => !isMail(x))];
}

/** The board with one rule, question, destination or the LLM put back. Refuses what the old editor refused. */
export function putItem(board: Board, it: ItemIn, owner: Pick<Routine, 'owner_id'>): Board {
  const b = copy(board);
  const n = it.at;
  const ids = new Set(b.dests.map((d) => d.id));
  const end = (old: { to?: string; next?: string }) => {
    if (it.goTo !== undefined && it.goTo !== '' && !ids.has(it.goTo)) refuse('No such destination');
    if (it.goTo !== undefined) return { to: it.goTo, next: it.goTo ? 'end' : it.moveOn ? 'rule' : 'end' };
    return { to: old.to ?? '', next: old.next ?? 'end' };
  };
  const put = <T>(list: T[], x: T) => {
    if (n === undefined) list.push(x);
    else if (n < list.length) list[n] = x;
    else refuse('No such item');
  };
  if (it.part === 'rule') {
    const was = n === undefined ? undefined : b.rules[n];
    const when = it.when ?? (was?.when as { field: string; op?: string; value: string }[] | undefined) ?? [];
    if (!when.every((c) => String(c.field ?? '').trim())) refuse('Each condition needs a field');
    const rule: BoardRule = { ...(was ?? {}), when: when.map((c) => ({ ...c, field: c.field.trim(), op: c.op || 'is' })), match: it.match ?? was?.match ?? 'all', then: chainOf(it.then ?? [], was?.then, b.mail), next: 'end' };
    Object.assign(rule, end(was ?? {}));
    if (!rule.to) delete rule.to;
    put(b.rules, rule);
  } else if (it.part === 'question') {
    const was = n === undefined ? undefined : b.questions[n];
    const text = (it.text ?? was?.text ?? '').trim();
    if (!text) refuse('Add the question');
    const e = end(was ?? {});
    const min = it.min === undefined ? (was ? was.min : Q_MIN) : it.min;
    put(b.questions, {
      id: was?.id ?? freeId(
        b.questions.map((q) => q.id),
        'q',
      ),
      text,
      ...(min == null ? {} : { min }),
      then: chainOf(it.then ?? [], was?.then, b.mail),
      to: e.to,
    });
  } else if (it.part === 'destination') {
    const was = n === undefined ? undefined : b.dests[n];
    const name = (it.name ?? was?.name ?? '').trim();
    if (!name) refuse('Name the destination');
    if (was) b.dests[n as number] = { ...was, name, then: chainOf(it.then ?? [], was.then, b.mail) };
    else if (n !== undefined) refuse('No such item');
    else {
      const id = destId(name, b.dests);
      b.dests.push({ id, name, then: chainOf(it.then ?? [], undefined, b.mail) });
      b.llm.to.push(id);
    }
  } else {
    const to = (it.dests ?? b.llm.to).filter((d) => ids.has(d));
    if (!to.length) refuse('Pick a destination');
    const unsure = it.unsure && to.includes(it.unsure) ? it.unsure : to.includes(b.llm.default) ? b.llm.default : (to[0] as string);
    b.llm = { ...b.llm, mode: it.mode ?? b.llm.mode, prompt: (it.prompt ?? b.llm.prompt).trim(), to, default: unsure };
    if (b.llm.mode !== 'sort') b.llm.as ||= ownerAs(owner);
  }
  return b;
}

/** The board without one rule, question or destination; whatever went to a removed destination ends instead. */
export function dropItem(board: Board, part: Exclude<Part, 'llm'>, at: number): Board {
  const b = copy(board);
  const list: unknown[] = part === 'rule' ? b.rules : part === 'question' ? b.questions : b.dests;
  if (at >= list.length) refuse('No such item');
  if (part !== 'destination') {
    list.splice(at, 1);
    return b;
  }
  if (b.dests.length < 2) refuse('A board keeps one destination at least');
  const [gone] = b.dests.splice(at, 1);
  for (const r of [...b.rules, ...b.questions]) {
    if (r.to === gone?.id) Object.assign(r, { to: '', next: 'end' });
  }
  b.llm.to = b.llm.to.filter((x) => x !== gone?.id);
  if (!b.llm.to.length) b.llm.to = [b.dests[0]?.id ?? ''];
  if (!b.llm.to.includes(b.llm.default)) b.llm.default = b.llm.to[0] ?? '';
  return b;
}

// ---------------------------------------------------------------- templates

/** Apply template is for an older inbox routine: a message routine whose one step is an llm step with no choose. */
export const takesTemplate = (r: Routine) =>
  !!r.can_edit && !r.readonly && r.trigger.type === 'message' && (r.steps ?? []).length === 1 && r.steps?.[0]?.type === 'llm' && !r.steps[0].choose;

const MAX_INSTRUCTIONS = 8000;

/** The template's rules, questions and destinations with the old llm step as the LLM: its prompt as the instructions
    and its own fields kept, as Sort + draft for a person or an agent; a team has no turn to draft with, so its prompt
    instructs the template's Sort. A switched-off step stays off. Null when the filled flow is not a board. */
export function applyFlow(r: Routine, filled: { steps?: unknown[]; destinations?: unknown[] | null }): { steps: Step[]; destinations: Dest[] } | null {
  const b = boardOf({ steps: filled.steps as Step[] | undefined, destinations: filled.destinations as Dest[] | null | undefined, trigger: r.trigger });
  if (!b) return null;
  const old = copy(r.steps?.[0]) as FlowStep | undefined;
  if (!old) return null;
  const as = ownerAs(r);
  const L = b.llm;
  const prompt = String(old.prompt || '').trim() ? String(old.prompt) : L.prompt;
  if (as) {
    // Only the rules and the questions stay beside the old step; a Sort it replaces goes.
    const kept = [b.rules.length ? b.src.rules : undefined, b.questions.length ? b.src.qd : undefined, b.questions.length ? b.src.qf : undefined].filter(
      (x): x is FlowStep => !!x,
    );
    if (kept.some((x) => x.id === old.id)) old.id = newId();
    if (kept.some((x) => x.out && x.out === old.out)) delete old.out;
    delete old.choose;
    b.llm = { mode: 'draft', prompt, to: [...L.to], default: L.default, as };
    b.src = { ...b.src, ls: old, sd: undefined, sf: undefined, sq: undefined };
  } else {
    if (L.to.length > 1 && [...prompt].length > MAX_INSTRUCTIONS) refuse(`The prompt is over ${MAX_INSTRUCTIONS.toLocaleString('en-US')} characters. Shorten it first.`);
    b.llm = { ...L, prompt };
  }
  const flow = flowOf(b);
  if (old.enabled === false) {
    for (const x of flow.steps.slice(b.llm.mode === 'sort' && b.llm.to.length > 1 ? -2 : -1)) (x as FlowStep).enabled = false;
  }
  return flow;
}
