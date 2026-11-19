// The calendar's arithmetic: which days a view shows, its steps, its words, and where a thing sits in the hours.
// Pure functions on local days ("2026-11-18"), so tests need no clock and no server.
import type { CalendarOccurrence } from '@/api/schema';

export type CalView = 'day' | '3day' | 'week' | 'month' | 'list';
export const VIEWS: readonly CalView[] = ['day', '3day', 'week', 'month', 'list'];

/** A meeting is solid, a due date outlined, a routine's run dashed (cal.css .km-m / -d / -r). */
export type Mark = 'm' | 'd' | 'r';
export const markOf = (o: Pick<CalendarOccurrence, 'kind'>): Mark => (o.kind === 'run' ? 'r' : o.kind === 'event' ? 'm' : 'd');

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export function parseDay(s: string | undefined | null): Date | null {
  const m = DAY_RE.exec(s ?? '');
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return dayKey(d) === s ? d : null;
}

export const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d: Date) => addDays(d, -d.getDay());

/** The days a view draws: [from, to) as local midnights, and for Month how many weeks. */
export interface Range {
  from: Date;
  to: Date;
  days: Date[];
  weeks: number;
}

export function rangeOf(view: CalView, day: Date): Range {
  let from: Date;
  let n: number;
  if (view === 'day') [from, n] = [day, 1];
  else if (view === '3day') [from, n] = [day, 3];
  else if (view === 'week') [from, n] = [startOfWeek(day), 7];
  else if (view === 'list') [from, n] = [day, 7];
  else {
    const first = new Date(day.getFullYear(), day.getMonth(), 1);
    from = startOfWeek(first);
    const last = new Date(day.getFullYear(), day.getMonth() + 1, 0).getDate();
    n = Math.ceil((first.getDay() + last) / 7) * 7;
  }
  const days = Array.from({ length: n }, (_, i) => addDays(from, i));
  return { from, to: addDays(from, n), days, weeks: Math.ceil(n / 7) };
}

/** One step back or on: a day, three days, a week, a month (to its first), a week of the list. */
export function step(view: CalView, day: Date, dir: -1 | 1): Date {
  if (view === 'day') return addDays(day, dir);
  if (view === '3day') return addDays(day, 3 * dir);
  if (view === 'month') return new Date(day.getFullYear(), day.getMonth() + dir, 1);
  return addDays(day, 7 * dir);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const LONG_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
export const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** "Nov 18" */
export const md = (d: Date) => `${MONTHS[d.getMonth()]} ${d.getDate()}`;
/** "Wed, Nov 18" */
export const dl = (d: Date) => `${DOW[d.getDay()]}, ${md(d)}`;

/** The range in the head: "Nov 15 to Nov 21" (a week or three days), "November 2026", "Wed, Nov 18". */
export function rangeWords(view: CalView, day: Date): string {
  if (view === 'day') return dl(day);
  if (view === 'month') return `${LONG_MONTHS[day.getMonth()]} ${day.getFullYear()}`;
  const r = rangeOf(view, day);
  return `${md(r.from)} to ${md(addDays(r.to, -1))}`;
}

/** The phone's short range: "Nov 16 – 22", "Nov 30 – Dec 2", "Nov 2026", "Nov 18". */
export function shortRange(view: CalView, day: Date): string {
  if (view === 'day') return md(day);
  if (view === 'month') return `${MONTHS[day.getMonth()]} ${day.getFullYear()}`;
  const r = rangeOf(view, day);
  const last = addDays(r.to, -1);
  return `${md(r.from)} – ${last.getMonth() === r.from.getMonth() ? last.getDate() : md(last)}`;
}

/** "Today", "Tomorrow", "Yesterday", else "Wed, Nov 18". */
export function dayName(d: Date, today: Date): string {
  const off = Math.round(
    (Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86_400_000,
  );
  return off === 0 ? 'Today' : off === 1 ? 'Tomorrow' : off === -1 ? 'Yesterday' : dl(d);
}

/** Minutes after local midnight: 630 is 10:30 AM. */
export const minutes = (d: Date) => d.getHours() * 60 + d.getMinutes();

/** "10:30 AM" */
export const clock = (m: number) => `${((Math.floor(m / 60) + 11) % 12) + 1}:${String(m % 60).padStart(2, '0')} ${m % 1440 < 720 ? 'AM' : 'PM'}`;
/** "10 AM", for the hours down the side. */
export const hourWord = (h: number) => `${((h + 11) % 12) + 1} ${h < 12 ? 'AM' : 'PM'}`;
/** "45m", "1h", "1h 15m" */
export function lengthWords(mins: number): string {
  const n = Math.max(0, Math.round(mins));
  return n >= 60 ? `${Math.floor(n / 60)}h${n % 60 ? ` ${n % 60}m` : ''}` : `${n}m`;
}
/** A running call: "4:12", or "1:04:12". */
export function running(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(h ? 2 : 1, '0');
  return `${h ? `${h}:` : ''}${mm}:${String(s % 60).padStart(2, '0')}`;
}

/** An occurrence as the grid needs it, on one day. */
export interface Placed {
  o: CalendarOccurrence;
  /** Minutes after this day's midnight; an all-day thing has none. */
  s: number | null;
  e: number | null;
}

/** What falls on `day`: all-day things by date, timed things clipped to the day. */
export function onDay(list: readonly CalendarOccurrence[], day: Date): Placed[] {
  const lo = day.getTime();
  const hi = addDays(day, 1).getTime();
  const key = dayKey(day);
  const out: Placed[] = [];
  for (const o of list) {
    if (o.all_day) {
      const from = o.start.slice(0, 10);
      const to = o.end ? o.end.slice(0, 10) : from;
      if (from <= key && (key < to || key === from)) out.push({ o, s: null, e: null });
      continue;
    }
    const a = new Date(o.start).getTime();
    const b = o.end ? new Date(o.end).getTime() : a;
    if (b < lo || a >= hi || (b === lo && a < lo)) continue;
    const s = a <= lo ? 0 : minutes(new Date(a));
    const e = b >= hi ? 1440 : minutes(new Date(b));
    out.push({ o, s, e: o.end ? Math.max(e, s) : null });
  }
  return out.sort((x, y) => (x.s ?? -1) - (y.s ?? -1));
}

/** A block in a day column: its column of `cols`; past `cap` columns the rest fold into one "n more". */
export interface Block {
  p?: Placed;
  s: number;
  e2: number;
  col: number;
  cols: number;
  /** Week: the later one lies over the earlier, set in from its left. */
  over?: boolean;
  /** Minutes until the next block starts over this one (its title takes only the lines it has). */
  clear?: number;
  more?: number;
}

/** Ported from cal.js `place`: things that overlap stand side by side, or (in a week) lie over each other. */
export function place(evs: readonly Placed[], cap: number, over: boolean): Block[] {
  const list = evs
    .filter((p) => p.s != null)
    .map((p) => ({ p, s: p.s as number, e2: Math.max(p.e ?? 0, (p.s as number) + 30), col: 0, cols: 1 }) as Block)
    .sort((a, b) => a.s - b.s || b.e2 - a.e2);
  const out: Block[] = [];
  let group: Block[] = [];
  let end = -1;
  const flush = () => {
    const cols: number[] = [];
    for (const ev of group) {
      let c = cols.findIndex((t) => t <= ev.s);
      if (c < 0) {
        c = cols.length;
        cols.push(0);
      }
      cols[c] = ev.e2;
      ev.col = c;
    }
    if (over)
      for (const ev of group) {
        const next = group.filter((x) => x.s > ev.s).map((x) => x.s);
        ev.clear = next.length ? Math.min(...next) - ev.s : 0;
        ev.col = Math.min(ev.col, 2);
        ev.cols = 1;
        ev.over = ev.col > 0;
        out.push(ev);
      }
    else if (cols.length <= cap)
      for (const ev of group) {
        ev.cols = cols.length;
        out.push(ev);
      }
    else {
      const hid = group.filter((ev) => ev.col >= cap - 1);
      for (const ev of group.filter((x) => x.col < cap - 1)) {
        ev.cols = cap;
        out.push(ev);
      }
      out.push({ more: hid.length, s: Math.min(...hid.map((h) => h.s)), e2: Math.max(...hid.map((h) => h.e2)), col: cap - 1, cols: cap });
    }
    group = [];
  };
  for (const ev of list) {
    if (group.length && ev.s >= end) flush();
    group.push(ev);
    end = Math.max(end, ev.e2);
  }
  if (group.length) flush();
  return out;
}

/** The hours the grid draws: 7 AM to 8 PM (9 PM in Day), wider when something sits outside them. */
export function hoursFor(placed: readonly Placed[], view: CalView): [number, number] {
  let h0 = 7;
  let h1 = view === 'day' ? 21 : 20;
  for (const p of placed) {
    if (p.s == null) continue;
    h0 = Math.min(h0, Math.floor(p.s / 60));
    h1 = Math.max(h1, Math.ceil((p.e ?? p.s + 30) / 60));
  }
  return [h0, Math.min(24, h1)];
}

/** On now: a meeting whose time holds `now`. */
export const isLive = (o: CalendarOccurrence, now: Date) => o.kind === 'event' && !o.all_day && new Date(o.start) <= now && now < new Date(o.end);

/** Over: it ended before now (all day: its day is before today). */
export function isPast(o: CalendarOccurrence, now: Date): boolean {
  if (o.all_day) return (o.end || o.start).slice(0, 10) <= dayKey(now) && o.start.slice(0, 10) < dayKey(now);
  return new Date(o.end || o.start) <= now;
}

/** "Seed box pricing, Wed, Nov 18, 10:30 AM to 11:15 AM" */
export function tip(o: CalendarOccurrence): string {
  const a = new Date(o.start);
  if (o.all_day) return `${o.title}, ${dl(parseDay(o.start.slice(0, 10)) ?? a)}`;
  return `${o.title}, ${dl(a)}, ${clock(minutes(a))}${o.end ? ` to ${clock(minutes(new Date(o.end)))}` : ''}`;
}

/** What the panel names the kind. */
export function kindWords(o: CalendarOccurrence): string {
  if (o.kind === 'run') return 'Routine';
  if (o.kind === 'due') return 'Due date';
  if (o.kind === 'away') return 'Away';
  if (o.meeting_url) return /meet\.google\.com/.test(o.meeting_url) ? 'Google Meet' : 'Video call';
  return o.links.meeting ? 'Voice call' : 'Event';
}

/** How to join a meeting: a Tico call by its id, or another app's link in a new tab. Null when it has neither. */
export type JoinWay = { call: string } | { url: string };
export function joinOf(o: CalendarOccurrence): JoinWay | null {
  const inApp = /\/call\/([0-9a-f-]{32,36})(?:[/?#]|$)/i.exec(o.meeting_url ?? '');
  if (inApp) return { call: inApp[1] as string };
  if (o.meeting_url) return { url: o.meeting_url };
  // A Tico call's event is named by the call's id without its dashes (backend/calls.py).
  const hex = o.can.join && o.home === 'tico' ? /^[0-9a-f]{32}$/i.exec(o.event_id ?? '') : null;
  if (hex) {
    const h = hex[0];
    return { call: `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}` };
  }
  return null;
}

/** The parts of a call a thread's header and card read (a Call, or a thread's call list row). */
export interface CallSeen {
  state: 'scheduled' | 'live' | 'ending' | 'ended';
  provider: 'tico' | 'google_meet';
  status_source: 'google' | 'recorder' | null;
  live_count: number;
  participants?: readonly { actor: string; in_call: boolean; joined_at: string; left_at: string | null; removed_at: string | null }[];
  bots?: readonly string[];
}

/** A Google Meet nothing reports on: Tico cannot know who is in it. */
const blind = (c: CallSeen) => c.provider === 'google_meet' && !c.status_source;
const stayed = (p: NonNullable<CallSeen['participants']>[number]) => !!p.joined_at && !p.left_at && !p.removed_at;

/** Who is in it now: the status source's count while there is one, else who joined and its agents (thread-call.ts callCount). */
export function callCount(c: CallSeen): number {
  if (blind(c)) return 0;
  if (c.status_source) return c.live_count || 0;
  return (c.participants ?? []).filter(stayed).length + (c.state === 'live' ? (c.bots ?? []).length : 0);
}

/** The faces on a live call's card: who the status source sees, else who joined. */
export function callFaces(c: CallSeen): string[] {
  if (blind(c)) return [];
  return (c.participants ?? []).filter((p) => (c.status_source ? p.in_call : stayed(p))).map((p) => p.actor);
}

/** When a booked call is: "2:30 PM" today, "Thu, Nov 19, 2:30 PM" another day. */
export function callWhen(at: string | null | undefined, now: Date): string {
  if (!at) return '';
  const d = new Date(at);
  const t = clock(minutes(d));
  return dayKey(d) === dayKey(now) ? t : `${dl(d)}, ${t}`;
}

/** How long a call was: "45s", "23m", "1h 5m". */
export const callLength = (s: number | null | undefined): string => (s == null ? '' : s < 60 ? `${Math.max(0, Math.round(s))}s` : lengthWords(s / 60));

/** The `panel` search value: `event:<occurrence id>` or `meeting:<meeting id>`. */
export type PanelRef = { kind: 'event' | 'meeting'; id: string };
export function panelOf(value: string | undefined): PanelRef | null {
  const m = /^(event|meeting):(.+)$/.exec(value ?? '');
  return m ? { kind: m[1] as PanelRef['kind'], id: m[2] as string } : null;
}

/** List's filter (cal-merge.js filterMenu): which meetings, what they kept, and more kinds than meetings. */
export const REVIEWS = ['pending', 'dismissed', 'shared'] as const;
export const HAS = ['summary', 'recording', 'tasks'] as const;
export const ALSO = ['due', 'run'] as const;
export type Has = (typeof HAS)[number];
export type Also = (typeof ALSO)[number];

/** The calendar's address, checked: a view and a date, the open panel; in List, words to find and the filter. */
export interface CalendarSearch {
  view?: CalView;
  date?: string;
  panel?: string;
  q?: string;
  review?: (typeof REVIEWS)[number];
  /** Comma separated: "summary,tasks". */
  has?: string;
  also?: string;
}

const listOf = <T extends string>(all: readonly T[], raw: unknown): T[] => (typeof raw === 'string' ? all.filter((x) => raw.split(',').includes(x)) : []);

/** A comma list in the address with one value turned on or off, or undefined when it is empty. */
export function toggled<T extends string>(all: readonly T[], raw: string | undefined, v: T): string | undefined {
  const now = listOf(all, raw);
  const next = now.includes(v) ? now.filter((x) => x !== v) : [...now, v];
  return next.length ? all.filter((x) => next.includes(x)).join(',') : undefined;
}
export const hasOf = (raw: string | undefined): Has[] => listOf(HAS, raw);
export const alsoOf = (raw: string | undefined): Also[] => listOf(ALSO, raw);

export function calendarSearch(raw: Record<string, unknown>): CalendarSearch {
  const out: CalendarSearch = {};
  if (typeof raw.view === 'string' && (VIEWS as readonly string[]).includes(raw.view)) out.view = raw.view as CalView;
  if (typeof raw.date === 'string' && parseDay(raw.date)) out.date = raw.date;
  if (typeof raw.panel === 'string' && panelOf(raw.panel)) out.panel = raw.panel;
  if (typeof raw.q === 'string' && raw.q.trim()) out.q = raw.q.slice(0, 200);
  if (typeof raw.review === 'string' && (REVIEWS as readonly string[]).includes(raw.review)) out.review = raw.review as CalendarSearch['review'];
  const has = listOf(HAS, raw.has);
  if (has.length) out.has = has.join(',');
  const also = listOf(ALSO, raw.also);
  if (also.length) out.also = also.join(',');
  return out;
}
