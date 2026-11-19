import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { dot } from '@/tico/dot';
import { ticoPowerQuery, type TicoPower } from './queries';

/* Tico's sheet and its folded strip both read this; the Dot is held off once while Tico is off, however many places
   draw it, and let go when it is back. */
let release: (() => void) | null = null;

function holdOff(off: boolean): void {
  if (off && !release) release = dot.set('off');
  else if (!off && release) {
    release();
    release = null;
  }
}

/** Whether Tico is on (undefined until known), keeping the Dot's off look in step. */
export function useTicoPower(): TicoPower | undefined {
  const { data } = useQuery(ticoPowerQuery);
  const off = data?.available === false;
  useEffect(() => {
    holdOff(off);
  }, [off]);
  return data;
}

export { ticoPowerQuery, type TicoPower };
