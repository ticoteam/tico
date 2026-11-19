// Calls this person declined. The server has no decline: a ring stops for them when they join or the call ends, so
// Decline only hides its card here (the old app's call-ring.ts did the same), keeping the last 50.
import { useSyncExternalStore } from 'react';

const KEY = 'tico-next:calls-declined';
const subs = new Set<() => void>();

const read = (): string[] => {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
};
let declined: readonly string[] = read();

export function decline(id: string): void {
  if (declined.includes(id)) return;
  declined = [...declined, id].slice(-50);
  try {
    localStorage.setItem(KEY, JSON.stringify(declined));
  } catch {
    // private window: declined for this visit only
  }
  subs.forEach((f) => f());
}

export const useDeclined = (): readonly string[] =>
  useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => declined,
    () => declined,
  );
