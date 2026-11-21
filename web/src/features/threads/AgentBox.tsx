import { useNavigate } from '@tanstack/react-router';
import { Composer, sendToDm, type FromPage } from '@/conversation';
import { useActor } from '@/data';
import { useMe } from './people';

export interface AgentBoxProps {
  /** The agent that handles the conversation (bot:<id or slug>). */
  agent: string;
  /** The conversation it is about: sent with each message as `from_page`, a pointer to it (no words). */
  from: FromPage;
}

/**
 * The box in a conversation from outside (mail, a text, Slack from outside): it talks only to the agent that handles
 * it, never to the outside party (plan/threads-team.html "From outside"). What is typed goes to your DM with exactly
 * the agent, which the server finds (or makes) as it writes (`sendToDm`, the Agent bar's path), with this
 * conversation as its page; that thread, where it went, opens beside, where the agent answers and drafts any reply.
 */
export function AgentBox({ agent, from }: AgentBoxProps) {
  const me = useMe();
  const who = useActor(agent);
  const navigate = useNavigate();
  if (!me) return null;
  return (
    <Composer
      id={`agent:${from.page}:${from.item?.id ?? ''}`}
      me={me}
      to={who?.name ?? 'the agent'}
      // a message with files cannot carry its page: the agent would not know which conversation it is about
      attach={false}
      onSend={async (text) => {
        // the box keeps the words until the server has them (a failure keeps them); then the thread it went to opens
        // beside. A held message (the org rules) went nowhere yet: nothing opens.
        let sent;
        try {
          sent = await sendToDm(agent, text, from);
        } catch {
          return false;
        }
        const thread = sent?.conversation_id || sent?.thread_id;
        if (thread) void navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, chat: `thread:${thread}` }) } as never);
        return true;
      }}
    />
  );
}
