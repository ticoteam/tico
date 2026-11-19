import type { InfiniteData } from '@tanstack/react-query';
import type { ThreadView } from '@/api/schema';

/* Plain shapes and rules for threads, mail, texts and updates: no server calls, no React (queries.ts and the pages
   share them, and the tests read them directly). */

export interface Member {
  actor: string;
  role?: string;
}

/** The viewer's own side of a thread (backend threads.py `_me`). */
export interface Mine {
  member?: boolean;
  following?: boolean;
  notify?: 'live' | 'mentions' | 'digest' | 'muted' | string;
  last_read_seq?: number;
  unread?: number;
  activity?: boolean;
  can_post?: boolean;
  can_manage?: boolean;
  pinned?: boolean;
}

/** A thread as the list and the head read it: the view plus what the server adds (`name`, `dm_with`). */
export type Thread = Omit<ThreadView, 'me' | 'members'> & {
  name?: string;
  dm_with?: { actor: string; name?: string }[];
  dm_with_count?: number;
  me?: Mine;
  members?: { members?: Member[]; next_cursor?: string | null };
};

export interface ListPage {
  rows: Thread[];
  next: string | null;
}
export type ThreadList = InfiniteData<ListPage, unknown>;

const time = (t: Thread) => (t.last_message_at ? Date.parse(t.last_message_at) : 0);
const newestFirst = (a: Thread, b: Thread) => time(b) - time(a);

/** One page of GET /api/v2/threads: its threads and its DMs, one list, newest first. */
export function toPage(body: { threads?: Thread[]; dms?: Record<string, unknown>[]; next_cursor?: string | null }): ListPage {
  const rows = [...(body.threads ?? []), ...((body.dms ?? []) as Thread[])].filter((t) => !t.archived_at && !t.parent_id);
  return { rows: rows.sort(newestFirst), next: body.next_cursor ?? null };
}

/** Every loaded row once, newest first (a row bumped by a live event may sit in a later page). */
export function rowsOf(data: ThreadList | undefined): Thread[] {
  if (!data) return [];
  const seen = new Set<string>();
  const out: Thread[] = [];
  for (const p of data.pages)
    for (const t of p.rows)
      if (!seen.has(t.id)) {
        seen.add(t.id);
        out.push(t);
      }
  return out.sort(newestFirst);
}

export const isDm = (t: Pick<Thread, 'audience'> | undefined) => t?.audience === 'personal' || t?.audience === 'participants';

/** Who else is in it: `dm_with`, else the members read with the view. The viewer is left out. */
export function othersOf(t: Thread, me: string | undefined): string[] {
  const list = t.dm_with ?? t.members?.members ?? [];
  return list.map((m) => m.actor).filter((a) => a && a !== me);
}

/** The one name a thread shows, as the old app picks it (ui/app/threads.ts threadTitle). */
export function titleOf(t: Thread, me: string | undefined, nameOf: (actor: string) => string | undefined): string {
  const others = othersOf(t, me);
  const total = Number(t.dm_with_count) || others.length;
  const name = (a: string) => t.dm_with?.find((m) => m.actor === a)?.name || nameOf(a) || a.slice(a.indexOf(':') + 1);
  const people = others.length ? others.slice(0, 3).map(name).join(', ') + (total > 3 ? ` +${total - 3}` : '') : '';
  const own = (t.title ?? '').trim();
  if (isDm(t) && !t.parent_id && people && !(total >= 2 && own && !/^(chat with|private\b)/i.test(own))) return people;
  if (own) return own;
  const home = typeof t.home?.actor === 'string' ? t.home.actor : '';
  if (t.audience === 'home' && home) return name(home);
  if (t.name && !isDm(t)) return t.name;
  return people || t.name || 'Thread';
}

export const unreadOf = (t: Thread | undefined) => Number(t?.me?.unread) || 0;
export const isMuted = (t: Thread | undefined) => t?.me?.notify === 'muted';

/* ---------------------------------------------------------------- live: the `threads` topic */

/** One frame of the `threads` topic (docs/dev/threads-contract.md 9). */
export interface ThreadEvent {
  thread_id?: string;
  root_id?: string;
  kind?: string;
  thread?: Partial<Thread> & { id?: string };
  last_read_seq?: number;
  last_seq?: number;
  last_unread_seq?: number;
  unread?: boolean;
  unread_count?: number;
  message_id?: string;
}

const FULL = new Set(['created', 'renamed', 'unarchived', 'audience', 'members']);

function mapRows(data: ThreadList, fn: (t: Thread) => Thread | null): ThreadList {
  let changed = false;
  const pages = data.pages.map((p) => {
    const rows: Thread[] = [];
    for (const t of p.rows) {
      const next = fn(t);
      if (next !== t) changed = true;
      if (next) rows.push(next);
    }
    return changed ? { ...p, rows } : p;
  });
  return changed ? { ...data, pages } : data;
}

const has = (data: ThreadList, id: string) => data.pages.some((p) => p.rows.some((t) => t.id === id));

function putFirst(data: ThreadList, row: Thread): ThreadList {
  const rest = mapRows(data, (t) => (t.id === row.id ? null : t));
  const [first, ...more] = rest.pages;
  if (!first) return data;
  return { ...rest, pages: [{ ...first, rows: [row, ...first.rows] }, ...more] };
}

/**
 * The list after one event. Returns the same object when nothing it holds changed, and null when the event says
 * something the list cannot place by itself (a row it never read): the caller reads the first page again.
 */
export function patchList(data: ThreadList, e: ThreadEvent): ThreadList | null {
  const id = e.root_id ?? e.thread_id;
  if (!id) return data;
  const sub = !!e.thread_id && e.thread_id !== id;
  switch (e.kind) {
    case 'archived':
      return mapRows(data, (t) => (t.id === id ? null : t));
    case 'read':
      return mapRows(data, (t) => (t.id === id && unreadOf(t) ? { ...t, me: { ...t.me, unread: 0, activity: false } } : t));
    case 'counters':
      if (sub) return data;
      return mapRows(data, (t) =>
        t.id === id ? { ...t, last_seq: e.last_seq ?? t.last_seq, last_unread_seq: e.last_unread_seq ?? t.last_unread_seq } : t,
      );
    case 'activity': {
      const held = data.pages.flatMap((p) => p.rows).find((t) => t.id === id);
      if (!held) return e.thread && !sub && e.thread.id === id ? putFirst(data, e.thread as Thread) : null;
      const unread = typeof e.unread_count === 'number' ? e.unread_count : unreadOf(held) + (e.unread ? 1 : 0);
      const at = e.thread?.last_message_at ?? new Date().toISOString();
      return putFirst(data, { ...held, ...(sub ? {} : e.thread), id, last_message_at: at, me: { ...held.me, unread } });
    }
    default:
      if (!FULL.has(e.kind ?? '') || sub || !e.thread?.id) return data;
      if (e.thread.archived_at || e.thread.parent_id) return mapRows(data, (t) => (t.id === id ? null : t));
      if (!has(data, id)) return putFirst(data, e.thread as Thread);
      return mapRows(data, (t) => (t.id === id ? { ...t, ...e.thread } : t));
  }
}

/* ---------------------------------------------------------------- mail and texts */

export interface Letter {
  msg_id: string;
  thread_id: string;
  mailbox: string;
  epoch?: number;
  date?: string;
  from_addr?: string;
  from_header?: string;
  to?: string[];
  cc?: string[];
  subject?: string;
  snippet?: string;
  labels?: string[];
  has_attachment?: boolean;
  body?: string;
}

/** A mail row: one per Gmail thread, its newest letter, and how many letters it holds in what is loaded. */
export interface LetterRow {
  letter: Letter;
  count: number;
}

export function letterRows(letters: Letter[]): LetterRow[] {
  const by = new Map<string, LetterRow>();
  for (const l of letters) {
    const row = by.get(l.thread_id);
    if (!row) by.set(l.thread_id, { letter: l, count: 1 });
    else {
      row.count += 1;
      if ((l.epoch ?? 0) > (row.letter.epoch ?? 0)) row.letter = l;
    }
  }
  return [...by.values()];
}

/** "Maya Okafor <maya@…>" → "Maya Okafor"; a bare address stays. */
export function senderName(l: Pick<Letter, 'from_header' | 'from_addr'>): string {
  const h = (l.from_header ?? '').trim();
  const m = /^"?([^"<]+?)"?\s*</.exec(h);
  return m?.[1]?.trim() || l.from_addr || h || 'Unknown';
}

export const letterUnread = (l: Letter) => !!l.labels?.includes('UNREAD');
export const letterAt = (l: Letter) => (l.epoch ? new Date(l.epoch * 1000) : (l.date ?? new Date(0)));

export interface Text {
  message_id: string;
  kind?: string;
  received_at: string;
  from?: string;
  subject?: string | null;
  preview?: string;
  body?: string;
  text?: string;
}

/* ---------------------------------------------------------------- updates */

export interface Update {
  id: string;
  /** An agent's slug, or human:<id>. */
  bot: string;
  kind: 'daily' | 'weekly' | string;
  day?: string;
  body?: string;
  slides?: { goal?: string; done?: string; focus?: string; blockers?: string } | null;
  read?: boolean;
  archived?: boolean;
  created_at: string;
  updated_at?: string;
}

/** An update has no title: its first line, without markdown marks. */
export function updateTitle(u: Update): string {
  const line = (u.body ?? '')
    .split('\n')
    .map((l) => l.replace(/^[#>*\-\s]+/, '').replace(/[*_`]/g, '').trim())
    .find(Boolean);
  return line || u.slides?.goal || (u.kind === 'weekly' ? 'Weekly update' : 'Daily update');
}

/** The update's paragraphs after the title line, as plain text. */
export function updateParagraphs(u: Update): string[] {
  const blocks = (u.body ?? '').split(/\n\s*\n/).map((b) => b.replace(/^[#>\s]+/gm, '').trim()).filter(Boolean);
  return blocks.slice(1);
}

const dayKey = (u: Update) => u.day || u.created_at.slice(0, 10);

/** Newest day first, each day's updates newest first. */
export function byDay(list: Update[]): { day: string; rows: Update[] }[] {
  const days = new Map<string, Update[]>();
  for (const u of list) {
    const k = dayKey(u);
    days.set(k, [...(days.get(k) ?? []), u]);
  }
  return [...days.entries()]
    .sort(([a], [b]) => (a < b ? 1 : -1))
    .map(([day, rows]) => ({ day, rows: rows.sort((a, b) => (a.created_at < b.created_at ? 1 : -1)) }));
}

/** "Today", "Yesterday", else the weekday and date. */
export function dayLabel(day: string, now = new Date()): string {
  const d = new Date(`${day}T12:00:00`);
  const today = new Date(now);
  today.setHours(12, 0, 0, 0);
  const diff = Math.round((today.getTime() - d.getTime()) / 864e5);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}
