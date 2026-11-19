// Per-device preferences in localStorage, every key prefixed `tico-next:` so the old app's keys never collide.
// Storage can be missing or throw (private windows, blocked site data): reads fall back to memory and the default.
import { useCallback, useSyncExternalStore } from 'react';

const PREFIX = 'tico-next:';
const memory = new Map<string, string>();
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function readRaw(key: string): string | null {
  const k = PREFIX + key;
  try {
    const s = storage();
    if (s) return s.getItem(k);
  } catch {
    // fall through to memory
  }
  return memory.get(k) ?? null;
}

export function readPref<T>(key: string, fallback: T): T {
  const raw = readRaw(key);
  if (raw == null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writePref<T>(key: string, value: T): void {
  const k = PREFIX + key;
  const raw = JSON.stringify(value);
  try {
    const s = storage();
    if (s) s.setItem(k, raw);
    else memory.set(k, raw);
  } catch {
    // full or blocked: memory keeps it for this tab
    memory.set(k, raw);
  }
  for (const fn of listeners) fn();
}

/** Forget a preference: it reads as its default again, here and in every other tab. */
export function removePref(key: string): void {
  const k = PREFIX + key;
  memory.delete(k);
  try {
    storage()?.removeItem(k);
  } catch {
    // blocked: memory is already clear
  }
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  // another tab changed a pref
  const onStorage = (e: StorageEvent) => {
    if (e.key == null || e.key.startsWith(PREFIX)) fn();
  };
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(fn);
    window.removeEventListener('storage', onStorage);
  };
}

/** A preference as state. Keys carry a version (`theme.v1`) so a shape change starts fresh. */
export function usePref<T>(key: string, fallback: T): [T, (value: T) => void] {
  // the raw string is the snapshot so the value is stable between renders
  const raw = useSyncExternalStore(subscribe, () => readRaw(key), () => null);
  let value = fallback;
  if (raw != null) {
    try {
      value = JSON.parse(raw) as T;
    } catch {
      // keep the fallback
    }
  }
  const set = useCallback((v: T) => writePref(key, v), [key]);
  return [value, set];
}
