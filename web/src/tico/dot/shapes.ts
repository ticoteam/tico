/* The Dot's shapes (tico/dot-states.js L34-45), unchanged: the three blob radii, the Flow mark's outline written as
   13 points, the same circle written the same way so one can become the other, and the stand-in voice. */
import { clamp, f } from '@/motion';

type P = [number, number];

/** --r-blob, --r-blob-2, --r-blob-3 as eight numbers each (percent). */
export const BLOB = [
  [43, 57, 55, 45, 38, 44, 56, 62],
  [56, 44, 42, 58, 52, 58, 42, 48],
  [47, 53, 60, 40, 44, 40, 60, 56],
] as const;

/** The Flow mark's own outline: one start and four curves (parts-kit.js playDot). */
const FLOW_PTS: P[] = [[16, 5.5], [8, 4], [4.5, 12], [5, 24], [4.5, 36], [8, 44], [16, 42.5], [27, 40], [45, 32], [45, 24], [45, 16], [27, 8], [16, 5.5]];
const flowAt = (dx: number): P[] => FLOW_PTS.map((q) => [q[0] * 0.7 + dx, q[1] * 0.7 + 7.2]);
const F_BACK = flowAt(-0.5);
const F_FRONT = flowAt(10.5);

/** A circle, centre 24,24, radius 19.2, as four arcs from -120deg, so its 13 points line up with the mark's. */
const ROUND: P[] = (() => {
  const r = 19.2;
  const c = 24;
  const A = [-120, -180, -240, -360, -480].map((a) => (a * Math.PI) / 180);
  const at = (a: number): P => [c + r * Math.cos(a), c + r * Math.sin(a)];
  const out: P[] = [at(A[0]!)];
  for (let i = 0; i < 4; i++) {
    const a0 = A[i]!;
    const a1 = A[i + 1]!;
    const k = (4 / 3) * Math.tan((a1 - a0) / 4) * r;
    const p0 = at(a0);
    const p1 = at(a1);
    out.push([p0[0] - k * Math.sin(a0), p0[1] + k * Math.cos(a0)], [p1[0] + k * Math.sin(a1), p1[1] - k * Math.cos(a1)], p1);
  }
  return out;
})();

const shapeD = (to: P[], e: number): string => {
  const v = ROUND.map((a, i) => `${f(a[0] + (to[i]![0] - a[0]) * e)} ${f(a[1] + (to[i]![1] - a[1]) * e)}`);
  return `M${v[0]}C${v.slice(1, 4).join(' ')} ${v.slice(4, 7).join(' ')} ${v.slice(7, 10).join(' ')} ${v.slice(10, 13).join(' ')}Z`;
};

/** The back and front paths at `e` (0 round, 1 the Flow mark). */
export const flowPaths = (e: number): [string, string] => [shapeD(F_BACK, e), shapeD(F_FRONT, e)];

/** The eight radius parts while a voice has the floor (DS.js L141-142): mostly the size, a little the shape. */
export function voiceRadius(s: number, m: number): string {
  const p = 0.5 + 0.5 * Math.sin(s * 1.9);
  const br = BLOB[0].map((b, i) => `${f(b + m * ((BLOB[1][i]! - b) * p + (BLOB[2][i]! - b) * (1 - p)))}%`);
  return `${br.slice(0, 4).join(' ')}/${br.slice(4).join(' ')}`;
}

/** The mock's voice with no sound: syllables inside phrases, with gaps (DS.js L34). For the gallery and tests. */
export const voice = (t: number): number => {
  const s = t / 1000;
  const syl = 0.5 + 0.5 * Math.sin(s * 13.1 + Math.sin(s * 3.3) * 1.7);
  const phr = clamp(0.62 + 0.5 * Math.sin(s * 2.1 + 1.2) + 0.25 * Math.sin(s * 0.83));
  const gate = clamp((Math.sin(s * 1.37 - 0.4) + 0.82) * 3);
  return clamp(syl * 0.75 * phr + phr * 0.25) * gate;
};

/** What the Dot follows: the same level, averaged over the last 180ms, so it never jitters (DS.js L35). */
export const smooth =
  (sig: (t: number) => number) =>
  (t: number): number =>
    (sig(t) + sig(t - 45) + sig(t - 90) + sig(t - 135) + sig(t - 180)) / 5;
