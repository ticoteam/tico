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
}

/**
 * Drag an element onto a zone (Pragmatic drag and drop, the browser's own drag). Returns a ref for each element that
 * drags; while it drags it has `is-drag`. Touch keeps its scroll and its long press: a phone moves things from a menu.
 */
export function useDrag(options: DragOptions) {
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  });

  return useCallback((el: HTMLElement | null) => {
    if (!el) return;
    let press = { touch: false, control: false };
    const down = (e: PointerEvent) => {
      const { controls } = latest.current;
      press = { touch: e.pointerType === 'touch', control: !!controls && !!(e.target as Element).closest(controls) };
    };
    el.addEventListener('pointerdown', down);

    let over: Element | null = null;
    const mark = (zone: Element | null) => {
      if (over === zone) return;
      over?.removeAttribute('data-drop');
      over = zone;
      zone?.setAttribute('data-drop', '');
    };
    // The zones take drops only while this element drags.
    let zones = () => {};

    const off = draggable({
      element: el,
      canDrag: () => !press.touch && !press.control,
      onGenerateDragPreview: () => {
        // before the browser draws the preview, so it draws it lifted
        el.classList.add('is-drag');
        const { zones: selector, takes } = latest.current;
        zones = combine(
          ...[...document.querySelectorAll(selector)].map((zone) =>
            dropTargetForElements({ element: zone, canDrop: () => takes?.(zone, el) ?? true }),
          ),
        );
      },
      onDropTargetChange: ({ location }) => mark(location.current.dropTargets[0]?.element ?? null),
      onDrop: ({ location }) => {
        el.classList.remove('is-drag');
        mark(null);
        zones();
        const zone = location.current.dropTargets[0]?.element;
        if (zone) latest.current.drop(zone, el);
      },
    });
    return () => {
      off();
      zones();
      mark(null);
      el.removeEventListener('pointerdown', down);
    };
  }, []);
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
