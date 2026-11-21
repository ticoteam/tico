import { useQuery } from '@tanstack/react-query';
import { tasksIn, type ChatMessage } from '@/conversation';
import { AskCard, taskAddress } from '@/features/task/exports';
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
      href={taskAddress(ok, undefined, false).to}
      actions={<span className="btn">Review and approve</span>}
    />
  );
}

/** What Tico's chat draws under a message: its approval card, when it links one. */
export const okCard = (m: ChatMessage) => <OkCard m={m} />;
