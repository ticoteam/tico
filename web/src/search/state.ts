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

/* iOS raises the keyboard only for a focus() made inside the tap's own handler, and the palette's field is drawn a
   moment after it. So opening first puts the cursor in a field that is always in the page, out of sight; the palette's
   field takes the cursor from it when it draws, with anything typed in between, and the keyboard stays up. A desktop
   gets the same: the first letter after Cmd+K is never lost. */
let catcher: HTMLInputElement | null = null;
const LET_GO_MS = 1500;

function catchKeys(): void {
  if (typeof document === 'undefined') return;
  if (!catcher?.isConnected) {
    catcher = document.createElement('input');
    catcher.type = 'text';
    catcher.className = 'kb-catch';
    catcher.tabIndex = -1;
    catcher.autocomplete = 'off';
    catcher.setAttribute('aria-hidden', 'true');
    // in the page and focusable, never seen; 16px so iOS does not zoom to it
    catcher.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;pointer-events:none';
    document.body.append(catcher);
  }
  catcher.value = '';
  catcher.focus({ preventScroll: true });
  const held = catcher;
  // nothing took the cursor (search did not draw): let it go, so the keyboard does not stay up over nothing
  window.setTimeout(() => {
    if (document.activeElement === held) held.blur();
  }, LET_GO_MS);
}

/** What was typed while the palette's field was being drawn. */
export const caught = (): string => catcher?.value ?? '';

/** The palette's field has the cursor: what the catcher held is in it now. */
export function released(): void {
  if (catcher) catcher.value = '';
}

/** Opens search with the cursor in its field. From a tap, call it inside the tap's handler (iOS's keyboard). */
export function openSearch(q = ''): void {
  catchKeys();
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
