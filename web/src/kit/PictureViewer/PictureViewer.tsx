import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Viewer } from '@/kit/Viewer';
import { actualScale, DRAG_PX, FIT, isFit, MAX_SCALE, pan, STEP, swipeStep, toggle, wheelPx, zoomTo, type Box, type Zoom } from './zoom';
import './PictureViewer.css';

export interface Picture {
  /** Only the app's own `/api/` addresses are drawn; anything else is left out. */
  src: string;
  /** Read aloud, and shown over the picture. */
  name: string;
  /** Where Download fetches the full file; `src` when left out. */
  download?: string;
}

export interface PictureViewerProps {
  pictures: Picture[];
  /** The one shown; null when the viewer is closed. */
  at: number | null;
  onAtChange: (at: number | null) => void;
  /** Pictures this page made itself (a diagram drawn in a chat) as `blob:` addresses of its own; only those, then. */
  local?: boolean;
}

const OWN = /^\/api\//i;
/** Two taps this close in time and place are a double tap. */
const DOUBLE_MS = 300;
const DOUBLE_PX = 30;

/** Only pictures this app serves: one from outside would call that server the moment it is drawn. With `local`, only
    the page's own `blob:` addresses, which load nothing from anywhere. */
export const ownPictures = (list: Picture[], local = false) =>
  list.filter((p) => (local ? p.src.startsWith(`blob:${location.origin}/`) : OWN.test(p.src)));

type Pt = { x: number; y: number };
type Gesture =
  | { kind: 'press' | 'drag'; type: string; onImg: boolean; swipes: boolean; start: Pt; last: Pt }
  | { kind: 'pinch'; d0: number; mid0: Pt; z0: Zoom };

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);
/** The picture at fit (its layout size, which a transform leaves alone) and the area it sits in. */
const boxOf = (area: HTMLElement | null, img: HTMLImageElement | null): Box | undefined =>
  area && img ? { w: img.offsetWidth, h: img.offsetHeight, areaW: area.clientWidth, areaH: area.clientHeight } : undefined;
/** A point on the screen, from the picture area's middle. */
function relTo(area: HTMLElement | null, clientX: number, clientY: number): Pt {
  const r = area?.getBoundingClientRect();
  return r ? { x: clientX - (r.left + r.width / 2), y: clientY - (r.top + r.height / 2) } : { x: 0, y: 0 };
}
const mid = (a: Pt, b: Pt) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

/**
 * A picture full size over the page (a chat's, a task's), in the full-screen layer (kit/Viewer) bare on its dim: the
 * arrow keys, buttons and a swipe walk the set it was opened from, Download saves the file, Escape or a click on the
 * dim closes. It zooms with a pinch or a double tap, the wheel, the bar's − and +, or a click (fit ⇄ actual size), and
 * a drag pans it; it opens each picture at fit.
 */
export function PictureViewer({ pictures, at, onAtChange, local = false }: PictureViewerProps) {
  const list = ownPictures(pictures, local);
  const i = at == null ? -1 : Math.min(at, list.length - 1);
  const p = i >= 0 ? list[i] : undefined;
  const many = list.length > 1;
  const shown = p ? `${i}\n${p.src}` : '';
  // the zoom belongs to the picture it was set on: another one, or the same one opened again, starts at fit
  const [held, setHeld] = useState<{ of: string; z: Zoom; moving: boolean }>({ of: shown, z: FIT, moving: false });
  if (held.of !== shown) setHeld({ of: shown, z: FIT, moving: false });
  const z = held.of === shown ? held.z : FIT;
  const moving = held.of === shown && held.moving;
  const setZ = (next: (was: Zoom) => Zoom) => setHeld((h) => (h.of === shown ? { ...h, z: next(h.z) } : h));
  const setMoving = (on: boolean) => setHeld((h) => (h.of === shown ? { ...h, moving: on } : h));
  const [area, setArea] = useState<HTMLDivElement | null>(null);
  const img = useRef<HTMLImageElement>(null);
  const points = useRef(new Map<number, Pt>());
  const gesture = useRef<Gesture | null>(null);
  const lastTap = useRef<{ t: number; at: Pt } | null>(null);
  useEffect(() => {
    points.current.clear();
    gesture.current = null;
    lastTap.current = null;
  }, [shown]);

  const box = (): Box | undefined => boxOf(area, img.current);
  const rel = (clientX: number, clientY: number) => relTo(area, clientX, clientY);
  const actual = () => actualScale(img.current?.naturalWidth ?? 0, img.current?.offsetWidth ?? 0);

  useEffect(() => {
    if (!area) return;
    const set = (next: (was: Zoom) => Zoom) => setHeld((h) => (h.of === shown ? { ...h, z: next(h.z) } : h));
    // non-passive, so the page itself neither scrolls nor zooms under the picture
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const pt = relTo(area, e.clientX, e.clientY);
      const k = Math.exp(-wheelPx(e.deltaY, e.deltaMode) * (e.ctrlKey ? 0.01 : 0.002));
      set((was) => zoomTo(was, was.scale * k, pt.x, pt.y, boxOf(area, img.current)));
    };
    // Safari's own pinch (a trackpad's): only one that began with no pointer down, as a touch pinch is the pointers'
    let base = -1;
    let own = false;
    const gStart = (e: Event) => {
      e.preventDefault();
      base = -1;
      own = points.current.size === 0;
    };
    const gChange = (e: Event) => {
      e.preventDefault();
      if (!own || points.current.size > 0) return;
      const g = e as Event & { scale: number; clientX: number; clientY: number };
      const pt = relTo(area, g.clientX, g.clientY);
      set((was) => {
        if (base < 0) base = was.scale;
        return zoomTo(was, base * g.scale, pt.x, pt.y, boxOf(area, img.current));
      });
    };
    const stop = (e: Event) => e.preventDefault();
    area.addEventListener('wheel', wheel, { passive: false });
    area.addEventListener('gesturestart', gStart, { passive: false });
    area.addEventListener('gesturechange', gChange, { passive: false });
    area.addEventListener('gestureend', stop, { passive: false });
    return () => {
      area.removeEventListener('wheel', wheel);
      area.removeEventListener('gesturestart', gStart);
      area.removeEventListener('gesturechange', gChange);
      area.removeEventListener('gestureend', stop);
    };
  }, [area, shown]);

  const step = (d: number) => {
    if (!many || i < 0) return;
    onAtChange((i + d + list.length) % list.length);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight') step(1);
    else if (e.key === 'ArrowLeft') step(-1);
    else return;
    e.preventDefault();
  };

  const down = (e: PointerEvent<HTMLDivElement>) => {
    // a right click, or a Control-click (the Mac's right click), is the browser's
    if (e.pointerType === 'mouse' && (e.button !== 0 || e.ctrlKey)) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    const pt = rel(e.clientX, e.clientY);
    points.current.set(e.pointerId, pt);
    const [a, b] = [...points.current.values()];
    if (a && b) gesture.current = { kind: 'pinch', d0: dist(a, b) || 1, mid0: mid(a, b), z0: z };
    else gesture.current = { kind: 'press', type: e.pointerType, onImg: e.target === img.current, swipes: true, start: pt, last: pt };
    setMoving(true);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (!g || !points.current.has(e.pointerId)) return;
    const pt = rel(e.clientX, e.clientY);
    points.current.set(e.pointerId, pt);
    if (g.kind === 'pinch') {
      const [a, b] = [...points.current.values()];
      if (!a || !b) return;
      const m = mid(a, b);
      setZ(() => pan(zoomTo(g.z0, (g.z0.scale * dist(a, b)) / g.d0, g.mid0.x, g.mid0.y), m.x - g.mid0.x, m.y - g.mid0.y, box()));
      return;
    }
    if (g.kind === 'press' && dist(pt, g.start) > DRAG_PX) g.kind = 'drag';
    if (g.kind === 'drag') {
      const dx = pt.x - g.last.x;
      const dy = pt.y - g.last.y;
      g.last = pt;
      if (!isFit(z)) setZ((was) => pan(was, dx, dy, box()));
    }
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (!points.current.delete(e.pointerId) || !g) return;
    const left = [...points.current.values()][0];
    if (g.kind === 'pinch') {
      // one finger still down after a pinch carries on as a drag, never a tap or a swipe
      gesture.current = left ? { kind: 'drag', type: 'touch', onImg: false, swipes: false, start: left, last: left } : null;
      if (!left) setMoving(false);
      return;
    }
    gesture.current = null;
    setMoving(false);
    if (e.type === 'pointercancel' || e.type === 'lostpointercapture') return;
    const pt = rel(e.clientX, e.clientY);
    if (g.kind === 'drag') {
      const d = g.swipes ? swipeStep(z, pt.x - g.start.x, pt.y - g.start.y) : 0;
      if (d) step(d);
      return;
    }
    if (g.type === 'mouse') {
      if (g.onImg) setZ((was) => toggle(was, actual(), pt.x, pt.y, box()));
      return;
    }
    const tap = lastTap.current;
    if (tap && e.timeStamp - tap.t < DOUBLE_MS && dist(tap.at, pt) < DOUBLE_PX) {
      lastTap.current = null;
      setZ((was) => toggle(was, actual(), pt.x, pt.y, box()));
    } else lastTap.current = { t: e.timeStamp, at: pt };
  };
  const by = (k: number) => setZ((was) => zoomTo(was, was.scale * k, 0, 0, box()));

  return (
    <Viewer
      open={!!p}
      onClose={() => onAtChange(null)}
      title={p?.name ?? 'Picture'}
      meta={many ? `${i + 1} of ${list.length}` : undefined}
      look="bare"
      onKeyDown={onKeyDown}
      actions={
        <>
          {/* a phone pinches and double taps instead: the bar keeps room for Close */}
          <IconButton icon="minus" className="pv-zoom" label="Zoom out" disabled={isFit(z)} onClick={() => by(1 / STEP)} />
          <IconButton icon="plus" className="pv-zoom" label="Zoom in" disabled={z.scale >= MAX_SCALE} onClick={() => by(STEP)} />
          {many && <IconButton icon="chevron" className="flip" label="Previous" onClick={() => step(-1)} />}
          {many && <IconButton icon="chevron" label="Next" onClick={() => step(1)} />}
          {/* A diagram the app drew is an SVG of its own: opened as a page it would run on the app's origin. */}
          {p && !local && (
            <a className="ib" href={p.download ?? p.src} download={p.name} aria-label="Download" title="Download">
              <Icon name="download" />
            </a>
          )}
        </>
      }
    >
      {p && (
        <div
          ref={setArea}
          className="pv-stage"
          data-zoomed={!isFit(z) || undefined}
          data-moving={moving || undefined}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
          onLostPointerCapture={up}
        >
          <img
            ref={img}
            className="pv-img"
            src={p.src}
            alt={p.name}
            draggable={false}
            style={isFit(z) ? undefined : { transform: `translate(${z.x}px, ${z.y}px) scale(${z.scale})` }}
          />
        </div>
      )}
    </Viewer>
  );
}
