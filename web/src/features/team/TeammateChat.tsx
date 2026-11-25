import { useQuery } from '@tanstack/react-query';
import { Conversation } from '@/conversation';
import { meQuery, useActor } from '@/data';
import { approvalCard, ChatQuestions, useApprovalCards } from '@/features/task/exports';
import { chatAddress } from '@/lib';
import { ReadFault } from '@/pagekinds/_parts/ReadFault';
import { dmQuery } from './queries';

/** Words an agent's empty chat offers to start with: they fill the box, never send. */
export const AGENT_STARTERS = ['Add a widget showing…', 'What did you make this week?'] as const;
/** The templates whose card leaves out the sidebar skill (`base_skip: [.claude/skills/arrange-your-sidebar]` in
    templates/catalog/<template>/card.yaml; TeammateChat.test.tsx checks they match): they are not asked for a widget. */
export const NO_SIDEBAR_SKILL: ReadonlySet<string> = new Set(['agent-ops', 'assistant']);
const startersFor = (template: string | undefined) => (template && NO_SIDEBAR_SKILL.has(template) ? AGENT_STARTERS.slice(1) : AGENT_STARTERS);

/**
 * The chat with a teammate, the wide sheet of their page: the direct thread with them (made the first time). Only the
 * root route can place it (the frame's chat place), so it is exported. On my own page there is none: I do not chat with
 * myself, and my assistant is Tico at the far left of every page (threads.js: the Assistant's own page is gone).
 */
export function TeammateChat({ who }: { who: string }) {
  const byKey = useActor(who);
  const byPerson = useActor(`human:${who}`);
  const actor = byKey ?? byPerson;
  const me = useQuery(meQuery);
  // Until I know who I am, any teammate could be me: the server refuses a DM with yourself (422).
  const other = !!actor && !!me.data && actor.id !== me.data.id;
  const dm = useQuery({ ...dmQuery(actor?.id ?? ''), enabled: other });
  // With an agent: what it asked you to approve, as cards among its messages; a card's page opens beside this chat.
  const agent = other && actor.kind === 'agent' ? actor.id : undefined;
  const approvals = useApprovalCards({ agent }, agent && chatAddress(agent, 'agent'));
  if (!other) return null;
  if (dm.isError) return <ReadFault what="The chat" reads={[dm]} />;
  if (!dm.data) return null;
  // A direct chat's @ reaches only its two members (the server refuses anyone else): the list offers just them.
  return <Conversation id={dm.data} variant="page" title={actor.name} actor={actor.id} card={approvalCard} extra={approvals} foot={agent && <ChatQuestions thread={dm.data} agent={agent} />} starters={agent ? startersFor(actor.template) : undefined} mentionOnly={[actor.id]} />;
}

/** An agent's chat, by its slug. */
export function AgentChat({ slug }: { slug: string }) {
  return <TeammateChat who={slug} />;
}
