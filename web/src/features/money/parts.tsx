import { Link, useRouter, useSearch } from '@tanstack/react-router';
import { Fragment, type MouseEvent, type ReactNode } from 'react';
import { refusal, registry } from '@/actions';
import { ApiError } from '@/api/client';
import { runIn } from '@/actions/run';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { useCards } from '@/kit/Popover';
import { toast } from '@/kit/Toast';
import type { PageState } from '@/pagekinds';
import { validateFinanceSearch } from './search';

/* The parts Finance and Growth share, ported from pages/3-company/finance.js. */

/** The teammate the owner reads Finance as (`?as=`), or undefined for their own view. */
export const useViewAs = () => validateFinanceSearch(useSearch({ strict: false }) as Record<string, unknown>).as;

/** Runs one of the feature's actions from a click. A refusal says so; the change is already taken back. */
export function usePress() {
  const router = useRouter();
  return async (id: string, input: unknown): Promise<boolean> => {
    const r = await runIn(registry, id, input, 'click', router);
    if (!r.ok) toast(r.error === 'needs_approval' ? { text: 'Sent for approval' } : refusal(r, "Couldn't save"));
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

/** The phone's cards, where a wide table becomes rows of a name, its words and one figure (finance.js rowsPhone). */
export const usePhone = useCards;

/** A read's page state. A refusal (403) is no access, without Try again, as the old app's "Not in your view." was
    (finance.ts finFail). A read paused offline with nothing held is a fault with Try again, not a skeleton that never ends. */
export const readState = (q: { isPending: boolean; isError: boolean; error: unknown; fetchStatus?: string }, empty = false): PageState =>
  q.isPending
    ? q.fetchStatus === 'paused'
      ? 'fault'
      : 'loading'
    : q.isError
      ? q.error instanceof ApiError && q.error.status === 403
        ? 'no-access'
        : 'fault'
      : empty
        ? 'empty'
        : 'ready';

/** One phone row: the name, its words under it, one figure, an optional meter under both (finance.js rowsPhone). */
export interface Fr {
  key: string;
  title: ReactNode;
  sub?: ReactNode[];
  fig?: ReactNode;
  /** `is-quiet`, `is-sub`, `is-total`, `is-selected`. */
  cls?: string | false;
  bar?: ReactNode;
  /** The row's own ⋯, after its figure. */
  act?: ReactNode;
}

/** On a phone a wide table becomes rows under group names (the mock's `.fr`), so nothing scrolls sideways at 390. */
export function PhoneRows({ label, groups, none }: { label: string; groups: { name?: string; count?: number; rows: Fr[] }[]; none?: string }) {
  const any = groups.some((g) => g.rows.length > 0);
  return (
    <div className="frs" role="list" aria-label={label}>
      {!any && none && <div className="fold">{none}</div>}
      {groups.map((g, i) =>
        g.rows.length === 0 && g.name ? null : (
          <Fragment key={g.name ?? i}>
            {g.name && (
              <div className="fr-group" role="presentation">
                <span>{g.name}</span>
                {g.count != null && <span className="count">{g.count}</span>}
              </div>
            )}
            {g.rows.map((r) => (
              <div key={r.key} className={['row fr', r.cls].filter(Boolean).join(' ')} role="listitem">
                <span className="fr-main">
                  <span className="row-title">{r.title}</span>
                  <span className="fr-sub">
                    {(r.sub ?? []).filter(Boolean).map((w, k) => (
                      <span key={k}>{w}</span>
                    ))}
                  </span>
                </span>
                <span className="row-fig">
                  {r.fig}
                  {r.act}
                </span>
                {r.bar && <span className="fr-bar">{r.bar}</span>}
              </div>
            ))}
          </Fragment>
        ),
      )}
    </div>
  );
}
