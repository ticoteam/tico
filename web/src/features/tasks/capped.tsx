import { useState, type ReactNode } from 'react';
import { VirtualList } from '@/kit/VirtualList';

/* The mock's caps (list.js): a list stage past twelve rows shows ten, a column past ten cards shows eight, then
   "N more", which opens the rest. A stage opened past this many rows draws only what is on screen. */
export const LIST_CAP = 12;
export const LIST_SHOW = 10;
export const COL_CAP = 10;
export const COL_SHOW = 8;
const LONG = 100;
export const ROW_H = 34;
export const CARD_H = 96;

/** The first `show` rows, or all of them once opened; past LONG, only what is on screen is drawn. */
export function Capped<T extends { id: string }>({
  list,
  cap,
  show,
  more,
  draw,
  moreRow,
  height,
  cards,
}: {
  list: readonly T[];
  cap: number;
  show: number;
  /** Read the next page; only while the server has one. */
  more: (() => void) | undefined;
  draw: (t: T) => ReactNode;
  moreRow: (n: number, open: () => void) => ReactNode;
  height: number;
  cards?: boolean;
}) {
  const [all, setAll] = useState(false);
  if (all && list.length > LONG)
    return (
      <VirtualList
        className="tk-long"
        items={list}
        getKey={(t) => t.id}
        estimateSize={height}
        measure={cards}
        renderItem={(t) => draw(t)}
        onReachEnd={more}
      />
    );
  if (all || list.length <= cap)
    return (
      <>
        {list.map(draw)}
        {more && moreRow(0, more)}
      </>
    );
  return (
    <>
      {list.slice(0, show).map(draw)}
      {moreRow(list.length - show, () => setAll(true))}
    </>
  );
}

/** "N more"; "N+ more" while the server holds more than was read. Zero is the next server page. */
export const moreText = (n: number, cut?: boolean) => (n ? `${n}${cut ? '+' : ''} more` : 'More tasks');

/** "N more" under a list stage's first rows. */
export const RowMore = ({ n, cut, onClick }: { n: number; cut?: boolean; onClick: () => void }) => (
  <button key="more" className="row task row-more" type="button" onClick={onClick}>
    <span className="row-mark" />
    <span className="row-title">{moreText(n, cut)}</span>
  </button>
);
