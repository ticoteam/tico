import { defineAction, s } from '@/actions';
import { decideProposal, makeRule, meId, rateMessage, reacted, reactMessage, sendMessage, unrateMessage } from './queries';

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

/* Feedback on a message an agent or a teammate wrote: Good, Not good with a reason, and a rule made from it. Tico may do
   these for the person: they are the person's own opinion, not money, law or anything that cannot be taken back. */

const chat = s.string({ min: 1, max: 200, what: 'a chat id' });

export const rateAction = defineAction({
  id: 'conversation.rate',
  title: 'Rate a message',
  kind: 'change',
  input: s.object({
    chat,
    message: s.string({ min: 1, max: 200, what: 'a message id' }),
    rating: s.oneOf(['up', 'down'] as const),
    reason: s.optional(s.string({ max: 1000 })),
    tellAgent: s.optional(s.boolean()),
  }),
  run: (_ctx, { chat, message, rating, reason, tellAgent }) => rateMessage(chat, message, rating, reason?.trim() ?? '', !!tellAgent),
  did: ({ rating, tellAgent }) => (tellAgent ? 'Sent to the agent' : rating === 'up' ? 'Marked good' : 'Marked not good'),
  target: ({ message }) => ({ kind: 'message', id: message }),
});

export const unrateAction = defineAction({
  id: 'conversation.unrate',
  title: 'Take back a rating',
  kind: 'change',
  input: s.object({ chat, feedback: s.string({ min: 1, max: 200 }), message: s.optional(s.string({ max: 200 })) }),
  run: (_ctx, { chat, feedback }) => unrateMessage(chat, feedback),
  did: () => 'Rating removed',
  target: ({ message }) => (message ? { kind: 'message', id: message } : null),
});

export const makeRuleAction = defineAction({
  id: 'conversation.makeRule',
  title: 'Make a rule from feedback',
  kind: 'change',
  input: s.object({ chat, feedback: s.string({ min: 1, max: 200 }), text: s.string({ min: 1, max: 1000 }), allAgents: s.optional(s.boolean()) }),
  run: (_ctx, { chat, feedback, text, allAgents }) => makeRule(chat, feedback, text.trim(), !!allAgents),
  did: (_input, out) => ((out?.rule as { status?: string } | undefined)?.status === 'active' ? 'Rule added' : 'Rule suggested'),
  target: () => null,
});

/* Reactions and replies on a message in a thread. Tico may do both for the person: ordinary talk among teammates, and
   a reaction can be taken off again. */

const signedIn = () => {
  const me = meId();
  if (!me) throw new Error('Not signed in');
  return me;
};

export const reactAction = defineAction({
  id: 'conversation.react',
  title: 'React to a message',
  kind: 'change',
  input: s.object({
    chat,
    message: s.string({ min: 1, max: 200, what: 'a message id' }),
    emoji: s.string({ min: 1, max: 32 }),
    /** false takes the reaction off; left out, it turns it on, or off when it is already there. */
    on: s.optional(s.boolean()),
  }),
  run: async (_ctx, { chat, message, emoji, on }) => {
    const me = signedIn();
    const turnOn = on ?? !reacted(chat, message, emoji, me);
    await reactMessage(chat, message, emoji, turnOn, me);
    return { on: turnOn };
  },
  did: ({ emoji }, out) => (out?.on === false ? `Took off ${emoji}` : `Reacted ${emoji}`),
  target: ({ message }) => ({ kind: 'message', id: message }),
});

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
