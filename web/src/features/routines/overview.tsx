import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Icon, type IconName } from '@/kit/Icon';
import { MenuItem, MenuSub } from '@/kit/Menu';
import { GROUPS, RANGE_WORDS, RANGES, SORTS, type GroupBy, type Range, type SortBy } from './flow';

/* The parts the Automations list and its Runs share (automations-overview.ts): the filters down the left with a count
   per choice, a run's bars over the time range, and the width a chart is drawn at. */

export interface Choice {
  value: string;
  label: string;
  /** How many rows it would show; none is left out unless picked. */
  count?: number;
  icon?: IconName;
  lead?: ReactNode;
}

/** One group of filters: a heading, then a choice per line with its count. One pick at a time unless `many`. */
export function Facet({ label, choices, picked, onPick }: { label: string; choices: readonly Choice[]; picked: readonly string[]; onPick: (value: string) => void }) {
  const shown = choices.filter((c) => c.count === undefined || c.count > 0 || picked.includes(c.value));
  if (!shown.length) return null;
  return (
    <div className="au-facet" role="group" aria-label={label}>
      <h3 className="au-facet-h">{label}</h3>
      {shown.map((c) => (
        <button key={c.value} type="button" className="au-fb" aria-pressed={picked.includes(c.value)} onClick={() => onPick(c.value)}>
          <span className="au-fb-lead">{c.lead ?? (c.icon ? <Icon name={c.icon} /> : null)}</span>
          <span className="au-fb-t">{c.label}</span>
          {c.count !== undefined && <span className="au-fb-n">{c.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** The filters' column: on a phone it shows only while the filter button is on. */
export function Rail({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <aside className={open ? 'au-rail is-open' : 'au-rail'} aria-label="Filters">
      {children}
    </aside>
  );
}

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
