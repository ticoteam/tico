// The words a chat puts on time: "10:02 AM" beside a message, "Today", "Yesterday" or "Tue, Nov 17" on a day break.

const time = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });
const day = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
const dayYear = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
const full = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

/** "Wed, Nov 18, 9:21 AM": when a letter or an update was written. */
export const stamp = (at: Date | string | undefined) => {
  const d = at instanceof Date ? at : parse(at);
  return d && Number.isFinite(d.getTime()) ? full.format(d) : '';
};

const parse = (iso: string | undefined) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t) : null;
};

export const clock = (iso: string | undefined) => {
  const d = parse(iso);
  return d ? time.format(d) : '';
};

/** A key that changes when the calendar day does (the person's own time zone). */
export const dayKey = (iso: string | undefined) => {
  const d = parse(iso);
  return d ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` : '';
};

export function dayLabel(iso: string | undefined, now = new Date()): string {
  const d = parse(iso);
  if (!d) return '';
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(now) - start(d)) / 86_400_000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return (d.getFullYear() === now.getFullYear() ? day : dayYear).format(d);
}

/** "21s", "4m", "1h 5m": how long a reply's steps took. */
export function took(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ''}`;
}
