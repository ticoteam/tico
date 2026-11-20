import { defineAction, s } from '@/actions';
import { decideProposal, meId, sendMessage } from './queries';

/* A card an agent posts in a chat: the person's yes or no to a proposal (`refs.action`). Tico never presses these
   for the person: confirm is the person's own click (the server refuses it from a token). */

const proposal = s.object({ action: s.string({ min: 1, max: 100 }) });

export const confirmProposal = defineAction({
  id: 'conversation.confirm',
  title: 'Confirm a proposal',
  kind: 'change',
  input: proposal,
  tico: false,
  run: (_ctx, { action }) => decideProposal(action, true),
  did: (_input, out) => (out?.summary ? out.summary : 'Confirmed'),
  target: ({ action }) => ({ kind: 'proposal', id: action }),
});

export const cancelProposal = defineAction({
  id: 'conversation.cancel',
  title: 'Turn down a proposal',
  kind: 'change',
  input: proposal,
  tico: false,
  run: (_ctx, { action }) => decideProposal(action, false),
  did: () => 'Turned down',
  target: ({ action }) => ({ kind: 'proposal', id: action }),
});

const chat = s.string({ min: 1, max: 200, what: 'a chat id' });

/* Replies on a message in a thread. Tico may do this for the person: ordinary talk among teammates. */

const signedIn = () => {
  const me = meId();
  if (!me) throw new Error('Not signed in');
  return me;
};

export const replyAction = defineAction({
  id: 'conversation.reply',
  title: 'Reply to a message',
  kind: 'change',
  input: s.object({
    chat,
    message: s.string({ min: 1, max: 200, what: 'a message id' }),
    text: s.string({ min: 1, max: 20000 }),
  }),
  run: async (_ctx, { chat, message, text }) => {
    const sent = await sendMessage({ id: chat, variant: 'page', text: text.trim(), me: signedIn(), replyTo: message });
    if (!sent) throw new Error('Not sent');
    return sent;
  },
  did: () => 'Replied',
  target: (_input, out) => (out ? { kind: 'message', id: out.id } : null),
});
