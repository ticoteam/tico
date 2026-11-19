import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { useAction } from '@/actions';
import type { TaskRequirement } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { toast } from '@/kit/Toast';
import { decisionName, decisionOf, reasonOf, tabAddress } from './model';
import { FlowMark } from '@/kit/FlowMark';
import { waitingQuery, type TaskData } from './queries';

export interface AnswerProps {
  t: TaskData;
  r: TaskRequirement;
  /** Opened from Flow: once answered, the next task that waits on the person opens. */
  flow: boolean;
  /** Told which requirement is being answered (`sending` while it is sent), and null once the answer is taken. */
  hold?: (r: TaskRequirement | null, sending?: boolean) => void;
}

/**
 * The answer card while something waits on the person. An answer is shown first, which takes the decision away; the
 * card is held through the send, and after a refusal until the task's truth is back, so it can still say why.
 */
export function Decision({ t, flow, dock = false }: { t: TaskData; flow: boolean; dock?: boolean }) {
  const [held, setHeld] = useState<{ r: TaskRequirement; sending: boolean } | null>(null);
  const live = decisionOf(t);
  // The truth is back after a refusal: let go (React's "adjust state while rendering").
  if (live && held && !held.sending) setHeld(null);
  const r = live ?? held?.r;
  if (!r) return null;
  const card = <Answer t={t} r={r} flow={flow} hold={(x, sending = false) => setHeld(x && { r: x, sending })} />;
  // The page's dock holds the card on a phone; on a desk it is in the task's chat.
  return dock ? (
    <div className="dock" data-dock>
      {card}
    </div>
  ) : (
    card
  );
}

/** The answer card (parts-kit.js answer): what is decided, Send back, Approve. A refusal says the server's reason. */
export function Answer({ t, r, flow, hold }: AnswerProps) {
  const approve = useAction('task.approve');
  const sendBack = useAction('task.sendBack');
  const navigate = useNavigate();
  const waiting = useQuery({ ...waitingQuery, enabled: flow });
  const [error, setError] = useState('');
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState('');
  const may = (v: 'ok' | 'no') => r.may.includes(v);

  // Picked before answering: the answered row leaves the list at once.
  const nextUp = () => {
    const rows = waiting.data ?? [];
    const i = rows.findIndex((w) => w.id === t.id);
    return (i >= 0 ? (rows[i + 1] ?? rows[i - 1]) : rows[0]) ?? null;
  };

  const answered = (text: string, next: ReturnType<typeof nextUp>) => {
    toast({ text });
    if (!flow) return;
    const to = next ? tabAddress(next, undefined, true) : { to: '/flow', search: {} };
    void navigate({ to: to.to as never, search: to.search as never });
  };

  const onApprove = async () => {
    setError('');
    const next = nextUp();
    hold?.(r, true);
    const res = await approve.run({ task: t.id, requirement: r.requirement_id });
    hold?.(res.ok ? null : r);
    if (!res.ok) return setError(reasonOf(res.error));
    answered('Approved', next);
  };

  const onSendBack = async () => {
    setError('');
    const next = nextUp();
    hold?.(r, true);
    const res = await sendBack.run({ task: t.id, requirement: r.requirement_id, note });
    hold?.(res.ok ? null : r);
    if (!res.ok) return setError(reasonOf(res.error));
    setAsking(false);
    setNote('');
    answered('Sent back', next);
  };

  return (
    <section className="answer tv-answer" aria-label="Answer" data-target={`task-answer:${t.id}`}>
      <div className="an-row">
        <FlowMark />
        <b className="an-name">{decisionName(t, r)}</b>
        <span className="grow" />
        {may('no') && (
          <Button disabled={sendBack.pending || approve.pending} onClick={() => setAsking(true)}>
            Send back
          </Button>
        )}
        {may('ok') && (
          <Button variant="primary" busy={approve.pending} disabled={approve.pending || sendBack.pending} onClick={() => void onApprove()}>
            Approve
          </Button>
        )}
      </div>
      {error && (
        <p className="an-err" role="alert">
          {error}
        </p>
      )}
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        title="Send back"
        actions={
          <>
            <Button onClick={() => setAsking(false)}>Cancel</Button>
            <Button variant="primary" busy={sendBack.pending} disabled={!note.trim() || sendBack.pending} onClick={() => void onSendBack()}>
              Send back
            </Button>
          </>
        }
      >
        <Field label="What needs to change" area wide autoFocus value={note} onValueChange={setNote} error={asking && error ? error : undefined} />
      </Dialog>
    </section>
  );
}
