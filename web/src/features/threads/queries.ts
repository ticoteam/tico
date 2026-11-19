import { infiniteQueryOptions, queryOptions, type InfiniteData } from '@tanstack/react-query';
import { api } from '@/api/client';
import { sendMessage, timelineQuery, type ChatMessage } from '@/conversation';
import { keys, queryClient, quietRoot, write } from '@/data';
import { toPage, type Letter, type ListPage, type Member, type Text, type Thread, type ThreadList, type Update } from './model';

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
  search: (q: string) => ['threads', 'search', q] as const,
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
  queryFn: async ({ pageParam, signal }): Promise<ListPage> =>
    toPage(await api.get<Parameters<typeof toPage>[0]>(`/api/v2/threads${qs({ sort: 'recent', limit: PAGE, cursor: pageParam })}`, { signal })),
  initialPageParam: null as string | null,
  getNextPageParam: (last) => last.next ?? undefined,
});

/** One thread: the view and its first page of members. */
export const threadQuery = (id: string) =>
  queryOptions({
    queryKey: threadKeys.view(id),
    queryFn: ({ signal }) => api.get<Thread>(`/api/v2/threads/${enc(id)}`, { signal }),
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

/** What is new, thread by thread (the "Catch up" row's count and its pane). */
export const catchUpQuery = queryOptions({
  queryKey: threadKeys.catchUp,
  queryFn: ({ signal }) => api.get<CatchUp>('/api/v2/threads/catch-up?limit=20', { signal }),
});

export interface SearchHit {
  thread_id: string;
  message_id: string;
  title?: string;
  excerpt: string;
  created_at?: string;
}
interface SearchPage {
  hits: SearchHit[];
  next_cursor: string | null;
}

/**
 * Threads whose messages hold every word of `q`, newest first, from the server's search (a company has far more
 * threads than the list loads). Not live: read again when it is stale.
 */
export const threadSearchQuery = (q: string) =>
  infiniteQueryOptions({
    queryKey: threadKeys.search(q),
    queryFn: ({ pageParam, signal }) =>
      api.get<SearchPage>(`/api/v2/threads/search${qs({ q, limit: PAGE, before: pageParam })}`, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 30_000,
    enabled: q.length > 0,
  });

/**
 * The second chat's thread. `thread:<id>` is the thread itself; `agent:<slug>` and `person:<id>` are the viewer's DM
 * with them, which the server opens when there is none yet (as the old app's "Message" does).
 */
export const chatQuery = (chat: string) =>
  queryOptions({
    queryKey: threadKeys.chat(chat),
    queryFn: async ({ signal }): Promise<Thread> => {
      const [kind, ...rest] = chat.split(':');
      const id = rest.join(':');
      if (kind === 'thread') return api.get<Thread>(`/api/v2/threads/${enc(id)}`, { signal });
      const actor = kind === 'agent' ? `bot:${id}` : `human:${id}`;
      return api.get<Thread>(`/api/v2/threads/dm${qs({ with: actor })}`, { signal });
    },
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

export function archiveThread(id: string, archived: boolean) {
  return write({
    key: threadKeys.list,
    optimistic: (d: ThreadList | undefined) =>
      d && { ...d, pages: d.pages.map((p) => ({ ...p, rows: p.rows.filter((t) => t.id !== id || !archived) })) },
    send: (idempotencyKey) => api.patch<Thread>(`/api/v2/threads/${enc(id)}`, { archived }, { idempotencyKey }),
  });
}

/** Muted is the follow level `muted` (the old app's Off); unmuting goes back to `live`. */
export function muteThread(id: string, muted: boolean) {
  const notify = muted ? 'muted' : 'live';
  const p = patchRow(id, (t) => ({ ...t, me: { ...t.me, following: true, notify } }));
  return write({
    key: [threadKeys.list, threadKeys.view(id)],
    optimistic: (d: ThreadList | Thread | undefined) => (d && 'pages' in d ? p.list(d) : p.view(d)),
    send: (idempotencyKey) => api.put(`/api/v2/threads/${enc(id)}/follow`, { notify }, { idempotencyKey }),
  });
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
 * A new DM with these people and its first message; two or more others need a name. Nothing shows before the server
 * answers (there is no thread id to show it under); the answer goes first in the list. Without `body` the DM is only
 * opened (or made).
 */
export function startNew(members: string[], body?: string, title?: string) {
  return write({
    key: threadKeys.list,
    optimistic: (d: ThreadList | undefined) => d,
    send: (idempotencyKey) =>
      api.post<Thread & { message?: unknown }>(
        '/api/v2/threads/dm',
        { members, ...(body ? { body } : {}), ...(title ? { title } : {}) },
        { idempotencyKey },
      ),
    settle: (d: ThreadList | undefined, t) => {
      const [first, ...more] = d?.pages ?? [];
      if (!d || !first || !t?.id) return d;
      return { ...d, pages: [{ ...first, rows: [t, ...first.rows.filter((r) => r.id !== t.id)] }, ...more] };
    },
  });
}

/**
 * A first message with files. The DM's start takes no files, so the DM is made first and the message then goes the
 * conversation's own way (its upload form), showing in the thread at once and marked "Not sent" if it fails.
 */
export async function startWithFiles(members: string[], text: string, files: File[], me: string, title?: string) {
  const t = await startNew(members, undefined, title);
  if (!t?.id) return t;
  await queryClient.ensureInfiniteQueryData(timelineQuery(t.id)).catch(() => undefined);
  void sendMessage({ id: t.id, variant: 'page', text, files, me });
  return t;
}

/* ---------------------------------------------------------------- mail */

export interface Mailbox {
  address: string;
  name?: string;
  error?: string | null;
  agent_bot?: string | null;
}

export const mailboxesQuery = queryOptions({
  queryKey: mailKeys.boxes,
  queryFn: async ({ signal }) => (await api.get<{ mailboxes: Mailbox[] }>('/api/v2/mail/mailboxes', { signal })).mailboxes,
});

export const mailListQuery = (box: string) =>
  infiniteQueryOptions({
    queryKey: mailKeys.list(box),
    queryFn: ({ pageParam, signal }) =>
      api.get<{ messages: Letter[]; next_cursor?: string | null }>(`/api/v2/mail/messages${qs({ mailbox: box, limit: PAGE, cursor: pageParam })}`, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!box,
  });

export const letterQuery = (box: string, id: string) =>
  queryOptions({
    queryKey: mailKeys.letter(box, id),
    queryFn: async ({ signal }) =>
      (await api.get<{ messages: Letter[] }>(`/api/v2/mail/threads/${enc(id)}${qs({ mailbox: box })}`, { signal })).messages,
  });

/* ---------------------------------------------------------------- texts (Messaging) */

export const textListQuery = infiniteQueryOptions({
  queryKey: textKeys.list,
  queryFn: ({ pageParam, signal }) =>
    api.get<{ messages: Text[]; next?: number | null }>(`/api/v2/inbound/messages${qs({ kind: 'sms', limit: PAGE, before: pageParam })}`, { signal }),
  initialPageParam: null as number | null,
  getNextPageParam: (last) => last.next ?? undefined,
});

export const textQuery = (id: string) =>
  queryOptions({
    queryKey: textKeys.one(id),
    queryFn: ({ signal }) => api.get<{ text: string; message_id: string }>(`/api/v2/inbound/messages/${enc(id)}`, { signal }),
  });

/* ---------------------------------------------------------------- updates */

export type UpdateTab = 'all' | 'daily' | 'weekly';
interface UpdatePage {
  updates: Update[];
  unread: number;
  next_before?: string | null;
}
export type UpdateList = InfiniteData<UpdatePage, string | null>;

export const updateListQuery = (tab: UpdateTab) =>
  infiniteQueryOptions({
    queryKey: updateKeys.list(tab),
    queryFn: ({ pageParam, signal }) =>
      api.get<UpdatePage>(`/api/v2/updates${qs({ kind: tab === 'all' ? undefined : tab, limit: PAGE, before: pageParam })}`, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before ?? undefined,
  });

export const updateQuery = (id: string) =>
  queryOptions({
    queryKey: updateKeys.one(id),
    queryFn: async ({ signal }) => (await api.get<{ update: Update }>(`/api/v2/updates/${enc(id)}`, { signal })).update,
  });

const TABS: UpdateTab[] = ['all', 'daily', 'weekly'];
const setRead = (ids: string[] | 'all', read: boolean) => (d: UpdateList | undefined) =>
  d && {
    ...d,
    pages: d.pages.map((p) => ({
      ...p,
      updates: p.updates.map((u) => (ids === 'all' || ids.includes(u.id) ? { ...u, read } : u)),
    })),
  };

export function markUpdatesRead(ids: string[] | 'all', read = true) {
  return write({
    key: TABS.map(updateKeys.list),
    optimistic: setRead(ids, read),
    send: (idempotencyKey) =>
      api.post('/api/v2/updates/read', ids === 'all' ? { all: true } : { ids, read }, { idempotencyKey }),
  });
}

export function archiveUpdates(ids: string[], archived = true) {
  return write({
    key: TABS.map(updateKeys.list),
    optimistic: (d: UpdateList | undefined) =>
      d && { ...d, pages: d.pages.map((p) => ({ ...p, updates: p.updates.filter((u) => !ids.includes(u.id) || !archived) })) },
    send: (idempotencyKey) => api.post('/api/v2/updates/archive', { ids, archived }, { idempotencyKey }),
  });
}
