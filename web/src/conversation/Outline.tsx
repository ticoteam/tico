import { useQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import { faultHides, ReadFault } from '@/pagekinds';
import { outlineQuery } from './queries';

/* A jump asked from the Outline reaches the open Conversation with that id, wherever it is drawn. One asked for a chat
   that is not listening yet (a task's history line opens the chat, then asks for the message) waits for it, once. */
const jumps = new EventTarget();
let waiting: { chat: string; message: string } | null = null;

export function jumpTo(chat: string, message: string): void {
  const taken = !jumps.dispatchEvent(new CustomEvent('jump', { detail: { chat, message }, cancelable: true }));
  waiting = taken ? null : { chat, message };
}

/** Listen for jumps into one chat; returns the unsubscribe. A jump that waits for this chat is taken at once. */
export function onJump(chat: string, fn: (message: string) => void): () => void {
  const h = (e: Event) => {
    const d = (e as CustomEvent<{ chat: string; message: string }>).detail;
    if (d.chat !== chat) return;
    e.preventDefault();
    fn(d.message);
  };
  jumps.addEventListener('jump', h);
  if (waiting?.chat === chat) {
    const { message } = waiting;
    waiting = null;
    fn(message);
  }
  return () => jumps.removeEventListener('jump', h);
}

export interface OutlineProps {
  /** The chat (a thread, or Tico's room). */
  id: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** A long chat's prompts as a list; a row takes the chat to that message, loading older pages to reach it. */
export function Outline({ id, open, onOpenChange }: OutlineProps) {
  const q = useQuery({ ...outlineQuery(id), enabled: open });
  const prompts = (faultHides(q) ? undefined : q.data) ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Outline">
      {q.isPending ? (
        <SkeletonRows rows={4} />
      ) : faultHides(q) ? (
        <ReadFault what="The outline" reads={[q]} />
      ) : prompts.length === 0 ? (
        <Empty title="No prompts yet" />
      ) : (
        <VirtualList
          className="co-list"
          label="Prompts"
          items={prompts}
          getKey={(p) => p.id}
          estimateSize={34}
          renderItem={(p) => {
            const text = p.text || 'Attached files.';
            return (
              <button
                type="button"
                className="row co-row"
                title={p.task ? `${text} (${p.task.title})` : text}
                onClick={() => {
                  onOpenChange(false);
                  jumpTo(id, p.id);
                }}
              >
                <span className="row-title">{text}</span>
                <Ago at={p.created_at} className="row-age" />
              </button>
            );
          }}
        />
      )}
    </Dialog>
  );
}
