import type { CSSProperties } from 'react';

export type FlowMarkProps = {
  /** Size in px (`--d`), for the dot. */
  size?: number;
  className?: string;
  /** What the dot says to a screen reader: "Waits on you", or "Asks you" on a conversation that asks you. */
  label?: string;
};

/** The mark that something waits on you (parts-kit.js flow()). Its jade lives in styles/dot.css. The only such mark:
    features use this one. */
export function FlowMark({ size = 12, className, label = 'Waits on you' }: FlowMarkProps) {
  return (
    <span className={className ? `dot flow ${className}` : 'dot flow'} style={{ '--d': `${size}px` } as CSSProperties} role="img" aria-label={label}>
      <i />
      <b />
    </span>
  );
}
