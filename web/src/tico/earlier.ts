import { useSyncExternalStore } from 'react';

/* Which earlier chat Tico's sheet shows in place of Tico's own (null: Tico's own). A view, not data: it lives here, so
   the actions that open and close it and the sheet that draws it share it without the address. */
let reading: string | null = null;
const subs = new Set<() => void>();

export function readEarlier(room: string | null): void {
  if (room === reading) return;
  reading = room;
  for (const f of subs) f();
}

export function useEarlier(): string | null {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => reading,
  );
}
