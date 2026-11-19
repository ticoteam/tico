import { useRef, type DragEvent, type MouseEvent, type PointerEvent } from 'react';

/** How far a press travels before it is a drag and not a click. */
const SLOP = 6;

interface Held {
  id: string;
  from: string | null;
  x: number;
  y: number;
  el: HTMLElement;
  moving: boolean;
}

/**
 * Drag a card onto another column with a pointer (no library). The column under the pointer is marked `data-drop`;
 * letting go there calls `drop(task, stage)`. Touch keeps its scroll: on a phone the long press menu moves a task.
 */
export function useCardDrag(drop: (task: string, stage: string) => void) {
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

  return (task: { id: string; stage_id: string | null }) => ({
    onPointerDown: (e: PointerEvent<HTMLElement>) => {
      if (e.button !== 0 || e.pointerType === 'touch' || (e.target as Element).closest('.tk-more')) return;
      held.current = { id: task.id, from: task.stage_id, x: e.clientX, y: e.clientY, el: e.currentTarget, moving: false };
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
      // Look under the card, not at it.
      h.el.style.visibility = 'hidden';
      const under = document.elementFromPoint(e.clientX, e.clientY);
      h.el.style.visibility = '';
      mark(under?.closest('.col[data-target^="stage:"]') ?? null);
    },
    onPointerUp: () => {
      const h = held.current;
      held.current = null;
      if (!h?.moving) return;
      const stage = over.current?.getAttribute('data-target')?.slice('stage:'.length) ?? null;
      reset(h);
      swallow.current = true;
      if (stage && stage !== h.from) drop(h.id, stage);
    },
    onPointerCancel: () => {
      const h = held.current;
      held.current = null;
      if (h?.moving) reset(h);
    },
    // A drag ends with a click on the card's link: it must not open the task.
    onClickCapture: (e: MouseEvent) => {
      if (!swallow.current) return;
      swallow.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
    onDragStart: (e: DragEvent) => e.preventDefault(),
  });
}
