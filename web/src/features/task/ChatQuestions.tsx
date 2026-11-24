import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { timelineQuery, type ChatMessage } from '@/conversation';
import { meQuery } from '@/data';
import { QuestionCard } from './QuestionCard';

/* A ❓ an agent put to the person in their 1:1 chat with it (backend/question_marks.py), pinned over the chat's message
   box as a task's chat pins its own: the Question card, answered in that chat. Only there: a shared chat's ❓ (a team's
   room, a group DM) is answered with a plain reply, and the server takes no card's answer in one. Read from the chat's
   own messages, which carry an open ❓ while it is open (`refs.question`: who it is for, and the answers it offers), so
   only a reader of the message sees it, and its answer or take-back takes it away as the message changes. */

interface Asked {
  id: string;
  to?: string;
  choices?: unknown;
}

/** The open ❓s to `me` that `agent` asked in the chat `thread` itself (never a sub-thread of it), oldest first. */
export function questionsIn(rows: readonly ChatMessage[], me: string | undefined, agent: string, thread: string): { m: ChatMessage; q: Asked }[] {
  if (!me) return [];
  return rows
    .flatMap((m) => {
      const q = m.refs?.question as Asked | undefined;
      const here = (m.thread_id ?? m.conversation_id ?? thread) === thread;
      return q?.id && q.to === me && m.sender_id === agent && here && !m.deleted && !m.hidden ? [{ m, q }] : [];
    })
    .sort((a, b) => ((a.m.created_at ?? '') < (b.m.created_at ?? '') ? -1 : 1));
}

/* The question's line as the server keeps it (backend/question_marks.py first_line): the message's first line, after
   any Warn rule's line put before its words (org_rule_check.unwarned), spaces collapsed, at most LINE_MAX. */
const LINE_MAX = 160;
const WARN_LINE = /^Warning: [^\n]*'s message (?:may break|was too long to check against|could not be checked against) the rule '[^\n]*'\.$/;

export function askedLine(body: string | null | undefined): string {
  let text = body ?? '';
  for (;;) {
    const cut = text.indexOf('\n\n');
    const head = cut < 0 ? text : text.slice(0, cut);
    if (!WARN_LINE.test(head)) break;
    text = cut < 0 ? '' : text.slice(cut + 2);
  }
  const one = (text.trim().split(/\r?\n/)[0] ?? '').split(/\s+/).filter(Boolean).join(' ');
  return one.length <= LINE_MAX ? one : `${one.slice(0, LINE_MAX - 1).trimEnd()}…`;
}

const choicesOf = (q: Asked) => (Array.isArray(q.choices) ? q.choices.filter((c): c is string => typeof c === 'string') : []);

/** The open ❓s of the viewer's 1:1 chat `thread` with `agent`, as pinned Question cards; nothing when none is open, or
    when the viewer may not post there. */
export function ChatQuestions({ thread, agent, canPost = true }: { thread: string; agent: string; canPost?: boolean }) {
  const me = useQuery(meQuery).data?.id;
  // the conversation's own read of this chat: one read for both
  const rows = useInfiniteQuery(timelineQuery(thread)).data?.pages.flatMap((p) => p.messages ?? []) ?? [];
  const asked = questionsIn(rows, me, agent, thread);
  if (!asked.length || !canPost) return null;
  return (
    <div className="chat-pins">
      {asked.map(({ m, q }) => (
        <QuestionCard key={q.id} id={`qmark:${q.id}`} thread={thread} line={askedLine(m.body)} choices={choicesOf(q)} who={agent} />
      ))}
    </div>
  );
}
