import type { AnyRouter } from '@tanstack/react-router';

/* What the screens lend the action layer. Actions sit below the screens (no import of shell or tico, lint), so
   ActionsRoot, rendered by the shell, hands these in. */
export interface Bound {
  router: AnyRouter | null;
  /** The element flights start from: `dot.el()` from @/tico/dot. */
  dot: () => Element | null;
  /** Fold (true) or open (false) a sheet by hand; undefined flips it. */
  fold: ((sheet: string, folded?: boolean) => void) | null;
}

export const bound: Bound = { router: null, dot: () => null, fold: null };

export function bind(next: Partial<Bound>): void {
  Object.assign(bound, next);
}
