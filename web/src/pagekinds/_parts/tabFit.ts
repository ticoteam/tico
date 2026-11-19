/**
 * A tab row wider than its sheet scrolls sideways and fades where it runs off an edge (pages/3-company/finance.js and
 * finance.css: `.docs.is-long`, `.is-scrolled`, `.is-end`). The open tab is brought into view. Pure, so it is tested
 * without a browser; PageTabs measures and applies it.
 */

/** The row's scroll box, as the browser reports it. */
export interface RowBox {
  scrollLeft: number;
  clientWidth: number;
  scrollWidth: number;
}

/** The open tab's edges, in the row's content (scroll) coordinates. */
export interface TabSpan {
  left: number;
  right: number;
}

export interface RowMarks {
  /** Desk only: the row runs past its sheet, so it reaches the sheet's edge and fades there. */
  long: boolean;
  /** Scrolled away from the start: the left edge fades too. */
  scrolled: boolean;
  /** Scrolled to the end: only the left edge fades. */
  end: boolean;
}

/** What the open tab keeps clear of the row's left edge when it is scrolled to (the mock's 40). */
export const LEAD = 40;
/** Room at the right a desk tab must clear to count as in view (the mock's 56: the 40 fade and some). */
const RIGHT_CLEAR = 56;
/** The left fade's width (28 in the mock); a tab under it is not in view. */
const LEFT_FADE = 28;

export function rowMarks(row: RowBox, phone: boolean): RowMarks {
  const scrolled = row.scrollLeft > 0;
  return {
    long: !phone && row.scrollWidth > row.clientWidth + 1,
    scrolled,
    end: scrolled && row.scrollLeft + row.clientWidth >= row.scrollWidth - 1,
  };
}

/**
 * Where the row should scroll so the open tab shows, or null to leave it. A phone always puts the open tab near the
 * start (the mock's phone rule); a desk moves only when the tab is under a fade or past the edge.
 */
export function scrollFor(row: RowBox, on: TabSpan | null, phone: boolean): number | null {
  if (!on) return null;
  const max = Math.max(0, row.scrollWidth - row.clientWidth);
  const to = Math.min(max, Math.max(0, on.left - LEAD));
  if (phone) return to === row.scrollLeft ? null : to;
  const fadeLeft = row.scrollLeft > 0 ? LEFT_FADE : 0;
  const inView = on.left >= row.scrollLeft + fadeLeft && on.right <= row.scrollLeft + row.clientWidth - RIGHT_CLEAR;
  if (inView || row.scrollWidth <= row.clientWidth + 1) return null;
  return to === row.scrollLeft ? null : to;
}

/** Arrow keys walk the row: the index to focus from `at`, wrapping at both ends. */
export function stepTab(at: number, count: number, key: string): number | null {
  if (count === 0) return null;
  if (key === 'ArrowRight') return (at + 1) % count;
  if (key === 'ArrowLeft') return (at - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
}
