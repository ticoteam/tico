import * as z from 'zod/mini';
import { defineAction } from '@/actions';
import { newId } from '@/lib';
import { deleteHistory, markTicketRead, sendToSupport } from './queries';

/* Tico support: the second chat (`?chat=support`) and what can be said in it. Tico may open it and write in it for the
   person; attaching diagnostics (data about this install) and deleting the history are the person's own presses. */

export const SUPPORT_CHAT = 'support';

const here = () => ({ kind: 'chat', id: SUPPORT_CHAT });
const message = z.string().check(z.minLength(1), z.maxLength(4000), z.describe('the message'));
const key = z.optional(z.string().check(z.minLength(1), z.maxLength(200)));
// A ticket id, or 'new' for a new conversation; left out, the newest open one (or a new one when none is open).
const ticket = z.optional(z.string().check(z.minLength(1), z.maxLength(64), z.describe('a ticket id or new')));
const openChat = (router: { navigate: (o: never) => unknown }) =>
  router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, chat: SUPPORT_CHAT }) } as never);

export const open = defineAction({
  id: 'support.open',
  title: 'Open Tico support',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => openChat(router),
  show: ({ router }) => openChat(router),
  did: () => 'Opened Tico support',
  target: here,
});

export const send = defineAction({
  id: 'support.send',
  title: 'Message Tico support',
  kind: 'change',
  input: z.strictObject({ message, ticket, key }),
  run: async (_ctx, i) => {
    await sendToSupport({ message: i.message, to: i.ticket ?? 'latest', key: i.key ?? newId() });
  },
  did: () => 'Sent to Tico support',
  target: here,
});

export const newTicket = defineAction({
  id: 'support.newTicket',
  title: 'Start a new conversation with Tico support',
  kind: 'change',
  input: z.strictObject({ message, key }),
  run: async (_ctx, i) => {
    await sendToSupport({ message: i.message, to: 'new', key: i.key ?? newId() });
  },
  did: () => 'Sent to Tico support',
  target: here,
});

export const sendDiagnostics = defineAction({
  id: 'support.sendDiagnostics',
  title: 'Message Tico support with diagnostics',
  kind: 'change',
  // What this install is and how it runs goes to Tico support: only after the person has read it.
  tico: false,
  input: z.strictObject({
    message,
    diagnostics: z.string().check(z.minLength(1), z.maxLength(64), z.describe('a diagnostics preview id')),
    edited: z.optional(z.string().check(z.maxLength(300_000))),
    ticket,
    key,
  }),
  run: async (_ctx, i) => {
    await sendToSupport({
      message: i.message,
      to: i.ticket ?? 'latest',
      key: i.key ?? newId(),
      diagnostics: i.diagnostics,
      ...(i.edited !== undefined && { edited: i.edited }),
    });
  },
  did: () => 'Sent to Tico support',
  target: here,
});

export const markRead = defineAction({
  id: 'support.markRead',
  title: 'Mark Tico support’s replies read',
  kind: 'change',
  input: z.strictObject({ ticket: z.string().check(z.minLength(1), z.maxLength(64), z.describe('a ticket id')) }),
  run: async (_ctx, { ticket: t }) => {
    await markTicketRead(t);
  },
  did: () => 'Marked read',
  target: here,
});

export const clearHistory = defineAction({
  id: 'support.deleteHistory',
  title: 'Delete the chat history with Tico support',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  run: () => deleteHistory(),
  did: () => 'Chat history deleted',
  target: here,
});
