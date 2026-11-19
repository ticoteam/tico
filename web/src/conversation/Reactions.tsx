// Reactions and replies on a message: the chips under it, the emoji to pick from, and the quote a reply carries.
import { useQuery } from '@tanstack/react-query';
import { refusal, run } from '@/actions';
import { directoryQuery, type Directory } from '@/data';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import type { ChatMessage, Reaction, ReplyQuote } from './types';

/** The emoji a message can be given: the old app's six. No emoji library. */
export const EMOJI = ['👍', '❤️', '😂', '🎉', '👀', '🙏'] as const;

/** Reacting on one message: whether I gave an emoji, and give or take it. */
export interface ReactControls {
  mine: (emoji: string) => boolean;
  pick: (emoji: string) => void;
}

/** My reaction on a message of `chat`, through the action (Tico does the same). */
export function reactControls(chat: string, m: ChatMessage, me: string | undefined): ReactControls {
  const mine = (emoji: string) => !!me && !!m.reactions?.find((r) => r.emoji === emoji)?.actors?.includes(me);
  const pick = (emoji: string) =>
    void run('conversation.react', { chat, message: m.id, emoji, on: !mine(emoji) }, { via: 'click' }).then(
      (r) => r.ok || toast(refusal(r, "Couldn't react")),
    );
  return { mine, pick };
}

const namesOf = (d: Directory) => Object.fromEntries(Object.entries(d.byId).map(([id, a]) => [id, { name: a.name }]));

/** Names from the directory the app already holds; nothing is fetched for a chip's title. Redraws when a name
    changes, not on an agent's status line. */
function useNames(): Record<string, { name: string }> {
  const { data } = useQuery({ ...directoryQuery, enabled: false, select: namesOf });
  return data ?? {};
}

const nameOf = (names: Record<string, { name: string }>, actor: string, me: string | undefined) =>
  actor === me ? 'You' : (names[actor]?.name ?? actor.replace(/^(human|bot):/, ''));

/** Who reacted, for the chip's title: "👍 You, Maya and 2 more". */
function whoReacted(r: Reaction, names: Record<string, { name: string }>, me: string | undefined): string {
  const actors = r.actors ?? [];
  const listed = actors.map((a) => nameOf(names, a, me));
  const more = r.count - actors.length;
  if (!listed.length) return r.emoji;
  return `${r.emoji} ${listed.join(', ')}${more > 0 ? ` and ${more} more` : ''}`;
}

/** The chips under a message: each emoji with its count. Mine is marked by weight and a ring, never a color. */
export function Reacts({ list, me, react }: { list: Reaction[] | undefined; me: string | undefined; react: ReactControls | null }) {
  const names = useNames();
  const shown = (list ?? []).filter((r) => r.count > 0);
  if (!shown.length) return null;
  return (
    <div className="reacts">
      {shown.map((r) => {
        const title = whoReacted(r, names, me);
        const inner = (
          <>
            {r.emoji}
            <b>{r.count}</b>
          </>
        );
        if (!react)
          return (
            <span key={r.emoji} className="react" title={title}>
              {inner}
            </span>
          );
        return (
          <button key={r.emoji} type="button" className="react" title={title} aria-label={`${r.emoji} ${r.count}`}
            aria-pressed={react.mine(r.emoji)} onClick={() => react.pick(r.emoji)}>
            {inner}
          </button>
        );
      })}
    </div>
  );
}

/** The emoji to pick from, opened from the smile under the pointer. */
export function ReactPicker({ react }: { react: ReactControls }) {
  return (
    <Menu trigger={<IconButton icon="smile" label="React" size="sm" quiet />} align="end">
      {EMOJI.map((e) => (
        <MenuItem key={e} label={e} on={react.mine(e)} onSelect={() => react.pick(e)} />
      ))}
    </Menu>
  );
}

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
