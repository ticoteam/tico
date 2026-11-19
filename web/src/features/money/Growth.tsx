import { useQueries, useQuery } from '@tanstack/react-query';
import { fmtAge, fmtMoney } from '@/lib';
import { Button } from '@/kit/Button';
import { Funnel, Lines, Sparkline } from '@/kit/Chart';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { EmptyRow, Table } from '@/kit/Table';
import { Dashboard, FiguresStrip, List, Section, SidePanel, type Figure, type PageTab } from '@/pagekinds';
import { GROWTH } from './head';
import { axis, count, funnelSteps, growthMonths, monShort, monthName, pct, ratio, statusWord } from './model';
import { ActLink, Pfig, Ptrace, usePress } from './parts';
import {
  growthReadQuery,
  growthSourcesQuery,
  segmentsQuery,
  type GrowthChannel,
  type GrowthFig,
  type GrowthMetric,
  type GrowthRead,
  type GrowthSource,
  type Segment,
} from './queries';
import { panelOf, type GrowthSearch } from './search';

/* Growth: read-only, as in the old app. A scope tab for all customers and each segment, a month, and a channel opened
   beside to show where its number comes from. Changes go through the Growth Manager's chat. Money here is dollars. */

const usd = (v: number | null | undefined, cents = false) => (v == null ? '' : fmtMoney(v, { cents }));
/** A figure, or the muted reason it has none. */
const orWhy = (f: GrowthFig | undefined, write: (v: number) => string) => (f?.value != null ? write(f.value) : statusWord(f?.status));
const head = { title: 'Growth', path: GROWTH, className: 'money' } as const;

export function GrowthPage({ search }: { search: GrowthSearch }) {
  if (search.tab === 'sources') return <GrowthSources />;
  return <GrowthBoard search={search} />;
}

function GrowthBoard({ search }: { search: GrowthSearch }) {
  const press = usePress();
  const segments = useQuery(segmentsQuery);
  const sources = useQuery(growthSourcesQuery);
  const read = useQuery(growthReadQuery(search.segment, search.month));
  const segs = segments.data?.segments ?? [];
  const one = segs.find((s) => s.slug === search.segment);
  const funnels = useQueries({ queries: (one ? [] : segs).map((s) => growthReadQuery(s.slug, search.month, false)) });
  const live = (sources.data?.sources ?? []).some((s) => !s.retired_at);
  const state = read.isPending || sources.isPending ? 'loading' : read.isError ? 'fault' : !live ? 'empty' : 'ready';
  const d = read.data;
  const h = d?.headline ?? {};
  const series = d?.series ?? [];
  const months = growthMonths(search.month);
  const month = search.month ?? months[0]!;
  const open = panelOf(search.panel);
  const channel = open?.[0] === 'channel' ? d?.channels.find((c) => c.slug === open[1]) : undefined;
  const spark = (pick: (p: (typeof series)[number]) => number | null) => {
    const pts = series.map(pick).filter((v): v is number => v != null);
    return pts.length > 1 ? <Sparkline points={pts} /> : undefined;
  };

  // A scope tab starts the board clean: no month, no channel open.
  const tabs: PageTab[] = [
    { label: 'All', to: GROWTH, target: 'tab:all' },
    ...segs.map((s) => ({ label: s.name, to: GROWTH, target: `tab:${s.slug}`, search: { segment: s.slug } })),
  ];
  const figures: Figure[] = [
    { label: 'New customers', value: orWhy(h.new_customers, count), spark: spark((p) => p.new_customers), target: 'figure:new' },
    { label: 'Spend', value: orWhy(h.cost, (v) => usd(v)), spark: spark((p) => p.cost), target: 'figure:spend' },
    { label: 'Cost per customer', value: orWhy(h.cac, (v) => usd(v, true)), spark: spark((p) => p.cac), target: 'figure:cac' },
    one
      ? { label: 'LTV to cost', value: orWhy(h.ltv_cac, ratio), target: 'figure:ltv' }
      : { label: 'Payback, months', value: orWhy(h.payback_months, ratio), target: 'figure:payback' },
  ];

  return (
    <Dashboard
      {...head}
      state={state}
      onRetry={() => void read.refetch()}
      empty={{
        title: 'No numbers yet',
        action: (
          <Button variant="primary" onClick={() => void press('growth.openSources', {})}>
            Set up
          </Button>
        ),
      }}
      actions={
        <div className="month-pick" data-target="range:growth">
          <Select
            label="Month"
            labelHidden
            options={months.map((m) => ({ value: m, label: monthName(m) }))}
            value={month}
            onValueChange={(m) => void press('growth.setMonth', { month: m })}
          />
        </div>
      }
      tabs={tabs}
      figures={<FiguresStrip figures={figures} />}
      chart={
        one ? (
          <Section name="Funnel">
            <Funnel steps={funnelSteps(h)} label={`Funnel, ${one.name}`} />
          </Section>
        ) : (
          <div className="two-col">
            {segs.map((s, i) => {
              const f = funnels[i]?.data;
              return (
                <Section key={s.slug} name={`Funnel, ${s.name}`}>
                  {f ? <Funnel steps={funnelSteps(f.headline)} label={`Funnel, ${s.name}`} /> : null}
                </Section>
              );
            })}
          </div>
        )
      }
      widgets={
        d && (
          <>
            <Channels read={d} segs={one ? [] : segs} selected={channel?.slug} />
            <Trend title="New customers, by month" points={series.map((p) => [p.period, p.new_customers] as const)} fmt={(v) => count(v)} />
            <Trend title="Cost per customer, by month" points={series.map((p) => [p.period, p.cac] as const)} fmt={(v) => usd(v, true)} />
            <Retention read={d} />
            <Kpis metrics={d.metrics} />
          </>
        )
      }
      panel={channel && d && <ChannelPanel channel={channel} read={d} month={month} sources={sources.data?.sources ?? []} />}
    />
  );
}

function Channels({ read, segs, selected }: { read: GrowthRead; segs: readonly Segment[]; selected?: string }) {
  const h = read.headline;
  const ltv = h.ltv?.value ?? null;
  // On All the channels stand under the segment that names them; one a segment does not name stands last.
  const named = new Set(segs.flatMap((s) => s.channels ?? []));
  const groups: [string | null, GrowthChannel[]][] = segs.length
    ? [
        ...segs.map((s) => [s.name, read.channels.filter((c) => (s.channels ?? []).includes(c.slug))] as [string, GrowthChannel[]]),
        ['Other', read.channels.filter((c) => !named.has(c.slug))],
      ]
    : [[null, read.channels]];
  const row = (c: GrowthChannel) => (
    <tr key={c.slug} className={c.slug === selected ? 'is-selected' : undefined} data-target={`channel:${c.slug}`}>
      <td className="t-title">
        <ActLink to={GROWTH} search={{ tab: undefined, panel: `channel:${c.slug}` }} action="growth.openChannel" input={{ channel: c.slug }} className="t-link">
          {c.name ?? c.slug}
        </ActLink>
      </td>
      <td className={c.cost ? 'num' : 'num zero'}>{usd(c.cost ?? 0)}</td>
      <td className="num">{count(c.new_customers)}</td>
      <td className="num t-fig">{c.cac != null ? usd(c.cac, true) : <span className="zero">{statusWord(c.status)}</span>}</td>
      <td className="num">{ltv != null && c.cac ? ratio(ltv / c.cac) : ''}</td>
    </tr>
  );
  return (
    <Section name="Channels" count={read.channels.length}>
      <div className="t-scroll">
        <Table label="Channels">
          <colgroup>
            <col />
            <col className="t-col" />
            <col className="t-n" />
            <col className="t-col" />
            <col className="t-col" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Channel</th>
              <th scope="col" className="num">
                Spend
              </th>
              <th scope="col" className="num">
                New
              </th>
              <th scope="col" className="num">
                Cost per customer
              </th>
              <th scope="col" className="num">
                LTV to cost
              </th>
            </tr>
          </thead>
          <tbody>
            {read.channels.length === 0 && <EmptyRow span={5}>No channels yet</EmptyRow>}
            {groups
              .filter(([, list]) => list.length > 0)
              .map(([name, list]) => [
                name && (
                  <tr key={`g:${name}`} className="is-group">
                    <td colSpan={5}>{name}</td>
                  </tr>
                ),
                ...list.map(row),
              ])}
            {read.channels.length > 0 && (
              <tr className="is-total">
                <td className="t-title">Total</td>
                <td className="num">{usd(h.cost?.value ?? null)}</td>
                <td className="num">{count(h.new_customers?.value ?? null)}</td>
                <td className="num">{usd(h.cac?.value ?? null, true)}</td>
                <td className="num">{ratio(h.ltv_cac?.value ?? null)}</td>
              </tr>
            )}
          </tbody>
        </Table>
      </div>
    </Section>
  );
}

/** One figure by month as a line, named at its end. */
function Trend({ title, points, fmt }: { title: string; points: readonly (readonly [string, number | null])[]; fmt: (v: number) => string }) {
  const known = points.filter((p): p is readonly [string, number] => p[1] != null);
  if (known.length < 2) return null;
  const pts = known.map((p) => p[1]);
  const n = pts.length;
  const label = (period: string) => (/^\d{4}-\d{2}/.test(period) ? monShort(period.slice(0, 7)) : period);
  return (
    <Section name={title}>
      <div className="chart-sec">
        <Lines
          series={[{ name: fmt(pts[n - 1]!), end: '', pts, cls: 'l1' }]}
          height={150}
          left={52}
          right={62}
          width={360}
          {...axis(pts)}
          fmt={fmt}
          xl={[
            [0, label(known[0]![0])],
            [n - 1, label(known[n - 1]![0])],
          ]}
          label={title}
        />
      </div>
    </Section>
  );
}

function Retention({ read }: { read: GrowthRead }) {
  const rows = read.retention ?? [];
  if (!rows.length) return null;
  return (
    <Section name="Retention">
      <Table label="Retention">
        <colgroup>
          <col />
          <col className="t-col" />
          <col className="t-n" />
          <col className="t-n" />
          <col className="t-n" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">First order</th>
            <th scope="col" className="num">
              Customers
            </th>
            <th scope="col" className="num">
              Day 30
            </th>
            <th scope="col" className="num">
              Day 90
            </th>
            <th scope="col" className="num">
              Day 180
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.cohort}>
              <td className="t-title">{/^\d{4}-\d{2}$/.test(r.cohort) ? monthName(r.cohort) : r.cohort}</td>
              <td className="num">{count(r.entered)}</td>
              {[r.kept_d30, r.kept_d90, r.kept_d180].map((w, i) => (
                <td key={i} className="num">
                  {w?.value != null ? pct(w.value) : ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
    </Section>
  );
}

const metricValue = (m: GrowthMetric) => {
  if (m.value == null) return '';
  const u = m.unit.toLowerCase();
  if (u === 'usd' || u === 'dollars' || u === '$') return usd(m.value, !Number.isInteger(m.value));
  if (u === 'percent' || u === '%' || u === 'rate') return m.value <= 1 ? pct(m.value) : `${Math.round(m.value)}%`;
  if (u === 'months' || u === 'ratio') return ratio(m.value);
  return count(m.value);
};

function Kpis({ metrics }: { metrics: readonly GrowthMetric[] }) {
  if (!metrics.length) return null;
  return (
    <Section name="KPIs" count={metrics.length}>
      <Table label="KPIs">
        <colgroup>
          <col />
          <col className="t-col" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">KPI</th>
            <th scope="col" className="num">
              Now
            </th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.id} className={m.estimate ? 'is-quiet' : undefined}>
              <td className="t-title">{m.name}</td>
              <td className="num t-fig">{metricValue(m)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Section>
  );
}

const fills = (list: readonly GrowthSource[], what: string) =>
  list
    .filter((s) => !s.retired_at && (s.fills ?? []).includes(what))
    .map((s) => s.name ?? s.system ?? s.id)
    .join(', ');

/** One channel, opened: its cost per customer and where each part of it comes from. */
function ChannelPanel({ channel: c, read, month, sources }: { channel: GrowthChannel; read: GrowthRead; month: string; sources: readonly GrowthSource[] }) {
  const press = usePress();
  const ltv = read.headline.ltv?.value ?? null;
  return (
    <SidePanel title={c.name ?? c.slug} target={`channel:${c.slug}`} onClose={() => void press('growth.closePanel', {})}>
      <div className="money">
        <Pfig big={c.cac != null ? usd(c.cac, true) : statusWord(c.status)} sub={`for a new customer, ${monthName(month)}`} />
        <Section name="Where this comes from">
          <Ptrace name="Spend" sub={fills(sources, 'spend') || statusWord(c.status)} fig={usd(c.cost ?? null)} />
          <Ptrace name="New customers" sub={fills(sources, 'customers') || fills(sources, 'new_customers')} fig={count(c.new_customers)} />
          {ltv != null && <Ptrace name="LTV" sub={fills(sources, 'revenue') || fills(sources, 'net_revenue')} fig={usd(ltv)} />}
          {ltv != null && c.cac ? <Ptrace name="LTV to cost" sub={`${usd(ltv)} over ${usd(c.cac, true)}`} fig={ratio(ltv / c.cac)} /> : null}
        </Section>
      </div>
    </SidePanel>
  );
}

const HEALTH: Record<GrowthSource['health'], string> = { fresh: '', stale: 'Stale', failing: 'Sign in again', waiting: 'Waiting', not_synced: 'Not synced' };
const words = (v: string[] | string | null | undefined) =>
  (Array.isArray(v) ? v : v ? [v] : []).map((x) => x.replace(/_/g, ' ')).map((x, i) => (i ? x : x.charAt(0).toUpperCase() + x.slice(1))).join(', ');

/** Where Growth's numbers come from, and what each word means here. Reading only; the Growth Manager changes them. */
function GrowthSources() {
  const q = useQuery(growthSourcesQuery);
  const segments = useQuery(segmentsQuery);
  const list = (q.data?.sources ?? []).filter((s) => !s.retired_at);
  const defs = (segments.data?.segments ?? []).filter((s) => s.customer_rule);
  return (
    <List
      title="Sources"
      path={`${GROWTH}?tab=sources`}
      crumb={[{ label: 'Growth', to: GROWTH }]}
      className="money"
      state={q.isPending ? 'loading' : q.isError ? 'fault' : 'ready'}
      onRetry={() => void q.refetch()}
      sections={
        <>
          <Section name="Sources" count={list.length}>
            <Table label="Sources">
              <colgroup>
                <col />
                <col className="t-line" />
                <col className="t-line" />
                <col className="t-col" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Feeds</th>
                  <th scope="col">Holds</th>
                  <th scope="col" className="num">
                    Last sync
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.length === 0 && <EmptyRow span={4}>No source connected</EmptyRow>}
                {list.map((s) => (
                  <tr key={s.id}>
                    <td className="t-title">
                      {s.name ?? s.system ?? s.id}
                      {HEALTH[s.health] && <Pill flag={s.health === 'failing'}>{HEALTH[s.health]}</Pill>}
                    </td>
                    <td className="t-words">{words(s.fills)}</td>
                    <td className="t-words">{words(s.truth_for)}</td>
                    <td className="num t-words">{s.last_run_at ? fmtAge(s.last_run_at) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Section>
          {defs.length > 0 && (
            <Section name="Definitions" count={defs.length}>
              {defs.map((s) => (
                <div key={s.slug} className="row def">
                  <span className="row-title">{s.name}</span>
                  <span>{s.customer_rule}</span>
                </div>
              ))}
            </Section>
          )}
        </>
      }
    />
  );
}
