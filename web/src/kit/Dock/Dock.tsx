import { createContext, use, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Where the phone's dock is: a place above its bar, outside the page and its cards, so what is docked there (a task's
    decision) stays in reach whatever card is up. Null off the phone. */
export const DockSlot = createContext<HTMLElement | null>(null);

/** What holds the bottom of a page: the phone's dock when there is one, else where it is drawn (a computer hides it,
    parts.css `.dock`). */
export function Dock({ children }: { children: ReactNode }) {
  const slot = use(DockSlot);
  const box = (
    <div className="dock" data-dock>
      {children}
    </div>
  );
  return slot ? createPortal(box, slot) : box;
}
