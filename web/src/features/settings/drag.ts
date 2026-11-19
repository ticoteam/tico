import { useRef, type DragEvent, type MouseEvent, type PointerEvent } from 'react';

/** How far a press travels before it is a drag and not a click (as on the task board). */
const SLOP = 6;
/** A computer's group in the agents list grouped by computer; "No computer" (an empty id) takes no drops. */
const GROUP = 'section.pk-sec[data-target^="group:computer:"]';
/** Presses on the row's own controls stay clicks. */
const CONTROLS = 'button, input, label, [role="checkbox"], [role="menuitem"]';

interface Held {
  slug: string;
  x: number;
  y: number;
  el: HTMLElement;
  moving: boolean;
}

/**
 * Drag an agent's row onto another computer's group with a pointer (features/tasks/drag.ts, no library). The group
 * under the pointer is marked `data-drop`; letting go there calls `drop(slug, computer)`. Touch keeps its scroll: on a
 * phone the Move button moves agents.
 */
export function useRowDrag(drop: (slug: string, computer: string) => void) {
  const held = useRef<Held | null>(null);
  const over = useRef<Element | null>(null);
  const swallow = useRef(false);

  const mark = (el: Element | null) => {
    if (over.current === el) return;
    over.current?.removeAttribute('data-drop');
    over.current = el;
    el?.setAttribute('data-drop', '');
  };
  const reset = (h: Held) => {
    h.el.style.transform = '';
    h.el.classList.remove('is-drag');
    mark(null);
  };

  return (slug: string) => ({
    onPointerDown: (e: PointerEvent<HTMLElement>) => {
      if (e.button !== 0 || e.pointerType === 'touch' || (e.target as Element).closest(CONTROLS)) return;
      held.current = { slug, x: e.clientX, y: e.clientY, el: e.currentTarget, moving: false };
    },
    onPointerMove: (e: PointerEvent<HTMLElement>) => {
      const h = held.current;
      if (!h) return;
      const dx = e.clientX - h.x;
      const dy = e.clientY - h.y;
      if (!h.moving) {
        if (Math.hypot(dx, dy) < SLOP) return;
        h.moving = true;
        h.el.setPointerCapture?.(e.pointerId);
        h.el.classList.add('is-drag');
      }
      h.el.style.transform = `translate(${dx}px, ${dy}px)`;
      // Look under the row, not at it.
      h.el.style.visibility = 'hidden';
      const under = document.elementFromPoint(e.clientX, e.clientY);
      h.el.style.visibility = '';
      const group = under?.closest(GROUP) ?? null;
      mark(group && group.getAttribute('data-target') !== 'group:computer:' && !group.contains(h.el) ? group : null);
    },
    onPointerUp: () => {
      const h = held.current;
      held.current = null;
      if (!h?.moving) return;
      const computer = over.current?.getAttribute('data-target')?.slice('group:computer:'.length) ?? '';
      reset(h);
      // The click a drag ends with comes in this same turn; a later click (another row's checkbox) is a click.
      swallow.current = true;
      setTimeout(() => (swallow.current = false), 0);
      if (computer) drop(h.slug, computer);
    },
    onPointerCancel: () => {
      const h = held.current;
      held.current = null;
      if (h?.moving) reset(h);
    },
    // A drag ends with a click on the row's name: it must not open the agent.
    onClickCapture: (e: MouseEvent) => {
      if (!swallow.current) return;
      swallow.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
    onDragStart: (e: DragEvent) => e.preventDefault(),
  });
}
