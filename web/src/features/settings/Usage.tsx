import { useQuery, type QueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useLayoutEffect, useRef, useState } from 'react';
import { refusal, run } from '@/actions';
import type { UsageFigures } from '@/api/schema';
import { directoryQuery } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { BarChart, Sparkline } from '@/kit/Chart';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { Segmented } from '@/kit/Segmented';
import { toast } from '@/kit/Toast';
import { Dashboard, FiguresStrip, Section } from '@/pagekinds';
import { Foot, Meter, Verb, Word, act, isForbidden, usd } from './parts';
import { agentUsageQuery, limitsQuery, usageQuery, type UsageRange, type UsageRow } from './queries';
import './settings.css';

/* Usage (kind: Dashboard; pages/8-settings/more.js). Spend: figures, a bar a day, then the money by agent or by model;
   an agent's row opens its tasks and its tokens. Limits: each agent's monthly limit, changed where it stands. The view,
   the range, the grouping and the opened agent are in the address. */

export interface UsageSearch {
  view?: 'limits';
  range?: UsageRange;
  by?: 'model';
  open?: string;
}

/** For the route's loader: the view's first read. Never throws. */
export const loadUsage = (qc: QueryClient, search: UsageSearch) =>
  search.view === 'limits' ? qc.prefetchQuery(limitsQuery) : qc.prefetchQuery(usageQuery(search.range ?? '7d', 'bot'));

const RANGES = [
  { value: 'today', label: 'Today' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'month', label: 'This month' },
] as const;

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

function DayChart({ range }: { range: UsageRange }) {
  const days = useQuery(usageQuery(range, 'day'));
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

function AgentOpen({ range, bot }: { range: UsageRange; bot: string }) {
  const q = useQuery(agentUsageQuery(range, bot));
  const u = q.data;
  if (!u) return null;
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
      {u.tasks.length > 0 && (
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

function ByAgent({ search, range }: { search: UsageSearch; range: UsageRange }) {
  const by = useQuery(usageQuery(range, 'bot'));
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
            <AgentOpen range={range} bot={slug} />
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

function ByModel({ range }: { range: UsageRange }) {
  const by = useQuery(usageQuery(range, 'model'));
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

function LimitDialog({ who, now, agent, onClose }: { who: string; now: number | null; agent?: string; onClose: () => void }) {
  const [value, setValue] = useState(now != null ? String(now) : '');
  const n = Number(value);
  const ok = value.trim() !== '' && Number.isFinite(n) && n >= 0;
  const save = () => {
    onClose();
    void (agent ? act('usage.setLimit', { agent, monthly: n }) : act('usage.setDefaultLimit', { monthly: n }));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Monthly limit for ${who}`}
      description="When it is reached, new work waits until the month turns over."
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!ok} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Dollars a month" type="number" value={value} onValueChange={setValue} autoFocus />
    </Dialog>
  );
}

/** Cancel the queued automatic wakes and waits, company-wide; asked first. */
function ClearQueue() {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const clear = async () => {
    setBusy(true);
    const r = await run('usage.clearQueue', { scope: 'company' }, { via: 'click' });
    setBusy(false);
    setAsking(false);
    toast(r.ok ? { text: r.did ?? 'Cleared', kind: 'done' } : refusal(r, "Couldn't clear"));
  };
  return (
    <>
      <Foot>
        <Verb onClick={() => setAsking(true)}>Clear queue</Verb>
      </Foot>
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title="Clear the automatic queue?"
        description="Cancels queued automatic wakes and waits. Keeps tasks, people's messages and running work."
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} onClick={() => void clear()}>
              Clear queue
            </Button>
          </>
        }
      />
    </>
  );
}

function Limits({ search }: { search: UsageSearch }) {
  const limits = useQuery(limitsQuery);
  const dir = useQuery(directoryQuery);
  const [editing, setEditing] = useState<{ who: string; now: number | null; agent?: string } | null>(null);
  const v = limits.data;
  if (!v) return null;
  const bots = Object.entries(v.bots).sort((a, b) => (b[1].month_spent ?? 0) - (a[1].month_spent ?? 0));
  return (
    <>
      <Section name="Defaults" target="limit:default">
        <Facts label="Defaults">
          <LabelValue
            label="A new agent"
            onEdit={v.may_edit_default ? () => setEditing({ who: 'a new agent', now: v.default.monthly_usd }) : undefined}
          >
            {v.default.monthly_usd != null ? `${usd(v.default.monthly_usd)} a month` : 'No limit'}
          </LabelValue>
          <LabelValue label="When reached">New work waits</LabelValue>
          <LabelValue label="Count plan runs">{v.default.count_subscription ? 'Yes' : 'No'}</LabelValue>
        </Facts>
        {v.may_edit_default && <ClearQueue />}
      </Section>
      <Section name="Agents" count={bots.length} target="setting:limits">
        {bots.length === 0 ? (
          <Empty title="No agents yet" />
        ) : (
          bots.map(([slug, l]) => {
            const who = dir.data?.byId[slug] ?? dir.data?.byId[`bot:${slug}`];
            const name = who?.name ?? slug;
            const isOpen = search.open === slug;
            const row = (
              <Link
                key={slug}
                to="/company/usage"
                search={{ ...search, open: isOpen ? undefined : slug }}
                className={l.blocked ? 'row g-lim is-bad' : 'row g-lim'}
                data-target={`limit:${slug}`}
                aria-expanded={isOpen}
                title={name}
              >
                <Avatar kind="agent" name={name} hue={who?.hue} decorative />
                <span className="row-title">
                  {name}
                  {l.blocked ? <Word>Paused</Word> : l.percent >= 90 ? <Pill flag>Near</Pill> : null}
                </span>
                <span className="u-bar">
                  <Meter pct={l.percent} over={!!l.blocked} />
                </span>
                <span className="row-fig u-used">{usd(l.month_spent ?? 0)}</span>
                <span className="row-fig l-of">{l.monthly_usd != null ? `of ${usd(l.monthly_usd)}` : 'No limit'}</span>
                <span className="row-cell u-per">month</span>
              </Link>
            );
            if (!isOpen) return row;
            return (
              <div key={slug} className="open">
                {row}
                <Facts label={name}>
                  <LabelValue
                    label="Limit"
                    onEdit={l.may_edit ? () => setEditing({ who: name, now: l.own_monthly_usd ?? l.monthly_usd, agent: slug }) : undefined}
                  >
                    {l.monthly_usd != null ? `${usd(l.monthly_usd)} a month` : 'No limit'}
                    {l.own_monthly_usd == null && l.monthly_usd != null ? ' (the default)' : ''}
                  </LabelValue>
                </Facts>
                {l.may_edit && l.own_monthly_usd != null && (
                  <Foot open>
                    <Verb onClick={() => void act('usage.setLimit', { agent: slug })}>Use the default</Verb>
                  </Foot>
                )}
              </div>
            );
          })
        )}
      </Section>
      {editing && <LimitDialog {...editing} onClose={() => setEditing(null)} />}
    </>
  );
}

export function UsagePage({ search }: { search: UsageSearch }) {
  const range = search.range ?? '7d';
  const limits = search.view === 'limits';
  const main = useQuery(usageQuery(range, 'bot'));
  const days = useQuery(usageQuery(range, 'day'));
  const lim = useQuery({ ...limitsQuery, enabled: limits });
  const q = limits ? lim : main;
  const t = main.data?.totals;
  const spark = (days.data?.rows ?? []).map(spent);
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
  return (
    <Dashboard
      title="Usage"
      path="/company/usage"
      className="set-page"
      actions={view}
      state={q.isPending ? 'loading' : q.isError && !isForbidden(q.error) ? 'fault' : undefined}
      onRetry={() => void q.refetch()}
      figures={
        limits ? undefined : (
          <>
            <div className="u-tools">
              <Segmented label="Range" options={RANGES} value={range} onValueChange={(r) => go({ ...search, range: r, open: undefined })} />
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
      chart={limits ? undefined : <DayChart range={range} />}
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
            {search.by === 'model' ? <ByModel range={range} /> : <ByAgent search={search} range={range} />}
          </Section>
        )
      }
    />
  );
}
