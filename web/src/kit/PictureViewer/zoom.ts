/* How a picture in the viewer zooms and pans, apart from the events that drive it: a scale over the picture's fit
   size (1 is fit) and an offset from the middle, in screen pixels. */

export interface Zoom {
  scale: number;
  x: number;
  y: number;
}

/** The picture as drawn at fit (its layout size, before any zoom) and the area it sits in. */
export interface Box {
  w: number;
  h: number;
  areaW: number;
  areaH: number;
}

export const FIT: Zoom = { scale: 1, x: 0, y: 0 };
export const MAX_SCALE = 8;
/** A press moves this far before it counts as a drag, so a shaky tap or click is still a tap. */
export const DRAG_PX = 5;
/** A swipe moves at least this far, and more across than down. */
export const SWIPE_PX = 48;
export const STEP = 1.5;

export const isFit = (z: Zoom) => z.scale <= 1.001;

/** Swiping walks to the next picture only at fit: zoomed in, the same gesture pans. */
export const canSwipe = (z: Zoom) => isFit(z);

/** Which way a finished drag at fit walks the set: 1 next, -1 previous, 0 not a swipe. */
export function swipeStep(z: Zoom, dx: number, dy: number): -1 | 0 | 1 {
  if (!canSwipe(z) || Math.abs(dx) < SWIPE_PX || Math.abs(dx) <= Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}

/** Kept so the picture's edge never comes inside the area while it is larger than it, and centred where it is smaller. */
export function clamp(z: Zoom, box?: Box): Zoom {
  const scale = Math.min(MAX_SCALE, Math.max(1, z.scale));
  if (scale <= 1.001) return FIT;
  if (!box) return { scale, x: z.x, y: z.y };
  const mx = Math.max(0, (box.w * scale - box.areaW) / 2);
  const my = Math.max(0, (box.h * scale - box.areaH) / 2);
  return { scale, x: Math.min(mx, Math.max(-mx, z.x)), y: Math.min(my, Math.max(-my, z.y)) };
}

/** To `scale`, keeping the point under (`px`, `py`), measured from the area's middle, where it is. */
export function zoomTo(z: Zoom, scale: number, px = 0, py = 0, box?: Box): Zoom {
  const next = Math.min(MAX_SCALE, Math.max(1, scale));
  const k = next / z.scale;
  return clamp({ scale: next, x: px - (px - z.x) * k, y: py - (py - z.y) * k }, box);
}

export const pan = (z: Zoom, dx: number, dy: number, box?: Box): Zoom => (isFit(z) ? z : clamp({ ...z, x: z.x + dx, y: z.y + dy }, box));

/** The scale that shows the picture pixel for pixel; twice the fit when the picture is no larger than its fit. */
export function actualScale(naturalW: number, fitW: number): number {
  const s = fitW > 0 ? naturalW / fitW : 0;
  return s > 1.05 ? Math.min(MAX_SCALE, s) : 2;
}

/** A click (or a double tap) goes from fit to actual size at that point, and from any zoom back to fit. */
export const toggle = (z: Zoom, actual: number, px = 0, py = 0, box?: Box): Zoom => (isFit(z) ? zoomTo(z, actual, px, py, box) : FIT);
