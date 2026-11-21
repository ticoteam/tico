import { useQuery } from '@tanstack/react-query';
import { tasksIn, type ChatMessage } from '@/conversation';
import { AskCard } from '@/features/task/exports';
import { flowQuery, type FlowItem } from '@/features/tasks/routing';
import { taskHref } from './tracked';

/* Tico's approval card (plan: the assistant as a delegator): a Tico message that links one task, whose OK waits on the
   person in their Needs you, shows one card under its words. The card only opens the task, beside the chat on a
   computer or as the page on a phone (the shell's own link handling), where the existing preview and Approve are. The
   chat never reads the OK itself (GET /api/v2/oks/{rid} records that the person was shown it) and never answers it.
   A message that links more than one task stays linked text. */

/** The pending OK a message's text links to: its one linked task, when an OK on it waits on the person; else null. */
export function linkedOk(text: string, items: readonly FlowItem[], origin: string): FlowItem | null {
  const linked = tasksIn(text, origin);
  if (linked.length !== 1) return null;
  const [{ pipeline, key }] = linked as [(typeof linked)[number]];
  return (
    items.find(
      (it) => (it.ask?.kind === 'ok' || it.ask?.kind === 'page') && (it.id === key || String(it.number) === key) && (!pipeline || pipeline === 'all' || it.pipeline_id === pipeline),
    ) ?? null
  );
}

export function OkCard({ m }: { m: ChatMessage }) {
  const text = m.body ?? '';
  const ok = useQuery({ ...flowQuery, enabled: !m.pending && text.includes('/'), select: (f) => linkedOk(text, f.items, location.origin) }).data;
  if (!ok) return null;
  return (
    <AskCard
      name={ok.ask?.line || ok.title}
      who={ok.ask?.by ?? ok.owner_id}
      href={taskHref(ok)}
      target={`ok:${ok.id}`}
      actions={<span className="btn">Review and approve</span>}
    />
  );
}

/** What Tico's chat draws under a message: its approval card, when it links one. */
export const okCard = (m: ChatMessage) => <OkCard m={m} />;
