import { useEffect, useRef, useState, type RefObject } from 'react';
import { MenuItem, MenuSub } from '@/kit/Menu';
import { GROUPS, RANGE_WORDS, RANGES, SORTS, type GroupBy, type Range, type SortBy } from './flow';

/* The parts the Automations list and its Runs share (automations-overview.ts): a row's bars over the time range, the
   width a chart is drawn at, and the sidebar's Group by, Sort by and (on a phone) time range. */

export interface Bucket {
  ok: number;
  failed: number;
}

/** A row's runs over the range as small bars (auSpark): failed ones darkest on top. */
export function Bars({ buckets, label }: { buckets: readonly Bucket[]; label: string }) {
  const n = buckets.length || 1;
  const w = 84;
  const h = 18;
  const max = Math.max(1, ...buckets.map((b) => b.ok + b.failed));
  const bw = w / n;
  return (
    <svg className="au-bars" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label}>
      {buckets.map((b, i) => {
        const v = b.ok + b.failed;
        const hv = v ? Math.max(2, (v / max) * h) : 1;
        const hf = b.failed ? Math.max(2, (b.failed / max) * h) : 0;
        const x = (i * bw + 0.5).toFixed(1);
        const wd = Math.max(1, bw - 1.5).toFixed(1);
        return (
          <g key={i}>
            <rect className={v ? 'is-on' : 'is-none'} x={x} y={(h - hv).toFixed(1)} width={wd} height={hv.toFixed(1)} rx="1" />
            {hf > 0 && <rect className="is-failed" x={x} y={(h - hf).toFixed(1)} width={wd} height={hf.toFixed(1)} rx="1" />}
          </g>
        );
      })}
    </svg>
  );
}

/** The width an element is drawn at, again whenever it changes: a chart draws at it so its text keeps its size. */
export function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    if (typeof ResizeObserver !== 'function') return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** The head's "⋯" choices for the list: Group by and Sort by; on a phone the time range too (its head has no room). */
export function ListChoices({ group, sort, range, phone, onGroup, onSort, onRange }: {
  group?: GroupBy;
  sort?: SortBy;
  range: Range;
  phone: boolean;
  onGroup?: (g: GroupBy) => void;
  onSort?: (s: SortBy) => void;
  onRange: (r: Range) => void;
}) {
  return (
    <>
      {phone && (
        <MenuSub label="Time range" icon="clock">
          {RANGES.map((r) => (
            <MenuItem key={r} label={RANGE_WORDS[r]} on={r === range} onSelect={() => onRange(r)} />
          ))}
        </MenuSub>
      )}
      {onGroup && (
        <MenuSub label="Group by" icon="columns">
          {GROUPS.map(([v, l]) => (
            <MenuItem key={v} label={l} on={v === group} onSelect={() => onGroup(v)} />
          ))}
        </MenuSub>
      )}
      {onSort && (
        <MenuSub label="Sort by" icon="list">
          {SORTS.map(([v, l]) => (
            <MenuItem key={v} label={l} on={v === sort} onSelect={() => onSort(v)} />
          ))}
        </MenuSub>
      )}
    </>
  );
}
