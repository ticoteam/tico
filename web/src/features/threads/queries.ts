import { infiniteQueryOptions, queryOptions, type InfiniteData } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { ChatMessage } from '@/conversation';
import { queryClient, quietRoot, write } from '@/data';
import { onTopic } from '@/live';
import {
  patchList,
  toPage,
  type Letter,
  type ListPage,
  type Member,
  type Text,
  type Thread,
  type ThreadEvent,
  type ThreadList,
  type Update,
} from './model';

const enc = encodeURIComponent;
const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
};

/* ---------------------------------------------------------------- keys */

export const threadKeys = {
  list: ['threads', 'list'] as const,
  view: (id: string) => ['threads', 'view', id] as const,
  catchUp: ['threads', 'catch-up'] as const,
  chat: (chat: string) => ['threads', 'chat', chat] as const,
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
 * answers (there is no thread id to show it under); the answer goes first in the list.
 */
export function startNew(members: string[], body: string, title?: string) {
  return write({
    key: threadKeys.list,
    optimistic: (d: ThreadList | undefined) => d,
    send: (idempotencyKey) =>
      api.post<Thread & { message?: unknown }>('/api/v2/threads/dm', { members, body, ...(title ? { title } : {}) }, { idempotencyKey }),
    settle: (d: ThreadList | undefined, t) => {
      const [first, ...more] = d?.pages ?? [];
      if (!d || !first || !t?.id) return d;
      return { ...d, pages: [{ ...first, rows: [t, ...first.rows.filter((r) => r.id !== t.id)] }, ...more] };
    },
  });
}

/* ---------------------------------------------------------------- live */

let lastCatchUp = 0;
let catchUpTimer: ReturnType<typeof setTimeout> | null = null;
const CATCH_UP_EVERY_MS = 3000;

// The contract: re-read Catch up at most every few seconds while activity comes in.
function catchUpAgain() {
  if (catchUpTimer) return;
  const wait = Math.max(0, lastCatchUp + CATCH_UP_EVERY_MS - Date.now());
  catchUpTimer = setTimeout(() => {
    catchUpTimer = null;
    lastCatchUp = Date.now();
    void queryClient.invalidateQueries({ queryKey: threadKeys.catchUp });
  }, wait);
}

/** Apply one frame of `threads` events to the list and the open views. Exported for the tests. */
export function applyThreadEvents(batch: ThreadEvent[]) {
  let readFirstPage = false;
  for (const e of batch) {
    const list = queryClient.getQueryData<ThreadList>(threadKeys.list);
    if (list) {
      const next = patchList(list, e);
      if (next === null) readFirstPage = true;
      else if (next !== list) queryClient.setQueryData(threadKeys.list, next);
    }
    const id = e.thread_id;
    if (id && e.thread && queryClient.getQueryData(threadKeys.view(id)))
      queryClient.setQueryData<Thread>(threadKeys.view(id), (t) => t && { ...t, ...e.thread, members: t.members });
    if (e.kind === 'members' && id) void queryClient.invalidateQueries({ queryKey: threadKeys.view(id) });
    if (e.kind === 'activity' || e.kind === 'read' || e.kind === 'archived') catchUpAgain();
  }
  if (readFirstPage)
    void queryClient.invalidateQueries({ queryKey: threadKeys.list, refetchType: 'active' }, { cancelRefetch: false });
}

// The `threads` topic's patcher, here until live/patch.ts has one. The topic is always followed (live/follow.ts BASE).
onTopic<ThreadEvent>('threads', applyThreadEvents);

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
