import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { run, useAction } from '@/actions';
import type { TaskRequirement } from '@/api/schema';
import { clock } from '@/conversation';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { FlowMark } from '@/kit/FlowMark';
import { toast } from '@/kit/Toast';
import { changesAskedOf, decisionName, decisionOf, effectOf, emailOf, failedToast, personOf, reasonOf, tabAddress } from './model';
import { Face } from './parts';
import { decisionShownQuery, emailSendQuery, waitingQuery, type TaskData } from './queries';

/* The decision (plan/approvals.html): one box wherever a request waits on the person. It names the thing, who asks and
   what Approve does; Request changes opens one line in it; Approve is the one filled button, bound to exactly what was
   shown. On a computer it sits at the foot of the task's chat; on a phone it holds the bottom of every tab. */

export interface DecisionProps {
  t: TaskData;
  /** Opened from Flow: once approved, the next task that waits on the person opens. */
  flow: boolean;
  /** The page's own dock (a phone's bottom); on a computer the box is in the task's chat. */
  dock?: boolean;
  /** Told while "What should change?" is open: the chat's own message box steps aside, so there is one place to type. */
  onAsking?: (asking: boolean) => void;
}

/** The decision while something waits on the person, or "Changes asked" once they asked for changes. An answer is shown
    first, which takes the request away; the box is held through the send, and after a refusal until the task's truth
    is back, so it can still say why. */
export function Decision({ t, flow, dock = false, onAsking }: DecisionProps) {
  const me = useQuery(meQuery).data?.id;
  const [held, setHeld] = useState<{ r: TaskRequirement; sending: boolean } | null>(null);
  const live = decisionOf(t);
  // The truth is back after a refusal: let go (React's "adjust state while rendering").
  if (live && held && !held.sending) setHeld(null);
  const r = live ?? held?.r;
  const asked = r ? null : changesAskedOf(t, me);
  if (!r && !asked) return null;
  const box = r ? (
    <Answer t={t} r={r} flow={flow} onAsking={onAsking} hold={(x, sending = false) => setHeld(x && { r: x, sending })} />
  ) : (
    <Asked t={t} r={asked as TaskRequirement} />
  );
  return dock ? (
    <div className="dock" data-dock>
      {box}
    </div>
  ) : (
    box
  );
}

/** The thing's name and what Approve does: an email is named by who it goes to, read from its own preview. */
function useNamed(t: TaskData, r: TaskRequirement) {
  const send = emailOf(r);
  const mail = useQuery({ ...emailSendQuery(t.id, send ?? ''), enabled: !!send });
  const to = mail.data?.send.recipients[0];
  return { name: decisionName(t, r, to ? personOf(to) : undefined), effect: effectOf(t, r) };
}

/** The face of who asks: the agent that asked, else whoever has the task. */
const askerOf = (t: TaskData, r: TaskRequirement) => r.asked_by ?? t.owner_id;

/** The version key of a stage's own OK, which has no hash: the page version it is asked on. */
const pageKey = (r: TaskRequirement) =>
  r.approval?.page_version ?? r.page?.page_version ?? `${r.approval?.file_id ?? r.page?.file_id ?? ''}@${r.approval?.version ?? r.page?.version ?? ''}`;

interface AnswerProps {
  t: TaskData;
  r: TaskRequirement;
  flow: boolean;
  onAsking?: (asking: boolean) => void;
  /** Told which requirement is being answered (`sending` while it is sent), and null once the answer is taken. */
  hold?: (r: TaskRequirement | null, sending?: boolean) => void;
}

/** The decision itself: the thing's name, who asks, what Approve does, then Request changes and Approve. */
export function Answer({ t, r, flow, onAsking, hold }: AnswerProps) {
  const approve = useAction('task.approve');
  const request = useAction('task.requestChanges');
  const navigate = useNavigate();
  const waiting = useQuery({ ...waitingQuery, enabled: flow });
  const shown = useQuery(decisionShownQuery(t.id, r.requirement_id, t.version));
  const { name, effect } = useNamed(t, r);
  const [error, setError] = useState('');
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState('');
  const may = (v: 'ok' | 'no') => r.may.includes(v);
  const busy = approve.pending || request.pending;

  // What Approve binds to, and what the person first saw: when it moves while the box is open, it says Changed.
  const key = shown.data ? (shown.data.hash ?? pageKey(r)) : null;
  const [seen, setSeen] = useState<string | null>(null);
  if (key !== null && seen === null) setSeen(key);
  const changed = key !== null && seen !== null && key !== seen;
  const ready = !!shown.data?.complete && !shown.isPlaceholderData;

  useEffect(() => {
    if (!asking) return;
    onAsking?.(true);
    return () => onAsking?.(false);
  }, [asking, onAsking]);

  // Picked before answering: the answered row leaves the list at once.
  const nextUp = () => {
    const rows = waiting.data ?? [];
    const i = rows.findIndex((w) => w.id === t.id);
    return (i >= 0 ? (rows[i + 1] ?? rows[i - 1]) : rows[0]) ?? null;
  };

  const onApprove = async () => {
    setError('');
    setSeen(key);
    const next = nextUp();
    hold?.(r, true);
    const res = await approve.run({ task: t.id, requirement: r.requirement_id, shown: shown.data?.hash ?? null });
    hold?.(res.ok ? null : r);
    if (!res.ok) {
      // It moved under the person: read it again, say Changed, and wait for a new press.
      if (res.error?.startsWith('task_changed')) return void shown.refetch();
      return setError(reasonOf(res.error));
    }
    // The answer to take back: a plain answer names it; an OK bound to what was shown leaves it on the task's request.
    const out = res.out as { answer_id?: string; task?: TaskData } | undefined;
    const answer = out?.answer_id ?? out?.task?.to_leave?.find((x) => x.requirement_id === r.requirement_id)?.answer?.id;
    toast({
      text: 'Approved',
      name,
      ...(answer && {
        action: {
          label: 'Undo',
          run: () => void run('task.undoAnswer', { task: t.id, answer }, { via: 'click' }).then((u) => (u.ok ? undefined : toast(failedToast(u)))),
        },
      }),
    });
    if (!flow) return;
    const to = next ? tabAddress(next, undefined, true) : { to: '/flow', search: {} };
    void navigate({ to: to.to as never, search: to.search as never });
  };

  const onSend = async () => {
    setError('');
    hold?.(r, true);
    const res = await request.run({ task: t.id, requirement: r.requirement_id, note });
    hold?.(res.ok ? null : r);
    if (!res.ok) return setError(reasonOf(res.error));
    setAsking(false);
    setNote('');
  };

  return (
    <section className={asking ? 'answer dcx is-asking' : 'answer dcx'} aria-label="The decision" data-target={`task-answer:${t.id}`}>
      <div className="dcx-top">
        <FlowMark />
        <b className="an-name">{name}</b>
        {(changed || effect) && <span className="dcx-eff">{changed ? 'Changed' : effect}</span>}
        <span className="grow" />
        <Face id={askerOf(t, r)} />
      </div>
      {asking ? (
        <>
          <label className="dcx-ask">
            <span>What should change?</span>
            <textarea className="dcx-field" rows={2} maxLength={4000} autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="an-row">
            <span className="grow" />
            <Button disabled={request.pending} onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={request.pending} disabled={!note.trim() || request.pending} onClick={() => void onSend()}>
              Send
            </Button>
          </div>
        </>
      ) : (
        <div className="an-row">
          <span className="grow" />
          {may('no') && (
            <Button disabled={busy} onClick={() => setAsking(true)}>
              Request changes
            </Button>
          )}
          {may('ok') && (
            <Button variant="primary" busy={approve.pending} disabled={busy || !ready} onClick={() => void onApprove()}>
              Approve
            </Button>
          )}
        </div>
      )}
      {(error || shown.isError) && (
        <p className="an-err" role="alert">
          {error || 'Part of it did not load'}
        </p>
      )}
    </section>
  );
}

/** Changes asked: the request waits on the agent's new version, and is out of the person's Flow until then. */
function Asked({ t, r }: { t: TaskData; r: TaskRequirement }) {
  const { name } = useNamed(t, r);
  const at = clock(r.answer?.at);
  return (
    <section className="answer dcx is-asked" aria-label="The decision" data-target={`task-answer:${t.id}`}>
      <div className="dcx-top">
        <i className="hollow" />
        <b className="an-name">{name}</b>
        <span className="dcx-eff">{at ? `Changes asked, ${at}` : 'Changes asked'}</span>
        <span className="grow" />
        <Face id={askerOf(t, r)} />
      </div>
    </section>
  );
}
