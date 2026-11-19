import { useQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import { outlineQuery } from './queries';

/* A jump asked from the Outline reaches the open Conversation with that id, wherever it is drawn. */
const jumps = new EventTarget();

export function jumpTo(chat: string, message: string): void {
  jumps.dispatchEvent(new CustomEvent('jump', { detail: { chat, message } }));
}

/** Listen for jumps into one chat; returns the unsubscribe. */
export function onJump(chat: string, fn: (message: string) => void): () => void {
  const h = (e: Event) => {
    const d = (e as CustomEvent<{ chat: string; message: string }>).detail;
    if (d.chat === chat) fn(d.message);
  };
  jumps.addEventListener('jump', h);
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
  const prompts = q.data ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Outline">
      {q.isPending ? (
        <SkeletonRows rows={4} />
      ) : prompts.length === 0 ? (
        <Empty title={q.isError ? "Couldn't load the outline" : 'No prompts yet'} />
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
