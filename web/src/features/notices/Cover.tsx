import { useEffect, useRef, type ReactNode } from 'react';
import './notices.css';

const forcedInert = new WeakSet<HTMLElement>();

/** Notices share a stacking level, so the last mounted notice in DOM order is the one on top. */
function syncCovers(parent: HTMLElement, leaving?: HTMLElement) {
  const siblings = [...parent.children].filter((child): child is HTMLElement => child instanceof HTMLElement);
  const top = siblings.findLast((child) => child !== leaving && child.classList.contains('nt-cover'));
  for (const child of siblings) {
    if (top && child !== top) {
      if (!child.inert) {
        child.inert = true;
        forcedInert.add(child);
      }
    } else if (forcedInert.delete(child)) {
      child.inert = false;
    }
  }
  top?.querySelector<HTMLElement>('.btn-fill')?.focus();
}

/** A card over the whole app: what is behind it cannot be reached (inert) until it goes, and its filled button has focus. */
export function Cover({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    const parent = el?.parentElement;
    if (!el || !parent) return;
    syncCovers(parent);
    return () => {
      // Strict Mode can clean up an effect while its element is still attached.
      syncCovers(parent, el);
    };
  }, []);
  return (
    <div ref={ref} className="nt-cover" role="alertdialog" aria-modal="true" aria-label={label}>
      {children}
    </div>
  );
}
