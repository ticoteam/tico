import { useEffect, useState } from 'react';
import { motionLevel } from '@/motion/level';
import { FlowMark } from './parts';

/* "Getting ready to flow" (ui/app/decide.ts decideStart): the mark and a line, over the first task of a walk for well
   under a second. The task is drawn and takes input under it. Stepping to the next task shows nothing; none with motion
   off. */

const SHOW_MS = 600;
/** A walk's tasks follow one another closer than this; past it, opening a task in Flow starts a new walk. */
const WALK_GAP_MS = 1500;
let walkLeft = 0;

export function FlowCover() {
  const [on, setOn] = useState(() => motionLevel() !== 'off' && Date.now() - walkLeft > WALK_GAP_MS);
  useEffect(() => {
    if (!on) return;
    const t = window.setTimeout(() => setOn(false), SHOW_MS);
    return () => window.clearTimeout(t);
  }, [on]);
  // Leaving a task of the walk: the next one, opened at once, is the same walk.
  useEffect(
    () => () => {
      walkLeft = Date.now();
    },
    [],
  );
  if (!on) return null;
  return (
    <div className="tv-start" aria-hidden="true">
      <FlowMark size={56} />
      <p>Getting ready to flow</p>
    </div>
  );
}
