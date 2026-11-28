import { useSyncExternalStore } from 'react';
import { api } from '@/api/client';
import type { NeedsYou, NeedsYouItem } from '@/api/schema';
import { queryClient } from './query-client';

/** The most a page holds (backend/views.py NEEDS_PAGE_MAX), and how many pages are read before the rest is left. */
const PAGE = 200;
const PAGES = 50;

/**
 * Every item of the Needs you list (GET /api/v2/needs-you), page after page: a page names the next with `next`. A
 * cursor from a list that moved meanwhile starts again from the top (`restart`), so what was read before it is dropped.
 */
export async function needsYouItems(signal?: AbortSignal): Promise<NeedsYouItem[]> {
  return (await needsYouRead(signal)).items;
}

/** needsYouItems, in Tico's order, with whether Tico is sorting the list in the background (`sorting`, until
    `sortingUntil`): a `needs` event follows with the new order. */
export async function needsYouRead(signal?: AbortSignal): Promise<{ items: NeedsYouItem[]; sorting: boolean; sortingUntil: string | null }> {
  let items: NeedsYouItem[] = [];
  let sorting = false;
  let sortingUntil: string | null = null;
  let cursor: string | null | undefined = null;
  for (let read = 0; read < PAGES; read++) {
    const r: NeedsYou = await api.get<NeedsYou>(`/api/v2/needs-you?limit=${PAGE}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal });
    if (!('items' in r)) break;
    items = r.restart ? r.items : [...items, ...r.items];
    if (!cursor || r.restart) {
      sorting = !!r.sorting;
      sortingUntil = r.sorting_until ?? null;
    }
    cursor = r.next;
    if (!cursor) break;
  }
  return { items, sorting, sortingUntil };
}

/** Needs-you items gone (`ids`): a reply sent in a chat, or the live `needs` event's `removed` (answered in another tab,
    taken back by its agent). Their asks leave the Agent bar now (Flow's list, under ['flow']), before it is read again. */
export function asksGone(ids: readonly string[]): void {
  if (!ids.length) return;
  queryClient.setQueryData<{ asks?: { id: string }[] }>(['flow'], (f) => (f?.asks?.some((a) => ids.includes(a.id)) ? { ...f, asks: f.asks.filter((a) => !ids.includes(a.id)) } : f));
}

/** An answer being sent: its task, what it says (`ok` Approve, `no` Request changes; none for a reply), and the change
    that takes it out of a Needs you read (features/tasks needsYouWithout, worked out on each read it is given). */
export interface Sending {
  task: string;
  how?: 'ok' | 'no';
  change: (read: unknown) => unknown;
}

/** Answers being sent, by `<task> <wait>`. A read that lands before an answer's reply still lists it, so each is applied
    to every read until the reply is heard: the row never comes back as if nothing was pressed. */
const sending = new Map<string, Sending>();
const heard = new Set<() => void>();
const told = () => heard.forEach((h) => h());

// The list dropped (a sign-out, a new team, the cache cleared): nothing read later is the list an answer was given on.
queryClient.getQueryCache().subscribe((e) => {
  if (e.type === 'removed' && e.query.queryKey.length === 1 && e.query.queryKey[0] === 'flow' && sending.size) {
    sending.clear();
    told();
  }
});

/** Keeps the answer on every Needs you read until it is let go (the reply heard, taken or refused). */
export function keepOut(key: string, answer: Sending): void {
  sending.set(key, answer);
  told();
}

/** The answer's reply heard: the reads after it are the server's own. */
export function letIn(key: string): void {
  if (sending.delete(key)) told();
}

/** A Needs you read with every answer still being sent taken out of it. */
export const withoutSending = <T>(read: T): T => [...sending.values()].reduce<unknown>((r, a) => a.change(r), read) as T;

const hear = (h: () => void) => {
  heard.add(h);
  return () => void heard.delete(h);
};

/** What an answer being sent on `task` says (`ok`, `no`), else null: its card and row show it is being sent. */
export function useSendingAnswer(task: string): 'ok' | 'no' | null {
  const how = () => [...sending.values()].find((a) => a.task === task && a.how)?.how ?? null;
  return useSyncExternalStore(hear, how, how);
}
