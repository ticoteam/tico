import { useSyncExternalStore } from 'react';

// Changes made while offline wait here, in memory only, and are sent in order once the stream is live again
// (platforms.md 7; architecture.md 4 keeps them out of storage). What may be held is the caller's call through
// write()'s `offline` option:
//   held:    a chat message, task field edits (title, description, assignee, stage, due date), comments,
//            reactions, marking read, files of 10MB or less;
//   refused: approvals and anything that spends money or acts outside Tico (approve, deny, run an agent, send
//            email, payments, invites, rights), deletes, sign-in changes, calls, files over 10MB.
// A reload while something is held asks first (the browser's leave prompt); nothing survives it.

export type HeldCount = number;

interface Item {
  send: () => Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (err: unknown) => void;
}

const items: Item[] = [];
const listeners = new Set<() => void>();
let flushing = false;
let prompting = false;
let offline = false;

/** A send that never reached the server (fetch's TypeError, or an API error marked as network). */
export const isNetworkError = (err: unknown): boolean =>
  err instanceof TypeError ||
  (typeof err === 'object' && err !== null && ((err as { network?: unknown }).network === true || (err as { status?: unknown }).status === 0));

/**
 * The stream's word on the connection (live/stream.ts calls this; data/ sits below the stream and does not ask it).
 * Offline, holdable changes wait; back to live, what waits is sent.
 */
export function connectionChanged(status: 'live' | 'connecting' | 'offline' | 'signed-out'): void {
  offline = status === 'offline';
  if (status === 'live') void flushHeld();
}

/** Offline by the stream, or by the browser (which can say online on dead Wi-Fi, never the reverse). */
export const isOffline = (): boolean => offline || navigator.onLine === false;

function changed(): void {
  // Only while something is held: a beforeunload listener can keep a page out of the browser's back cache.
  if (items.length && !prompting) window.addEventListener('beforeunload', leavePrompt);
  else if (!items.length && prompting) window.removeEventListener('beforeunload', leavePrompt);
  prompting = items.length > 0;
  for (const fn of [...listeners]) fn();
}

function leavePrompt(e: BeforeUnloadEvent): void {
  e.preventDefault();
}

/** Hold `send` until the connection is back; the promise settles with its answer then. */
export function hold<T>(send: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    items.push({ send, resolve: resolve as (v: unknown) => void, reject });
    changed();
  });
}

/** Send what is held, one at a time, in order. A network failure stops it; the rest waits for the next return. */
export async function flushHeld(): Promise<void> {
  if (flushing) return;
  flushing = true;
  try {
    while (items.length) {
      const item = items[0] as Item;
      try {
        item.resolve(await item.send());
      } catch (err) {
        if (isNetworkError(err)) return;
        item.reject(err); // refused (a task moved, rights changed): write() reverts it and the row says so
      }
      items.shift();
      changed();
    }
  } finally {
    flushing = false;
  }
}

export const heldCount = (): HeldCount => items.length;

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => void listeners.delete(fn);
};

/** How many changes are held: the offline line says "Offline. 2 changes are held and will send." */
export function useHeldCount(): HeldCount {
  return useSyncExternalStore(subscribe, heldCount, heldCount);
}

/** Sign-out and tests: drop what is held. Each one's write() reverts. */
export function clearHeld(): void {
  offline = false;
  const dropped = items.splice(0);
  changed();
  for (const item of dropped) item.reject(new Error('Signed out before it was sent'));
}
