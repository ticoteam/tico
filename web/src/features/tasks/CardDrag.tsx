import { useRef, useState } from 'react';
import { createPortal, flushSync } from 'react-dom';
import { useAction } from '@/actions';
import { announce, useDrag } from '@/kit/Drag';
import { toast } from '@/kit/Toast';
import type { Stage } from './queries';

/** Where a board card lands: a stage's column, or its chip in the strip a drag brings up. */
const ZONES = '.col[data-target^="stage:"], .stage-strip [data-stage]';
/** A finger held this still this long, then moved, drags a card; let go unmoved, it opens Move to (Base UI's long
    press is the same half second). */
const HOLD_MS = 500;
/** A drag this close to the board's left or right edge slides it a column, once a second while it stays. */
const EDGE_PX = 32;
const SLIDE_MS = 1000;
/** How long a drag's "Moved to" toast keeps its Undo. */
const UNDO_MS = 8000;

const stageOf = (zone: Element | null) => zone?.getAttribute('data-stage') ?? zone?.getAttribute('data-target')?.slice('stage:'.length) ?? null;

export interface CardDrag {
  /** The ref for each card's wrapper; the wrapper carries `data-task` and `data-stage`. */
  ref: (el: HTMLElement | null) => void;
  /** The stage under the dragged card (its column or its chip), never the card's own. */
  over: string | null;
  /** The strip of every stage along the bottom, while a card drags. */
  strip: React.ReactNode;
}

/**
 * Drag a board card to another stage, with a mouse or (after a still hold) a finger: a strip of every stage comes up
 * along the bottom, the board slides at its edges, and a drop moves the task with a toast that takes it back.
 */
export function useCardDrag(stages: readonly Stage[]): CardDrag {
  const move = useAction('tasks.move');
  const [from, setFrom] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const slide = useRef<EdgeSlide | null>(null);
  const nameOf = (id: string | null) => stages.find((s) => s.id === id)?.name ?? '';

  const ref = useDrag({
    zones: ZONES,
    controls: '.tk-more',
    hold: HOLD_MS,
    takes: (zone, card) => stageOf(zone) !== card.getAttribute('data-stage'),
    // the strip is drawn now, so its chips are zones of this drag
    start: (card) => {
      flushSync(() => setFrom(card.getAttribute('data-stage') ?? ''));
      slide.current = edgeSlide(card.closest<HTMLElement>('.board'));
    },
    over: (zone) => setOver(stageOf(zone)),
    move: (x) => slide.current?.at(x),
    end: () => {
      slide.current?.stop();
      slide.current = null;
      setFrom(null);
      setOver(null);
    },
    drop: (zone, card) => {
      const task = card.getAttribute('data-task');
      const back = card.getAttribute('data-stage');
      const to = stageOf(zone);
      if (!task || !back || !to || to === back) return;
      const name = nameOf(to);
      void move.run({ task, to }).then((r) => {
        if (!r.ok) return;
        announce(`${r.did} to ${name}`);
        toast({
          text: `Moved to ${name}`,
          timeout: UNDO_MS,
          action: { label: 'Undo', run: () => void move.run({ task, to: back }).then((b) => b.ok && toast({ text: `Moved back to ${nameOf(back)}` })) },
        });
      });
    },
  });

  const strip =
    from !== null &&
    createPortal(
      <div className="stage-strip">
        <span className="stage-strip-line">{over ? `Move to ${nameOf(over)}` : 'Drop on a stage'}</span>
        <ul aria-label="Drop on a stage">
          {stages.map((s) => (
            <li key={s.id} data-stage={s.id} aria-disabled={s.id === from || undefined}>
              {s.name}
            </li>
          ))}
        </ul>
      </div>,
      document.body,
    );
  return { ref, over, strip };
}

interface EdgeSlide {
  /** The pointer is at `x`: near an edge the slide starts that way, elsewhere it stops. */
  at: (x: number) => void;
  stop: () => void;
}

/** Slide `board` a column at a time while a drag stays near its left or right edge. A computer's board moves by its
    own arrows (BoardFit pages whole columns); a phone's, which has none, scrolls a column's width. */
function edgeSlide(board: HTMLElement | null): EdgeSlide {
  let dir = 0;
  let timer: ReturnType<typeof setInterval> | undefined;
  const step = () => {
    if (!board) return;
    const wrap = board.parentElement;
    const arrow = wrap?.querySelector<HTMLButtonElement>(`:scope > .board-more.${dir > 0 ? 'is-next' : 'is-prev'} button`);
    if (arrow) return arrow.click();
    if (wrap?.classList.contains('has-more')) return;
    const col = board.querySelector<HTMLElement>('.col:not(.is-fold)');
    const gap = parseFloat(getComputedStyle(board.firstElementChild ?? board).columnGap) || 0;
    board.scrollBy({ left: dir * ((col?.offsetWidth || board.clientWidth) + gap), behavior: 'smooth' });
  };
  const stop = () => {
    clearInterval(timer);
    dir = 0;
  };
  return {
    at: (x) => {
      if (!board) return;
      const r = board.getBoundingClientRect();
      const d = x < r.left + EDGE_PX ? -1 : x > r.right - EDGE_PX ? 1 : 0;
      if (d === dir) return;
      stop();
      dir = d;
      if (d) timer = setInterval(step, SLIDE_MS);
    },
    stop,
  };
}
