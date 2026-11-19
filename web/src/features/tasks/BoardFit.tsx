import { useLayoutEffect, useRef, useState } from 'react';
import { IconButton } from '@/kit/IconButton';
import { FlowMark } from '@/kit/FlowMark';

/* The board's fit (list.js fitBoard): whole columns only. Open columns share one width so the columns on screen end on
   the board's right edge; what does not fit lies past that edge, behind an arrow that says how many stages are there
   and how many of their tasks wait on you. MIN is the width a column is planned at; EDGE leaves room for a card's
   outline at both ends. */
const GAP = 10;
const STRIP = 40;
const MIN = 138;
const MAX = 296;
const EDGE = 4;

interface Edge {
  stages: string[];
  waits: number;
}

const isOpen = (c: Element) => !c.classList.contains('is-fold');
const need = (list: Element[], w: number) => list.reduce((s, c) => s + (isOpen(c) ? w : STRIP), 0) + GAP * Math.max(0, list.length - 1);
const stageOf = (c: Element) => c.getAttribute('data-target')?.slice('stage:'.length) ?? '';
/** The column's own count of tasks that wait on you, from its head. */
const waitsIn = (c: Element) => Number(c.querySelector(':scope > .col-head .col-flow b')?.textContent ?? 0);
const edge = (list: Element[]): Edge | null =>
  list.length ? { stages: list.map(stageOf), waits: list.reduce((n, c) => n + waitsIn(c), 0) } : null;

/**
 * Drawn inside the board (its arrows are placed against `.board-wrap`). `shape` changes whenever columns open, fold
 * or fill, so the fit runs again.
 */
export function BoardFit({ shape }: { shape: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [start, setStart] = useState(0);
  const [edges, setEdges] = useState<{ prev: Edge | null; next: Edge | null; end: number }>({ prev: null, next: null, end: 0 });

  useLayoutEffect(() => {
    const inn = ref.current?.parentElement;
    const board = inn?.parentElement;
    const wrap = board?.parentElement;
    if (!inn || !board || !wrap) return;
    const fit = () => {
      const cols = Array.from(inn.querySelectorAll(':scope > .col'));
      wrap.classList.remove('has-more');
      inn.style.paddingRight = '';
      if (document.documentElement.dataset.layout === 'cards' || !board.clientWidth) {
        inn.style.removeProperty('--col-w');
        return setEdges({ prev: null, next: null, end: cols.length });
      }
      const more = need(cols, MIN) > board.clientWidth - EDGE;
      wrap.classList.toggle('has-more', more);
      const avail = board.clientWidth - EDGE;
      const from = more ? Math.min(start, Math.max(0, cols.length - 1)) : 0;
      let end = from;
      while (end < cols.length && need(cols.slice(from, end + 1), MIN) <= avail + 0.5) end++;
      end = Math.max(end, from + 1);
      const shown = cols.slice(from, end);
      const opens = shown.filter(isOpen).length;
      const w = opens ? Math.min(MAX, (avail - need(shown, 0)) / opens) : MIN;
      inn.style.setProperty('--col-w', `${w}px`);
      if (more && from) {
        // room to bring a late stage to the left edge
        inn.style.paddingRight = `${Math.max(0, avail - need(cols.slice(from), w))}px`;
        board.scrollLeft = (cols[from] as HTMLElement).offsetLeft - (cols[0] as HTMLElement).offsetLeft;
      } else board.scrollLeft = 0;
      setEdges(more ? { prev: edge(cols.slice(0, from)), next: edge(cols.slice(end)), end } : { prev: null, next: null, end });
    };
    fit();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(fit);
    ro.observe(board);
    return () => ro.disconnect();
  }, [shape, start]);

  const arrow = (e: Edge | null, cls: string, label: string, to: number) =>
    e && (
      <div className={`board-more ${cls}`}>
        <IconButton icon="chevron" label={label} size="sm" className={cls === 'is-prev' ? 'flip' : undefined} onClick={() => setStart(to)} />
        <span className="more-n">+{e.stages.length}</span>
        {e.waits > 0 && (
          <span className="col-flow" title={`${e.waits} wait${e.waits === 1 ? 's' : ''} on you`}>
            <FlowMark />
            <b>{e.waits}</b>
          </span>
        )}
      </div>
    );

  return (
    <>
      <span ref={ref} hidden />
      {arrow(edges.prev, 'is-prev', 'Earlier stages', Math.max(0, start - Math.max(1, edges.end - start)))}
      {arrow(edges.next, 'is-next', 'Later stages', edges.end)}
    </>
  );
}
