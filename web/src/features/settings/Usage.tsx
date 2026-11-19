import { useQuery, type QueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useLayoutEffect, useRef, useState } from 'react';
import { run } from '@/actions';
import type { UsageFigures } from '@/api/schema';
import { directoryQuery } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { BarChart, Sparkline } from '@/kit/Chart';
import { DatePick } from '@/kit/DatePick';
import { Empty } from '@/kit/Empty';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { Popover } from '@/kit/Popover';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Dashboard, FiguresStrip, PageMore, Section } from '@/pagekinds';
import { Meter, Verb, isForbidden, usd } from './parts';
import {
  agentDays,
  agentUsageWhereQuery,
  limitsQuery,
  rangeDays,
  usageChoicesQuery,
  usageQuery,
  usageWhereQuery,
  USAGE_FILTERS,
  type UsageRange,
  type UsageRow,
  type UsageWhere,
} from './queries';
import { AddLimit, LimitPanel, Limits } from './UsageLimits';
import './settings.css';

/* Usage (kind: Dashboard; pages/8-settings/more.js). Spend: figures, a bar a day, then the money by agent or by model;
   an agent's row opens its tasks and its tokens. Limits: the company's, each team's and each agent's monthly limit,
   a row opening beside the list (UsageLimits.tsx). The view, the range, the grouping and the opened row are in the
   address; a custom range and the filter are the page's own (the route keeps only the four ranges). */

export interface UsageSearch {
  view?: 'limits';
  range?: UsageRange;
  by?: 'model';
  open?: string;
}

/** For the route's loader: the view's first read. Never throws. */
export const loadUsage = (qc: QueryClient, search: UsageSearch) =>
  search.view === 'limits' ? qc.prefetchQuery(limitsQuery) : qc.prefetchQuery(usageQuery(search.range ?? '7d', 'bot'));

const RANGES: readonly { value: UsageRange | 'custom'; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'month', label: 'This month' },
  { value: 'custom', label: 'Custom' },
];

/** API-equivalent: what the runs would have cost on keys, plan runs included. */
export const spent = (f: Pick<UsageFigures, 'est_cost_usd' | 'subscription_equiv_usd'>) => (f.est_cost_usd ?? 0) + (f.subscription_equiv_usd ?? 0);
const tokens = (f: UsageFigures) => f.input_tokens + f.cached_tokens + f.cache_write_tokens + f.output_tokens;
export const fmtTok = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));

function useWidth(fallback = 640) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => e && e.contentRect.width > 0 && setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

const go = (search: UsageSearch) => void run('usage.open', search, { via: 'click' });

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / p) * p;
}

function DayChart({ where }: { where: UsageWhere }) {
  const days = useQuery(usageWhereQuery(where, 'day'));
  const [ref, width] = useWidth();
  const rows = days.data?.rows ?? [];
  const points = rows.map((r) => spent(r));
  const max = niceMax(Math.max(0, ...points));
  const every = Math.max(1, Math.ceil(rows.length / 7));
  return (
    <div ref={ref} className="chart-sec">
      {rows.length > 0 && (
        <BarChart
          label="Spend by day"
          points={points}
          width={width}
          max={max}
          ticks={[0, max / 2, max]}
          fmt={(v) => `$${Math.round(v)}`}
          xLabels={rows.flatMap((r, i) => (i % every === 0 && r.day ? [[i, new Date(`${r.day}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })] as const] : []))}
        />
      )}
    </div>
  );
}

function AgentOpen({ where, bot }: { where: UsageWhere; bot: string }) {
  const q = useQuery(agentUsageWhereQuery(where, bot));
  const u = q.data;
  if (!u?.totals) return null;
  const t = u.totals;
  const parts = [
    ['Cache reads', t.cached_tokens],
    ['Cache writes', t.cache_write_tokens],
    ['New input', t.input_tokens],
    ['Output', t.output_tokens],
  ] as const;
  const all = Math.max(1, tokens(t));
  // The share of input read from cache, as the old app and the mock give it (ui/app/usage.ts useCacheRate).
  const input = t.input_tokens + t.cached_tokens + t.cache_write_tokens;
  const cache = input ? t.cached_tokens / input : null;
  return (
    <>
      <div className="u-sum">
        <span>
          <b>{usd(spent(t))}</b> API-equivalent
        </span>
        <span>
          <b>{t.runs}</b> runs
        </span>
        <span>
          <b>{fmtTok(tokens(t))}</b> tokens
        </span>
        {cache != null && (
          <span>
            <b>{cache > 0 && cache < 0.005 ? '<1%' : `${Math.round(cache * 100)}%`}</b> from cache
          </span>
        )}
      </div>
      <div className="tokbar" role="img" aria-label="Tokens by kind">
        {parts.map(([k, n], i) => (n > 0 ? <i key={k} className={`tok-${i}`} style={{ width: `${((n / all) * 100).toFixed(1)}%` }} /> : null))}
      </div>
      <div className="tok-key">
        {parts.map(([k, n], i) => (
          <span key={k}>
            <i className={`tok-${i}`} />
            {k} <b>{fmtTok(n)}</b>
          </span>
        ))}
      </div>
      {(u.tasks ?? []).length > 0 && (
        <>
          <div className="row g-utask u-cols" aria-hidden="true">
            <span>Task</span>
            <span>Runs</span>
            <span>Tokens</span>
            <span>Share</span>
            <span>Output</span>
            <span>Spent</span>
          </div>
          {u.tasks.map((r, i) => (
            <div key={r.value ?? i} className="row g-utask" title={r.name ?? ''}>
              <span className="row-title">{r.name ?? r.value}</span>
              <span className="row-fig">{r.runs}</span>
              <span className="row-fig">{fmtTok(tokens(r))}</span>
              <span className="row-fig">{r.share != null ? `${Math.round(r.share * 100)}%` : ''}</span>
              <span className="row-fig">{fmtTok(r.output_tokens)}</span>
              <span className="row-fig is-strong">{usd(spent(r))}</span>
            </div>
          ))}
        </>
      )}
    </>
  );
}

function ByAgent({ search, where }: { search: UsageSearch; where: UsageWhere }) {
  const by = useQuery(usageWhereQuery(where, 'bot'));
  const dir = useQuery(directoryQuery);
  const rows = [...(by.data?.rows ?? [])].sort((a, b) => spent(b) - spent(a));
  const top = Math.max(1e-9, ...rows.map(spent));
  if (rows.length === 0) return <Empty title="No runs in this range" />;
  const totals = by.data?.totals;
  return (
    <>
      <div className="tbl-head g-use" aria-hidden="true">
        <span />
        <span>Agent</span>
        <span>Team</span>
        <span className="is-num">Runs</span>
        <span className="is-num">Tokens</span>
        <span />
        <span className="is-num">Spent</span>
      </div>
      {rows.map((r) => {
        const slug = r.bot ?? '';
        const who = dir.data?.byId[slug] ?? dir.data?.byId[`bot:${slug}`];
        const name = who?.name ?? r.name ?? slug;
        const isOpen = search.open === slug;
        const row = (
          <Link
            key={slug}
            to="/company/usage"
            search={{ ...search, open: isOpen ? undefined : slug }}
            className="row g-use"
            data-target={`agent:${slug}`}
            aria-expanded={isOpen}
            title={name}
          >
            <Avatar kind="agent" name={name} hue={who?.hue} decorative />
            <span className="row-title">{name}</span>
            <span className="row-cell">{who?.team ?? ''}</span>
            <span className="row-fig">{r.runs}</span>
            <span className="row-fig u-tok">{fmtTok(tokens(r))}</span>
            <Meter pct={(spent(r) / top) * 100} />
            <span className="row-fig is-strong">{usd(spent(r))}</span>
          </Link>
        );
        return isOpen ? (
          <div key={slug} className="open">
            {row}
            <AgentOpen where={where} bot={slug} />
          </div>
        ) : (
          row
        );
      })}
      {totals && (
        <div className="row g-use is-total">
          <span />
          <span className="row-title">Total</span>
          <span className="row-cell" />
          <span className="row-fig">{totals.runs}</span>
          <span className="row-fig u-tok">{fmtTok(tokens(totals))}</span>
          <span />
          <span className="row-fig is-strong">{usd(spent(totals))}</span>
        </div>
      )}
    </>
  );
}

function ByModel({ where }: { where: UsageWhere }) {
  const by = useQuery(usageWhereQuery(where, 'model'));
  const rows: UsageRow[] = [...(by.data?.rows ?? [])].sort((a, b) => spent(b) - spent(a));
  const top = Math.max(1e-9, ...rows.map(spent));
  if (rows.length === 0) return <Empty title="No runs in this range" />;
  return (
    <>
      <div className="tbl-head g-mod" aria-hidden="true">
        <span>Model</span>
        <span />
        <span className="is-num">Runs</span>
        <span className="is-num">Tokens</span>
        <span />
        <span className="is-num">Spent</span>
      </div>
      {rows.map((r, i) => (
        <div key={r.value ?? i} className="row g-mod" title={r.name ?? r.value ?? ''}>
          <span className="row-title">{r.name ?? r.value ?? 'Unknown'}</span>
          <span className="row-cell" />
          <span className="row-fig">{r.runs}</span>
          <span className="row-fig u-tok">{fmtTok(tokens(r))}</span>
          <Meter pct={(spent(r) / top) * 100} />
          <span className="row-fig is-strong">{usd(spent(r))}</span>
        </div>
      ))}
    </>
  );
}

/* ---------------------------------------------------------------- custom range, filter, CSV */

/** A UTC day ('2026-11-19') as the date picker's local midnight, and back. */
const dayDate = (s: string) => new Date(`${s}T00:00:00`);
const dayOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const FILTER_LABEL: Record<(typeof USAGE_FILTERS)[number], string> = {
  department: 'Team',
  harness: 'Harness',
  model: 'Model',
  effort: 'Effort',
  subscription: 'Plan',
};

/** The filter icon's card: a team, then each dimension with a choice to make in it (ui/app/usage.ts useFilterMenu). */
function UsageFilter({ where, filter, onChange }: { where: UsageWhere; filter: Record<string, string>; onChange: (f: Record<string, string>) => void }) {
  const [open, setOpen] = useState(false);
  const choices = useQuery({ ...usageChoicesQuery(where), enabled: open });
  const on = USAGE_FILTERS.filter((k) => filter[k]).length;
  const c = choices.data;
  const values = (k: (typeof USAGE_FILTERS)[number]) => {
    const got = k === 'department' ? (c?.departments ?? []) : (c?.dimensions[k] ?? []);
    return [...new Set([...got, ...(filter[k] ? [filter[k]] : [])])].filter(Boolean).sort();
  };
  const label = (k: string, v: string) => c?.labels[k]?.[v] ?? (v === '__unknown__' ? 'Not recorded' : v);
  return (
    <Popover
      trigger={<IconButton icon="filter" label={on ? `Filter, ${on} on` : 'Filter'} on={on > 0 || undefined} quiet />}
      className="u-filter"
      label="Filter usage"
      open={open}
      onOpenChange={setOpen}
      align="end"
    >
      <div className="set-add">
        {USAGE_FILTERS.map((k) => {
          const vs = values(k);
          if (!filter[k] && vs.length < 2) return null;
          return (
            <Select
              key={k}
              label={FILTER_LABEL[k]}
              look="field"
              value={filter[k] ?? ''}
              options={[{ value: '', label: 'All' }, ...vs.map((v) => ({ value: v, label: label(k, v) }))]}
              onValueChange={(v) => onChange({ ...filter, [k]: v })}
            />
          );
        })}
        {c && USAGE_FILTERS.every((k) => !filter[k] && values(k).length < 2) && <p className="u-more">Nothing to narrow in this range</p>}
        {on > 0 && <Verb onClick={() => onChange({})}>Reset</Verb>}
      </div>
    </Popover>
  );
}

const CSV_FIGS = ['runs', 'input_tokens', 'cached_tokens', 'cache_write_tokens', 'output_tokens', 'est_cost_usd', 'subscription_equiv_usd'] as const;

/** Rows as CSV, in the units the API returns; a cell with a comma, quote or line break is quoted. */
export function usageCsv(head: readonly string[], rows: readonly Record<string, unknown>[]): string {
  const cell = (v: unknown) => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [head.join(','), ...rows.map((r) => head.map((k) => cell(r[k])).join(','))].join('\n') + '\n';
}

function save(name: string, text: string) {
  if (typeof URL.createObjectURL !== 'function') return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** Download CSV: the open agent's days (as the old app's Export CSV), else the list on screen. */
async function download(where: UsageWhere, search: UsageSearch, rows: UsageRow[], from: string, to: string) {
  try {
    if (search.open && search.view !== 'limits') {
      const d = await agentDays(where, search.open);
      return save(`usage-${search.open}-${d.from}-${d.to}.csv`, usageCsv(['day', ...CSV_FIGS], d.daily));
    }
    const key = search.by === 'model' ? 'value' : 'bot';
    save(`usage-${search.by === 'model' ? 'models' : 'agents'}-${from}-${to}.csv`, usageCsv([key, 'name', ...CSV_FIGS], rows as unknown as Record<string, unknown>[]));
  } catch {
    toast({ text: "Couldn't download", kind: 'failed' });
  }
}

export function UsagePage({ search }: { search: UsageSearch }) {
  const range = search.range ?? '7d';
  const limits = search.view === 'limits';
  const [span, setSpan] = useState<{ from: string; to: string } | null>(null);
  const [filter, setFilter] = useState<Record<string, string>>({});
  const where: UsageWhere = { range, ...(span ?? {}), filter };
  const main = useQuery(usageWhereQuery(where, 'bot'));
  const days = useQuery(usageWhereQuery(where, 'day'));
  const listed = useQuery({ ...usageWhereQuery(where, 'model'), enabled: !limits && search.by === 'model' });
  const lim = useQuery({ ...limitsQuery, enabled: limits });
  const q = limits ? lim : main;
  const t = main.data?.totals;
  const spark = (days.data?.rows ?? []).map(spent);
  const pickRange = (r: UsageRange | 'custom') => {
    if (r === 'custom') return setSpan(span ?? rangeDays(range));
    setSpan(null);
    go({ ...search, range: r, open: undefined });
  };
  const setDay = (end: 'from' | 'to', d: Date) => {
    if (!span) return;
    const next = { ...span, [end]: dayOf(d) };
    setSpan(next.from > next.to ? { from: next.to, to: next.from } : next);
  };
  const view = (
    <Segmented
      label="View"
      options={[
        { value: 'spend', label: 'Spend' },
        { value: 'limits', label: 'Limits' },
      ]}
      value={limits ? 'limits' : 'spend'}
      onValueChange={(v) => go({ ...search, view: v === 'limits' ? 'limits' : undefined, open: undefined })}
    />
  );
  const rows = (search.by === 'model' ? listed.data?.rows : main.data?.rows) ?? [];
  const more = limits ? undefined : (
    <PageMore target="usage:more">
      <MenuItem label="Download CSV" icon="download" onSelect={() => void download(where, search, rows, main.data?.from ?? '', main.data?.to ?? '')} />
    </PageMore>
  );
  return (
    <Dashboard
      title="Usage"
      path="/company/usage"
      className="set-page"
      actions={
        <>
          {view}
          {limits && !isForbidden(lim.error) && <AddLimit />}
        </>
      }
      more={more}
      state={q.isPending ? 'loading' : q.isError && !isForbidden(q.error) ? 'fault' : undefined}
      onRetry={() => void q.refetch()}
      figures={
        limits ? undefined : (
          <>
            <div className="u-tools">
              <Segmented label="Range" options={RANGES} value={span ? 'custom' : range} onValueChange={pickRange} />
              {span && (
                <>
                  <DatePick label="From" withTime={false} value={dayDate(span.from)} onValueChange={(d) => setDay('from', d)} />
                  <DatePick label="To" withTime={false} value={dayDate(span.to)} onValueChange={(d) => setDay('to', d)} />
                </>
              )}
              <span className="grow" />
              <UsageFilter where={where} filter={filter} onChange={setFilter} />
            </div>
            {t && (
              <FiguresStrip
                label="Usage"
                figures={[
                  { label: 'API-equivalent', value: usd(spent(t)), spark: spark.length > 1 ? <Sparkline points={spark} label="Spend by day" /> : undefined },
                  { label: 'On plans', value: usd(t.subscription_equiv_usd) },
                  { label: 'Tokens', value: fmtTok(tokens(t)) },
                  { label: 'Runs', value: t.runs.toLocaleString('en-US') },
                ]}
              />
            )}
          </>
        )
      }
      chart={limits ? undefined : <DayChart where={where} />}
      panel={limits && search.open && !isForbidden(lim.error) ? <LimitPanel search={search} /> : undefined}
      widgets={
        isForbidden(q.error) ? (
          <Empty icon="lock" title="Owners and admins only" />
        ) : limits ? (
          <Limits search={search} />
        ) : (
          <Section
            name={search.by === 'model' ? 'By model' : 'By agent'}
            actions={
              <Segmented
                label="Group by"
                options={[
                  { value: 'agent', label: 'Agent' },
                  { value: 'model', label: 'Model' },
                ]}
                value={search.by === 'model' ? 'model' : 'agent'}
                onValueChange={(b) => go({ ...search, by: b === 'model' ? 'model' : undefined, open: undefined })}
              />
            }
          >
            {search.by === 'model' ? <ByModel where={where} /> : <ByAgent search={search} where={where} />}
          </Section>
        )
      }
    />
  );
}
