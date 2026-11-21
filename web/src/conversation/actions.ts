import * as z from 'zod/mini';
import { defineAction } from '@/actions';
import { fromPage, pageAt } from '@/shell/pageOwner';
import { decideProposal, meId, sendMessage } from './queries';

/* A card an agent posts in a chat: the person's yes or no to a proposal (`refs.action`). Tico never presses these
   for the person: confirm is the person's own click (the server refuses it from a token). */

const proposal = z.strictObject({ action: z.string().check(z.minLength(1), z.maxLength(100)) });

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

const chat = z.string().check(z.minLength(1), z.maxLength(200), z.describe('a chat id'));

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
  input: z.strictObject({
    chat,
    message: z.string().check(z.minLength(1), z.maxLength(200), z.describe('a message id')),
    text: z.string().check(z.minLength(1), z.maxLength(20000)),
  }),
  run: async (ctx, { chat, message, text }) => {
    // the page it was written on, as every message says it (the send boundary keeps what is open there for Tico)
    const at = ctx.router.state.location;
    const from = fromPage(pageAt(at.pathname, at.searchStr));
    const sent = await sendMessage({ id: chat, variant: 'page', text: text.trim(), me: signedIn(), replyTo: message, from });
    if (!sent) throw new Error('Not sent');
    return sent;
  },
  did: () => 'Replied',
  target: (_input, out) => (out ? { kind: 'message', id: out.id } : null),
});
