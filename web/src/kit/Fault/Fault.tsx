import type { CSSProperties } from 'react';
import './Fault.css';

export type FaultProps = {
  /** What went wrong, read aloud and shown on hover ("Failed", "Signed out"). */
  label: string;
  /** 12 in a pill, 14 in a row, 16 beside a title. */
  size?: 12 | 14 | 16;
};

/** The grey fault: one solid ink triangle with the mark cut out of it (pages/14-acts). No color of its own. */
export function Fault({ label, size = 14 }: FaultProps) {
  return (
    <span className="fmark" style={{ '--f': `${size}px` } as CSSProperties} role="img" aria-label={label} title={label}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          className="fm-a"
          d="M12 2.8a2.3 2.3 0 012 1.15l8.1 14a2.3 2.3 0 01-2 3.45H3.9a2.3 2.3 0 01-2-3.45l8.1-14a2.3 2.3 0 012-1.15z"
        />
        <path className="fm-b" d="M12 9.2v5M12 17.6v.1" />
      </svg>
    </span>
  );
}
