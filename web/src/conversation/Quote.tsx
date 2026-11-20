// Replies on a message: the quote a reply carries.
import { useQuery } from '@tanstack/react-query';
import { directoryQuery, type Directory } from '@/data';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import type { ChatMessage, ReplyQuote } from './types';

const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** An agent's words for itself when nothing names it: its slug in words ("release-owner" reads "Release owner"); an
    id is no name ("Agent"). */
export const agentWords = (actor: string): string => {
  const rest = actor.replace(/^bot:/, '');
  return ID.test(rest) ? 'Agent' : rest.replace(/[-_]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
};

const namesOf = (d: Directory) => Object.fromEntries(Object.entries(d.byId).map(([id, a]) => [id, { name: a.name }]));

/** Names from the directory the app already holds; nothing is fetched for a quote. Redraws when a name changes,
    not on an agent's status line. */
function useNames(): Record<string, { name: string }> {
  const { data } = useQuery({ ...directoryQuery, enabled: false, select: namesOf });
  return data ?? {};
}

const nameOf = (names: Record<string, { name: string }>, actor: string, me: string | undefined) =>
  actor === me ? 'You' : (names[actor]?.name ?? (actor.startsWith('human:') ? 'Person' : agentWords(actor)));

/** A message's start in plain words, as a quote shows it. */
export function excerpt(text: string | null | undefined): string {
  const plain = String(text ?? '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`#>~|]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return plain.length > 120 ? `${plain.slice(0, 119)}…` : plain;
}

/** The quote of a message being answered, as the server would give it. */
export const quoteFrom = (m: ChatMessage): ReplyQuote => ({
  id: m.id,
  author: m.sender_id,
  author_name: m.sender_name,
  excerpt: excerpt(m.deleted ? 'Deleted' : m.body),
});

/** What a message quotes: the server's `reply_to`, else (from the stream) the loaded message it names. */
export function quoteOf(m: ChatMessage, byId: Map<string, ChatMessage>): ReplyQuote | null {
  if (m.reply_to !== undefined) return m.reply_to;
  if (!m.in_reply_to) return null;
  const target = byId.get(m.in_reply_to);
  return target ? quoteFrom(target) : { id: m.in_reply_to };
}

function useQuoteName(q: ReplyQuote, me: string | undefined): string {
  const names = useNames();
  if (q.author && q.author === me) return 'You';
  return q.author_name || (q.author ? nameOf(names, q.author, me) : '');
}

/** The quote above a reply. Pressing it goes to the message answered, when it is loaded. */
export function Quote({ q, me, onOpen }: { q: ReplyQuote; me: string | undefined; onOpen?: (id: string) => void }) {
  const who = useQuoteName(q, me);
  if (q.hidden)
    return (
      <span className="quote">
        <Icon name="reply" />
        <span>Message not shown</span>
      </span>
    );
  return (
    <button type="button" className="quote" onClick={() => onOpen?.(q.id)}>
      <Icon name="reply" />
      {who && <b>{who}</b>}
      <span>{q.excerpt || 'Earlier message'}</span>
    </button>
  );
}

/** Above the box while a reply is being written: what it answers, and a way to drop it. */
export function Replying({ q, me, onDrop }: { q: ReplyQuote; me: string | undefined; onDrop: () => void }) {
  const who = useQuoteName(q, me);
  return (
    <div className="quote is-replying" role="group" aria-label="Replying to">
      <Icon name="reply" />
      {who && <b>{who}</b>}
      <span>{q.excerpt || 'Earlier message'}</span>
      <IconButton icon="close" label="Cancel reply" size="sm" quiet onClick={onDrop} />
    </div>
  );
}
