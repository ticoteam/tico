import type { CSSProperties } from 'react';

export type FlowMarkProps = {
  /** Size in px (`--d`), for the dot. The play mark is 20px, 22px in the Flow button (styles/dot.css). */
  size?: number;
  className?: string;
  /** The Flow button's mark (parts-kit.js playDot): two play-shaped dots, the one behind a darker shade. */
  play?: boolean;
  /** The play mark rides while Flow is on. */
  live?: boolean;
  /** What the dot says to a screen reader: "Waits on you", or "Asks you" on a conversation that asks you. */
  label?: string;
};

const FLOW_PLAY = 'M16 5.5C8 4 4.5 12 5 24 4.5 36 8 44 16 42.5 27 40 45 32 45 24 45 16 27 8 16 5.5Z';

/** Flow's mark: something waits on you (parts-kit.js flow()), or with `play` the Flow button's own. Its jade lives in
    styles/dot.css. The only Flow mark: features use this one. */
export function FlowMark({ size = 12, className, play, live, label = 'Waits on you' }: FlowMarkProps) {
  if (play)
    return (
      <span className={`flow-mark${live ? ' live' : ''}${className ? ` ${className}` : ''}`} aria-hidden="true">
        <svg viewBox="0 0 48 48" focusable="false">
          <g className="f1">
            <path d={FLOW_PLAY} transform="translate(-.5 7.2) scale(.7)" />
          </g>
          <g className="f2">
            <path d={FLOW_PLAY} transform="translate(10.5 7.2) scale(.7)" />
          </g>
        </svg>
      </span>
    );
  return (
    <span className={className ? `dot flow ${className}` : 'dot flow'} style={{ '--d': `${size}px` } as CSSProperties} role="img" aria-label={label}>
      <i />
      <b />
    </span>
  );
}
