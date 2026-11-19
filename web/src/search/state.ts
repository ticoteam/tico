import { useSyncExternalStore } from 'react';

/* Whether search is open and what it was opened with. A few lines of module state, not a store library. */

export interface SearchState {
  open: boolean;
  /** The words it opened with (Tico's "Searched for frost"); the field starts with them. */
  q: string;
  /** Bumps on every open, so the field resets even when it was already open. */
  n: number;
}

let state: SearchState = { open: false, q: '', n: 0 };
const listeners = new Set<() => void>();

function set(next: SearchState) {
  state = next;
  for (const l of listeners) l();
}

export function openSearch(q = ''): void {
  set({ open: true, q, n: state.n + 1 });
}

export function closeSearch(): void {
  if (state.open) set({ ...state, open: false });
}

export const searchState = (): SearchState => state;

export function useSearchState(): SearchState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}
