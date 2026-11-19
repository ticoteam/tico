import { useEffect, useRef, type ReactNode } from 'react';
import './notices.css';

/** A card over the whole app: what is behind it cannot be reached (inert) until it goes, and its filled button has focus. */
export function Cover({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    const parent = el?.parentElement;
    if (!el || !parent) return;
    const behind = [...parent.children].filter((c): c is HTMLElement => c !== el && c instanceof HTMLElement && !c.inert && !c.classList.contains('nt-cover'));
    for (const b of behind) b.inert = true;
    el.querySelector<HTMLElement>('.btn-fill')?.focus();
    return () => {
      for (const b of behind) b.inert = false;
    };
  }, []);
  return (
    <div ref={ref} className="nt-cover" role="alertdialog" aria-modal="true" aria-label={label}>
      {children}
    </div>
  );
}
