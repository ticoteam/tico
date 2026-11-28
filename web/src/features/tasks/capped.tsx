import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from 'react';
import { VirtualList } from '@/kit/VirtualList';

/* A task list shows every task it holds and runs on down the page as it scrolls: no "N more" (task 3535). Past LONG
   rows only those near the screen are drawn, still in the page's own scroll. */
const LONG = 100;
export const ROW_H = 34;
export const PHONE_ROW_H = 44;
export const CARD_H = 96;
/** The scroller a list runs on in: List's body, the board, a page body (each marked `data-scroll`). */
const SCROLLER = '[data-scroll]';
/** How far below the screen a list's end starts the next read, so the rows are there by the time it shows. */
const NEAR = '0px 0px 600px 0px';

/** Every row, one after another; past LONG, only those near the screen are drawn, in the page's own scroll. */
export function AllRows<T extends { id: string }>({
  list,
  draw,
  height,
  measure,
}: {
  list: readonly T[];
  draw: (t: T) => ReactNode;
  height: number;
  measure?: boolean;
}) {
  if (list.length > LONG)
    return (
      <VirtualList key={height} items={list} getKey={(t) => t.id} estimateSize={height} measure={measure} renderItem={(t) => draw(t)} scrollParent={SCROLLER} />
    );
  return <>{list.map(draw)}</>;
}

/**
 * Where a list ends while the server holds more of it. `of` names the list (its read's params): a filter that swaps the
 * list under the same page starts a fresh end, so a failed or running read of the last list never holds up the new one.
 */
export function ReadOn({ of, ...end }: { of: string; more: (() => Promise<boolean>) | undefined; look?: 'row' | 'col' }) {
  return <End key={of} {...end} />;
}

/**
 * One list's end. The next page is read as this comes near the screen, with no
 * click, and again after each page while the end stays near. It reads only when no read is running and none has
 * failed; a failed read leaves a "Try again" row. `more` resolves false when the read failed.
 */
function End({ more, look = 'row' }: { more: (() => Promise<boolean>) | undefined; look?: 'row' | 'col' }) {
  const end = useRef<HTMLDivElement>(null);
  const busy = useRef(false);
  const [failed, setFailed] = useState(false);
  // a page that came in: watch again, so an end still on screen reads the next one
  const [round, setRound] = useState(0);
  const watch = typeof IntersectionObserver !== 'undefined';
  const read = () => {
    if (!more || busy.current) return;
    busy.current = true;
    setFailed(false);
    void more().then((ok) => {
      busy.current = false;
      if (ok) setRound((n) => n + 1);
      else setFailed(true);
    });
  };
  const near = useEffectEvent(read);
  const on = !!more;
  useEffect(() => {
    const el = end.current;
    if (!on || failed || !el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && near(), { root: el.parentElement?.closest<HTMLElement>(SCROLLER) ?? null, rootMargin: NEAR });
    io.observe(el);
    return () => io.disconnect();
  }, [on, failed, round]);
  if (!more) return null;
  // without a way to see the end come near (an old browser), a press reads on
  if (failed || !watch) {
    const text = failed ? 'Try again' : 'More tasks';
    return look === 'col' ? (
      <button className="col-more tk-end" type="button" onClick={read}>
        {text}
      </button>
    ) : (
      <button className="row task row-more tk-end" type="button" onClick={read}>
        <span className="row-mark" />
        <span className="row-title">{text}</span>
      </button>
    );
  }
  return <div ref={end} className="tk-end" aria-hidden="true" />;
}
