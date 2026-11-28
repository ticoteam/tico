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
  /** Drawn still, with no controller: Tico's face in an avatar. */
  still?: boolean;
};

/** The one sprite (tico/look.ts): a pixel face, two eyes and a smile on a 10 by 10 grid, shown under
    `data-sprite="pixel"` (styles/dot.css). */
const PIXEL_FACE = 'M3 3h1v2H3zM6 3h1v2H6zM2 5h1v1H2zM7 5h1v1H7zM3 6h4v1H3z';

/** The droplets' start angles (DS.js L50). */
const START = [40, 200, 130, 290, 245];
const [ROUND_BACK, ROUND_FRONT] = flowPaths(0);

export function Dot({ size = 30, state, work, message, of, still }: DotProps) {
  const [own] = useState(createDot);
  const local = state !== undefined || work !== undefined || message !== undefined;
  const ctl = of ?? (local ? own : dot);
  const ref = useRef<HTMLSpanElement>(null);
  const gid = `fg${useId().replace(/[^\w-]/g, '')}`;

  useEffect(() => (ref.current && !still ? ctl.attach(ref.current) : undefined), [ctl, still]);
  useEffect(() => {
    if (ctl === own && local && !still) own.show(state ?? 'rest', work ?? 0, !!message);
  }, [ctl, own, local, state, work, message, still]);

  return (
    <span ref={ref} className={still ? 'dw is-still' : 'dw'} style={{ '--d': `${size}px` } as CSSProperties} aria-hidden="true">
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
        <svg className="sprite" viewBox="0 0 10 10" shapeRendering="crispEdges" aria-hidden="true">
          <path d={PIXEL_FACE} />
        </svg>
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
