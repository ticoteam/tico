import * as z from 'zod/mini';
import { defineAction, run } from '@/actions';
import { sendMessage, ticoRoomQuery } from '@/conversation';
import { meQuery, queryClient } from '@/data';
import { fromPage, pageAt } from '@/shell/pageOwner';
import { ticoPowerQuery, turnOnTico } from './queries';

/* Handing words to Tico: search's last row, and anything else that asks Tico on the person's behalf. Tico does not
   ask itself, so it is kept from Tico. While Tico is off, its sheet opens on the off state and nothing is sent. */
export const ask = defineAction({
  id: 'tico.ask',
  title: 'Ask Tico',
  kind: 'change',
  tico: false,
  input: z.strictObject({ text: z.string().check(z.minLength(1), z.maxLength(4000)) }),
  run: async (ctx, { text }) => {
    await run('shell.fold', { sheet: 'tico', folded: false }, { via: ctx.via });
    const power = await queryClient.ensureQueryData(ticoPowerQuery);
    if (!power.available) throw Object.assign(new Error('Tico is off'), { code: 'assistant_off' });
    const [room, me] = await Promise.all([queryClient.ensureQueryData(ticoRoomQuery), queryClient.ensureQueryData(meQuery)]);
    if (!room) throw Object.assign(new Error('Tico has no chat yet'), { code: 'no_room' });
    await sendMessage({ id: room, variant: 'tico', text, me: me.id, from: fromPage(pageAt(ctx.router.state.location.pathname, ctx.router.state.location.searchStr)) });
  },
  did: () => 'Asked Tico',
  target: () => ({ kind: 'sheet', id: 'tico' }),
});

/* The owner's Turn on, in Tico's sheet while it is off. The server takes it only from the person's own press (Tico
   asking needs their OK on a card), and an off Tico cannot run it anyway, so it is kept from Tico. Returns the state:
   'planned' when it still needs a computer. */
export const turnOn = defineAction({
  id: 'tico.turnOn',
  title: 'Turn on Tico',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  can: (ctx) => ctx.queryClient.getQueryData(ticoPowerQuery.queryKey)?.can_turn_on !== false,
  run: () => turnOnTico(),
  did: (_input, state) => (state === 'active' ? 'Turned on Tico' : 'Tico needs a computer'),
  target: () => ({ kind: 'sheet', id: 'tico' }),
});
