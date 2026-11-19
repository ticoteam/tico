import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s } from '@/actions';
import { toast } from '@/kit/Toast';
import {
  addPeople,
  addViewer,
  archiveMail,
  archiveThread,
  archiveUpdates,
  changeThread,
  deleteThread,
  editLearned,
  followThread,
  forgetLearned,
  joinThread,
  markMailUnread,
  markUpdatesRead,
  openEarlier,
  readAll,
  removeMember,
  removeViewer,
  renameThread,
  replyMail,
  startNew,
  summarizeThread,
  unarchiveThread,
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

export const addPeopleAction = defineAction({
  id: 'threads.addPeople',
  title: 'Add people to a thread',
  kind: 'change',
  input: s.object({ thread, actors: s.array(actor, { max: 50 }) }),
  // Adding a teammate to a thread is ordinary work among teammates: Tico's too.
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

export const removePersonAction = defineAction({
  id: 'threads.removePerson',
  title: 'Remove someone from a thread',
  kind: 'change',
  input: s.object({ thread, actor }),
  run: (_ctx, { thread, actor }) => removeMember(thread, actor),
  did: () => 'Removed from the thread',
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const notifyAction = defineAction({
  id: 'threads.notify',
  title: 'Choose how a thread notifies you',
  kind: 'change',
  input: s.object({ thread, notify: s.oneOf(['live', 'mentions', 'digest', 'muted'] as const) }),
  run: (_ctx, { thread, notify }) => followThread(thread, notify),
  did: ({ notify }) => ({ live: 'Notifies on every message', mentions: 'Notifies on mentions', digest: 'Notifies in a digest', muted: 'Muted' })[notify],
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const pinForEveryoneAction = defineAction({
  id: 'threads.pinForEveryone',
  title: 'Pin a thread for everyone in it',
  kind: 'change',
  input: s.object({ thread, pinned: s.optional(s.boolean()) }),
  run: (_ctx, { thread, pinned = true }) => changeThread(thread, { pinned_for_everyone: pinned }),
  did: ({ pinned = true }) => (pinned ? 'Pinned for everyone' : 'Unpinned for everyone'),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const keepMessagesAction = defineAction({
  id: 'threads.keepMessages',
  title: 'Choose how long a thread keeps its messages',
  kind: 'change',
  input: s.object({ thread, days: s.nullable(s.oneOf([30, 90, 365] as const)) }),
  // A shorter keep deletes older messages for good: the person's own press. Keeping them always stays Tico's.
  can: (ctx, { days }) => !(days !== null && ctx.via === 'tico'),
  run: (_ctx, { thread, days }) => changeThread(thread, { retention_days: days }),
  did: ({ days }) => (days ? `Keeps messages ${days} days` : 'Keeps messages always'),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const whoCanReadAction = defineAction({
  id: 'threads.whoCanRead',
  title: 'Choose who can read a thread',
  kind: 'change',
  input: s.object({
    thread,
    audience: s.oneOf(['members', 'team', 'company'] as const),
    team: s.optional(id("a team's id, when a team reads it")),
  }),
  // Opening a thread wider (to a team or the whole company) is the person's own press; closing it to its members stays Tico's.
  can: (ctx, { audience, team }) => !(audience !== 'members' && ctx.via === 'tico') && (audience !== 'team' || !!team),
  run: (_ctx, { thread, audience, team }) => changeThread(thread, { audience, audience_group_id: audience === 'team' ? (team ?? null) : null }),
  did: ({ audience }) =>
    audience === 'company' ? 'Everyone in the company can read it' : audience === 'team' ? 'Its team can read it' : 'Only its members can read it',
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const summarizeAction = defineAction({
  id: 'threads.summarize',
  title: 'Summarize what is new in a thread',
  kind: 'change',
  input: s.object({ thread }),
  run: async ({ via }, { thread }) => {
    const r = await summarizeThread(thread);
    // The summary is posted in the person's chat with Tico; with nothing new, the server says so in words. Tico's own
    // run says it in its did line instead.
    if (via !== 'tico') toast({ text: r.posted ? 'Summary in your chat with Tico' : r.words || 'Nothing new' });
    return r;
  },
  did: (_input, out) => (out?.posted ? 'Summary in your chat with Tico' : 'Nothing new to summarize'),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const openEarlierAction = defineAction({
  id: 'threads.openEarlier',
  title: 'Open earlier messages to readers',
  kind: 'change',
  input: s.object({ thread }),
  // Shows readers what was said before they joined: widening access, the person's own press.
  tico: false,
  run: (_ctx, { thread }) => openEarlier(thread),
  did: (_input, out) => (out ? `Opened ${out.messages} earlier messages` : 'Opened earlier messages'),
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const deleteThreadAction = defineAction({
  id: 'threads.delete',
  title: 'Delete a thread',
  kind: 'change',
  input: s.object({ thread }),
  // Gone for good: the person's own press.
  tico: false,
  run: async ({ router }, { thread }) => {
    await deleteThread(thread);
    await router.navigate({ to: '/threads' });
  },
  did: () => 'Deleted',
  target: () => ({ kind: 'page', id: '/threads' }),
});

export const joinThreadAction = defineAction({
  id: 'threads.join',
  title: 'Join a thread',
  kind: 'change',
  input: s.object({ thread }),
  run: (_ctx, { thread }) => joinThread(thread),
  did: () => 'Joined',
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
});

export const unarchiveThreadAction = defineAction({
  id: 'threads.unarchive',
  title: 'Move an archived thread back',
  kind: 'change',
  input: s.object({ thread }),
  run: (_ctx, { thread }) => unarchiveThread(thread),
  did: () => 'Moved back',
  target: ({ thread }) => ({ kind: 'thread', id: thread }),
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

export const openInstructions = defineAction({
  id: 'mail.openInstructions',
  title: "Read a mailbox agent's instructions",
  kind: 'view',
  input: s.object({ box: id('a mailbox address') }),
  run: ({ router }, { box }) => router.navigate({ to: '/threads/mail', search: { box, view: 'instructions' } }),
  did: () => 'Opened the instructions',
  target: () => ({ kind: 'page', id: '/threads/mail' }),
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

export const openContact = defineAction({
  id: 'messaging.open',
  title: "Open a contact's texts",
  kind: 'view',
  input: s.object({ contact: id('who the texts came from') }),
  run: ({ router }, { contact }) => router.navigate({ to: '/threads/messaging', search: { open: contact } }),
  did: () => 'Opened the texts',
  target: ({ contact }) => ({ kind: 'contact', id: contact }),
});

const box = id('a mailbox address');
const letter = id('a mail thread id');

export const replyMailAction = defineAction({
  id: 'mail.reply',
  title: 'Reply to a mail',
  kind: 'change',
  input: s.object({ box, letter, text: s.string({ min: 1, max: 20000 }) }),
  // Ordinary work, Tico's too: the send gate holds every email for its undo window and asks any OK it needs.
  run: (_ctx, { box, letter, text }) => replyMail(box, letter, text),
  did: () => 'Reply sent',
  target: ({ letter }) => ({ kind: 'letter', id: letter }),
});

export const archiveMailAction = defineAction({
  id: 'mail.archive',
  title: 'Archive a mail',
  kind: 'change',
  input: s.object({ box, letter, ids: s.array(id('a letter id'), { max: 500 }) }),
  run: async ({ router }, { box, letter, ids }) => {
    await archiveMail(box, letter, ids);
    if ((router.state.location.search as { open?: string }).open === letter)
      await router.navigate({ to: '/threads/mail', search: { box } });
  },
  did: () => 'Archived',
  target: () => ({ kind: 'page', id: '/threads/mail' }),
});

export const markMailUnreadAction = defineAction({
  id: 'mail.markUnread',
  title: 'Mark a mail unread',
  kind: 'change',
  input: s.object({ box, letter: id('a letter id') }),
  run: (_ctx, { box, letter }) => markMailUnread(box, letter),
  did: () => 'Marked unread',
  target: () => ({ kind: 'page', id: '/threads/mail' }),
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
  input: s.object({ update: id('an update id'), tab: s.optional(s.string({ pattern: /^(daily|weekly|archive)$/, what: 'daily, weekly or archive' })) }),
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
    // The open update leaves the list it was opened from (archived from Updates, or moved back from Archive).
    if (open && ids.includes(open)) await router.navigate({ to: router.state.location.pathname, search: {} });
  },
  did: ({ archived = true }) => (archived ? 'Archived' : 'Moved back'),
  target: () => ({ kind: 'page', id: '/updates' }),
});

export const openUpdatesArchive = defineAction({
  id: 'updates.openArchive',
  title: 'Open archived updates',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => router.navigate({ to: '/updates/$tab', params: { tab: 'archive' } }),
  did: () => 'Opened the archive',
  target: () => ({ kind: 'page', id: '/updates/archive' }),
});

/* ---------------------------------------------------------------- what Tico has learned */

/* Changing what Tico keeps about a person, and who may read their chat with Tico, is consent: the server refuses each
   of these from anyone acting for the person, Tico included (backend/learned.py _own_entry, assistant_share.py
   _owner_only). So they are the person's own clicks. */

const entry = id('a learned line id');
const agent = s.string({ min: 1, max: 200, what: "an agent's slug" });

export const openLearned = defineAction({
  id: 'learned.open',
  title: 'Open what Tico has learned',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => router.navigate({ to: '/learned' }),
  did: () => 'Opened Learned',
  target: () => ({ kind: 'page', id: '/learned' }),
});

export const editLearnedAction = defineAction({
  id: 'learned.edit',
  title: 'Change a line Tico has learned',
  kind: 'change',
  input: s.object({ entry, text: s.string({ min: 1, max: 280 }) }),
  tico: false,
  run: (_ctx, { entry, text }) => editLearned(entry, text.trim()),
  did: () => 'Changed',
  target: ({ entry }) => ({ kind: 'learned', id: entry }),
});

export const forgetLearnedAction = defineAction({
  id: 'learned.forget',
  title: 'Delete a line Tico has learned',
  kind: 'change',
  input: s.object({ entry }),
  tico: false,
  run: (_ctx, { entry }) => forgetLearned(entry),
  did: () => 'Deleted',
  target: () => ({ kind: 'page', id: '/learned' }),
});

export const addViewerAction = defineAction({
  id: 'learned.addReader',
  title: "Let an agent read your chat with Tico",
  kind: 'change',
  input: s.object({ agent }),
  tico: false,
  run: (_ctx, { agent }) => addViewer(agent),
  did: () => 'Can read your chat now',
  target: () => ({ kind: 'page', id: '/learned' }),
});

export const removeViewerAction = defineAction({
  id: 'learned.removeReader',
  title: "Stop an agent reading your chat with Tico",
  kind: 'change',
  input: s.object({ agent }),
  tico: false,
  run: (_ctx, { agent }) => removeViewer(agent),
  did: () => 'Can no longer read your chat',
  target: () => ({ kind: 'page', id: '/learned' }),
});

/* ---------------------------------------------------------------- finding mail */

export const findMail = defineAction({
  id: 'mail.find',
  title: 'Find mail',
  kind: 'view',
  input: s.object({
    box: s.optional(id('a mailbox address')),
    q: s.optional(s.string({ max: 500, what: 'the words to find' })),
  }),
  // Each search replaces the last in history: Back leaves the list, not each word typed.
  run: ({ router }, { box, q }) => router.navigate({ to: '/threads/mail', search: { box, q: q || undefined }, replace: true }),
  did: ({ q }) => (q ? 'Searched the mail' : 'Showed all mail'),
  target: () => ({ kind: 'page', id: '/threads/mail' }),
});
