import { useEffect, useImperativeHandle, useLayoutEffect, useRef, type CSSProperties, type ReactNode, type Ref } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import './VirtualList.css';

/** What a list's owner may do to it: a chat goes to its end when the person sends. */
export type VirtualListHandle = {
  scrollToEnd: () => void;
  /** How far the scroller is from its end, in px (0 before it is drawn). */
  fromEnd: () => number;
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
  /** The scroll container's class from the page (`tl-list`, `tasks-body`); it owns overflow and padding. */
  className?: string;
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
  className,
  label,
  role = 'list',
  ref,
}: VirtualListProps<T>) {
  // The virtualizer mutates itself between renders; the React Compiler must not memoize what it returns.
  'use no memo';
  const scrollRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line react-hooks/incompatible-library -- 'use no memo' above opts this component out
  const v = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    getItemKey: (i) => getKey(items[i]!),
    overscan: 6,
    anchorTo: anchor,
    followOnAppend: anchor === 'end',
  });
  useImperativeHandle(ref, () => ({
    scrollToEnd: () => {
      const el = scrollRef.current;
      el?.scrollTo({ top: el.scrollHeight });
    },
    fromEnd: () => {
      const el = scrollRef.current;
      return el ? el.scrollHeight - el.scrollTop - el.clientHeight : 0;
    },
  }));
  const rows = v.getVirtualItems();
  const first = rows[0]?.index;
  const last = rows[rows.length - 1]?.index;

  // A timeline opens at its newest message.
  const opened = useRef(false);
  useLayoutEffect(() => {
    if (opened.current || anchor !== 'end' || items.length === 0) return;
    opened.current = true;
    v.scrollToIndex(items.length - 1, { align: 'end' });
  }, [anchor, items.length, v]);

  useEffect(() => {
    if (first === 0 && items.length > 0) onReachStart?.();
  }, [first, items.length, onReachStart]);
  useEffect(() => {
    if (last !== undefined && last === items.length - 1) onReachEnd?.();
  }, [last, items.length, onReachEnd]);

  return (
    <div ref={scrollRef} className={className} role={role} aria-label={label}>
      <div className="vl-space" style={{ '--h': `${v.getTotalSize()}px` } as CSSProperties}>
        {rows.map((r) => (
          <div
            key={r.key}
            className="vl-item"
            role={role === 'list' ? 'listitem' : undefined}
            data-index={r.index}
            ref={measure ? v.measureElement : undefined}
            style={{ '--y': `${r.start}px` } as CSSProperties}
          >
            {renderItem(items[r.index]!, r.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
