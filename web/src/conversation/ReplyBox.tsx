import { queryOptions, useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { api } from '@/api/client';
import { useActor, useActors } from '@/data';
import { IconButton } from '@/kit/IconButton';
import { Composer } from './Composer';
import { agentWords } from './Quote';
import type { ChatMessage } from './types';

/** Who a message in `thread` would reach (GET /api/v2/threads/{id}/reach): a Reply to `reply` reaches its author, when
    they can be reached. Writes nothing. */
export const reachQuery = (thread: string, reply: string) =>
  queryOptions({
    queryKey: ['thread', thread, 'reach', reply] as const,
    queryFn: ({ signal }) =>
      api.get<{ reaches: { actor: string }[] }>(`/api/v2/threads/${encodeURIComponent(thread)}/reach?in_reply_to=${encodeURIComponent(reply)}`, { signal }),
    staleTime: 30_000,
  });

const nameOf = (actor: string, name: string | undefined) => name ?? (actor.startsWith('bot:') ? agentWords(actor) : 'Person');

/** Who a Reply reaches, said before it is sent: "Reaches Build agent", or "Reaches nobody" (an agent that cannot take a
    turn, or your own message). */
function Reaches({ thread, reply }: { thread: string; reply: string }) {
  const { data } = useQuery(reachQuery(thread, reply));
  const actors = useActors();
  if (!data) return <span className="reply-reach" role="status" />;
  const names = data.reaches.map((r) => nameOf(r.actor, actors.find((a) => a.id === r.actor)?.name));
  return (
    <span className="reply-reach" role="status">
      {names.length ? `Reaches ${names.join(', ')}` : 'Reaches nobody'}
    </span>
  );
}

export interface ReplyBoxProps {
  /** The thread the message is in. */
  thread: string;
  /** The message answered. */
  m: ChatMessage;
  me: string;
  /** Where its draft is kept. */
  draft: string;
  canSend?: boolean;
  /** Actor ids the box's @ list names first (a task's people and agents). */
  mentionFirst?: readonly string[];
  onSend: (text: string, files: File[]) => void;
  onDrop: () => void;
}

/** Reply in place, under the comment it answers: the chat's own message box, "Reply to <name>", and who it reaches. */
export function ReplyBox({ thread, m, me, draft, canSend = true, mentionFirst, onSend, onDrop }: ReplyBoxProps) {
  const author = m.sender_id ?? '';
  const known = useActor(author)?.name;
  // your own comment: a reply under it, to nobody
  const label = author === me ? 'Reply' : `Reply to ${nameOf(author, m.sender_name || known)}`;
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.querySelector('textarea')?.focus(), []);
  return (
    // Escape drops it (an open @ list takes its own Escape first), and only it: taken (the phone card checks), and no
    // further (the frame listens on the document)
    <div
      className="reply-box"
      ref={box}
      role="group"
      aria-label={label}
      onKeyDown={(e) => {
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        e.preventDefault();
        e.stopPropagation();
        onDrop();
      }}
    >
      <Composer id={draft} me={me} to={label} label={label} canSend={canSend} mentionFirst={mentionFirst} onSend={(text, files) => onSend(text, files)} />
      <div className="reply-foot">
        <Reaches thread={thread} reply={m.id} />
        <IconButton icon="close" label="Cancel reply" size="sm" quiet onClick={onDrop} />
      </div>
    </div>
  );
}
