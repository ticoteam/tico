import { useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type ReactNode, type Ref } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import './VirtualList.css';

/** Within this many px of its end, a timeline counts as at it: a change in its window's height keeps it there. */
const END_PX = 24;

/** What a list's owner may do to it: a chat goes to its end when the person sends. */
export type VirtualListHandle = {
  /** `smooth`: glide there (Jump to latest), landing on the true end as rows are measured on the way. */
  scrollToEnd: (smooth?: boolean) => void;
  /** Bring one row to the middle of the window; false when no row has that key. */
  scrollToKey: (key: string) => boolean;
  /** How far the scroller is from its end, in px (0 before it is drawn). */
  fromEnd: () => number;
  /** The window's height in px (0 before it is drawn). */
  height: () => number;
};

export type VirtualListProps<T> = {
  items: readonly T[];
  /** Rows are keyed by id, never by index, so a live insert does not redraw the rows after it. */
  getKey: (item: T) => string;
  /** A row's height in px, or a guess when `measure` is on. */
  estimateSize: number;
  /** Rows of different heights (messages): each is measured once drawn. Leave off for fixed rows. */
  measure?: boolean;
  renderItem: (item: T, index: number) => ReactNode;
  /** 'end' for a chat timeline: it opens at the newest, stays there as messages arrive, and keeps its place when
      older pages load above. */
  anchor?: 'start' | 'end';
  /** The first row is drawn: load older pages (a timeline). */
  onReachStart?: () => void;
  /** The last row is drawn: load the next page. */
  onReachEnd?: () => void;
  /** The scroll position changed, even if the same rows are still drawn. */
  onScroll?: () => void;
  /** The scroll container's class from the page (`tl-list`, `tasks-body`); it owns overflow and padding. */
  className?: string;
  /** Use a containing scroller, keeping section heads and neighboring lists in the same scrolling area. */
  scrollParent?: string;
  label?: string;
  role?: 'list' | 'log' | 'feed';
  ref?: Ref<VirtualListHandle>;
};

/** The only wrapper over TanStack Virtual (architecture.md 4): any list that can pass 50 rows. Overscan 6. */
export function VirtualList<T>({
  items,
  getKey,
  estimateSize,
  measure,
  renderItem,
  anchor = 'start',
  onReachStart,
  onReachEnd,
  onScroll,
  className,
  scrollParent,
  label,
  role = 'list',
  ref,
}: VirtualListProps<T>) {
  // The virtualizer mutates itself between renders; the React Compiler must not memoize what it returns.
  'use no memo';
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  const scroller = () => (scrollParent ? scrollRef.current?.closest<HTMLElement>(scrollParent) ?? null : scrollRef.current);
  useLayoutEffect(() => {
    const list = scrollRef.current;
    const parent = scrollParent && list?.closest<HTMLElement>(scrollParent);
    if (!list || !parent) return;
    const measureOffset = () =>
      setScrollMargin(list.getBoundingClientRect().top - parent.getBoundingClientRect().top - parent.clientTop + parent.scrollTop);
    measureOffset();
    if (typeof ResizeObserver === 'undefined') return;
    const resize = new ResizeObserver(measureOffset);
    const observeChildren = () => {
      resize.disconnect();
      resize.observe(parent);
      for (const child of parent.children) resize.observe(child);
      measureOffset();
    };
    observeChildren();
    const children = new MutationObserver(observeChildren);
    children.observe(parent, { childList: true });
    return () => {
      resize.disconnect();
      children.disconnect();
    };
  }, [scrollParent]);
  // eslint-disable-next-line react-hooks/incompatible-library -- 'use no memo' above opts this component out
  const v = useVirtualizer({
    count: items.length,
    getScrollElement: scroller,
    estimateSize: () => estimateSize,
    scrollMargin,
    getItemKey: (i) => getKey(items[i]!),
    overscan: 6,
    anchorTo: anchor,
    followOnAppend: anchor === 'end',
    // Scrolling moves the rows in the DOM; React draws again only when the rows in view change.
    directDomUpdates: true,
    // Each row is registered so it can be placed; fixed rows keep their given height rather than being measured.
    useCachedMeasurements: !measure,
  });
  useImperativeHandle(ref, () => ({
    scrollToEnd: (smooth) => {
      if (smooth) v.scrollToEnd({ behavior: 'smooth' });
      else if (scrollParent) v.scrollToIndex(items.length - 1, { align: 'end' });
      else {
        const el = scrollRef.current;
        el?.scrollTo({ top: el.scrollHeight });
      }
    },
    scrollToKey: (key) => {
      const i = items.findIndex((it) => getKey(it) === key);
      if (i >= 0) v.scrollToIndex(i, { align: 'center' });
      return i >= 0;
    },
    fromEnd: () => {
      const el = scroller();
      return el ? (scrollParent ? v.getTotalSize() + scrollMargin : el.scrollHeight) - el.scrollTop - el.clientHeight : 0;
    },
    height: () => scroller()?.clientHeight ?? 0,
  }));
  const rows = v.getVirtualItems();
  const first = rows[0]?.index;
  const last = rows[rows.length - 1]?.index;

  /* A timeline opens at its newest message. To the end, not to a row by its index: an older page that loads above while
     the rows are still being measured moves every index, and a scroll aimed at row N would settle mid-way up. */
  const opened = useRef(false);
  // Unmounted, it opens at the end again when it comes back (StrictMode's second mount, which reattaches at the top).
  useLayoutEffect(
    () => () => {
      opened.current = false;
    },
    [],
  );
  useLayoutEffect(() => {
    if (opened.current || anchor !== 'end' || items.length === 0) return;
    opened.current = true;
    v.scrollToEnd();
  }, [anchor, items.length, v]);

  /* A timeline at its end stays there: when its rows grow (a long last message measured after the first scroll, a
     picture that loads, a new message) and when its window changes height (the box under it grows, the asks over the
     box come or go). Scrolled up, it keeps its place. Read from the scroller itself, not the virtualizer's guesses, so
     however late a row is measured the chat lands on its true end. A window that shrinks keeps its top, so how far it
     was from the end is read with the old height. */
  useLayoutEffect(() => {
    const el = scrollRef.current;
    const space = el?.firstElementChild;
    if (anchor !== 'end' || scrollParent || !el || typeof ResizeObserver === 'undefined') return;
    let was = el.clientHeight;
    let atEnd = true; // it opens at its end
    const onScroll = () => {
      atEnd = el.scrollHeight - el.scrollTop - el.clientHeight <= END_PX;
    };
    const resize = new ResizeObserver(() => {
      const h = el.clientHeight;
      if (h !== was) {
        atEnd = el.scrollHeight - el.scrollTop - was <= END_PX;
        was = h;
      }
      if (atEnd && el.scrollHeight - el.scrollTop - h > 0) el.scrollTop = el.scrollHeight;
    });
    el.addEventListener('scroll', onScroll, { passive: true });
    resize.observe(el);
    if (space) resize.observe(space);
    return () => {
      el.removeEventListener('scroll', onScroll);
      resize.disconnect();
    };
  }, [anchor, scrollParent]);

  useEffect(() => {
    if (first === 0 && items.length > 0) onReachStart?.();
  }, [first, items.length, onReachStart]);
  useEffect(() => {
    if (last !== undefined && last === items.length - 1) onReachEnd?.();
  }, [last, items.length, onReachEnd]);

  return (
    // A list read from the top keeps its place for Back (router.tsx); a timeline always opens at its end.
    <div ref={scrollRef} className={className} role={role} aria-label={label} onScroll={onScroll} data-scroll-restoration-id={!scrollParent && anchor === 'start' && label ? `vl:${label}` : undefined}>
      <div className="vl-space" ref={v.containerRef}>
        {rows.map((r) => (
          <div key={r.key} className="vl-item" role={role === 'list' ? 'listitem' : undefined} data-index={r.index} ref={v.measureElement}>
            {renderItem(items[r.index]!, r.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
