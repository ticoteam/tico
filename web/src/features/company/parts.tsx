import { Link } from '@tanstack/react-router';
import type { CSSProperties, ReactNode } from 'react';
import { useAction } from '@/actions';
import { useActor } from '@/data';
import { Avatar, type AvatarSize } from '@/kit/Avatar';
import { toast } from '@/kit/Toast';

/* The small parts Company, Strategy, Products and Risk share, ported from pages/3-company/company.js and
   pages/10-records/records.js. */

/** Flow's mark: it waits on you. The kit has none yet (shell/Menu.tsx draws the same). */
export function FlowMark({ size = 10, className }: { size?: number; className?: string }) {
  return (
    <span className={className ? `dot flow ${className}` : 'dot flow'} style={{ '--d': `${size}px` } as CSSProperties} role="img" aria-label="Waits on you">
      <i />
      <b />
    </span>
  );
}

const bare = (id: string) => id.slice(id.indexOf(':') + 1);

/** A teammate's face by actor id ('human:<id>', 'bot:<slug>', or a bare slug). */
export function Face({ id, size = 22, live }: { id: string | null | undefined; size?: AvatarSize; live?: boolean }) {
  const a = useActor(id);
  if (!id) return null;
  return <Avatar kind={a?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={a?.name ?? bare(id)} hue={a?.hue} size={size} live={live ?? a?.working} />;
}

/** A teammate's name by actor id. */
export function NameOf({ id }: { id: string | null | undefined }) {
  const a = useActor(id);
  return <>{a?.name ?? (id ? bare(id) : '')}</>;
}

/** Where a teammate's page is: `/team/<slug or id>`. */
export const teamPath = (id: string) => {
  const b = bare(id);
  return `/team/${encodeURIComponent(b)}`;
};

/** A link to an address the route tree may not list yet (a tab, a page another builder owns). */
export function Go({ to, search, className, title, children, ...rest }: { to: string; search?: Record<string, string | undefined>; className?: string; title?: string; children: ReactNode; 'aria-label'?: string; 'data-target'?: string }) {
  return (
    <Link to={to as never} search={search as never} className={className} title={title} {...rest}>
      {children}
    </Link>
  );
}

/** A link that sets `?panel=` on this address; Back closes it. */
export function PanelLink({ panel, className, title, children, ...rest }: { panel: string; className?: string; title?: string; children: ReactNode; 'data-target'?: string }) {
  return (
    <Link to="." search={((prev: Record<string, unknown>) => ({ ...prev, panel })) as never} className={className} title={title} {...rest}>
      {children}
    </Link>
  );
}

/** Run one of this feature's actions from a click; a refusal says so (the change is already taken back). */
export function useDo(id: string) {
  const a = useAction(id);
  return {
    pending: a.pending,
    run: async (input: unknown): Promise<boolean> => {
      const r = await a.run(input);
      if (!r.ok) toast({ text: r.error?.startsWith('bad_input') ? 'Check the fields' : "Couldn't save", kind: 'failed' });
      return r.ok;
    },
  };
}

/** How a KPI has moved: its last readings as one thin line that ends in a point (company.js trend). */
export function Trend({ points }: { points: readonly number[] }) {
  const w = 56;
  const h = 16;
  const n = points.length;
  if (!n) return <span className="trend" />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const x = (i: number) => (n > 1 ? 2.5 + (i * (w - 5)) / (n - 1) : w - 2.5);
  const y = (v: number) => (max === min ? h / 2 : h - 3 - ((v - min) / (max - min)) * (h - 6));
  return (
    <svg className="trend" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {n > 1 && <path d={points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')} />}
      <circle cx={x(n - 1).toFixed(1)} cy={y(points[n - 1] ?? 0).toFixed(1)} r="2" />
    </svg>
  );
}

/* A number with its unit, as the server writes it (backend/kpis.py amount): '52%', '$1,200', '148 studios'. */
const SYMBOLS = new Set(['%', 'pp', '$', '€', '£']);
const COUNT_WORDS = /^(count|people|customers|users|orders|tasks|items|studios)$/i;
export function amount(value: number | null | undefined, unit = ''): string {
  if (value == null || !Number.isFinite(value)) return '';
  const u = unit.trim();
  const whole = Number.isInteger(value) || (!!u && !SYMBOLS.has(u) && COUNT_WORDS.test(u));
  const text = whole ? Math.round(value).toLocaleString('en-US') : (Math.round(value * 10) / 10).toLocaleString('en-US', { maximumFractionDigits: 1 });
  if (!u) return text;
  if (u === '%' || u === 'pp') return text + u;
  if (u === '$' || u === '€' || u === '£') return u + text;
  return `${text} ${u}`;
}

/** Dollars, whole: '$1,284'. Money from a figure, not from a ledger, so no cents. */
export const usd = (v: number) => `$${Math.round(v).toLocaleString('en-US')}`;

/** A short dollar figure for a strip: '$2.4B', '$310M', '$48k'. */
export function usdShort(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (a >= 1e4) return `$${Math.round(v / 1e3)}k`;
  return usd(v);
}
