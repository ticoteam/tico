import { useQuery } from '@tanstack/react-query';
import { Link, useLocation } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { refusal as shared, useAction, type Result } from '@/actions';
import { ApiError } from '@/api/client';
import { useActor } from '@/data';
import { Avatar, type AvatarSize } from '@/kit/Avatar';
import { OwnerButton } from '@/kit/OwnerButton';
import { toast, type ToastInput } from '@/kit/Toast';
import { chatAddress } from '@/lib';
import type { PageState } from '@/pagekinds';
import { pageOwnersQuery } from './queries';

/* The small parts Company, Strategy, Products and Risk share, ported from pages/3-company/company.js and
   pages/10-records/records.js. */

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
export function PanelLink({ panel, className, title, children, ...rest }: { panel: string; className?: string; title?: string; children: ReactNode; 'data-target'?: string; 'aria-label'?: string }) {
  return (
    <Link to="." search={((prev: Record<string, unknown>) => ({ ...prev, panel })) as never} className={className} title={title} {...rest}>
      {children}
    </Link>
  );
}

/** A read's page state. A refusal (403) is no access, without Try again; a read paused offline with nothing to show
    is a fault with Try again, not a skeleton that never ends. */
export const readState = (q: { isPending: boolean; isError: boolean; error: unknown; fetchStatus: string }, empty = false): PageState =>
  q.isError
    ? q.error instanceof ApiError && q.error.status === 403
      ? 'no-access'
      : 'fault'
    : q.isPending
      ? q.fetchStatus === 'paused'
        ? 'fault'
        : 'loading'
      : empty
        ? 'empty'
        : 'ready';

/** The page owner's face in the head (Settings › Page owners): it opens their chat beside the page. Nothing when the
    page has no owner. */
export function PageOwner({ page }: { page: 'strategy' | 'goals' | 'products' | 'risk' }) {
  const bot = useQuery(pageOwnersQuery).data?.[page]?.bot ?? null;
  const actor = bot && !bot.includes(':') ? `bot:${bot}` : bot;
  const who = useActor(actor);
  // `chat` is the shell's, in no route's search schema: built as an address, it keeps the page's own search.
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      if (actor) q.set('chat', chatAddress(actor, 'agent'));
      return `${l.pathname}?${q.toString()}`;
    },
  });
  if (!actor) return null;
  return <OwnerButton name={who?.name ?? 'the owner'} kind="agent" hue={who?.hue} live={who?.working} href={href} target={`chat:${chatAddress(actor, 'agent')}`} />;
}

/** A refusal's toast: "Couldn't save", then the server's reason. A bare code is never shown, not even made readable. */
export function refusal(r: Result): ToastInput {
  if (r.error === 'librarian') return { text: 'The Librarian is not running yet', kind: 'failed' };
  return shared(r, r.error?.startsWith('bad_input') ? 'Check the fields' : "Couldn't save", 'Not allowed');
}

/** Run one of this feature's actions from a click; a refusal says why (the change is already taken back). */
export function useDo(id: string) {
  const a = useAction(id);
  return {
    pending: a.pending,
    run: async (input: unknown): Promise<boolean> => {
      const r = await a.run(input);
      if (!r.ok) toast(refusal(r));
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
