import { useRouter } from '@tanstack/react-router';
import { refusalFor } from '@/actions';
import { Composer, sendToDm, type FromPage } from '@/conversation';
import { useActor } from '@/data';
import { toast } from '@/kit/Toast';
import { useMe } from './people';

export interface AgentBoxProps {
  /** The agent that handles the conversation (bot:<id or slug>). */
  agent: string;
  /** The conversation it is about: sent with each message as `from_page`, a pointer to it (no words). */
  from: FromPage;
  /** The draft's key: the conversation's own whole address, as it always was (`agent:<path>`). */
  draftId: string;
}

/**
 * The box in a conversation from outside (mail, a text, Slack from outside): it talks only to the agent that handles
 * it, never to the outside party (plan/threads-team.html "From outside"). What is typed goes to your DM with exactly
 * the agent, which the server finds (or makes) as it writes (`sendToDm`, the Agent bar's path), with this
 * conversation as its page; that thread, where it went, opens beside, where the agent answers and drafts any reply.
 * Refused, the words stay in the box; held by the org rules, they are in the hold and nothing opens.
 */
export function AgentBox({ agent, from, draftId }: AgentBoxProps) {
  const me = useMe();
  const who = useActor(agent);
  const router = useRouter();
  // where the box is: the page and all its address names (Mail's mailbox, view and open mail), but the chat beside it
  const here = () => {
    const q = new URLSearchParams(router.state.location.searchStr);
    q.delete('chat');
    q.sort();
    return `${router.state.location.pathname}?${q.toString()}`;
  };
  if (!me) return null;
  return (
    <Composer
      // one box per conversation: its draft and its sending state go with it
      key={draftId}
      id={draftId}
      me={me}
      to={who?.name ?? 'the agent'}
      // a message with files cannot carry its page: the agent would not know which conversation it is about
      attach={false}
      onSend={async (text) => {
        const at = here();
        let sent;
        try {
          sent = await sendToDm(agent, text, from);
        } catch (e) {
          toast(refusalFor(e, 'Not sent'));
          return false;
        }
        if (!sent) {
          toast({ text: 'Held for a check' });
          return true;
        }
        // the thread it went to, as the server answered it, opens beside, unless the person moved on to another item
        const thread = sent.conversation_id || sent.thread_id;
        if (thread && here() === at) void router.navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, chat: `thread:${thread}` }) } as never);
        return true;
      }}
    />
  );
}
