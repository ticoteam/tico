import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Composer, sendMessage, type FromPage } from '@/conversation';
import { useActor } from '@/data';
import { chatAddress } from '@/lib';
import { useMe } from './people';
import { chatQuery } from './queries';

export interface AgentBoxProps {
  /** The agent that handles the conversation (bot:<id or slug>). */
  agent: string;
  /** The conversation it is about: sent with each message as `from_page`, its item. */
  from: FromPage;
}

/**
 * The box in a conversation from outside (mail, a text, Slack from outside): it talks only to the agent that handles
 * it, never to the outside party (plan/threads-team.html "From outside"). What is typed goes to your DM with the agent
 * with this conversation as its page, and that DM opens beside, where the agent answers and drafts any reply.
 */
export function AgentBox({ agent, from }: AgentBoxProps) {
  const me = useMe();
  const who = useActor(agent);
  const chat = chatAddress(agent, who?.kind ?? 'agent');
  const dm = useQuery(chatQuery(chat));
  const navigate = useNavigate();
  if (!me) return null;
  return (
    <Composer
      id={`agent:${from.page}:${from.item?.id ?? ''}`}
      me={me}
      to={who?.name ?? 'the agent'}
      canSend={!!dm.data}
      // a message with files cannot carry its page: the agent would not know which conversation it is about
      attach={false}
      onSend={(text, files) => {
        if (!dm.data) return;
        void sendMessage({ id: dm.data.id, variant: 'page', text, files, me, from, toDm: agent });
        void navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, chat }) } as never);
      }}
    />
  );
}
