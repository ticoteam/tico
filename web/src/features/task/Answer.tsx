import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { run, useAction } from '@/actions';
import type { TaskRequirement } from '@/api/schema';
import { clock } from '@/conversation';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { Pill } from '@/kit/Pill';
import { toast } from '@/kit/Toast';
import { useFollow } from '@/live/follow';
import { askOf, changesAskedOf, decisionName, decisionOf, effectOf, emailOf, failedToast, heldUntil, personOf, reviewOf, sendTitle } from './model';
import { answered } from './answered';
import { AskCard, Docked, useAnswering } from './AskCard';
import { HeldDecision, isRuleReview } from './Held';
import { StoppedDecision } from './Stopped';
import { followSend } from './sending';
import { addressesOf, answerKey, decisionShownQuery, emailSendQuery, sendStateQuery, type AnswerKey, type BoundAction, type BoundRule, type TaskData } from './queries';
import { Facts, LabelValue } from '@/kit/LabelValue';

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
  /** The email whose tab is open: its own OK is the decision; null on another tab of the task: never an email's OK
      (its preview read counts as showing it); left out, whatever waits first. */
  email?: string | null;
  /** The ask the address names (`?ask=`, a card opened it): that requirement is the decision, off an email tab. */
  requirement?: string;
}

/** The decision while something waits on the person, or "Changes asked" once they asked for changes. An answer is shown
    first, which takes the request away; the box is held through the send, and after a refusal until the task's truth
    is back, so it can still say why. */
export function Decision({ t, flow, dock = false, onAsking, email, requirement }: DecisionProps) {
  const me = useQuery(meQuery).data?.id;
  const [held, setHeld] = useState<{ r: TaskRequirement; sending: boolean } | null>(null);
  const live = requirement && !email ? askOf(t, requirement) : decisionOf(t, email);
  // The truth is back after a refusal: let go (React's "adjust state while rendering").
  if (live && held && !held.sending) setHeld(null);
  const r = held?.sending ? held.r : (live ?? held?.r);
  const asked = r ? null : changesAskedOf(t, me, email);
  // A rule's review asks no OK: what it held waits on the person in the same box (Held.tsx), as a stopped agent's notice
  // waits on its Release (Stopped.tsx). An email tab shows only that email's own decision.
  if (!r && !asked) {
    if (typeof email === 'string') return null;
    if (isRuleReview(t)) return <HeldDecision t={t} flow={flow} dock={dock} />;
    // Only while the notice is open: Tico's own release closes it, and the stream says so before the task is read again.
    return t.stopped_agent && t.status === 'open' ? <StoppedDecision t={t} flow={flow} dock={dock} /> : null;
  }
  const box = r ? (
    <Answer key={`${t.id}:${r.requirement_id}`} t={t} r={r} flow={flow} onAsking={onAsking} hold={(x, sending = false) => setHeld(x && { r: x, sending })} />
  ) : (
    <Asked t={t} r={asked as TaskRequirement} />
  );
  return <Docked dock={dock}>{box}</Docked>;
}

/** The thing's name and what Approve does: an email is named by who it goes to, read from its own preview, else from
    the task's title while the preview is not read; one held for a later day says when it goes ("Sends Nov 25"). */
function useNamed(t: TaskData, r: TaskRequirement) {
  const send = emailOf(r);
  const mail = useQuery({ ...emailSendQuery(t.id, send ?? ''), enabled: !!send });
  const all = mail.data ? addressesOf(mail.data).to : [];
  const to = all[0] ?? sendTitle(t.title)?.to;
  // The day it is held for, live: the send's shared state (patched from its case's row) once read, an explicit null
  // there when the hold was cleared; else the preview's.
  const state = useQuery({ ...sendStateQuery(send ?? ''), enabled: false }).data;
  const notBefore = state && 'not_before' in state ? state.not_before : mail.data?.not_before;
  const effect = heldUntil(notBefore) ?? effectOf(t, r);
  return { name: decisionName(t, r, to ? personOf(to) : undefined), effect, send, recipients: all.length ? all : to ? [to] : [], files: mail.data?.files ?? [] };
}

/** One task the stream follows, for its status alone. */
function Follow({ task }: { task: string }) {
  useFollow({ task });
  return null;
}

/** The second review's state beside the ask. The stream follows each review, so the pill turns as one ends or reopens;
    one filed later arrives with the task's children, read again on the relation's event (live/patch.ts). */
function ReviewPill({ t }: { t: TaskData }) {
  const review = reviewOf(t);
  return (
    <>
      {t.children.filter((c) => c.role === 'review').map((c) => <Follow key={c.id} task={c.id} />)}
      {review && <Pill>{review}</Pill>}
    </>
  );
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
  const { error, setError, here, nextUp, refused, moveOn } = useAnswering(t, flow);
  const shown = useQuery(decisionShownQuery(t.id, r.requirement_id, t.version, emailOf(r), !!r.shows?.some((s) => s.startsWith('outbound:'))));
  const { name, effect, send, recipients, files } = useNamed(t, r);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState('');
  const [askedOn, setAskedOn] = useState<AnswerKey | undefined>(undefined);
  const may = (v: 'ok' | 'no') => r.may.includes(v);
  const busy = approve.pending || request.pending;

  // What Approve binds to, and what the person first saw: when it moves while the box is open, it says Changed.
  const preview = shown.data;
  const ready = preview?.complete === true && !shown.isPlaceholderData;
  const key = ready ? (preview.hash ?? pageKey(r)) : null;
  const [seen, setSeen] = useState<string | null>(null);
  if (key !== null && seen === null) setSeen(key);
  const changed = key !== null && seen !== null && key !== seen;
  // A refusal, or what Approve binds to did not load: said in place of what Approve does, never over it.
  const fault = !!error || shown.isError || (preview?.complete === false && !shown.isPlaceholderData);

  useEffect(() => {
    if (!asking) return;
    onAsking?.(true);
    return () => onAsking?.(false);
  }, [asking, onAsking]);

  const onApprove = async () => {
    setError('');
    setSeen(key);
    const next = nextUp();
    hold?.(r, true);
    const drawn = answerKey(r, t.visit_id); // the version and visit this card shows, never newer ones read since
    const res = await approve.run({ task: t.id, requirement: r.requirement_id, shown: preview?.hash ?? null, ...(drawn && { key: drawn }) });
    hold?.(res.ok ? null : r);
    if (!res.ok) {
      // It moved under the person: read it again, say Changed, and wait for a new press.
      if (res.error?.startsWith('task_changed') && here.current) return void shown.refetch();
      return refused(res);
    }
    // The answer to take back: a plain answer names it; an OK bound to what was shown leaves it on the task's request.
    const out = res.out as { answer_id?: string; task?: TaskData } | undefined;
    const answer = out?.answer_id ?? out?.task?.to_leave?.find((x) => x.requirement_id === r.requirement_id)?.answer?.id;
    // An email: the toast follows it through its undo window until it has gone.
    if (send) followSend({ send, to: recipients, task: t.id, answer });
    else
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
    moveOn(next);
  };

  const onSend = async () => {
    setError('');
    hold?.(r, true);
    const res = await request.run({ task: t.id, requirement: r.requirement_id, note, ...(askedOn && { key: askedOn }) });
    hold?.(res.ok ? null : r);
    if (!res.ok) return refused(res);
    setAsking(false);
    setNote('');
    if (!flow && here.current) answered();
  };

  return (
    <AskCard
      name={name}
      note={!fault && (changed ? 'Changed' : effect)}
      who={askerOf(t, r)}
      pill={<ReviewPill t={t} />}
      asking={asking}
      target={`task-answer:${t.id}`}
      actions={
        asking ? (
          <>
            <Button disabled={request.pending} onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={request.pending} disabled={!note.trim() || request.pending} onClick={() => onSend()}>
              Send
            </Button>
          </>
        ) : (
          <>
            {may('no') && (
              <Button
                disabled={busy}
                onClick={() => {
                  setAskedOn(answerKey(r, t.visit_id));
                  setAsking(true);
                }}
              >
                Request changes
              </Button>
            )}
            {may('ok') && (
              <Button variant="primary" busy={approve.pending} disabled={busy || !ready} onClick={() => onApprove()}>
                Approve
              </Button>
            )}
          </>
        )
      }
      error={
        fault && (
          <>
            {error || 'Part of it did not load'}
            {!error && (
              <Button variant="quiet" onClick={() => shown.refetch()}>
                Try again
              </Button>
            )}
          </>
        )
      }
    >
      {!asking && preview?.rule && <RuleShown rule={preview.rule} />}
      {!asking && preview?.action && <ActionShown action={preview.action} />}
      {!asking && files.length > 0 && (
        <span className="askcard-files">
          {files.map((f) => (
            <span key={f.part} className="file">
              <Icon name="file" />
              <span>{f.name}</span>
              {f.archived && <em>Archived</em>}
            </span>
          ))}
        </span>
      )}
      {asking && (
        <label className="askcard-field">
          <span>What should change?</span>
          <textarea rows={2} maxLength={4000} autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      )}
    </AskCard>
  );
}

const names = (s: { groups?: { id: string; name?: string }[]; bots?: { id: string; name?: string }[]; people?: { id: string; name?: string }[] }) =>
  [...(s.groups ?? []), ...(s.bots ?? []), ...(s.people ?? [])].map((x) => x.name || x.id);
const BROKEN_WORDS = { block: 'Stop', escalate: 'Ask first', warn: 'Warn' } as const;

/** The rule a rule's OK adds, exactly as it was asked: its words and every field that decides what it stops. */
function RuleShown({ rule }: { rule: BoundRule }) {
  // each part of Who on its own: Everyone does not hide Outside editions
  const who = [
    ...(rule.who.everyone ? ['Everyone'] : []),
    ...(rule.who.all_bots ? ['Every agent'] : []),
    ...(rule.who.outside_editions ? ['Outside editions'] : []),
    ...names(rule.who),
  ];
  const withWho = names(rule.with);
  const c = rule.exact_check;
  return (
    <div className="askcard-rule" data-target={`rule:${rule.rule}`}>
      <p className="t-read">{rule.text}</p>
      <Facts label="The rule">
        <LabelValue label="Who">{who.join(', ') || 'No one'}</LabelValue>
        <LabelValue label="With">{withWho.join(', ') || 'Anyone'}</LabelValue>
        <LabelValue label="Where">{rule.where.join(', ') || 'Everywhere'}</LabelValue>
        <LabelValue label="If broken">{BROKEN_WORDS[rule.if_broken]}</LabelValue>
        {/* a named reviewer decides its holds whatever If broken says, so it is shown whenever there is one */}
        {(rule.reviewer || rule.if_broken === 'escalate') && <LabelValue label="Ask">{rule.reviewer?.name || rule.reviewer?.id || 'Manager'}</LabelValue>}
        <LabelValue label="Checked exactly">
          {!c ? (
            'No'
          ) : c.kind === 'max_usd' ? (
            `${c.action}: ${c.field} at most $${c.max_usd}`
          ) : (
            <>
              {`${c.action}: ${c.field} only`}
              {/* one item per allowed value, so a value holding a comma never reads as two */}
              <ul className="askcard-values">
                {c.values.map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </>
          )}
        </LabelValue>
      </Facts>
    </div>
  );
}

const PAYLOAD_LABELS: Record<string, string> = { to: 'Status', body: 'Text' };

/** The outbound action an OK lets go, exactly as it was asked: where it goes, what it does, and its payload's words. */
function ActionShown({ action }: { action: BoundAction }) {
  const fields = Object.entries(action.payload).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)] as const);
  return (
    <div className="askcard-rule" data-target={`outbound:${action.channel}`}>
      <Facts label="The action">
        <LabelValue label="Channel">{action.channel}</LabelValue>
        <LabelValue label="Action">{action.operation}</LabelValue>
        <LabelValue label="Where">{action.destination}</LabelValue>
        {fields.map(([k, v]) => (
          <LabelValue key={k} label={PAYLOAD_LABELS[k] ?? k}>
            <span className="askcard-text">{v}</span>
          </LabelValue>
        ))}
      </Facts>
    </div>
  );
}

/** Changes asked: the request waits on the agent's new version, and is out of the person's Flow until then. */
function Asked({ t, r }: { t: TaskData; r: TaskRequirement }) {
  const { name } = useNamed(t, r);
  const at = clock(r.answer?.at);
  return <AskCard name={name} note={at ? `Changes asked, ${at}` : 'Changes asked'} who={askerOf(t, r)} asked target={`task-answer:${t.id}`} />;
}
