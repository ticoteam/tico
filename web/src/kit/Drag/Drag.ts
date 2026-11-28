import { draggable, dropTargetForElements } from '@atlaskit/pragmatic-drag-and-drop/adapter/element-adapter';
import { combine } from '@atlaskit/pragmatic-drag-and-drop/utils/combine';
import { useCallback, useLayoutEffect, useRef } from 'react';

export interface DragOptions {
  /** Where a drag may land: the elements matching it when a drag starts. The innermost one under the pointer is marked
      `data-drop`. */
  zones: string;
  /** Presses on these (the row's own controls) stay clicks. */
  controls?: string;
  /** Whether `zone` takes `dragged`; one that does not is passed over for the zone around it. */
  takes?: (zone: Element, dragged: HTMLElement) => boolean;
  /** Let go over `zone`. */
  drop: (zone: Element, dragged: HTMLElement) => void;
  /** A drag starts, before its zones are read: zones drawn now (synchronously) take part. */
  start?: (dragged: HTMLElement) => void;
  /** The zone under the pointer changed (null: none). */
  over?: (zone: Element | null) => void;
  /** Where the pointer is while it drags, in the window's coordinates. */
  move?: (x: number, y: number) => void;
  /** The drag ended, dropped or not. */
  end?: () => void;
  /** Touch drags too: a finger held still this long (ms), then moved, lifts the element. Without it a finger only
      scrolls, and its long press is the row's menu. */
  hold?: number;
}

/** How far a finger may wander and still be holding still, and how far it moves before a held press drags. The menu's
    long press (Base UI) gives up past the same 10px. */
const STILL = 10;
/** How long after a finger lifts its click may still arrive. */
const CLICK_AFTER_MS = 400;

/**
 * Drag an element onto a zone. A mouse drags with the browser's own drag (Pragmatic drag and drop); a finger, which the
 * browser's drag does not serve, drags with pointer events after a still hold. Returns a ref for each element that
 * drags; while it drags it has `is-drag`.
 */
export function useDrag(options: DragOptions) {
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  });

  return useCallback((el: HTMLElement | null) => {
    if (!el) return;
    let over: Element | null = null;
    /** A drag has begun and not yet finished. */
    let active = false;
    const mark = (zone: Element | null) => {
      if (over === zone) return;
      over?.removeAttribute('data-drop');
      over = zone;
      zone?.setAttribute('data-drop', '');
      latest.current.over?.(zone);
    };
    const begin = () => {
      active = true;
      latest.current.start?.(el);
      el.classList.add('is-drag');
    };
    const finish = (zone: Element | null) => {
      active = false;
      el.classList.remove('is-drag');
      mark(null);
      latest.current.end?.();
      if (zone) latest.current.drop(zone, el);
    };

    let press = { touch: false, control: false };
    const down = (e: PointerEvent) => {
      const { controls, hold } = latest.current;
      press = { touch: e.pointerType === 'touch', control: !!controls && !!(e.target as Element).closest(controls) };
      if (press.touch && !press.control && hold && e.isPrimary) touchDrag(e, hold);
    };
    el.addEventListener('pointerdown', down);

    /** The zone a finger at (x, y) is over: the innermost one that takes this element. */
    const zoneAt = (x: number, y: number) => {
      const { zones, takes } = latest.current;
      let at = document.elementFromPoint(x, y)?.closest(zones) ?? null;
      while (at && takes && !takes(at, el)) at = at.parentElement?.closest(zones) ?? null;
      return at;
    };

    let stopTouch = () => {};
    const touchDrag = (first: PointerEvent, hold: number) => {
      stopTouch();
      const done = new AbortController();
      const on = <K extends keyof DocumentEventMap>(type: K, fn: (e: DocumentEventMap[K]) => void, passive = true) =>
        document.addEventListener(type, fn, { signal: done.signal, passive, capture: true });
      // Android's Chrome starts the browser's own drag on a long press of a draggable element, which cancels the
      // finger's pointer: for the length of a touch press the element is not one
      el.draggable = false;
      let state: 'waiting' | 'held' | 'dragging' = 'waiting';
      let lifted: HTMLElement | null = null;
      let last = { x: first.clientX, y: first.clientY };
      const timer = setTimeout(() => (state = 'held'), hold);
      const stop = (zone: Element | null) => {
        clearTimeout(timer);
        done.abort();
        el.draggable = true;
        stopTouch = () => {};
        if (state !== 'dragging') return;
        lifted?.remove();
        // the browser may still turn the lifted finger into a click: a drag never opens the task. Only that click: one
        // that follows a new press (a tap on another card) is the person's own
        const quit = new AbortController();
        const off = { capture: true, signal: quit.signal };
        document.addEventListener('pointerdown', () => quit.abort(), off);
        document.addEventListener(
          'click',
          (e) => {
            e.preventDefault();
            quit.abort();
          },
          off,
        );
        setTimeout(() => quit.abort(), CLICK_AFTER_MS);
        finish(zone);
      };
      stopTouch = () => stop(null);

      on('pointermove', (e) => {
        if (e.pointerId !== first.pointerId) return;
        const far = Math.abs(e.clientX - first.clientX) > STILL || Math.abs(e.clientY - first.clientY) > STILL;
        if (state === 'waiting') {
          if (far) stop(null); // a swipe: the page scrolls
          return;
        }
        if (state === 'held') {
          if (!far) return;
          state = 'dragging';
          begin();
          lifted = liftOf(el, first.clientY);
        }
        last = { x: e.clientX, y: e.clientY };
        if (lifted) lifted.style.translate = `${e.clientX - first.clientX}px ${e.clientY - first.clientY}px`;
        mark(zoneAt(e.clientX, e.clientY));
        latest.current.move?.(e.clientX, e.clientY);
      });
      // the board slid under a still finger: what it is over now is another zone
      on('scroll', () => state === 'dragging' && mark(zoneAt(last.x, last.y)));
      // once held, the finger drags rather than scrolls
      on(
        'touchmove',
        (e) => {
          if (state !== 'waiting' && e.cancelable) e.preventDefault();
        },
        false,
      );
      on('pointerup', (e) => e.pointerId === first.pointerId && stop(state === 'dragging' ? zoneAt(e.clientX, e.clientY) : null));
      on('pointercancel', (e) => e.pointerId === first.pointerId && stop(null));
    };

    // The zones take drops only while this element drags.
    let zones = () => {};

    const off = draggable({
      element: el,
      canDrag: () => !press.touch && !press.control,
      onGenerateDragPreview: () => {
        // before the browser draws the preview, so it draws it lifted
        begin();
        const { zones: selector, takes } = latest.current;
        zones = combine(
          ...[...document.querySelectorAll(selector)].map((zone) =>
            dropTargetForElements({ element: zone, canDrop: () => takes?.(zone, el) ?? true }),
          ),
        );
      },
      onDropTargetChange: ({ location }) => mark(location.current.dropTargets[0]?.element ?? null),
      onDrag: ({ location }) => latest.current.move?.(location.current.input.clientX, location.current.input.clientY),
      onDrop: ({ location }) => {
        zones();
        finish(location.current.dropTargets[0]?.element ?? null);
      },
    });
    return () => {
      off();
      zones();
      stopTouch();
      // the element went (its card left the board) mid-drag: the drag ends, so its strip and edge slide go too
      if (active) finish(null);
      else mark(null);
      el.removeEventListener('pointerdown', down);
    };
  }, []);
}

/** How far above the finger the lifted copy rides, so the finger never hides the stage it is over. */
const ABOVE_PX = 16;

/** The copy that rides with a finger: drawn over everything, its foot just above the finger (`y`, where it pressed),
    and never what the finger is over. */
function liftOf(el: HTMLElement, y: number): HTMLElement {
  const box = el.getBoundingClientRect();
  const copy = el.cloneNode(true) as HTMLElement;
  copy.classList.remove('is-drag');
  copy.classList.add('drag-lift');
  copy.removeAttribute('data-task');
  copy.setAttribute('aria-hidden', 'true');
  Object.assign(copy.style, { position: 'fixed', left: `${box.left}px`, top: `${Math.min(box.top, y - box.height - ABOVE_PX)}px`, width: `${box.width}px`, margin: '0', pointerEvents: 'none' });
  document.body.append(copy);
  return copy;
}

let region: HTMLElement | null = null;

/** Say what a drop did to a screen reader, in a polite live region off screen. */
export function announce(text: string) {
  if (!region?.isConnected) {
    region = document.createElement('div');
    region.setAttribute('role', 'status');
    Object.assign(region.style, { position: 'absolute', width: '1px', height: '1px', overflow: 'hidden', clipPath: 'inset(50%)', whiteSpace: 'nowrap' });
    document.body.append(region);
  }
  region.textContent = text;
}
