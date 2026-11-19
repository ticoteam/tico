import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s } from '@/actions';
import {
  addPeople,
  archiveThread,
  archiveUpdates,
  markUpdatesRead,
  muteThread,
  readAll,
  renameThread,
  startNew,
} from './queries';

const id = (what: string) => s.string({ min: 1, max: 200, what });
const thread = id('a thread id');
const actor = s.string({ min: 3, max: 200, pattern: /^(human|bot|group):[^\s]+$/, what: 'human:<id>, bot:<slug> or group:<id>' });

/* ---------------------------------------------------------------- threads */

export const openThread = defineAction({
  id: 'threads.open',
  title: 'Open a thread',
  kind: 'view',
  input: s.object({ thread }),
  run: ({ router }, { thread }) => router.navigate({ to: '/threads/$thread', params: { thread } }),
  show: ({ router }, { thread }) => router.navigate({ to: '/threads/$thread', params: { thread } }),
  did: () => 'Opened the thread',
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const composeThread = defineAction({
  id: 'threads.compose',
  title: 'New message',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => router.navigate({ to: '/threads/new' }),
  did: () => 'Opened New message',
  target: () => ({ kind: 'page', id: '/threads/new' }),
});

export const newThread = defineAction({
  id: 'threads.new',
  title: 'Send a new message',
  kind: 'change',
  input: s.object({
    members: s.array(actor, { max: 50 }),
    body: s.string({ min: 1, max: 20000 }),
    title: s.optional(s.string({ max: 200 })),
  }),
  run: async ({ router }, { members, body, title }) => {
    const t = await startNew(members, body, title);
    if (t?.id) await router.navigate({ to: '/threads/$thread', params: { thread: t.id } });
    return t;
  },
  did: () => 'Sent',
  target: (_input, out) => (out?.id ? { kind: 'thread', id: out.id } : null),
});

export const renameThreadAction = defineAction({
  id: 'threads.rename',
  title: 'Rename a thread',
  kind: 'change',
  input: s.object({ thread, title: s.string({ min: 1, max: 200 }) }),
  run: (_ctx, { thread, title }) => renameThread(thread, title.trim()),
  did: ({ title }) => `Renamed to ${title}`,
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const archiveThreadAction = defineAction({
  id: 'threads.archive',
  title: 'Archive a thread',
  kind: 'change',
  input: s.object({ thread, archived: s.optional(s.boolean()) }),
  run: async ({ router }, { thread, archived = true }) => {
    await archiveThread(thread, archived);
    const open = router.state.location.pathname.endsWith(`/threads/${encodeURIComponent(thread)}`);
    if (archived && open) await router.navigate({ to: '/threads' });
  },
  did: ({ archived = true }) => (archived ? 'Archived' : 'Moved back'),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const muteThreadAction = defineAction({
  id: 'threads.mute',
  title: 'Mute a thread',
  kind: 'change',
  input: s.object({ thread, muted: s.optional(s.boolean()) }),
  run: (_ctx, { thread, muted = true }) => muteThread(thread, muted),
  did: ({ muted = true }) => (muted ? 'Muted' : 'Unmuted'),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const addPeopleAction = defineAction({
  id: 'threads.addPeople',
  title: 'Add people to a thread',
  kind: 'change',
  input: s.object({ thread, actors: s.array(actor, { max: 50 }) }),
  run: (_ctx, { thread, actors }) => addPeople(thread, actors),
  did: ({ actors }) => (actors.length === 1 ? 'Added 1 teammate' : `Added ${actors.length} teammates`),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const markThreadsRead = defineAction({
  id: 'threads.markRead',
  title: 'Mark threads read',
  kind: 'change',
  input: s.object({ threads: s.array(thread, { max: 100 }) }),
  run: (_ctx, { threads }) => readAll(threads),
  did: () => 'Marked read',
  target: () => ({ kind: 'page', id: '/threads/catch-up' }),
});

/* ---------------------------------------------------------------- mail and texts */

export const openLetter = defineAction({
  id: 'mail.open',
  title: 'Open a mail',
  kind: 'view',
  input: s.object({ box: id('a mailbox address'), letter: id('a mail thread id') }),
  run: ({ router }, { box, letter }) =>
    router.navigate({ to: '/threads/mail', search: { box, open: letter } }),
  did: () => 'Opened the mail',
  target: ({ letter }) => ({ kind: 'letter', id: letter }),
});

export const pickMailbox = defineAction({
  id: 'mail.pickBox',
  title: 'Read another mailbox',
  kind: 'view',
  input: s.object({ box: id('a mailbox address') }),
  run: ({ router }, { box }) => router.navigate({ to: '/threads/mail', search: { box } }),
  did: ({ box }) => `Reading ${box}`,
  target: () => ({ kind: 'page', id: '/threads/mail' }),
});

export const openText = defineAction({
  id: 'messaging.open',
  title: 'Open a text',
  kind: 'view',
  input: s.object({ text: id('a text id') }),
  run: ({ router }, { text }) => router.navigate({ to: '/threads/messaging', search: { open: text } }),
  did: () => 'Opened the text',
  target: ({ text }) => ({ kind: 'text', id: text }),
});

/* ---------------------------------------------------------------- updates */

const openUpdateIn = (router: AnyRouter, { update, tab }: { update: string; tab?: string }) =>
  tab
    ? router.navigate({ to: '/updates/$tab', params: { tab }, search: { open: update } })
    : router.navigate({ to: '/updates', search: { open: update } });

export const openUpdate = defineAction({
  id: 'updates.open',
  title: 'Open an update',
  kind: 'view',
  input: s.object({ update: id('an update id'), tab: s.optional(s.string({ pattern: /^(daily|weekly)$/, what: 'daily or weekly' })) }),
  run: ({ router }, input) => openUpdateIn(router, input),
  show: ({ router }, input) => openUpdateIn(router, input),
  did: () => 'Opened the update',
  target: ({ update }) => ({ kind: 'update', id: update }),
});

export const markUpdatesReadAction = defineAction({
  id: 'updates.markRead',
  title: 'Mark updates read',
  kind: 'change',
  input: s.object({ ids: s.optional(s.array(id('an update id'), { max: 200 })), read: s.optional(s.boolean()) }),
  run: (_ctx, { ids, read = true }) => markUpdatesRead(ids ?? 'all', read),
  did: ({ read = true }) => (read ? 'Marked read' : 'Marked unread'),
  target: ({ ids }) => (ids?.length === 1 ? { kind: 'update', id: ids[0]! } : { kind: 'page', id: '/updates' }),
});

export const archiveUpdatesAction = defineAction({
  id: 'updates.archive',
  title: 'Archive updates',
  kind: 'change',
  input: s.object({ ids: s.array(id('an update id'), { max: 200 }), archived: s.optional(s.boolean()) }),
  run: async ({ router }, { ids, archived = true }) => {
    await archiveUpdates(ids, archived);
    const open = (router.state.location.search as { open?: string }).open;
    if (archived && open && ids.includes(open)) await router.navigate({ to: router.state.location.pathname, search: {} });
  },
  did: ({ archived = true }) => (archived ? 'Archived' : 'Moved back'),
  target: () => ({ kind: 'page', id: '/updates' }),
});
