import type { CSSProperties } from 'react';
import './Chart.css';

const f1 = (n: number) => n.toFixed(1);

export type SparklineProps = {
  points: readonly number[];
  /** Read aloud: what it shows and where it ends. Without one the line is decoration. */
  label?: string;
};

/** A small trend line ending in a point (pages/3-company `.spark`). */
export function Sparkline({ points, label }: SparklineProps) {
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const x = (i: number) => 3 + (points.length > 1 ? (i / (points.length - 1)) * 66 : 66);
  const y = (v: number) => (hi === lo ? 13 : 22 - ((v - lo) / (hi - lo)) * 18);
  const d = points.map((v, i) => `${i ? 'L' : 'M'}${f1(x(i))} ${f1(y(v))}`).join(' ');
  const last = points.length - 1;
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true as const };
  return (
    <svg className="spark" viewBox="0 0 72 26" {...a11y}>
      <path d={d} />
      {last >= 0 && <circle cx={f1(x(last))} cy={f1(y(points[last]!))} r="2.4" />}
    </svg>
  );
}

/** One bar: a value, or a stack of values bottom first, each in its own grey (`c1` to `c3`, darkest at the bottom). */
export type BarPoint = number | readonly number[];

const total = (p: BarPoint) => (typeof p === 'number' ? p : p.reduce((a, b) => a + b, 0));

export type BarChartProps = {
  points: readonly BarPoint[];
  /** The width the chart is drawn at, in px: the page measures it, so text keeps its size. */
  width: number;
  height?: number;
  max: number;
  /** Values that get a gridline and an axis label. */
  ticks: readonly number[];
  /** Writes a value: money is whole dollars, and "k" only on an axis. */
  fmt: (v: number) => string;
  /** [index, label] under the bars. */
  xLabels: readonly (readonly [number, string])[];
  /** Name every bar; otherwise only the last is named, at its top. */
  each?: boolean;
  label: string;
};

/** Bars by day or month: greys, the last bar (now) in ink-2 and named (pages/8-settings/more.js bars()). A stacked bar
    takes its greys from the stack instead (pages/3-company/company.js postsSVG). */
export function BarChart({ points, width: W, height: H = 136, max, ticks, fmt, xLabels, each, label }: BarChartProps) {
  const L = 44;
  const T = 16;
  const B = 22;
  const n = points.length;
  const step = (W - L) / n;
  const bw = Math.max(3, Math.min(34, step * 0.66));
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const cx = (i: number) => L + i * step + step / 2;
  const endLabel = (i: number) => !each && i === n - 1;
  // An unnamed run ends in one label: lift it clear of the bars it overhangs.
  const labelY = (v: number) =>
    each ? y(v) : y(Math.max(...points.slice(-Math.ceil((fmt(v).length * 7.5) / step) - 1).map(total)));
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {ticks.map((t) => (
        <g key={t}>
          <line className="grid" x1={L} x2={W} y1={f1(y(t))} y2={f1(y(t))} />
          <text x={L - 8} y={f1(y(t) + 4)} textAnchor="end">
            {fmt(t)}
          </text>
        </g>
      ))}
      <line className="base" x1={L} x2={W} y1={H - B + 0.5} y2={H - B + 0.5} />
      {points.map((p, i) => {
        const v = total(p);
        return (
          <g key={i}>
            {typeof p === 'number' ? (
              <rect
                className={i === n - 1 ? 'cbar is-now' : 'cbar'}
                x={f1(cx(i) - bw / 2)}
                y={f1(y(v))}
                width={f1(bw)}
                height={f1(H - B - y(v))}
                rx={f1(Math.min(3, bw / 3))}
              />
            ) : (
              <Stack parts={p} x={cx(i) - bw / 2} w={bw} y={y} />
            )}
            {(each || i === n - 1) && (
              <text
                className={i === n - 1 ? 'lbl' : undefined}
                x={f1(cx(i))}
                y={f1(labelY(v) - 6)}
                textAnchor={endLabel(i) ? 'end' : 'middle'}
                dx={endLabel(i) ? f1(bw / 2) : undefined}
              >
                {fmt(v)}
              </text>
            )}
          </g>
        );
      })}
      {xLabels.map(([i, text]) => (
        <text
          key={i}
          x={f1(cx(i))}
          y={H - 5}
          textAnchor={endLabel(i) ? 'end' : 'middle'}
          dx={endLabel(i) ? f1(bw / 2) : undefined}
        >
          {text}
        </text>
      ))}
    </svg>
  );
}

/** One stacked bar, bottom first, with a hairline of the page between its parts as the mock draws it. */
function Stack({ parts, x, w, y }: { parts: readonly number[]; x: number; w: number; y: (v: number) => number }) {
  let under = 0;
  return parts.map((part, k) => {
    const top = y(under + part);
    const h = y(under) - top - (k < parts.length - 1 ? 1 : 0);
    under += part;
    return <rect key={k} className={`c${Math.min(k + 1, 3)}`} x={f1(x)} y={f1(top)} width={f1(w)} height={f1(Math.max(0, h))} rx="1.5" />;
  });
}

export type LineChartProps = {
  points: readonly number[];
  width?: number;
  height?: number;
  /** A dashed line at this value, named at the right: a KPI's target. */
  target?: number | null;
  /** Writes a value: the last point's name and the target's. */
  fmt: (v: number) => string;
  /** Under the line's two ends: its first and last dates. */
  from?: string;
  to?: string;
  label: string;
};

/** Readings as a line, the last a point named at the right, the target dashed (pages/3-company/company.js readings()). */
export function LineChart({ points, width: W = 312, height: H = 124, target, fmt, from, to, label }: LineChartProps) {
  const L = 2;
  const R = 34;
  const T = 10;
  const B = from || to ? 20 : 6;
  const all = target != null ? [...points, target] : [...points];
  const span = Math.max(...all) - Math.min(...all) || Math.abs(all[0] ?? 1) || 1;
  const lo = Math.min(...all) - span * 0.1;
  const hi = Math.max(...all) + span * 0.1;
  const x = (i: number) => L + (points.length > 1 ? (i * (W - L - R)) / (points.length - 1) : W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const last = points.length - 1;
  const now = points[last];
  // The target's name gives way to the last point's when the two would touch.
  const nameTarget = target != null && (now == null || Math.abs(y(target) - y(now)) >= 12);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {target != null && <line className="lim" x1={L} x2={W - R} y1={f1(y(target))} y2={f1(y(target))} />}
      {nameTarget && (
        <text x={W} y={f1(y(target) + 4)} textAnchor="end">
          {fmt(target)}
        </text>
      )}
      <path className="ln" d={points.map((v, i) => `${i ? 'L' : 'M'}${f1(x(i))} ${f1(y(v))}`).join(' ')} />
      {now != null && (
        <>
          <circle className="pt-now" cx={f1(x(last))} cy={f1(y(now))} r="3" />
          <text className="now" x={W} y={f1(y(now) + 4)} textAnchor="end">
            {fmt(now)}
          </text>
        </>
      )}
      {from && (
        <text x={L} y={H - 3}>
          {from}
        </text>
      )}
      {to && (
        <text x={W - R} y={H - 3} textAnchor="end">
          {to}
        </text>
      )}
    </svg>
  );
}

export type FunnelStep = { name: string; count: number };

export type FunnelProps = {
  steps: readonly FunnelStep[];
  label: string;
  /** Writes a count; whole numbers with separators by default. */
  fmt?: (n: number) => string;
};

/** A funnel: each bar is its share of the first step (at least 2px), so it only narrows; the step-to-step % stays as
    muted words (aaa-audit-a Top 3). */
export function Funnel({ steps, label, fmt = (n) => n.toLocaleString('en-US') }: FunnelProps) {
  const first = steps[0]?.count ?? 0;
  return (
    <div className="hbars is-funnel" role="list" aria-label={label}>
      {steps.map((s, i) => {
        const share = first > 0 ? (s.count / first) * 100 : 0;
        const prev = steps[i - 1]?.count ?? 0;
        const conv = i > 0 && prev > 0 ? `${Math.round((s.count / prev) * 100)}%` : '';
        return (
          <div key={s.name} className="hbar" role="listitem">
            <span className="hbar-name">{s.name}</span>
            <span className="hbar-track" aria-hidden="true">
              <i style={{ '--w': `${f1(share)}%` } as CSSProperties} />
            </span>
            <span className="row-fig">{fmt(s.count)}</span>
            <span className="count">{conv}</span>
          </div>
        );
      })}
    </div>
  );
}
