import type { Hook, OrgRule, StageRule, Trigger } from './types';

/* Plain words for what the server stores: a schedule, a trigger, a stage rule's When / If / Then. Ported from the old
   app (ui/app/format.ts cronWords, ui/app/automations.ts auCronWords and auSource, ui/app/stage-rules.ts srParts),
   with "agent" where it said "bot". */

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const LONG_DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Server text people read: "bot" said as "agent" (the app's word). */
export const agentWords = (s: string) =>
  s.replace(/\b([Bb])ots?\b/g, (w, b: string) => (b === 'B' ? 'Agent' : 'agent') + (w.endsWith('s') ? 's' : '')).replace(/\ba agent/g, 'an agent').replace(/\bA agent/g, 'An agent');

/** "every day at 9 AM", "weekdays at 3:30 PM", "every 15 minutes"; anything else comes back as the cron (format.ts:110). */
export function cronWords(expr: string): string {
  const f = expr.trim().split(/\s+/);
  if (f.length !== 5) return expr;
  const [mi = '', hr = '', dom = '', mon = '', dow = ''] = f;
  if (mon === '*' && dom === '*' && dow === '*') {
    if (/^\*\/\d+$/.test(mi) && hr === '*') return `every ${mi.slice(2)} minutes`;
    if (/^\d+$/.test(mi) && hr === '*') return +mi ? `every hour at :${mi.padStart(2, '0')}` : 'every hour';
    if (/^\d+$/.test(mi) && /^\*\/\d+$/.test(hr)) return `every ${hr.slice(2)} hours` + (+mi ? ` at :${mi.padStart(2, '0')}` : '');
  }
  if (!/^\d+$/.test(mi) || mon !== '*') return expr;
  const clock = (h: number) => `${h % 12 || 12}${+mi ? ':' + mi.padStart(2, '0') : ''} ${h < 12 ? 'AM' : 'PM'}`;
  const times = hr.split(',').every((h) => /^\d+$/.test(h)) ? hr.split(',').map((h) => clock(+h)).join(' and ') : null;
  if (!times) return expr;
  const days = (d: string) =>
    d
      .split(',')
      .map((x) => (x.includes('-') ? x.split('-').map(Number) : [Number(x), Number(x)]))
      .flatMap(([a = 0, b = 0]) => Array.from({ length: b - a + 1 }, (_, i) => DAYS[a + i]));
  let when: string;
  if (dom === '*' && dow === '*') when = 'every day';
  else if (dom === '*' && dow === '1-5') when = 'weekdays';
  else if (dom === '*' && /^[\d,-]+$/.test(dow)) when = days(dow).join(', ');
  else if (dow === '*' && /^\*\/(\d+)$/.test(dom)) when = `every ${dom.slice(2)} days`;
  else if (dom === '1-7' && /^\d$/.test(dow)) when = `first ${DAYS[+dow]} of the month`;
  else if (dow === '*' && /^\d+$/.test(dom)) when = `day ${dom} of the month`;
  else return expr;
  return `${when} at ${times}`;
}

/** Every hour (or few) on some weekdays reads "every hour, Mon, Tue", not its cron (automations.ts:105). */
export function scheduleWords(cron: string): string {
  const f = cron.trim().split(/\s+/);
  const [mi = '', hr = '', dom = '', mon = '', dow = ''] = f;
  if (f.length === 5 && dom === '*' && mon === '*' && /^[\d,-]+$/.test(dow) && /^(\*|\*\/\d+)$/.test(hr) && /^(\d+|\*\/\d+)$/.test(mi)) {
    const each = cronWords(`${mi} ${hr} * * *`);
    const days = cronWords(`0 0 * * ${dow}`).replace(/ at 12 AM$/, '');
    if (each !== `${mi} ${hr} * * *` && !days.includes('*')) return `${each}, ${days}`;
  }
  return cronWords(cron);
}

// ---------------------------------------------------------------- the schedule picker

/** The schedule picker's choices; anything they cannot say is `custom` and keeps its cron. */
export type Every = 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom';
export interface Schedule {
  every: Every;
  /** 'HH:MM', 24 h. */
  at: string;
  /** weekly: 0 (Sunday) to 6. */
  day: number;
  cron: string;
}

export const EVERY: readonly { value: Every; label: string }[] = [
  { value: 'hourly', label: 'Every hour' },
  { value: 'daily', label: 'Every day' },
  { value: 'weekdays', label: 'Weekdays' },
  { value: 'weekly', label: 'Every week' },
  { value: 'monthly', label: 'Every month' },
  { value: 'custom', label: 'Custom' },
];
export const WEEKDAYS = LONG_DAYS.map((label, i) => ({ value: String(i), label }));

const pad = (n: number) => String(n).padStart(2, '0');

export function parseSchedule(cron: string): Schedule {
  const [mi = '', hr = '', dom = '', mon = '', dow = ''] = cron.trim().split(/\s+/);
  const ok = cron.trim().split(/\s+/).length === 5 && /^\d+$/.test(mi) && mon === '*';
  const at = /^\d+$/.test(hr) ? `${pad(+hr)}:${pad(+mi)}` : '09:00';
  const base = { at, day: 1, cron };
  if (ok && hr === '*' && dom === '*' && dow === '*' && +mi === 0) return { ...base, every: 'hourly' };
  if (ok && /^\d+$/.test(hr)) {
    if (dom === '*' && dow === '*') return { ...base, every: 'daily' };
    if (dom === '*' && dow === '1-5') return { ...base, every: 'weekdays' };
    if (dom === '*' && /^[0-6]$/.test(dow)) return { ...base, every: 'weekly', day: +dow };
    if (dom === '1' && dow === '*') return { ...base, every: 'monthly' };
  }
  return { ...base, every: 'custom' };
}

export function scheduleCron(s: Omit<Schedule, 'cron'> & { cron?: string }): string {
  const [h = '9', m = '0'] = s.at.split(':');
  const mi = String(+m);
  const hr = String(+h);
  switch (s.every) {
    case 'hourly':
      return '0 * * * *';
    case 'daily':
      return `${mi} ${hr} * * *`;
    case 'weekdays':
      return `${mi} ${hr} * * 1-5`;
    case 'weekly':
      return `${mi} ${hr} * * ${s.day}`;
    case 'monthly':
      return `${mi} ${hr} 1 * *`;
    default:
      return s.cron ?? '0 9 * * 1-5';
  }
}

/** The picker's times: every half hour, plus the one already set. */
export function timeChoices(current: string): { value: string; label: string }[] {
  const all = new Set<string>([current]);
  for (let h = 0; h < 24; h++) for (const m of [0, 30]) all.add(`${pad(h)}:${pad(m)}`);
  return [...all].sort().map((value) => {
    const [h = 0, m = 0] = value.split(':').map(Number);
    return { value, label: `${h % 12 || 12}:${pad(m)} ${h < 12 ? 'AM' : 'PM'}` };
  });
}

export const browserZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

// ---------------------------------------------------------------- a trigger in words

const EVENTS: Record<string, string> = {
  'task.created': 'a task is created',
  'task.assigned': 'a task is given to someone',
  'task.step_changed': 'a task changes stage',
  'task.moved': 'a task changes pipeline',
  'task.commented': 'a task gets a comment',
  'task.done': 'a task is finished',
  'task.closed': 'a task is closed',
  'task.reopened': 'a task is reopened',
  'thread.created': 'a thread starts',
  'message.created': 'a message is sent',
  'message.held': 'a message is held',
  'bot.run_failed': 'an agent’s run fails',
  'bot.held': 'an agent is held',
  'bot.released': 'an agent is released',
  'org_rule.retired': 'a rule is removed',
  'rules.blocked': 'a rule blocks something',
  'rules.escalated': 'a rule asks first',
  'rules.warned': 'a rule warns',
  'meeting.ready': 'a meeting is ready',
  'strategy.published': 'the strategy is published',
  'brand.published': 'the brand is published',
  'inbound.received': 'a message comes in',
  'webhook.received': 'a webhook is called',
  'tico.builtin.wakes': 'anything happens',
};

export function eventWords(name: string): string {
  if (EVENTS[name]) return `When ${EVENTS[name]}`;
  if (name.startsWith('tico.builtin.job.')) return 'System job';
  const words = name.replace(/^(tico\.builtin\.|tico\.|custom\.)/, '').replace(/[._*-]+/g, ' ').trim();
  return words ? `When ${agentWords(words)}` : 'When something happens';
}

const REPEATS: Record<string, string> = { once: 'once', weekly: 'every week', monthly: 'every month', quarterly: 'every quarter', yearly: 'every year' };

/** A day with no zone ("2026-03-01") as "Mar 1". */
export function dayWords(iso: string | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? '');
  if (!m) return '';
  return new Date(+m[1]!, +m[2]! - 1, +m[3]!).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** When a routine runs, in a few words (automations.ts:134 auSource). `hookName` names a webhook trigger's caller. */
export function triggerWords(t: Trigger, names: { hook?: (id: string) => string | undefined; who?: (id: string) => string | undefined } = {}): string {
  switch (t.type) {
    case 'schedule':
      return t.cron ? cap(scheduleWords(t.cron)) : 'On a schedule';
    case 'event':
      return t.name ? eventWords(t.name) : 'When something happens';
    case 'webhook': {
      const name = (t.id && names.hook?.(t.id)) || t.id;
      return name ? `When ${name} calls` : 'When a webhook is called';
    }
    case 'due': {
      const day = dayWords(t.first_due);
      const before = t.open_days ?? 14;
      return `${before} days before ${day || 'a due date'}${t.repeats && t.repeats !== 'once' ? `, ${REPEATS[t.repeats] ?? t.repeats}` : ''}`;
    }
    case 'message':
      return t.inbox ? `When ${t.inbox} gets a message` : 'When a message comes in';
    case 'task_received': {
      const who = (t.to && names.who?.(t.to)) || t.to;
      return who ? `When ${who} gets a task` : 'When a task comes in';
    }
    case 'listening':
      return limitsWords(t.limits);
    default:
      return cap(t.type);
  }
}

/** A watch's limits: "10 a day". */
export function limitsWords(limits: Trigger['limits']): string {
  const first = limits?.[0];
  if (!first) return '';
  const per: Record<string, string> = { hour: 'an hour', day: 'a day', week: 'a week', month: 'a month' };
  return `${first.count} ${per[first.per] ?? `a ${first.per}`}`;
}

// ---------------------------------------------------------------- rules in one line

export const STAGE_EVENTS = [
  { value: 'enters', label: 'A task enters' },
  { value: 'owner_changes', label: 'The owner changes' },
  { value: 'bot_snoozes', label: 'An agent snoozes' },
  { value: 'timer', label: 'A timer' },
] as const;
export const STAGE_TIMERS = [
  { value: 'in_stage', label: 'in the stage' },
  { value: 'quiet', label: 'quiet' },
  { value: 'waiting_person', label: 'waiting on a person' },
] as const;
export const STAGE_THENS = [
  { value: 'block', label: 'Block' },
  { value: 'tell_manager', label: 'Tell the manager' },
  { value: 'tell_owner', label: 'Tell the owner' },
  { value: 'move_back', label: 'Move back' },
] as const;
export const BROKEN = [
  { value: 'block', label: 'Stop' },
  { value: 'escalate', label: 'Ask first' },
  { value: 'warn', label: 'Warn' },
] as const;

function stageWhen(r: Pick<StageRule, 'event' | 'timer'>, where: string): string {
  if (r.event === 'timer' && r.timer?.hours) {
    const kind = r.timer.kind ?? 'in_stage';
    if (kind === 'in_stage' && where) return `${r.timer.hours} h in ${where}`;
    return `${r.timer.hours} h ${STAGE_TIMERS.find((t) => t.value === kind)?.label ?? 'in the stage'}`;
  }
  const said = r.event
    .split(',')
    .filter(Boolean)
    .map((e, i) => {
      const w: string = e === 'enters' && where ? `Enters ${where}` : (STAGE_EVENTS.find((x) => x.value === e)?.label ?? 'Something happens');
      return i ? w.charAt(0).toLowerCase() + w.slice(1) : w;
    });
  return said.join(' or ') || 'Something happens';
}

/** One fixed condition in words (stage-rules.ts:230 srCond); one it does not know reads "a condition holds". */
function condWords(k: string, v: unknown, team: (id: string) => string | undefined): string {
  const val = typeof v === 'string' || typeof v === 'number' ? String(v) : '';
  if (k === 'owner_on_team' || k === 'owner_not_on_team') return `the owner is ${k === 'owner_on_team' ? 'on' : 'not on'} ${(val && team(val)) || val || "the manager's team"}`;
  if (k === 'owner_kind') return `the owner is ${val === 'person' ? 'a person' : 'an agent'}`;
  if (k === 'turn') return `it is ${val === 'person' ? "a person's" : "an agent's"} turn`;
  if (k === 'snooze_hours_over') return `snoozed over ${val} h`;
  if (k === 'label') return `labeled ${val}`;
  if (k === 'requester_person') return v ? 'asked by a person' : 'asked by an agent';
  if (k === 'sent_back') return val ? `sent back ${val} times` : 'sent back';
  return 'a condition holds';
}

/** A stage rule as When, the checks, Then: each part on its own so the row can set a mark between them. */
export function stageParts(
  r: Pick<StageRule, 'event' | 'timer' | 'cond' | 'jev' | 'then' | 'stage_names'>,
  team: (id: string) => string | undefined = () => undefined,
): string[] {
  const ifs = Object.entries(r.cond ?? {}).flatMap(([k, v]): string[] => {
    if (k === 'owner_on_team' || k === 'owner_not_on_team') return [condWords(k, v, team)];
    if (v === '' || v === null || v === undefined || (v === false && k !== 'requester_person')) return [];
    return [condWords(k, v, team)];
  });
  if (r.jev) ifs.push('the decision model decides');
  const then = STAGE_THENS.find((t) => t.value === r.then)?.label ?? 'It acts';
  return [stageWhen(r, (r.stage_names ?? []).join(' or ')), ...(ifs.length ? [ifs.join(', ')] : []), then];
}

/** A company rule as its own words, then what happens when it is broken. */
export function orgParts(r: Pick<OrgRule, 'text' | 'if_broken' | 'reviewer'>): string[] {
  const text = agentWords(r.text.trim());
  if (r.if_broken === 'escalate') return [text, r.reviewer?.name ? `Ask ${r.reviewer.name}` : 'Ask first'];
  if (r.if_broken === 'warn') return [text, 'Warn'];
  return [text, 'Stop'];
}

// ---------------------------------------------------------------- built in, and a run

export const MAINTENANCE_EVERY = [
  { value: 'hourly', label: 'Every hour' },
  { value: '2h', label: 'Every 2 hours' },
  { value: '3h', label: 'Every 3 hours' },
  { value: '6h', label: 'Every 6 hours' },
  { value: '12h', label: 'Every 12 hours' },
  { value: 'daily', label: 'Every day' },
] as const;
export type MaintenanceEvery = (typeof MAINTENANCE_EVERY)[number]['value'];

export const maintenanceWords = (schedule: string) => MAINTENANCE_EVERY.find((e) => e.value === schedule)?.label ?? cap(schedule);

/** Heartbeat's choices, inside the server's own bounds. */
export const heartbeatChoices = (min = 5, max = 60, current?: number) =>
  [...new Set([5, 10, 15, 20, 30, 45, 60, ...(current ? [current] : [])])]
    .filter((s) => s >= min && s <= max)
    .sort((a, b) => a - b)
    .map((s) => ({ value: String(s), label: `Every ${s} seconds` }));

const RUN_WORDS: Record<string, string> = {
  queued: 'Waiting to start',
  running: 'Running',
  waiting: 'Waiting on an answer',
  ok: 'Done',
  partial: 'Partly done',
  failed: 'Failed',
  empty: 'Nothing to do',
  skipped: 'Skipped',
  held: 'Held',
};
export const runWords = (status: string) => RUN_WORDS[status] ?? cap(status);

/** What a webhook starts, in a few words. */
export const startsWords = (h: Pick<Hook, 'automations'>) => {
  const names = (h.automations ?? []).map((a) => a.name);
  return names.length ? `Starts ${names.join(', ')}` : 'Starts nothing yet';
};
