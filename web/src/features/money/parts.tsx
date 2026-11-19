import { Link, useRouter } from '@tanstack/react-router';
import { useState, type MouseEvent, type ReactNode } from 'react';
import { registry } from '@/actions';
import { runIn } from '@/actions/run';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { toast } from '@/kit/Toast';

/* The parts Finance and Growth share, ported from pages/3-company/finance.js. */

/** Runs one of the feature's actions from a click. A refusal says so; the change is already taken back. */
export function usePress() {
  const router = useRouter();
  return async (id: string, input: unknown): Promise<boolean> => {
    const r = await runIn(registry, id, input, 'click', router);
    if (!r.ok) toast(r.error === 'needs_approval' ? { text: 'Sent for approval' } : { text: "Couldn't save", kind: 'failed' });
    return r.ok;
  };
}

const plain = (e: MouseEvent) => e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;

/**
 * A link to a view on the page it is on: its address is real (open in a new tab, copy it), and a plain click runs the
 * action, so a click and Tico do the same thing. Like the action, it keeps what the address holds and changes `search`.
 */
export function ActLink({
  to,
  search,
  action,
  input,
  className,
  title,
  children,
  ...rest
}: {
  to: string;
  /** The change to the address's search; an undefined key drops it. */
  search: Record<string, unknown>;
  action: string;
  input: unknown;
  className?: string;
  title?: string;
  children: ReactNode;
  'aria-current'?: 'page';
  'data-target'?: string;
}) {
  const press = usePress();
  return (
    <Link
      to={to as never}
      search={((prev: Record<string, unknown>) => ({ ...prev, ...search })) as never}
      className={className}
      title={title}
      {...rest}
      onClick={(e: MouseEvent) => {
        if (!plain(e)) return;
        e.preventDefault();
        void press(action, input);
      }}
    >
      {children}
    </Link>
  );
}

export interface SearchTab {
  id: string;
  label: string;
  search: Record<string, unknown>;
  action: string;
  input: unknown;
}

/** The page's tab row where the tab is in the address's search (`?tab=`): the same markup as the kinds' tab row. */
export function SearchTabs({ to, tabs, on, label }: { to: string; tabs: readonly SearchTab[]; on: string; label: string }) {
  return (
    <nav className="page-tabs pk-tabs" aria-label={label}>
      <div className="docs">
        {tabs.map((t) => (
          <ActLink
            key={t.id}
            className={t.id === on ? 'doc-tab is-on' : 'doc-tab'}
            aria-current={t.id === on ? 'page' : undefined}
            to={to}
            search={t.search}
            action={t.action}
            input={t.input}
            data-target={`tab:${t.id}`}
          >
            <span data-t={t.label}>{t.label}</span>
          </ActLink>
        ))}
      </div>
    </nav>
  );
}

/** How much of a budget is used, with a tick for where today is in the month (finance.js `meter`). */
export function Meter({ pct, pace }: { pct: number; pace?: number }) {
  const p = Math.max(0, pct);
  return (
    <span className={p > 100 ? 'meter is-over' : 'meter'} role="img" aria-label={`${Math.round(p)}%`}>
      <i style={{ width: `${Math.min(100, p)}%` }} />
      {pace != null && <u style={{ left: `${pace}%` }} />}
    </span>
  );
}

/** The panel's one big figure, its words and a meter (finance.js `pfig`). */
export function Pfig({ big, sub, children }: { big: string; sub: string; children?: ReactNode }) {
  return (
    <div className="pfig">
      <b>{big}</b>
      <span>{sub}</span>
      {children}
    </div>
  );
}

/** A panel row: a name, a muted word, a figure (finance.js `prow`). */
export function Prow({ name, sub, fig, plan }: { name: ReactNode; sub?: string; fig: string; plan?: boolean }) {
  return (
    <div className={plan ? 'row prow is-plan' : 'row prow'}>
      <span className="row-title">{name}</span>
      <span className="row-sub">{sub ?? ''}</span>
      <span className="row-fig">{fig}</span>
    </div>
  );
}

/** Where a number comes from: its name, its source in words, the figure (finance.js `ptrace`). */
export function Ptrace({ name, sub, fig }: { name: string; sub: string; fig: string }) {
  return (
    <div className="row ptrace">
      <span className="ptrace-main">
        <span className="row-title">{name}</span>
        <span className="row-sub">{sub}</span>
      </span>
      <span className="row-fig">{fig}</span>
    </div>
  );
}

/** A teammate's face and name in a table cell (finance.js `who`). */
export function Who({ id }: { id: string }) {
  const a = useActor(id);
  const name = a?.name ?? id.slice(id.indexOf(':') + 1);
  return (
    <span className="who-cell">
      <Avatar kind={a?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={name} hue={a?.hue} size={22} />
      <span>{name}</span>
    </span>
  );
}

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

export interface Series {
  name: string;
  /** Written after the name at the line's end. */
  end: string;
  pts: readonly number[];
  cls: 'l1' | 'l2' | 'l3';
}

export interface LineChartProps {
  series: readonly Series[];
  height?: number;
  /** Room for the axis words at the left, and for the end labels at the right. */
  left?: number;
  right?: number;
  min: number;
  max: number;
  ticks: readonly number[];
  fmt: (v: number) => string;
  /** [index, label] under the line. */
  xl: readonly (readonly [number, string])[];
  label: string;
  /** The width before it is measured (and in a panel, where it is fixed). */
  width?: number;
}

/** Lines over time in greys, each named at its end (finance.js `lineChart`). The kit has bars and a funnel, no line. */
export function LineChart({ series, height: H = 176, left: L = 56, right: R = 116, min, max, ticks, fmt, xl, label, width = 640 }: LineChartProps) {
  const [W, ref] = useWidth(width);
  const n = series[0]?.pts.length ?? 0;
  if (n < 2) return null;
  const T = 10;
  const B = 24;
  const x = (i: number) => L + (i * (W - L - R)) / (n - 1);
  const y = (v: number) => T + ((max - v) / (max - min || 1)) * (H - T - B);
  const labs = series.map((s) => ({ s, y: y(s.pts[n - 1] ?? 0) })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < labs.length; i++) if (labs[i]!.y - labs[i - 1]!.y < 16) labs[i]!.y = labs[i - 1]!.y + 16;
  const f1 = (v: number) => v.toFixed(1);
  return (
    <div ref={ref}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
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
