import type { CSSProperties } from 'react';

export type FlowMarkProps = {
  /** Size in px (`--d`). */
  size?: number;
  className?: string;
};

/** Flow's mark: something waits on you (parts-kit.js flow()). Its jade lives in styles/dot.css. */
export function FlowMark({ size = 12, className }: FlowMarkProps) {
  return (
    <span className={className ? `dot flow ${className}` : 'dot flow'} style={{ '--d': `${size}px` } as CSSProperties} role="img" aria-label="Waits on you">
      <i />
      <b />
    </span>
  );
}
