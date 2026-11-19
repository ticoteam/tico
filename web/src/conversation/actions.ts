import { defineAction, s } from '@/actions';
import { decideProposal } from './queries';

/* A card an agent posts in a chat: the person's yes or no to a proposal (`refs.action`). Tico never presses these
   for the person: confirm is the person's own click (the server refuses it from a token). */

const proposal = s.object({ action: s.string({ min: 1, max: 100 }) });

export const confirmProposal = defineAction({
  id: 'conversation.confirm',
  title: 'Confirm a proposal',
  kind: 'change',
  input: proposal,
  run: (_ctx, { action }) => decideProposal(action, true),
  did: (_input, out) => (out?.summary ? out.summary : 'Confirmed'),
  target: ({ action }) => ({ kind: 'proposal', id: action }),
});

export const cancelProposal = defineAction({
  id: 'conversation.cancel',
  title: 'Turn down a proposal',
  kind: 'change',
  input: proposal,
  run: (_ctx, { action }) => decideProposal(action, false),
  did: () => 'Turned down',
  target: ({ action }) => ({ kind: 'proposal', id: action }),
});
