/* Gesture math for the phone cards (motion.md section 6). Pure: positions are a card's translateY in px,
   stops are ordered top first (full, half, lip), velocity is px/ms with up negative. */

export const GESTURE = {
  own: 6, // px of vertical travel before a card owns the gesture
  sideCancel: 10, // px of horizontal travel that hands the gesture back (a board swipe, the back swipe)
  drag: 28, // the mock's flick threshold: a drag this long lands on the nearest stop
  flick: 0.5, // px/ms: one stop in the direction of travel
  fling: 1.8, // px/ms: up from the lip goes straight to the top stop
  window: 100, // ms of samples that make up the release speed
  band: 0.55, // rubber band stiffness past an end stop
} as const;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Which stop wins on release. `stops` are positions top first; `start` is the index the drag began at. */
export function pickStop({ stops, start, ty, dy, v }: { stops: readonly number[]; start: number; ty: number; dy: number; v: number }): number {
  const last = stops.length - 1;
  if (Math.abs(v) > GESTURE.flick) {
    if (v < 0 && -v > GESTURE.fling && start === last) return 0;
    return clamp(start + (v < 0 ? -1 : 1), 0, last);
  }
  if (Math.abs(dy) > GESTURE.drag) {
    let best = 0;
    stops.forEach((s, i) => {
      if (Math.abs(s - ty) < Math.abs((stops[best] ?? 0) - ty)) best = i;
    });
    return best;
  }
  return start;
}

/** A position past an end stop is shown pulled back: `(1 - 1 / (over * .55 / size + 1)) * size`. */
export function rubber(ty: number, top: number, bottom: number, size: number): number {
  const band = (over: number) => (1 - 1 / ((over * GESTURE.band) / size + 1)) * size;
  if (ty < top) return top - band(top - ty);
  if (ty > bottom) return bottom + band(ty - bottom);
  return ty;
}

export type Sample = { t: number; y: number };

/** Release speed over the last 100ms of samples, px/ms. */
export function velocity(samples: readonly Sample[]): number {
  const end = samples[samples.length - 1];
  if (!end) return 0;
  const first = samples.find((s) => end.t - s.t <= GESTURE.window) ?? end;
  const dt = end.t - first.t;
  return dt < 1 ? 0 : (end.y - first.y) / dt;
}

/** 0..1 for how far a card has come up from `from` toward `to` (used for the page's set-back and the crossfade). */
export const progress = (ty: number, from: number, to: number, lead = 0, span = from - to) =>
  span <= 0 ? 0 : clamp((from - ty - lead) / span, 0, 1);
