import { QueryObserver, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { run } from '@/actions';
import type { EmailSend, TaskEmailSend } from '@/api/schema';
import { clock, dayKey, stamp } from '@/conversation';
import { queryClient } from '@/data';
import { closeToast, toast, updateToast } from '@/kit/Toast';
import { follow } from '@/live/follow';
import { failedToast, heldUntil } from './model';
import { sendStateQuery } from './queries';

/* An email the person approved, until it has gone. The toast says who it goes to and counts down the undo window, with
   Undo, then says Sent (or Not sent, which stays). The email's tab and the card of the task it was asked on carry one
   quiet line: "Sending · 1:40", then "Sent 5:23 AM". Each move of the send comes on the live stream, on its tasks'
   rows (`email_send`, features/task/queries.ts): the card reads it from its row, so every device and every person who
   reads the task sees it; the toast and the tab from the send's state, which those rows keep current. */

type Send = Pick<EmailSend, 'state' | 'send_after' | 'finished_at' | 'detail'> & { not_before?: string | null };

/** Where an approved send is: in its window (`until`, when it may still be taken back) and, held for a later day, the
    day it goes (`on`, its matter's not before); gone, or not gone and why. */
export type SendWhere =
  | { kind: 'sending'; until: number | null; on?: string }
  | { kind: 'sent'; at: string | null }
  | { kind: 'not_sent' | 'unsure'; detail: string };

/** Where a send is. A send still waiting on an OK is "sending" only when the person just gave it (`approved`): the
    server takes the OK into the send within seconds. */
export function whereOf(s: Send, approved: boolean): SendWhere | null {
  switch (s.state) {
    case 'sent':
      return { kind: 'sent', at: s.finished_at };
    case 'pending': {
      const until = Date.parse(s.send_after);
      return { kind: 'sending', until: Number.isFinite(until) ? until : null, ...(s.not_before && { on: s.not_before }) };
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

/** Where a send is for its quiet line (a card, the Email tab): a canceled send says nothing there. An agent cancels one
    to rewrite it; nothing failed, so "Not sent" would read as a fault. */
function lineWhere(s: Send, approved: boolean): SendWhere | null {
  return s.state === 'canceled' ? null : whereOf(s, approved);
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
  const day = w.on && heldUntil(w.on);
  if (day) return left ? `${day} to ${to} · undo ${left}` : `${day} to ${to}`;
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
  return [(w.on && heldUntil(w.on)) || 'Sending', left].filter(Boolean).join(' · ');
}

/** How long a card keeps saying how a send ended: a day. */
const RECENT_MS = 24 * 3600_000;

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

/** The quiet line for a send at `w`. */
function SendLine({ w, className }: { w: SendWhere | null; className: string }) {
  const now = useTick(w?.kind === 'sending' && w.until !== null);
  if (!w) return null;
  return (
    <span className={w.kind === 'not_sent' || w.kind === 'unsure' ? `${className} is-off` : className} data-send={w.kind}>
      {markLine(w, now)}
    </span>
  );
}

/** One quiet line for a send the person approved, or that has left its OK behind: nothing while it waits on an OK. */
export function SendMark({ send, approved = false, className = 'send-mark' }: { send: string; approved?: boolean; className?: string }) {
  const q = useQuery(sendStateQuery(send, approved));
  return <SendLine w={q.data ? lineWhere(q.data, approved) : null} className={className} />;
}

/** Where a task row's send is (`email_send`: the newest send that belongs to the task or whose OK sits on it), for its
    card: nothing while it waits on an OK, once it was canceled, nor a day after it ended. */
export function rowWhere(s: TaskEmailSend | null | undefined, now = Date.now()): SendWhere | null {
  if (!s) return null;
  const w = lineWhere(s, false);
  const ended = s.finished_at ? Date.parse(s.finished_at) : NaN;
  return w && w.kind !== 'sending' && Number.isFinite(ended) && now - ended > RECENT_MS ? null : w;
}

/** A card's or a row's quiet line for its task's email, from the task's own row. */
export const TaskSend = ({ send }: { send?: TaskEmailSend | null }) => <SendLine w={rowWhere(send)} className="send-mark" />;

/* ---------------------------------------------------------------- the toast after Approve */

/** How long the toast follows a send before it lets go (a send held for another reason). */
const FOLLOW_MS = 15 * 60_000;

/** After Approve on an email: "Sending to dana@… in 1:58" with Undo while the window lasts, then "Sent to dana@…", or
    "Not sent: <why>", which stays until dismissed. `answer` takes the OK back (the send waits on an OK again). */
export function followSend({ send, to, task, answer }: { send: string; to: readonly string[]; task: string; answer?: string | null }) {
  // The stream sends this task's rows, each with the send's state: no reading the send again and again.
  const unfollow = follow({ task });
  const who = toLine(to);
  let w: SendWhere = { kind: 'sending', until: null };
  let done = false;
  const undo = answer
    ? {
        label: 'Undo',
        run: () => {
          stop();
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
    unfollow();
    clearInterval(tick);
    clearTimeout(limit);
  }
  return stop;
}
