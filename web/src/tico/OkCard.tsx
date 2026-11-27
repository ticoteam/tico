import { useQuery } from '@tanstack/react-query';
import { refusal, run } from '@/actions';
import { tasksIn, type ChatMessage } from '@/conversation';
import { AskCard, taskAddress, taskDetailQuery } from '@/features/task/exports';
import { Button } from '@/kit/Button';
import { toast } from '@/kit/Toast';
import { flowQuery, type FlowItem } from '@/features/tasks/routing';

/* Tico's approval card (plan: the assistant as a delegator): a Tico message that links one task, whose OK waits on the
   person in their Needs you, shows one card under its words. The card only opens the task, beside the chat on a
   computer or as the page on a phone (the shell's own link handling), where the existing preview and Approve are. The
   chat never reads the OK itself (GET /api/v2/oks/{rid} records that the person was shown it), never answers it, and
   fetches nothing: it reads the Needs-you list Tico's tracker already holds. A message that links more than one task
   stays linked text. */

/** The pending approval a message's text links to: its one linked task (its addresses counted as one), when an
    approval on it waits on the person; else null. A task is matched by its number or id alone, so a link written
    before the task moved to another pipeline still counts; the card opens it where it is now. */
export function linkedOk(text: string, approvals: readonly FlowItem[], origin: string): FlowItem | null {
  const found = new Map<string, FlowItem | null>();
  for (const key of tasksIn(text, origin)) {
    const ok = approvals.find((it) => it.id === key || String(it.number) === key);
    found.set(ok ? ok.id : key, ok ?? null);
  }
  return found.size === 1 ? ([...found.values()][0] ?? null) : null;
}

export function OkCard({ m }: { m: ChatMessage }) {
  const text = m.body ?? '';
  // Observed, never fetched here: the tracker over the chat reads it, and the stream keeps it current.
  const ok = useQuery({ ...flowQuery, enabled: false, select: (f) => linkedOk(text, f.approvals ?? [], location.origin) }).data;
  if (!ok) return null;
  return (
    <AskCard
      name={ok.about || ok.ask?.line || ok.title}
      who={ok.ask?.by ?? ok.owner_id}
      href={taskAddress(ok)}
      actions={<span className="btn">Review and approve</span>}
    />
  );
}

/** An OK the person's Assistant gave on their word, as its receipt's refs carry it (backend/assistant_oks.py receipt). */
export interface AssistantOk {
  answer_id: string;
  task_id: string;
  title: string;
  stage: string;
}

/** The receipt a message carries (`refs.assistant_ok`), when it is whole; else null. Message refs are free-form. */
export function receiptOf(m: ChatMessage): AssistantOk | null {
  const r = m.refs?.assistant_ok as Partial<AssistantOk> | undefined;
  if (!r || typeof r !== 'object') return null;
  const { answer_id, task_id, title, stage } = r;
  return [answer_id, task_id, title, stage].every((x) => typeof x === 'string' && x) ? (r as AssistantOk) : null;
}

/* The receipt's card: the task and stage named back, and Undo while that OK still stands, which is the person's own
   Undo of an answer (task.undoAnswer). Whether it stands changes after the receipt is written, so the card reads the
   task: Undo shows while one of its requirements holds this answer and may take it back. A refusal (the stage moved
   on in the meantime) is said, and the task is read again. */
function Receipt({ ok }: { ok: AssistantOk }) {
  const task = useQuery(taskDetailQuery(ok.task_id));
  const holds = task.data?.to_leave?.find((r) => r.answer?.id === ok.answer_id);
  const undo = async () => {
    const r = await run('task.undoAnswer', { task: ok.task_id, answer: ok.answer_id }, { via: 'click' });
    if (!r.ok) {
      toast(refusal(r, "Couldn't undo"));
      void task.refetch();
    }
  };
  return (
    <AskCard
      name={ok.title}
      note={`${ok.stage} · via assistant`}
      label="OK'd for you"
      target={`assistant-ok:${ok.answer_id}`}
      actions={holds?.may.includes('undo') ? <Button onClick={() => void undo()}>Undo</Button> : undefined}
    />
  );
}

/** What Tico's chat draws under a message: the receipt of an OK it gave for the person, else its approval card when
    it links one. */
export const okCard = (m: ChatMessage) => {
  const ok = receiptOf(m);
  return ok ? <Receipt ok={ok} /> : <OkCard m={m} />;
};
