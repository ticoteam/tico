/* The mock's curves (tico/dot-states.js L25-29), unchanged. */

export const clamp = (x: number, a = 0, b = 1): number => Math.min(b, Math.max(a, x));

/** Ease in-out quad: `t<.5 ? 2t² : 1-(-2t+2)²/2`. */
export const eio = (t: number): number => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

/** Ease out cubic: `1-(1-t)³`. */
export const eout = (t: number): number => 1 - Math.pow(1 - t, 3);

/** The inverse of `eio`, for placing a step exactly where the eased value crosses a level. */
export const eioInv = (y: number): number => (y < 0.5 ? Math.sqrt(y / 2) : 1 - Math.sqrt((1 - y) * 2) / 2);

/** Rounds to three places, as the mock writes every number into a style. */
export const f = (n: number): number => Math.round(n * 1000) / 1000;

/** Stops `[[at, v1, v2..]]` to the values at k, eased from stop to stop with `eio` (DS.js L29). */
export const kf =
  (stops: ReadonlyArray<ReadonlyArray<number>>) =>
  (k: number): number[] => {
    let i = 1;
    while (i < stops.length - 1 && k > (stops[i]![0] as number)) i++;
    const a = stops[i - 1]!;
    const b = stops[i]!;
    const u = eio(clamp((k - a[0]!) / (b[0]! - a[0]! || 1)));
    return a.slice(1).map((v, j) => v + (b[j + 1]! - v) * u);
  };

/** Samples a function of k (0..1) into linear keyframes, which the browser plays exactly (motion.md 7). */
export function sample(fn: (k: number) => Keyframe, steps = 60): Keyframe[] {
  const out: Keyframe[] = [];
  for (let s = 0; s <= steps; s++) {
    const k = s / steps;
    out.push({ ...fn(k), offset: k });
  }
  return out;
}

/* The Dot's one-shots on its wrapper (DS.js L30-32, L130, L341). */
export const KICK = kf([[0, 1, 1], [0.35, 0.86, 0.92], [0.7, 1.1, 1.06], [1, 1, 1]]);
export const SHAKE = kf([[0, 0], [0.18, -8], [0.4, 7], [0.62, -4], [0.82, 2], [1, 0]]);
export const BUMP = kf([[0, 0, 1, 1], [0.3, -24, 0.94, 1.08], [0.62, 0, 1.1, 0.9], [0.82, -3, 0.98, 1.02], [1, 0, 1, 1]]);
export const pulseOf = (top: number) => kf([[0, 1], [0.4, top], [0.78, 0.985], [1, 1]]);
