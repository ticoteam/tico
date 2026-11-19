/* Tico's Dot (tico/dot.html). The markup is the mock's (dot-states.js L48-53): the Flow mark's shape under the Dot, the
   Dot, five tiny droplets for work in progress and the message droplet. What it does comes from a controller:
   Tico's own `dot` by default, one given in `of`, or its own when `state` is given. */
import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { flowPaths } from './shapes';
import { createDot, dot, type DotController, type DotShape } from './store';

export type DotProps = {
  /** Size in px (`--d`). */
  size?: number;
  /** Draw this shape rather than Tico's: for a Dot that only shows a state. */
  state?: DotShape;
  /** With `state`: how many things are in progress (droplets, five at most). */
  work?: number;
  /** With `state`: a message droplet on the edge. */
  message?: boolean;
  /** Drive it from this controller rather than Tico's. */
  of?: DotController;
};

/** The droplets' start angles (DS.js L50). */
const START = [40, 200, 130, 290, 245];
const [ROUND_BACK, ROUND_FRONT] = flowPaths(0);

export function Dot({ size = 30, state, work, message, of }: DotProps) {
  const [own] = useState(createDot);
  const local = state !== undefined || work !== undefined || message !== undefined;
  const ctl = of ?? (local ? own : dot);
  const ref = useRef<HTMLSpanElement>(null);
  const gid = `fg${useId().replace(/[^\w-]/g, '')}`;

  useEffect(() => (ref.current ? ctl.attach(ref.current) : undefined), [ctl]);
  useEffect(() => {
    if (ctl === own && local) own.show(state ?? 'rest', work ?? 0, !!message);
  }, [ctl, own, local, state, work, message]);

  return (
    <span ref={ref} className="dw" style={{ '--d': `${size}px` } as CSSProperties} aria-hidden="true">
      <svg className="fshape" viewBox="0 0 48 48" aria-hidden="true">
        <defs>
          <radialGradient id={gid} cx=".3" cy=".2" r="1.2">
            <stop offset="0" style={{ stopColor: 'var(--dot-hi)' }} />
            <stop offset=".52" style={{ stopColor: 'var(--dot)' }} />
            <stop offset="1" style={{ stopColor: 'var(--dot-lo)' }} />
          </radialGradient>
        </defs>
        <path className="fb" d={ROUND_BACK} />
        <path className="ff" fill={`url(#${gid})`} d={ROUND_FRONT} />
      </svg>
      <span className="dot">
        <i />
        <b />
      </span>
      <span className="motes">
        {START.map((a) => (
          <em key={a} style={{ '--a0': `${a}deg` } as CSSProperties} />
        ))}
      </span>
      <span className="bud" />
    </span>
  );
}
