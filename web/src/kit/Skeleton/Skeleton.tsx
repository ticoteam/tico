import { useEffect, useState, type CSSProperties } from 'react';
import './Skeleton.css';

/** False until `ms` have passed: a read faster than that never shows a skeleton (architecture.md 8). */
export function useDelayed(ms = 150): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setShown(true), ms);
    return () => clearTimeout(t);
  }, [ms]);
  return shown;
}

export type SkeletonProps = {
  /** A percentage of the cell it sits in. */
  width?: number;
  className?: string;
};

/** One breathing grey bar (parts.css `.sk`). No spinner, ever. */
export function Skeleton({ width, className }: SkeletonProps) {
  return (
    <span
      className={className ? `sk ${className}` : 'sk'}
      style={width === undefined ? undefined : ({ '--w': `${width}%` } as CSSProperties)}
    />
  );
}

// Fixed, uneven widths so the bars read as titles, never as a pattern, and never change between renders.
const WIDTHS = [62, 44, 71, 53, 38, 66, 48, 57];

export type SkeletonRowsProps = {
  rows?: number;
  /** Read aloud while it waits. */
  label?: string;
};

/** Task-shaped rows while a list loads, after 150 ms (parts-kit.js skRow). The page head keeps its title. */
export function SkeletonRows({ rows = 6, label = 'Loading' }: SkeletonRowsProps) {
  const shown = useDelayed(150);
  if (!shown) return null;
  return (
    <div role="status" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="row task is-skeleton" aria-hidden="true">
          <span className="row-mark" />
          <Skeleton width={WIDTHS[i % WIDTHS.length]} />
          <span />
          <Skeleton className="sk-av" />
          <Skeleton className="sk-age" />
        </div>
      ))}
    </div>
  );
}
