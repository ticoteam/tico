import { infiniteQueryOptions, keepPreviousData, queryOptions, type InfiniteData } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { ChatMessage } from '@/conversation';
import { keys, queryClient, quietRoot, write } from '@/data';
import { toPage, type Letter, type ListPage, type Member, type Text, type Thread, type ThreadList, type Update } from './model';
import { chatActor } from '@/lib';

const enc = encodeURIComponent;
const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
};

/* ---------------------------------------------------------------- keys */

// The list, a view and Catch up sit under `keys.threads`, where live/patch.ts patches them from the `threads` topic.
export const threadKeys = {
  list: keys.threads.list(),
  view: keys.threads.view,
  catchUp: keys.threads.catchUp(),
  chat: (chat: string) => ['threads', 'chat', chat] as const,
  team: (group: string) => ['threads', 'team', group] as const,
};
const mailKeys = {
  boxes: ['mail', 'boxes'] as const,
  list: (box: string) => ['mail', 'list', box] as const,
  letter: (box: string, id: string) => ['mail', 'letter', box, id] as const,
};
const textKeys = { list: ['texts', 'list'] as const, one: (id: string) => ['texts', 'one', id] as const };
export const updateKeys = {
  list: (tab: string) => ['updates', 'list', tab] as const,
  one: (id: string) => ['updates', 'one', id] as const,
};

// No topic carries mail, texts or updates: they are read again after the person's own writes and on reconnect.
quietRoot('mail');
quietRoot('texts');
quietRoot('updates');

/* ---------------------------------------------------------------- threads */

const PAGE = 50;

/** The list: threads and DMs, newest activity first, a page at a time. */
export const threadListQuery = infiniteQueryOptions({
  queryKey: threadKeys.list,
  // Route loaders share this request with the menu's Threads tab, even while its observers unmount: no signal.
  queryFn: async ({ pageParam }): Promise<ListPage> =>
    toPage(await api.get<Parameters<typeof toPage>[0]>(`/api/v2/threads${qs({ sort: 'recent', limit: PAGE, cursor: pageParam })}`)),
  initialPageParam: null as string | null,
  getNextPageParam: (last) => last.next ?? undefined,
});

const readThread = (id: string, signal: AbortSignal) => api.get<Thread>(`/api/v2/threads/${enc(id)}`, { signal });

/** One thread: the view and its first page of members. */
export const threadQuery = (id: string) =>
  queryOptions({
    queryKey: threadKeys.view(id),
    queryFn: ({ signal }) => readThread(id, signal),
  });

export interface CatchUpItem {
  thread: Thread;
  messages: ChatMessage[];
  new_count: number;
  more?: boolean;
  mention?: boolean;
}
export interface CatchUp {
  count: number;
  items: CatchUpItem[];
  next_cursor?: string | null;
}

/** What is new, thread by thread, mentions first: which conversations ask you (the Dot), and whether Tico has
    something to catch you up on. */
export const catchUpQuery = queryOptions({
  queryKey: threadKeys.catchUp,
  queryFn: ({ signal }) => api.get<CatchUp>('/api/v2/threads/catch-up?limit=20', { signal }),
});

/**
 * The second chat's thread. `thread:<id>` is the thread itself; `agent:<slug>` and `person:<id>` are the viewer's DM
 * with them, which the server opens when there is none yet (as the old app's "Message" does).
 */
export const chatQuery = (chat: string) => {
  const thread = chat.startsWith('thread:') ? chat.slice('thread:'.length) : undefined;
  return queryOptions({
    queryKey: thread === undefined ? threadKeys.chat(chat) : threadKeys.view(thread),
    queryFn: async ({ signal }): Promise<Thread> => {
      if (thread !== undefined) return readThread(thread, signal);
      const actor = chatActor(chat);
      if (!actor) throw new Error('not a chat address');
      return api.get<Thread>(`/api/v2/threads/dm${qs({ with: actor })}`, { signal });
    },
  });
};

/** A team's own thread: its Home (`group:<id>`), which the server makes the first time it is opened. */
export const teamThreadQuery = (group: string) =>
  queryOptions({
    queryKey: threadKeys.team(group),
    queryFn: ({ signal }) => api.get<Thread>(`/api/v2/threads/home/${enc(`group:${group}`)}`, { signal }),
    staleTime: Infinity,
  });

/* ---------------------------------------------------------------- thread changes */

function patchRow(id: string, fn: (t: Thread) => Thread) {
  return {
    list: (d: ThreadList | undefined) =>
      d && { ...d, pages: d.pages.map((p) => ({ ...p, rows: p.rows.map((t) => (t.id === id ? fn(t) : t)) })) },
    view: (t: Thread | undefined) => t && fn(t),
  };
}

export function renameThread(id: string, title: string) {
  const p = patchRow(id, (t) => ({ ...t, title }));
  return write({
    key: [threadKeys.list, threadKeys.view(id)],
    optimistic: (d: ThreadList | Thread | undefined) => (d && 'pages' in d ? p.list(d) : p.view(d)),
    send: (idempotencyKey) => api.patch<Thread>(`/api/v2/threads/${enc(id)}`, { title }, { idempotencyKey }),
  });
}

/** Archive a thread: its row leaves the list at once and goes to Archived at its foot. */
export async function archiveThread(id: string, archived: boolean) {
  const out = await write({
    key: threadKeys.list,
    optimistic: (d: ThreadList | undefined) =>
      d && { ...d, pages: d.pages.map((p) => ({ ...p, rows: p.rows.filter((t) => t.id !== id || !archived) })) },
    send: (idempotencyKey) => api.patch<Thread>(`/api/v2/threads/${enc(id)}`, { archived }, { idempotencyKey }),
  });
  void queryClient.invalidateQueries({ queryKey: memberKeys.archived });
  return out;
}

export async function addPeople(id: string, actors: string[]) {
  const view = threadKeys.view(id);
  await write({
    key: view,
    optimistic: (t: Thread | undefined) =>
      t && { ...t, members: { ...t.members, members: [...(t.members?.members ?? []), ...actors.map((actor): Member => ({ actor }))] } },
    send: async (idempotencyKey) => {
      for (const actor of actors)
        await api.post(`/api/v2/threads/${enc(id)}/members`, { actor }, { idempotencyKey: `${idempotencyKey}:${actor}` });
    },
  });
  void queryClient.invalidateQueries({ queryKey: view });
}

/** Read everything in a thread and its sub-threads (Catch up's "Mark read"). */
export function readAll(ids: string[]) {
  return write({
    key: [threadKeys.catchUp, threadKeys.list],
    optimistic: (d: CatchUp | ThreadList | undefined) => {
      if (!d) return d;
      if ('pages' in d)
        return { ...d, pages: d.pages.map((p) => ({ ...p, rows: p.rows.map((t) => (ids.includes(t.id) ? { ...t, me: { ...t.me, unread: 0 } } : t)) })) };
      const items = d.items.filter((i) => !ids.includes(i.thread.id));
      return { ...d, items, count: Math.max(0, d.count - (d.items.length - items.length)) };
    },
    send: async (idempotencyKey) => {
      for (const id of ids) await api.post(`/api/v2/threads/${enc(id)}/read-all`, {}, { idempotencyKey: `${idempotencyKey}:${id}` });
    },
  });
}

/**
 * The one group DM for exactly these people (you too): the open one, or a new one. Nothing is sent. The server still
 * asks a new one with two or more others for a name, so it is named after its people, as its row shows it anyway.
 * The answer goes first in the list.
 */
export function groupDm(members: string[], title: string) {
  return write({
    key: threadKeys.list,
    optimistic: (d: ThreadList | undefined) => d,
    send: (idempotencyKey) => api.post<Thread>('/api/v2/threads/dm', { members, title }, { idempotencyKey }),
    settle: (d: ThreadList | undefined, t) => {
      const [first, ...more] = d?.pages ?? [];
      if (!d || !first || !t?.id) return d;
      return { ...d, pages: [{ ...first, rows: [t, ...first.rows.filter((r) => r.id !== t.id)] }, ...more] };
    },
  });
}

/* ---------------------------------------------------------------- mail */

export interface Mailbox {
  address: string;
  name?: string;
  error?: string | null;
  agent_bot?: string | boolean | null;
  /** Set when an agent works this mailbox from written instructions. */
  agent_instructions?: string | boolean | null;
  /** What the person may do here (backend mailbox_access.may). */
  may?: { send?: boolean; organize?: boolean; draft?: boolean };
  /** Which Google credential the server reads this mailbox with; 'none': nothing can until someone connects it. */
  connection?: 'workspace' | 'personal' | 'none';
}

export const mailboxesQuery = queryOptions({
  queryKey: mailKeys.boxes,
  queryFn: async ({ signal }) => (await api.get<{ mailboxes?: Mailbox[] }>('/api/v2/mail/mailboxes', { signal })).mailboxes ?? [],
});

/** A mailbox's inbox, its first page: what the menu's Threads list shows from it (the mailbox page reads the rest). */
export const inboxQuery = (box: string) =>
  queryOptions({
    queryKey: [...mailKeys.list(box), 'inbox'] as const,
    queryFn: ({ signal }) => api.get<{ messages: Letter[] }>(`/api/v2/mail/messages${qs({ mailbox: box, limit: PAGE })}`, { signal }),
  });

export const letterQuery = (box: string, id: string) =>
  queryOptions({
    queryKey: mailKeys.letter(box, id),
    queryFn: async ({ signal }) =>
      (await api.get<{ messages: Letter[] }>(`/api/v2/mail/threads/${enc(id)}${qs({ mailbox: box })}`, { signal })).messages,
  });

/** A reply in a mail thread: Gmail drafts it in the thread, then the send gate sends that draft (its undo window and
    any OK it needs are the gate's). Nothing shows before: the thread is read again after, as mail is after every write. */
export function replyMail(box: string, thread: string, body: string) {
  return write({
    key: mailKeys.letter(box, thread),
    optimistic: (d: Letter[] | undefined) => d,
    send: async (idempotencyKey) => {
      const d = await api.post<{ draft: string; thread?: string }>('/api/v2/mail/drafts', { mailbox: box, body, reply_to: thread }, { idempotencyKey });
      return api.post<{ send: unknown }>('/api/v2/email/sends', { mailbox: box, draft_id: d.draft, thread_id: d.thread || thread }, { idempotencyKey: `${idempotencyKey}:send` });
    },
  });
}

type MailList = InfiniteData<{ messages: Letter[] }>;
/** Every loaded list of this mailbox (each search is its own). */
const mailLists = (box: string) => {
  const found = queryClient.getQueriesData({ queryKey: mailKeys.list(box) }).map(([k]) => k);
  return found.length ? found : [mailKeys.list(box)];
};
const organize = (box: string, action: string, ids: string[], idempotencyKey: string) =>
  api.post('/api/v2/mail/messages/organize', { mailbox: box, action, ids }, { idempotencyKey });

/** Archive a mail thread (Gmail takes it out of the inbox; nothing is deleted): its row leaves the list at once. */
export function archiveMail(box: string, thread: string, letters: string[]) {
  return write({
    key: mailLists(box),
    // the mailbox page's lists and the menu's inbox (one page, no `pages`)
    optimistic: (d: MailList | { messages: Letter[] } | undefined) => {
      const keep = (list: Letter[]) => list.filter((l) => l.thread_id !== thread);
      if (!d) return d;
      return 'pages' in d ? { ...d, pages: d.pages.map((p) => ({ ...p, messages: keep(p.messages) })) } : { ...d, messages: keep(d.messages) };
    },
    send: (idempotencyKey) => organize(box, 'archive', letters, idempotencyKey),
  });
}

/** Mark a mail thread unread again: its newest letter. */
export function markMailUnread(box: string, letter: string) {
  return write({
    key: mailLists(box),
    optimistic: (d: MailList | { messages: Letter[] } | undefined) => {
      const mark = (list: Letter[]) => list.map((l) => (l.msg_id === letter ? { ...l, labels: [...(l.labels ?? []), 'UNREAD'] } : l));
      if (!d) return d;
      return 'pages' in d ? { ...d, pages: d.pages.map((p) => ({ ...p, messages: mark(p.messages) })) } : { ...d, messages: mark(d.messages) };
    },
    send: (idempotencyKey) => organize(box, 'mark_unread', [letter], idempotencyKey),
  });
}

/* ---------------------------------------------------------------- texts (Messaging) */

/** The server matches substrings in `from`; a single contact's timeline keeps only exact matches. */
export const textsBySenderQuery = (from?: string) =>
  infiniteQueryOptions({
    queryKey: from ? [...textKeys.list, from] as const : textKeys.list,
    queryFn: ({ pageParam, signal }) =>
      api.get<{ messages: Text[]; next?: number | null }>(
        `/api/v2/inbound/messages${qs({ kind: 'sms', from, limit: PAGE, before: pageParam })}`,
        { signal },
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });

export const textListQuery = textsBySenderQuery();

export const textQuery = (id: string) =>
  queryOptions({
    queryKey: textKeys.one(id),
    queryFn: async ({ signal }) => {
      let text = '';
      let offset = 0;
      while (true) {
        const part = await api.get<{ text: string; next_offset?: number | null }>(
          `/api/v2/inbound/messages/${enc(id)}${qs({ offset: offset || undefined })}`, { signal },
        );
        text += part.text;
        if (part.next_offset == null || part.next_offset <= offset) return { text };
        offset = part.next_offset;
      }
    },
  });

/* ---------------------------------------------------------------- updates */

/** `archive` is the updates the person archived (or a later day's update replaced). */
export type UpdateTab = 'all' | 'daily' | 'weekly' | 'archive';
interface UpdatePage {
  updates: Update[];
  unread: number;
  next_before?: string | null;
}
export type UpdateList = InfiniteData<UpdatePage, string | null>;

export const updateListQuery = (tab: UpdateTab) =>
  infiniteQueryOptions({
    queryKey: updateKeys.list(tab),
    // The menu can stop observing while the page loader still needs the same request.
    queryFn: ({ pageParam }) =>
      api.get<UpdatePage>(
        `/api/v2/updates${qs({ kind: tab === 'daily' || tab === 'weekly' ? tab : undefined, archive: tab === 'archive' || undefined, limit: PAGE, before: pageParam })}`,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before ?? undefined,
  });

export const updateQuery = (id: string) =>
  queryOptions({
    queryKey: updateKeys.one(id),
    queryFn: async ({ signal }) => (await api.get<{ update: Update }>(`/api/v2/updates/${enc(id)}`, { signal })).update,
  });

const TABS: UpdateTab[] = ['all', 'daily', 'weekly', 'archive'];
const setRead = (ids: string[] | 'all', read: boolean) => (d: UpdateList | undefined) =>
  d && {
    ...d,
    pages: d.pages.map((p) => ({
      ...p,
      updates: p.updates.map((u) => (ids === 'all' || ids.includes(u.id) ? { ...u, read } : u)),
    })),
  };

export function markUpdatesRead(ids: string[] | 'all', read = true) {
  const details = ids === 'all'
    ? queryClient.getQueriesData<Update>({ queryKey: ['updates', 'one'] }).map(([key]) => key)
    : ids.map(updateKeys.one);
  const markList = setRead(ids, read);
  return write({
    key: [...TABS.map(updateKeys.list), ...details],
    optimistic: (d: UpdateList | Update | undefined) => d && ('pages' in d ? markList(d) : { ...d, read }),
    send: (idempotencyKey) =>
      api.post('/api/v2/updates/read', ids === 'all' ? { all: true } : { ids, read }, { idempotencyKey }),
  });
}

/** Archive or move back. Rows keep their place with the new mark; each list shows only its own side (inArchive). */
export function archiveUpdates(ids: string[], archived = true) {
  return write({
    key: TABS.map(updateKeys.list),
    optimistic: (d: UpdateList | undefined) =>
      d && { ...d, pages: d.pages.map((p) => ({ ...p, updates: p.updates.map((u) => (ids.includes(u.id) ? { ...u, archived } : u)) })) },
    send: (idempotencyKey) => api.post('/api/v2/updates/archive', { ids, archived }, { idempotencyKey }),
  });
}

/* ---------------------------------------------------------------- members, settings, browse */

const memberKeys = {
  list: (id: string) => ['threads', 'members', id] as const,
  browse: (q: string) => ['threads', 'browse', q] as const,
  archived: ['threads', 'archived'] as const,
};

export interface MemberRow {
  actor: string;
  role: 'owner' | 'member' | string;
  added_by?: string | null;
  created_at?: string;
}
interface MemberPage {
  members: MemberRow[];
  next_cursor?: string | null;
}
export type MemberList = InfiniteData<MemberPage, string | null>;

/** Who is in a thread, by actor id, a page at a time (a company thread can hold everyone). */
export const membersQuery = (id: string) =>
  infiniteQueryOptions({
    queryKey: memberKeys.list(id),
    queryFn: ({ pageParam, signal }) =>
      api.get<MemberPage>(`/api/v2/threads/${enc(id)}/members${qs({ limit: 200, cursor: pageParam })}`, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

const refreshThread = (id: string) => {
  void queryClient.invalidateQueries({ queryKey: memberKeys.list(id) });
  void queryClient.invalidateQueries({ queryKey: threadKeys.view(id) });
};

/** Take someone out of a thread (a manager, or yourself to leave). */
export async function removeMember(id: string, actor: string) {
  const out = await write({
    key: [memberKeys.list(id), threadKeys.view(id)],
    optimistic: (d: MemberList | Thread | undefined) => {
      if (!d) return d;
      if ('pages' in d) return { ...d, pages: d.pages.map((p) => ({ ...p, members: p.members.filter((m) => m.actor !== actor) })) };
      return { ...d, members: { ...d.members, members: (d.members?.members ?? []).filter((m) => m.actor !== actor) } };
    },
    send: (idempotencyKey) => api.delete(`/api/v2/threads/${enc(id)}/members/${enc(actor)}`, { idempotencyKey }),
  });
  refreshThread(id);
  return out;
}

export type Notify = 'live' | 'mentions' | 'digest' | 'muted';

/** How the viewer hears about a thread; following it is what joins a company or team thread. */
export async function followThread(id: string, notify: Notify) {
  const p = patchRow(id, (t) => ({ ...t, me: { ...t.me, following: true, notify } }));
  const out = await write({
    key: [threadKeys.list, threadKeys.view(id)],
    optimistic: (d: ThreadList | Thread | undefined) => (d && 'pages' in d ? p.list(d) : p.view(d)),
    send: (idempotencyKey) => api.put(`/api/v2/threads/${enc(id)}/follow`, { notify }, { idempotencyKey }),
  });
  return out;
}

export interface ThreadChange {
  pinned_for_everyone?: boolean;
  retention_days?: number | null;
  audience?: 'members' | 'team' | 'company';
  audience_group_id?: string | null;
}

/** A manager's change to the thread itself (PATCH answers with the thread's view). */
export function changeThread(id: string, change: ThreadChange) {
  const p = patchRow(id, (t) => ({ ...t, ...change }) as Thread);
  return write({
    key: [threadKeys.list, threadKeys.view(id)],
    optimistic: (d: ThreadList | Thread | undefined) => (d && 'pages' in d ? p.list(d) : p.view(d)),
    send: (idempotencyKey) => api.patch<Thread>(`/api/v2/threads/${enc(id)}`, change, { idempotencyKey }),
    settle: (d: ThreadList | Thread | undefined, t) => (d && !('pages' in d) && t?.id ? { ...d, ...t, members: d.members } : d),
  });
}

export interface Summary {
  posted?: unknown;
  words?: string;
}

/** Tico reads what is new here and posts a summary in the person's own chat with Tico. */
export const summarizeThread = (id: string) => api.post<Summary>(`/api/v2/threads/${enc(id)}/catch-up`, { scope: 'subtree' });

/** A bot's Home: open the messages from before it had its own room to the Home's readers. */
export async function openEarlier(id: string) {
  const out = await api.post<{ home: string; messages: number }>(`/api/v2/threads/${enc(id)}/open-earlier`, {});
  void queryClient.invalidateQueries({ queryKey: threadKeys.view(id) });
  void queryClient.invalidateQueries({ queryKey: keys.thread.timeline(id) });
  return out;
}

/** Delete a thread for good: the server allows it only when nobody else is in it and nothing in it is left. */
export function deleteThread(id: string) {
  return write({
    key: threadKeys.list,
    optimistic: (d: ThreadList | undefined) =>
      d && { ...d, pages: d.pages.map((p) => ({ ...p, rows: p.rows.filter((t) => t.id !== id) })) },
    send: (idempotencyKey) => api.delete<{ deleted: string }>(`/api/v2/threads/${enc(id)}`, { idempotencyKey }),
  });
}

export type BrowseRow = Thread & { member_count?: number };
interface BrowsePage {
  threads: BrowseRow[];
  next_cursor: string | null;
}
export type BrowseList = InfiniteData<BrowsePage, string | null>;

/** Company and team threads the viewer can read and is not in, newest activity first; `q` matches the title. */
export const browseQuery = (q: string) =>
  infiniteQueryOptions({
    queryKey: memberKeys.browse(q),
    queryFn: ({ pageParam, signal }) =>
      api.get<BrowsePage>(`/api/v2/threads/browse${qs({ limit: PAGE, q, cursor: pageParam })}`, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 30_000,
    // new words keep the last rows on screen until the answer comes, rather than a skeleton per key press
    placeholderData: keepPreviousData,
  });

/** Archived threads the viewer manages. */
export const archivedQuery = infiniteQueryOptions({
  queryKey: memberKeys.archived,
  queryFn: ({ pageParam, signal }) => api.get<BrowsePage>(`/api/v2/threads/archived${qs({ limit: PAGE, cursor: pageParam })}`, { signal }),
  initialPageParam: null as string | null,
  getNextPageParam: (last) => last.next_cursor ?? undefined,
  staleTime: 30_000,
});

const dropFrom = (id: string) => (d: BrowseList | undefined) =>
  d && { ...d, pages: d.pages.map((p) => ({ ...p, threads: p.threads.filter((t) => t.id !== id) })) };

/** Join a thread from Browse: follow it, then it is in the list. */
export async function joinThread(id: string) {
  await write({
    key: queryClient.getQueriesData({ queryKey: ['threads', 'browse'] }).map(([k]) => k),
    optimistic: dropFrom(id),
    send: (idempotencyKey) => api.put(`/api/v2/threads/${enc(id)}/follow`, { notify: 'live' }, { idempotencyKey }),
  });
  void queryClient.invalidateQueries({ queryKey: threadKeys.list });
}

/** Bring an archived thread back into the list. */
export async function unarchiveThread(id: string) {
  await write({
    key: memberKeys.archived,
    optimistic: dropFrom(id),
    send: (idempotencyKey) => api.patch<Thread>(`/api/v2/threads/${enc(id)}`, { archived: false }, { idempotencyKey }),
  });
  void queryClient.invalidateQueries({ queryKey: threadKeys.list });
}

/* ---------------------------------------------------------------- a mailbox's instructions */

export interface MailInstructions {
  mailbox: string;
  bot: string;
  instructions: string;
  updated_at?: string | null;
}

/** What the mailbox's agent works from (written by the agent's own files; read only here). */
export const mailInstructionsQuery = (box: string) =>
  queryOptions({
    queryKey: ['mail', 'agent', box] as const,
    queryFn: ({ signal }) => api.get<MailInstructions>(`/api/v2/mail/agent${qs({ mailbox: box })}`, { signal }),
    enabled: !!box,
  });

/* ---------------------------------------------------------------- what Tico has learned */

quietRoot('learned');

export interface LearnedEntry {
  id: string;
  layer: 'personal' | 'company' | string;
  text: string;
  created_at: string;
  updated_at?: string | null;
  taught_in_chat?: boolean;
}
export interface Learned {
  personal: LearnedEntry[];
  company: {
    vision?: string | null;
    description?: string | null;
    priorities?: { id: string; title: string; status?: string }[];
    entries: LearnedEntry[];
  } | null;
}
export interface Viewers {
  bots: { bot: string; slug?: string; name?: string; added_at?: string; last_read_at?: string | null }[];
  include_learned: boolean;
}

const learnedKeys = { all: ['learned', 'entries'] as const, viewers: ['learned', 'viewers'] as const };

export const learnedQuery = queryOptions({
  queryKey: learnedKeys.all,
  queryFn: ({ signal }) => api.get<Learned>('/api/v2/assistant/learned', { signal }),
});

/** The agents that can read the person's chat with Tico. */
export const viewersQuery = queryOptions({
  queryKey: learnedKeys.viewers,
  queryFn: ({ signal }) => api.get<Viewers>('/api/v2/assistant/viewers', { signal }),
});

const mapEntries = (d: Learned | undefined, fn: (list: LearnedEntry[]) => LearnedEntry[]): Learned | undefined =>
  d && { personal: fn(d.personal), company: d.company && { ...d.company, entries: fn(d.company.entries) } };

export function editLearned(id: string, text: string) {
  return write({
    key: learnedKeys.all,
    optimistic: (d: Learned | undefined) => mapEntries(d, (l) => l.map((e) => (e.id === id ? { ...e, text } : e))),
    send: (idempotencyKey) => api.patch<{ entry: LearnedEntry }>(`/api/v2/assistant/learned/${enc(id)}`, { text }, { idempotencyKey }),
  });
}

export function forgetLearned(id: string) {
  return write({
    key: learnedKeys.all,
    optimistic: (d: Learned | undefined) => mapEntries(d, (l) => l.filter((e) => e.id !== id)),
    send: (idempotencyKey) => api.delete(`/api/v2/assistant/learned/${enc(id)}`, { idempotencyKey }),
  });
}

export function addViewer(bot: string) {
  return write({
    key: learnedKeys.viewers,
    optimistic: (d: Viewers | undefined) => d && { ...d, bots: [...d.bots.filter((b) => b.bot !== bot && b.slug !== bot), { bot, slug: bot }] },
    send: (idempotencyKey) => api.post<Viewers>('/api/v2/assistant/viewers', { bot }, { idempotencyKey }),
    settle: (_d: Viewers | undefined, answer) => answer,
  });
}

export function removeViewer(bot: string) {
  return write({
    key: learnedKeys.viewers,
    optimistic: (d: Viewers | undefined) => d && { ...d, bots: d.bots.filter((b) => b.bot !== bot && b.slug !== bot) },
    send: (idempotencyKey) => api.delete<Viewers>(`/api/v2/assistant/viewers/${enc(bot)}`, { idempotencyKey }),
    settle: (_d: Viewers | undefined, answer) => answer,
  });
}

/* ---------------------------------------------------------------- the Mail list, or the mail with some words in it */

/** While new words load, the rows already shown stay (the search field keeps focus). */
export const mailFindQuery = (box: string, q?: string) =>
  infiniteQueryOptions({
    queryKey: [...mailKeys.list(box), { q: q ?? '' }] as const,
    queryFn: ({ pageParam, signal }) =>
      api.get<{ messages: Letter[]; next_cursor?: string | null }>(`/api/v2/mail/messages${qs({ mailbox: box, q, limit: PAGE, cursor: pageParam })}`, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // Only within one mailbox: another mailbox's rows never stand in for this one's.
    placeholderData: (previous, last) => (last?.queryKey[2] === box ? previous : undefined),
    enabled: !!box,
  });
