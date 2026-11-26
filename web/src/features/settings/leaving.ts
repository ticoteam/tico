import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';

/** One bot that removing someone touches. */
export type LeavingBot = { id: string; slug: string; name: string };
type Named = { id: string; name: string };

/** What removing a person would do, changing nothing (GET /api/v2/humans/{pid}/leaving, backend/leaving.py). */
export type Leaving = {
  person: Named;
  plans: { id: string; name: string; kind: string; bots: LeavingBot[] }[];
  bots: LeavingBot[];
  reports: Named[];
  to: Named | null;
  tasks: number;
  asks: number;
  message_bot: (LeavingBot & { address: string }) | null;
  groups: Named[];
};

/** What Restore brought back and what it could not (POST /api/v2/access/humans/{pid} with left: false). */
export type Restored = { name?: string; reports: Named[]; plans: { name: string; kind: string }[]; bots: LeavingBot[] };

export const leavingQuery = (pid: string) =>
  queryOptions({
    queryKey: ['settings', 'leaving', pid],
    queryFn: ({ signal }) => api.get<Leaving>(`/api/v2/humans/${encodeURIComponent(pid)}/leaving`, { signal }),
    staleTime: 0,
  });

const count = (n: number, one: string, many: string) => (n === 1 ? `1 ${one}` : `${n} ${many}`);

/** "Builder, Support and Finance". */
export function andList(names: string[]): string {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** The Remove button: it names how many bots lose their model, else plain Remove. */
export function removeLabel(v: Leaving | undefined): string {
  const n = v?.bots.length ?? 0;
  return n ? `Remove, and stop ${n === 1 ? "1 bot's" : `${n} bots'`} model` : 'Remove';
}

/** The lines after the plans, each left out when it has nothing in it. */
export function otherLines(v: Leaving, name: string): string[] {
  const lines: string[] = [];
  if (v.reports.length) {
    const names = v.reports.map((r) => r.name).join(', ');
    lines.push(`${count(v.reports.length, 'report moves', 'reports move')} to ${v.to?.name ?? 'the top of the chart'}: ${names}.`);
  }
  const waits = [v.tasks ? count(v.tasks, 'open task', 'open tasks') : '', v.asks ? count(v.asks, 'ask', 'asks') : ''].filter(Boolean);
  if (waits.length) lines.push(`${waits.join(' and ')} ${v.tasks + v.asks === 1 ? 'waits' : 'wait'} on ${name}; they will show in your Needs you.`);
  if (v.message_bot) lines.push(`${v.message_bot.name} stops reading ${v.message_bot.address || 'their mailbox'}.`);
  if (v.groups.length) lines.push(`${andList(v.groups.map((g) => g.name))} ${v.groups.length === 1 ? 'has' : 'have'} no lead after this.`);
  return lines;
}

/** Whether removing them moves anything beyond their sign-in: if not, the dialog reads as it always did. */
export function movesAnything(v: Leaving | undefined): boolean {
  return !!v && (v.plans.length > 0 || otherLines(v, '').length > 0);
}

/** What Restore says: who is back, the reports that moved back, and each plan the person must reconnect. */
export function restoredText(out: { restored?: Restored } | undefined, fallback: string): string {
  const r = out?.restored;
  if (!r) return 'Restored';
  const name = r.name || fallback;
  const parts = [`${name} is back.`];
  if (r.reports.length) parts.push(`${count(r.reports.length, 'report', 'reports')} moved back: ${r.reports.map((p) => p.name).join(', ')}.`);
  for (const plan of r.plans) {
    const bots = r.bots.length ? `, then set ${andList(r.bots.map((b) => b.name))} to it` : '';
    parts.push(`Their ${plan.name} was removed: ${name} reconnects it in Settings > Models${bots}.`);
  }
  return parts.join(' ');
}
