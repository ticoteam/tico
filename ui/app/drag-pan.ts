/* ui/app/drag-pan.ts — a mouse drags a scrolling pane about (a finger pans it natively). Strategy › Org's chart and
   Rules › Internal's map use it. A drag starts after 5px; while it runs the pane has .dragging, and the click that ends
   it is swallowed, so a drag is never a tap on the card it started on. A still click goes through.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface DragPanOpts {
  /** Called on each move once the drag has started (the Org chart marks itself panned by hand). */
  onDrag?: () => void;
}

function dragPan(pane: HTMLElement, {onDrag}: DragPanOpts = {}): void {
  let drag: {x: number; y: number; left: number; top: number; moved: boolean} | null = null;
  pane.addEventListener('pointerdown', ev => {
    if (ev.pointerType !== 'mouse' || ev.button !== 0) return;
    drag = {x: ev.clientX, y: ev.clientY, left: pane.scrollLeft, top: pane.scrollTop, moved: false};
  });
  pane.addEventListener('pointermove', ev => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    drag.moved = true; onDrag?.(); pane.classList.add('dragging');
    pane.scrollLeft = drag.left - dx; pane.scrollTop = drag.top - dy;
  });
  const end = () => { if (drag?.moved) { pane.classList.remove('dragging'); pane.dataset.dragged = '1'; setTimeout(() => delete pane.dataset.dragged, 0); } drag = null; };
  pane.addEventListener('pointerup', end);
  pane.addEventListener('pointerleave', end);
  pane.addEventListener('click', ev => { if (pane.dataset.dragged) { ev.stopPropagation(); ev.preventDefault(); } }, true);
}
