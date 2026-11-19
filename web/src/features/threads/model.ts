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
  /** An agent's Home with messages from before it had its own room, still closed to its readers. */
  earlier?: { count: number } | null;
  deletable?: boolean;
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

/* ---------------------------------------------------------------- what asks you */

/** What asks the viewer something: the Dot on a row (plan/threads-team.html "What needs you"). No number. */
export interface Asks {
  /** Thread ids. */
  threads: ReadonlySet<string>;
  /** Teammates (actor ids) whose DM with the viewer asks them. */
  mates: ReadonlySet<string>;
  /** The owners of Homes that ask (`group:<id>` for a team's thread). */
  homes: ReadonlySet<string>;
}

/**
 * A conversation asks you when Catch up flags it `mention` (an @mention, an ask, an assignment, or a person's reply to
 * you, past your read mark), or when it is a DM from a person with something you have not read. An agent's DM counts
 * only through the flag: its unread status lines ask nothing. `catchUp` is Catch up's first page (mentions come first
 * in it); `rows` the loaded list. A task's thread is the task's, not a conversation's.
 */
export function asksOf(catchUp: { items: { thread: Thread; mention?: boolean }[] } | undefined, rows: readonly Thread[], me: string | undefined): Asks {
  const threads = new Set<string>();
  const mates = new Set<string>();
  const homes = new Set<string>();
  const add = (t: Thread) => {
    if (t.task_id) return;
    threads.add(t.id);
    const other = t.audience === 'personal' ? othersOf(t, me)[0] : undefined;
    if (other) mates.add(other);
    if (t.audience === 'home' && typeof t.home?.actor === 'string') homes.add(t.home.actor);
  };
  for (const it of catchUp?.items ?? []) if (it.mention) add(it.thread);
  for (const t of rows) if (t.audience === 'personal' && unreadOf(t) > 0 && othersOf(t, me)[0]?.startsWith('human:')) add(t);
  return { threads, mates, homes };
}

/** The menu's Threads list: every conversation you are in, newest first. A task's thread belongs to its task. */
export const conversationsOf = (rows: readonly Thread[]) => rows.filter((t) => !t.task_id);

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
  /** With the body (an open mail): each file's name and bytes. */
  attachments?: { name: string; type?: string; size?: number }[];
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
  /** Its one-line summary (the body's first line, as written). */
  headline?: string;
  /** How many replies it has. */
  replies?: number;
  body?: string;
  slides?: { goal?: string; done?: string; focus?: string; blockers?: string } | null;
  read?: boolean;
  archived?: boolean;
  created_at: string;
  updated_at?: string;
}

const updateLines = (u: Update) => (u.body ?? '').split(/\r?\n/);
const titleText = (line: string) => line.replace(/^[#>*\-\s]+/, '').replace(/[*_`]/g, '').trim();

/** An update has no title: its first line, without markdown marks. */
export function updateTitle(u: Update): string {
  const line = updateLines(u).map(titleText).find(Boolean);
  return line || u.slides?.goal || (u.kind === 'weekly' ? 'Weekly update' : 'Daily update');
}

/** What an update says after its title line, as written (markdown). */
export function updateRest(u: Update): string {
  const lines = updateLines(u);
  const title = lines.findIndex(titleText);
  return title < 0 ? '' : lines.slice(title + 1).join('\n').trim();
}

const dayKey = (u: Update) => u.day || u.created_at.slice(0, 10);
/** The Monday of a day's week ("2026-01-05" for any day Jan 5 to 11). */
const weekOf = (day: string) => {
  const d = new Date(`${day}T12:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/** Newest day (or, `week`, newest week, named by its Monday) first, each group's updates newest first. */
export function byDay(list: Update[], week = false): { day: string; rows: Update[] }[] {
  const days = new Map<string, Update[]>();
  for (const u of list) {
    const k = week ? weekOf(dayKey(u)) : dayKey(u);
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
