import { useState } from 'react';
import './Chart.css';
import './Line.css';

/** The width a chart is drawn at, so its words keep their size: the box's, measured once it is on the page. */
function useWidth(fallback: number) {
  const [w, setW] = useState(fallback);
  const ref = (el: HTMLDivElement | null) => {
    if (!el) return;
    const read = () => {
      const now = Math.round(el.clientWidth);
      if (now > 0) setW((was) => (Math.abs(was - now) > 1 ? now : was));
    };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  };
  return [w, ref] as const;
}

export type LineSeries = {
  name: string;
  /** Written after the name at the line's end. */
  end: string;
  pts: readonly number[];
  /** Darkest first. */
  cls: 'l1' | 'l2' | 'l3';
};

export type LinesProps = {
  series: readonly LineSeries[];
  height?: number;
  /** Room for the axis words at the left, and for the end names at the right. */
  left?: number;
  right?: number;
  min: number;
  max: number;
  ticks: readonly number[];
  fmt: (v: number) => string;
  /** [index, words] under the lines. */
  xl: readonly (readonly [number, string])[];
  label: string;
  /** The width before it is measured (and in a panel, where it is fixed). */
  width?: number;
};

/** Several lines over time in greys on a ruled axis, each named at its end (pages/3-company/finance.js lineChart()). */
export function Lines({ series, height: H = 176, left: L = 56, right: R = 116, min, max, ticks, fmt, xl, label, width = 640 }: LinesProps) {
  const [W, ref] = useWidth(width);
  const n = series[0]?.pts.length ?? 0;
  if (n < 2) return null;
  const T = 10;
  const B = 24;
  const x = (i: number) => L + (i * (W - L - R)) / (n - 1);
  const y = (v: number) => T + ((max - v) / (max - min || 1)) * (H - T - B);
  // End names that would touch move down to the next free line.
  const labs = series.map((s) => ({ s, y: y(s.pts[n - 1] ?? 0) })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < labs.length; i++) if (labs[i]!.y - labs[i - 1]!.y < 16) labs[i]!.y = labs[i - 1]!.y + 16;
  const f1 = (v: number) => v.toFixed(1);
  return (
    <div ref={ref}>
      <svg className="chart lines" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={L} x2={W - R} y1={f1(y(t))} y2={f1(y(t))} />
            <text x={L - 8} y={f1(y(t) + 4)} textAnchor="end">
              {fmt(t)}
            </text>
          </g>
        ))}
        {xl.map(([i, words]) => (
          <text key={i} x={f1(x(i))} y={H - 4} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
            {words}
          </text>
        ))}
        {series.map((s) => (
          <g key={s.name}>
            <path className={s.cls} d={s.pts.map((v, i) => `${i ? 'L' : 'M'}${f1(x(i))} ${f1(y(v))}`).join(' ')} />
            <circle className={s.cls.replace('l', 'd')} cx={f1(x(n - 1))} cy={f1(y(s.pts[n - 1] ?? 0))} r="3" />
          </g>
        ))}
        {labs.map((a) => (
          <text key={a.s.name} x={W - R + 10} y={f1(a.y + 4)}>
            <tspan className="lbl">{a.s.name}</tspan> {a.s.end}
          </text>
        ))}
      </svg>
    </div>
  );
}
