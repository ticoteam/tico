import { defineAction, run, s } from '@/actions';
import { sendMessage, ticoRoomQuery } from '@/conversation';
import { meQuery, queryClient } from '@/data';
import { readEarlier } from './earlier';
import { pageName } from './pageName';
import { ticoPowerQuery, turnOnTico } from './queries';

/* Handing words to Tico: search's last row, and anything else that asks Tico on the person's behalf. Tico does not
   ask itself, so it is kept from Tico. While Tico is off, its sheet opens on the off state and nothing is sent. */
export const ask = defineAction({
  id: 'tico.ask',
  title: 'Ask Tico',
  kind: 'change',
  tico: false,
  input: s.object({ text: s.string({ min: 1, max: 4000 }) }),
  run: async (ctx, { text }) => {
    await run('shell.fold', { sheet: 'tico', folded: false }, { via: ctx.via });
    const power = await queryClient.ensureQueryData(ticoPowerQuery);
    if (!power.available) throw Object.assign(new Error('Tico is off'), { code: 'assistant_off' });
    const [room, me] = await Promise.all([queryClient.ensureQueryData(ticoRoomQuery), queryClient.ensureQueryData(meQuery)]);
    if (!room) throw Object.assign(new Error('Tico has no chat yet'), { code: 'no_room' });
    await sendMessage({ id: room, variant: 'tico', text, me: me.id, page: { path: location.pathname, title: pageName() } });
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
  input: s.object({}),
  can: (ctx) => ctx.queryClient.getQueryData(ticoPowerQuery.queryKey)?.can_turn_on !== false,
  run: () => turnOnTico(),
  did: (_input, state) => (state === 'active' ? 'Turned on Tico' : 'Tico needs a computer'),
  target: () => ({ kind: 'sheet', id: 'tico' }),
});

/* The owner's earlier chats (the old Assistant page's "Earlier chats"): one opens in Tico's sheet, read only, and Back
   returns to Tico's own chat. Reading is a view, so Tico may open them too. */
export const openEarlier = defineAction({
  id: 'tico.openEarlier',
  title: 'Read an earlier chat',
  kind: 'view',
  input: s.object({ room: s.string({ min: 1, max: 200, what: "an earlier chat's id" }) }),
  run: async (ctx, { room }) => {
    const power = await queryClient.ensureQueryData(ticoPowerQuery);
    if (power.earlier?.room_id !== room) throw Object.assign(new Error('That earlier chat is not yours'), { code: 'not_found' });
    await run('shell.fold', { sheet: 'tico', folded: false }, { via: ctx.via });
    readEarlier(room);
  },
  did: () => 'Opened the earlier chat',
  target: () => ({ kind: 'sheet', id: 'tico' }),
});

export const closeEarlier = defineAction({
  id: 'tico.closeEarlier',
  title: "Back to Tico's chat",
  kind: 'view',
  input: s.object({}),
  run: () => readEarlier(null),
  did: () => "Back to Tico's chat",
  target: () => ({ kind: 'sheet', id: 'tico' }),
});
