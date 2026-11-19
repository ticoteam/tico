// Pure rules for the team pages: which teammate an address names, who manages a team, the model line, the figures.
import type { Actor, Directory } from '@/data';
import type { AgentFile, AgentRow, Change, Group, Learned, ListRow, Model, PersonRow, Run } from './queries';

/** The menu's address for a team: its name, lower case, words joined by "-" (shell/Menu teamSlug). */
export const teamSlug = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');

export type Who =
  | { kind: 'agent'; actor: Actor }
  | { kind: 'person'; actor: Actor }
  | { kind: 'team'; group: Group };

/** `/team/<who>`: an agent's slug, a person's id, or a team (its id or its name as the menu writes it). */
export function resolveWho(who: string, dir: Directory | undefined, groups: readonly Group[] | undefined): Who | null {
  const actor = dir?.byId[who] ?? dir?.byId[`human:${who}`] ?? dir?.byId[`bot:${who}`];
  if (actor) return actor.kind === 'agent' ? { kind: 'agent', actor } : { kind: 'person', actor };
  const group = groups?.find((g) => g.id === who || teamSlug(g.name) === who);
  return group ? { kind: 'team', group } : null;
}

/** The address of a teammate's page. */
export const whoHref = (a: Pick<Actor, 'id' | 'kind' | 'slug'>) =>
  `/team/${encodeURIComponent(a.kind === 'agent' && a.slug ? a.slug : a.id.slice(a.id.indexOf(':') + 1))}`;

/** The id inside an actor id: an agent's bot id (a uuid for newer agents, not its slug) or a person's id. */
export const bareId = (actor: string) => actor.slice(actor.indexOf(':') + 1);

/** A team's members as actor ids (`human:<id>`, `bot:<bot id>`): groups list agents by bot id, not slug. */
export const memberIds = (g: Pick<Group, 'people' | 'bots'>) => [...g.people.map((p) => `human:${p}`), ...g.bots.map((b) => `bot:${b}`)];

/**
 * A team's manager: its lead when one is set; otherwise the teammate most of its members report to, when that one is in
 * the team (ui/app team pages took the same vote). An actor id, or null.
 */
export function managerOf(group: Group, agents: readonly AgentRow[], people: readonly PersonRow[]): string | null {
  if (group.lead) return group.lead.replace(/^person:/, 'human:');
  // reports_to holds a bare bot id or person id.
  const actorOf = (id: string) => (agents.some((a) => a.id === id) ? `bot:${id}` : `human:${id}`);
  const members = new Set(memberIds(group));
  const votes = new Map<string, number>();
  for (const a of agents) if (group.bots.includes(a.id) && a.reports_to) votes.set(actorOf(a.reports_to), (votes.get(actorOf(a.reports_to)) ?? 0) + 1);
  for (const p of people) if (group.people.includes(p.id) && p.reports_to) votes.set(actorOf(p.reports_to), (votes.get(actorOf(p.reports_to)) ?? 0) + 1);
  let best: string | null = null;
  let most = 0;
  for (const [k, n] of votes) if (members.has(k) && n > most) [best, most] = [k, n];
  return best;
}

/** The teams directly inside `parent`, in their order. */
export const subTeams = (groups: readonly Group[], parent: string) =>
  groups.filter((g) => g.parent === parent && g.id !== parent).sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.localeCompare(b.name));

/** Every teammate in a team and the teams inside it. */
export function membersUnder(groups: readonly Group[], id: string): { bots: string[]; people: string[] } {
  const bots = new Set<string>();
  const people = new Set<string>();
  const walk = (gid: string, seen: Set<string>) => {
    if (seen.has(gid)) return;
    seen.add(gid);
    const g = groups.find((x) => x.id === gid);
    g?.bots.forEach((b) => bots.add(b));
    g?.people.forEach((p) => people.add(p));
    for (const s of subTeams(groups, gid)) walk(s.id, seen);
  };
  walk(id, new Set());
  return { bots: [...bots], people: [...people] };
}

/** The team a teammate is in: its group. `id` is a bot id or a person id. */
export const teamOf = (groups: readonly Group[], kind: 'agent' | 'person', id: string) =>
  groups.find((g) => (kind === 'agent' ? g.bots : g.people).includes(id));

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const EFFORT: Record<string, string> = { xhigh: 'XHigh' };
export const effortLabel = (e: string | null | undefined) => (e ? (EFFORT[e] ?? cap(e)) : 'Default');

/** "Opus 5.5, High": the model it runs and its effort, as the server resolved them. */
export function modelLine(row: Pick<AgentRow, 'model' | 'resolved_model' | 'resolved_model_label' | 'reasoning_effort' | 'resolved_effort'>, models?: readonly Model[]) {
  const id = row.resolved_model || row.model;
  const label = models?.find((m) => m.id === id)?.label ?? row.resolved_model_label ?? id;
  if (!label) return '';
  return `${cap(label)}, ${effortLabel(row.reasoning_effort || null)}`;
}

/* ---------------------------------------------------------------- figures */

export const startOfDay = (now: Date) => new Date(now.getFullYear(), now.getMonth(), now.getDate());
/** Monday. */
export const startOfWeek = (now: Date) => {
  const d = startOfDay(now);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
};
export const startOfMonth = (now: Date) => new Date(now.getFullYear(), now.getMonth(), 1);

const runAt = (r: Run) => r.finished_at ?? r.last_event_at ?? null;

/** Turns since midnight and spend since the first of the month, from its newest runs. */
export function runFigures(runs: readonly Run[], now: Date) {
  const day = startOfDay(now).getTime();
  const month = startOfMonth(now).getTime();
  let turns = 0;
  let spend = 0;
  for (const r of runs) {
    const at = runAt(r);
    const t = at ? new Date(at).getTime() : NaN;
    if (t >= day) turns++;
    if (t >= month) spend += r.cost_usd ?? 0;
  }
  return { turns, spend };
}

/* ---------------------------------------------------------------- history */

export interface HistLine {
  key: string;
  kind: 'run' | 'set' | 'learn';
  title: string;
  at: string;
  /** A stopped run: its pill. */
  bad?: string;
  live?: boolean;
  dur?: string;
  cost?: number;
  /** A run's task. */
  task?: string;
}

/** "8m", "1h 12m". */
export function fmtDuration(s: number | null | undefined): string {
  if (!s || s < 0) return '';
  const m = Math.round(s / 60);
  if (m < 1) return `${Math.round(s)}s`;
  if (m < 60) return `${m}m`;
  return m % 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 60)}h`;
}

const FIELD: Record<string, string> = {
  model: 'Model changed',
  definition: 'Profile changed',
  instructions: 'Instructions changed',
  rules: 'Rules changed',
  machine: 'Computer changed',
  tools: 'Tools changed',
};

const GOOD = new Set(['success', 'succeeded', 'done', 'ok', 'completed', '']);

/** What it did, what changed in its settings and what it learned, as one list, newest first. */
export function historyOf(runs: readonly Run[], learned: readonly Learned[], changes: readonly Change[]): HistLine[] {
  const out: HistLine[] = [];
  for (const r of runs) {
    const at = runAt(r);
    if (!at && r.state !== 'running') continue;
    const live = r.state === 'running';
    out.push({
      key: `run:${r.id}`,
      kind: 'run',
      title: r.issue ? `Task ${r.issue}` : 'Chat turn',
      at: at ?? new Date().toISOString(),
      live,
      bad: !live && r.outcome && !GOOD.has(r.outcome) ? 'Stopped' : undefined,
      dur: fmtDuration(r.duration_s),
      cost: r.cost_usd ?? undefined,
      task: r.issue ?? undefined,
    });
  }
  for (const c of changes) out.push({ key: `set:${c.id}`, kind: 'set', title: FIELD[c.field] ?? 'Settings changed', at: c.created_at });
  for (const l of learned) out.push({ key: `learn:${l.sha}`, kind: 'learn', title: l.subject, at: l.when });
  return out.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

/* ---------------------------------------------------------------- computers */

export const GB = 1024 ** 3;

/** "Online", "Offline since 10:41 AM" and the disk, as the status line's short facts. */
export function computerStatus(
  c: { online: boolean; last_seen_at?: string | null; readiness?: { disk?: { free_bytes?: number; total_bytes?: number } } | null },
  fmtTime: (d: Date) => string,
): string[] {
  const out = [c.online ? 'Online' : c.last_seen_at ? `Offline since ${fmtTime(new Date(c.last_seen_at))}` : 'Offline'];
  const disk = c.readiness?.disk;
  if (disk?.total_bytes) {
    const used = Math.round((disk.total_bytes - (disk.free_bytes ?? 0)) / GB);
    out.push(`Disk ${used} of ${Math.round(disk.total_bytes / GB)} GB`);
  }
  return out;
}

/* ---------------------------------------------------------------- what it learned: decisions and dreams */

export interface Decision {
  key: string;
  title: string;
  /** Its day, YYYY-MM-DD, or '' when it has none. */
  on: string;
  /** The whole entry, for the row's hover. */
  text: string;
}

const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

/**
 * The entries of `memory/decisions.md`, newest first (ui bot-learnings learnNewestFirst): each top-level `- ` item,
 * dated by its `added:`, a date at its start, or its `## ` heading's date. Agents append at the bottom; an undated
 * entry stays with the dated one above it, and undated ones before every dated one stay first.
 */
export function decisionsOf(text: string): Decision[] {
  const year = (text.match(/\b20\d\d(?=-\d\d-\d\d\b)/g) ?? []).sort().pop() ?? '';
  const when = (t: string): string => {
    const iso = /\b(20\d\d-\d\d-\d\d)\b/.exec(t);
    if (iso) return iso[1] ?? '';
    const md = year ? /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})\b/i.exec(t) : null;
    return md ? `${year}-${String(MONTHS.indexOf((md[1] ?? '').toLowerCase()) + 1).padStart(2, '0')}-${(md[2] ?? '').padStart(2, '0')}` : '';
  };
  const items: { lines: string[]; head: string }[] = [];
  let head = '';
  let fence = false;
  let cur: { lines: string[]; head: string } | null = null;
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    if (!fence && /^## /.test(line)) {
      head = when(line);
      cur = null;
    } else if (!fence && /^[-*] /.test(line)) {
      cur = { lines: [line], head };
      items.push(cur);
    } else if (cur && (fence || /^\s+\S/.test(line))) cur.lines.push(line);
    else if (line.trim()) cur = null;
  }
  const rows = items.map((it, i): Decision => {
    const first = (it.lines[0] ?? '').replace(/^[-*]\s+/, '');
    const all = it.lines.join('\n');
    const on = /added:\s*(20\d\d-\d\d-\d\d)/.exec(all)?.[1] ?? (when(first.slice(0, 16)) || it.head);
    const title = first
      .replace(/^\W{0,3}(20\d\d-\d\d-\d\d|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2})\W{0,3}\s*[:—–-]?\s*/i, '')
      .replace(/\*\*|__|`/g, '')
      .trim();
    return { key: `d${i}`, title: title || first, on, text: all.replace(/^[-*]\s+/, '').trim() };
  });
  const groups: { on: string; rows: Decision[] }[] = [];
  for (const r of rows) {
    const last = groups[groups.length - 1];
    if (r.on || !last) groups.push({ on: r.on, rows: [r] });
    else last.rows.push(r);
  }
  const lead = groups[0] && !groups[0].on ? (groups.shift()?.rows ?? []) : [];
  groups.sort((a, b) => b.on.localeCompare(a.on));
  return [...lead, ...groups.flatMap((g) => g.rows)];
}

/** A dream's question as cards to answer: numbered questions ("1. …", "2) …") one each, anything else one card. */
export function dreamCards(text: string): { n: string; text: string }[] {
  const lines = text.split('\n');
  const parts: { n: string; lines: string[] }[] = [];
  const lead: string[] = [];
  for (const line of lines) {
    const m = /^\s*(?:\*\*)?(\d{1,2})[.)](?:\*\*)?\s+(.*)$/.exec(line);
    if (m) parts.push({ n: m[1] ?? '', lines: [m[2] ?? ''] });
    else if (parts.length) parts[parts.length - 1]?.lines.push(line);
    else lead.push(line);
  }
  if (parts.length < 2) return [{ n: '', text: text.trim() }];
  const intro = lead.join('\n').trim();
  return parts.map((p, i) => ({ n: p.n, text: `${i === 0 && intro ? `${intro}\n\n` : ''}${p.lines.join('\n').trim()}` }));
}

/** The answers as one reply, numbered to match the questions. */
export const dreamReply = (cards: { n: string }[], answers: string[]) =>
  cards.length === 1 ? (answers[0] ?? '').trim() : cards.map((c, i) => `${c.n}. ${(answers[i] ?? '').trim() || 'No answer'}`).join('\n');

/* ---------------------------------------------------------------- its own lists */

/** The filter fields a list's rows take here (backend/bot_task_tabs.py FIELDS, less Stage and Pipeline: see below). */
export const LIST_FIELDS = ['status', 'assigned_to', 'asked_by', 'waiting_on', 'needs', 'created_within', 'updated_within', 'done_within', 'due', 'label'] as const;
export type ListField = (typeof LIST_FIELDS)[number];
/** A row as a person or Tico writes it: the value in words ("open,done", "7", "overdue", "this_bot"). */
export interface ListLine {
  field: ListField;
  value: string;
}

export const FIELD_NAME: Record<string, string> = {
  status: 'Status',
  assigned_to: 'Assigned to',
  asked_by: 'Asked by',
  waiting_on: 'Waiting on',
  needs: 'Needs',
  created_within: 'Created within',
  updated_within: 'Updated within',
  done_within: 'Done within',
  due: 'Due',
  label: 'Label',
  stage: 'Stage',
  pipeline: 'Pipeline',
};
const DAYS = new Set<string>(['created_within', 'updated_within', 'done_within']);

/** A written row as the server stores it: a list for Status, whole days for the day fields and Due. */
export function rowOf(l: ListLine): ListRow {
  const v = l.value.trim();
  if (l.field === 'status') return { field: l.field, value: v.split(',').map((x) => x.trim()).filter(Boolean) };
  if (DAYS.has(l.field) || (l.field === 'due' && v !== 'overdue')) return { field: l.field, value: Number(v) };
  return { field: l.field, value: v };
}

/** A stored row in words, for the editor. */
export const lineOf = (r: ListRow): { field: string; value: string } => ({
  field: r.field,
  value: Array.isArray(r.value) ? r.value.join(',') : String(r.value),
});

export const isListField = (f: string): f is ListField => (LIST_FIELDS as readonly string[]).includes(f);

/* ---------------------------------------------------------------- its files */

/** A file's name as the old widget showed it: a stored file's own name when the title is "folder/name". */
export function fileName(f: Pick<AgentFile, 'title' | 'name' | 'locator'>): string {
  const title = f.title || f.name || '';
  if (f.locator === 'remote_link') return title;
  const name = f.name ?? '';
  return name && title.endsWith(`/${name}`) ? name : title;
}

const httpsOnly = (url: unknown): string => {
  try {
    return new URL(String(url)).protocol === 'https:' ? String(url) : '';
  } catch {
    return '';
  }
};

/**
 * Where a file opens: a stored file over the page (`view`), a Google file too, any other link at its provider over
 * https only; a file that never synced opens nowhere.
 */
export function fileOpen(f: Pick<AgentFile, 'locator' | 'open'>): { href: string; view: boolean } | null {
  const url = f.open?.url;
  if (typeof url !== 'string' || !url) return null;
  if (f.locator !== 'remote_link') return { href: url, view: true };
  const safe = httpsOnly(url);
  return safe ? { href: safe, view: /^https:\/\/(docs|drive)\.google\.com\//.test(safe) } : null;
}
