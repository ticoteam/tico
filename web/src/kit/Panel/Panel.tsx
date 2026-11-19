import { useEffect, useRef, type ReactNode } from 'react';
import { IconButton } from '@/kit/IconButton';
import './Panel.css';

export type PanelProps = {
  /** Names the panel for a screen reader. */
  label: string;
  /** What sits in the head before the close button: usually `<b>` and the name. */
  head: ReactNode;
  /** `.rail-sec` sections. */
  children: ReactNode;
  /** Buttons for the whole panel. */
  foot?: ReactNode;
  onClose: () => void;
  /** False leaves the x out: the head has its own way back (a phone's back arrow). Escape still closes. */
  x?: boolean;
  /** Added to the sheet's classes, for a panel of one kind (a record, a form). */
  className?: string;
};

/**
 * The side panel: its own sheet, with a head, sections and a foot. Where it sits (beside the page or over it) is the
 * shell's call. It closes with its x or Escape, and focus goes back to whatever opened it, usually a row.
 */
export function Panel({ label, head, children, foot, onClose, x = true, className }: PanelProps) {
  const sheet = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const el = sheet.current;
    el?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) close.current();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      const focus = document.activeElement;
      const lost = !focus || focus === document.body || el?.contains(focus);
      if (lost && opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  return (
    <aside ref={sheet} className={className ? `pane side pnl ${className}` : 'pane side pnl'} aria-label={label} tabIndex={-1}>
      <header className="side-head">
        {head}
        <span className="grow" />
        {x && <IconButton icon="close" label="Close" quiet onClick={() => onClose()} />}
      </header>
      <div className="rail-body">{children}</div>
      {foot && <footer className="pnl-foot">{foot}</footer>}
    </aside>
  );
}
