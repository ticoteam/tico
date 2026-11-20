import { QueryObserver, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { run } from '@/actions';
import type { EmailSend } from '@/api/schema';
import { clock, dayKey, stamp } from '@/conversation';
import { queryClient } from '@/data';
import { closeToast, toast, updateToast } from '@/kit/Toast';
import { readPref, usePref, writePref } from '@/prefs';
import { failedToast } from './model';
import { sendStateQuery } from './queries';

/* An email the person approved, until it has gone. The toast says who it goes to and counts down the undo window, with
   Undo, then says Sent (or Not sent, which stays). The email's tab and the card of the task it was asked on carry one
   quiet line: "Sending · 1:40", then "Sent 5:23 AM". */

type Send = Pick<EmailSend, 'state' | 'send_after' | 'finished_at' | 'detail'>;

/** Where an approved send is: in its window (`until`, when it goes), gone, or not gone and why. */
export type SendWhere = { kind: 'sending'; until: number | null } | { kind: 'sent'; at: string | null } | { kind: 'not_sent' | 'unsure'; detail: string };

/** Where a send is. A send still waiting on an OK is "sending" only when the person just gave it (`approved`): the
    server takes the OK into the send within seconds. */
export function whereOf(s: Send, approved: boolean): SendWhere | null {
  switch (s.state) {
    case 'sent':
      return { kind: 'sent', at: s.finished_at };
    case 'pending': {
      const until = Date.parse(s.send_after);
      return { kind: 'sending', until: Number.isFinite(until) ? until : null };
    }
    case 'dispatching':
      return { kind: 'sending', until: null };
    case 'waiting_approval':
      return approved ? { kind: 'sending', until: null } : null;
    case 'failed':
    case 'canceled':
    case 'blocked':
      return { kind: 'not_sent', detail: s.detail };
    case 'unknown':
      return { kind: 'unsure', detail: s.detail };
    default:
      return null;
  }
}

/** "1:58" until it goes; null once the window is over. */
export function countdown(until: number | null, now: number): string | null {
  if (until === null) return null;
  const left = Math.ceil((until - now) / 1000);
  if (left <= 0) return null;
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
}

/** "dana@acme.com" from "Dana Ruiz <dana@acme.com>"; a second address shows as "+1". */
export function toLine(to: readonly string[]): string {
  const first = to[0] ?? '';
  const bare = /<([^>]+)>/.exec(first)?.[1] ?? first;
  return to.length > 1 ? `${bare} +${to.length - 1}` : bare;
}

/** The toast's words for where the send is. */
export function toastLine(w: SendWhere, to: string, now: number): string {
  if (w.kind === 'sent') return `Sent to ${to}`;
  if (w.kind !== 'sending') {
    const head = w.kind === 'unsure' ? 'Not confirmed' : 'Not sent';
    return w.detail ? `${head}: ${w.detail}` : head;
  }
  const left = countdown(w.until, now);
  return left ? `Sending to ${to} in ${left}` : `Sending to ${to}`;
}

/** The quiet line's words: "Sending · 1:40", "Sent 5:23 AM" (a day's date before the time on another day). */
export function markLine(w: SendWhere, now: number): string {
  if (w.kind === 'sent') {
    if (!w.at) return 'Sent';
    return `Sent ${dayKey(w.at) === dayKey(new Date(now).toISOString()) ? clock(w.at) : stamp(w.at)}`;
  }
  if (w.kind !== 'sending') return w.kind === 'unsure' ? 'Not confirmed' : 'Not sent';
  const left = countdown(w.until, now);
  return left ? ['Sending', left].join(' · ') : 'Sending';
}

/* ---------------------------------------------------------------- which tasks an approved send belongs to */

/** The tasks a send was approved on, a day at most, on this device: its card shows the line even after a reload. */
const KEY = 'approved-sends.v1';
const KEEP_MS = 24 * 3600_000;
type Kept = Record<string, { send: string; at: number }>;

const fresh = (all: Kept, now = Date.now()): Kept => Object.fromEntries(Object.entries(all).filter(([, v]) => !!v?.send && now - v.at < KEEP_MS));

/** Remember that these tasks' send is `send` (the task it was asked on, its matter, the send's own task). */
export function keepSend(send: string, tasks: readonly (string | null | undefined)[]) {
  const all = fresh(readPref<Kept>(KEY, {}));
  const at = Date.now();
  const add = tasks.filter((t): t is string => !!t && all[t]?.send !== send);
  if (!add.length) return;
  for (const t of add) all[t] = { send, at };
  writePref(KEY, all);
}

/** Forget a send: its approval was taken back. */
export function forgetSend(send: string) {
  const all = fresh(readPref<Kept>(KEY, {}));
  writePref(KEY, Object.fromEntries(Object.entries(all).filter(([, v]) => v.send !== send)));
}

/** The approved send of the first of these tasks that has one. */
export function useSendOf(...tasks: (string | null | undefined)[]): string | null {
  const [all] = usePref<Kept>(KEY, {});
  // Kept a day at most: an older one is dropped at the next write.
  const [since] = useState(() => Date.now() - KEEP_MS);
  for (const t of tasks) {
    const v = t ? all[t] : undefined;
    if (v?.send && v.at > since) return v.send;
  }
  return null;
}

/** The time now, read again every second while `on`. */
function useTick(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [on]);
  return now;
}

/** One quiet line for a send the person approved, or that has left its OK behind: nothing while it waits on an OK. */
export function SendMark({ send, approved = false, className = 'send-mark' }: { send: string; approved?: boolean; className?: string }) {
  const q = useQuery(sendStateQuery(send, approved));
  const w = q.data ? whereOf(q.data, approved) : null;
  const now = useTick(w?.kind === 'sending' && w.until !== null);
  if (!w) return null;
  return (
    <span className={w.kind === 'not_sent' || w.kind === 'unsure' ? `${className} is-off` : className} data-send={w.kind}>
      {markLine(w, now)}
    </span>
  );
}

/** The send a task's card shows: the email approved on it, or on the task its turn waits on (a matter's Send task). */
export function useTaskSend(t: { id: string; turn?: { state: string; who: string | null } | null }): string | null {
  return useSendOf(t.id, t.turn?.state === 'tasks' ? t.turn.who : null);
}

/** A card's or a row's quiet line for its approved email. */
export const TaskSend = ({ send }: { send: string | null }) => (send ? <SendMark send={send} approved /> : null);

/* ---------------------------------------------------------------- the toast after Approve */

/** How long the toast follows a send before it lets go (a send held for another reason). */
const FOLLOW_MS = 15 * 60_000;

/** After Approve on an email: "Sending to dana@… in 1:58" with Undo while the window lasts, then "Sent to dana@…", or
    "Not sent: <why>", which stays until dismissed. `answer` takes the OK back (the send waits on an OK again). */
export function followSend({ send, to, task, answer }: { send: string; to: readonly string[]; task: string; answer?: string | null }) {
  keepSend(send, [task]);
  const who = toLine(to);
  let w: SendWhere = { kind: 'sending', until: null };
  let done = false;
  const undo = answer
    ? {
        label: 'Undo',
        run: () => {
          stop();
          forgetSend(send);
          void run('task.undoAnswer', { task, answer }, { via: 'click' }).then((u) => (u.ok ? undefined : toast(failedToast(u))));
        },
      }
    : undefined;
  const id = toast({ text: toastLine(w, who, Date.now()), kind: 'sending', timeout: 0, action: undo });
  // Undo only while it may still be taken back: before the provider has it.
  const show = (s?: Send) =>
    updateToast(id, {
      text: toastLine(w, who, Date.now()),
      kind: 'sending',
      timeout: 0,
      action: s?.state === 'dispatching' ? undefined : undo,
    });

  let last: Send | undefined;
  const observer = new QueryObserver(queryClient, { ...sendStateQuery(send, true), staleTime: 0 });
  const tick = setInterval(() => {
    if (w.kind === 'sending' && w.until !== null) show(last);
  }, 1000);
  const limit = setTimeout(() => {
    stop();
    closeToast(id);
  }, FOLLOW_MS);
  const off = observer.subscribe((r) => {
    if (done || !r.data) return;
    last = r.data;
    keepSend(send, [r.data.task_id, r.data.approval_task_id]);
    const next = whereOf(r.data, true);
    if (!next) return;
    w = next;
    if (w.kind === 'sending') return show(r.data);
    stop();
    if (w.kind === 'sent') updateToast(id, { text: toastLine(w, who, Date.now()), kind: 'done', timeout: 5000 });
    else updateToast(id, { text: toastLine(w, who, Date.now()), kind: 'failed', timeout: 0, dismiss: true });
  });
  function stop() {
    done = true;
    off();
    clearInterval(tick);
    clearTimeout(limit);
  }
  return stop;
}
