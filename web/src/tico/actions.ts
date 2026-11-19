import { defineAction, run, s } from '@/actions';
import { sendMessage, ticoRoomQuery } from '@/conversation';
import { meQuery, queryClient } from '@/data';

/* Handing words to Tico: search's last row, and anything else that asks Tico on the person's behalf. Tico does not
   ask itself, so it is kept from Tico. */
export const ask = defineAction({
  id: 'tico.ask',
  title: 'Ask Tico',
  kind: 'change',
  tico: false,
  input: s.object({ text: s.string({ min: 1, max: 4000 }) }),
  run: async (ctx, { text }) => {
    await run('shell.fold', { sheet: 'tico', folded: false }, { via: ctx.via });
    const [room, me] = await Promise.all([queryClient.ensureQueryData(ticoRoomQuery), queryClient.ensureQueryData(meQuery)]);
    if (!room) throw Object.assign(new Error('Tico has no chat yet'), { code: 'no_room' });
    await sendMessage({ id: room, variant: 'tico', text, me: me.id, page: { path: location.pathname, title: document.title } });
  },
  did: () => 'Asked Tico',
  target: () => ({ kind: 'sheet', id: 'tico' }),
});
