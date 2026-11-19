import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { directoryQuery, useActor } from '@/data';
import { fmtAge, fmtMoney } from '@/lib';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Lines, Sparkline } from '@/kit/Chart';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { EmptyRow, Table } from '@/kit/Table';
import { Dashboard, FiguresStrip, List, PageMore, Section, type Figure, type PageTab } from '@/pagekinds';
import { GROWTH } from './head';
import { ChannelPanel, ManagerFace, SourcePanel } from './GrowthPanels';
import { axis, count, firstRead, growthMonths, isYm, lastFullMonth, loudHealth, monShort, monthName, pct, ratio, statusWord, valueKind, ymOf } from './model';
import { ActLink, readState, usePhone, usePress } from './parts';
import {
  growthManager,
  growthReadQuery,
  growthSourcesQuery,
  pageOwnersQuery,
  segmentsQuery,
  type GrowthChannel,
  type GrowthCohort,
  type GrowthFig,
  type GrowthHealth,
  type GrowthMetric,
  type GrowthPoint,
  type GrowthRead,
  type GrowthSource,
  type GrowthVerdict,
} from './queries';
import { panelOf, type GrowthSearch } from './search';

/* Growth: one read (GET /api/v2/growth/read) for a scope (All or a segment) and a month, read only, as in the old app.
   Top to bottom: the ask line to the Growth Manager, trust lines when they matter, the headline tiles that fit the
   company, where to bet (channels), trends, retention, KPIs, and the read's health at the foot. A channel opens beside.
   Every change is asked of the Growth Manager in its chat. Money here is dollars. */

const usd = (v: number | null | undefined, cents = false) => (v == null ? '' : fmtMoney(v, { cents }));
const head = { title: 'Growth', path: GROWTH, className: 'money' } as const;

export function GrowthPage({ search }: { search: GrowthSearch }) {
  if (search.tab === 'sources') return <GrowthSources search={search} />;
  return <GrowthBoard search={search} />;
}

const ARROW: Record<'up' | 'down' | 'flat', string> = { up: '↑', down: '↓', flat: '→' };
const ARROW_WORD = { up: 'Up on the last three months', down: 'Down on the last three months', flat: 'Flat on the last three months' };

/** A figure's verdict against its trailing three months; none in a partial month or on too few. */
function Verdict({ v }: { v?: GrowthVerdict | null }) {
  if (!v || v === 'too_few') return null;
  return (
    <span className="gr-verdict" role="img" aria-label={ARROW_WORD[v]} title={ARROW_WORD[v]}>
      {ARROW[v]}
    </span>
  );
}

/** A figure the read cannot give: its status word, never 0. */
const shown = (f: GrowthFig | undefined): f is GrowthFig & { value: number } => f?.value != null && f.status !== 'no_spend';
const leftOut = (f: GrowthFig | undefined) => !f || f.status === 'nobody' || f.status === 'not_supplied';

/** What a tile says under its value: the cohort and how many it rests on, or the trailing average. */
function figSub(f: GrowthFig, write: (v: number) => string) {
  if (!shown(f)) return '';
  if (f.cohort && f.n != null) return `${monShort(f.cohort)} cohort of ${count(f.n)}`;
  if (f.vs_3mo != null) return `avg ${write(f.vs_3mo)}`;
  return '';
}

interface Tile {
  key: string;
  label: string;
  fig: GrowthFig;
  write: (v: number) => string;
  sub?: string;
  spark?: (p: GrowthPoint) => number | null;
}

/** The headline tiles that fit the company: revenue (CAC, LTV to CAC) or retention (activated, kept; never CAC or LTV). */
function tilesOf(d: GrowthRead): Tile[] {
  const h = d.headline;
  const money = (v: number) => usd(v);
  const all: (Tile | null)[] =
    valueKind(d) === 'retention'
      ? [
          { key: 'signups', label: 'Sign-ups', fig: h.signups, write: count, spark: (p) => p.signups },
          { key: 'activated', label: 'Activated', fig: h.activated_rate, write: (v) => pct(v) },
          { key: 'kept', label: 'Kept', fig: h.kept, write: (v) => pct(v), spark: (p) => p.kept },
          (h.cost.value ?? 0) > 0 ? { key: 'cost', label: 'Cost', fig: h.cost, write: money, spark: (p) => p.cost } : null,
        ]
      : [
          { key: 'signups', label: 'Sign-ups', fig: h.signups, write: count, spark: (p) => p.signups },
          { key: 'new', label: 'New customers', fig: h.new_customers, write: count, spark: (p) => p.new_customers },
          { key: 'cost', label: 'Cost', fig: h.cost, write: money, spark: (p) => p.cost },
          { key: 'cac', label: 'CAC', fig: h.cac, write: (v) => usd(v, true), spark: (p) => p.cac },
          h.ltv_cac
            ? {
                key: 'ltv',
                label: 'LTV to CAC',
                fig: h.ltv_cac,
                write: ratio,
                sub: shown(h.payback_months) ? `payback ${ratio(h.payback_months.value)} months` : undefined,
              }
            : null,
          { key: 'kept', label: 'Kept', fig: h.kept, write: (v) => pct(v), spark: (p) => p.kept },
        ];
  return all.filter((t): t is Tile => !!t && !leftOut(t.fig));
}

const metricValue = (m: GrowthMetric) => {
  if (m.value == null) return '';
  const u = m.unit.toLowerCase();
  if (u === 'usd' || u === 'dollars' || u === '$') return usd(m.value, !Number.isInteger(m.value));
  if (u === 'percent' || u === '%' || u === 'rate') return m.value <= 1 ? pct(m.value) : `${Math.round(m.value)}%`;
  if (u === 'months' || u === 'ratio') return ratio(m.value);
  return count(m.value);
};

function GrowthBoard({ search }: { search: GrowthSearch }) {
  const press = usePress();
  const phone = usePhone();
  const segments = useQuery(segmentsQuery);
  const read = useQuery(growthReadQuery(search.segment, search.month));
  const owners = useQuery(pageOwnersQuery);
  const dir = useQuery(directoryQuery);
  const manager = growthManager(owners.data, dir.data);
  const segs = segments.data?.segments ?? [];
  const d = read.data;
  const series = d?.series ?? [];
  const months = growthMonths(search.month);
  const month = search.month ?? lastFullMonth();
  const thisMonth = ymOf(new Date());
  const open = panelOf(search.panel);
  const opened = open?.[0] === 'channel' ? open[1] : undefined;

  const tiles = d ? tilesOf(d) : [];
  // Before revenue (every funnel tile 0 or left out), the company's own KPIs lead and the funnel folds below them.
  const funnelBlank = tiles.every((t) => !t.fig.value);
  const metrics = d?.metrics ?? [];
  const kpisLead = funnelBlank && metrics.length > 0;
  const anyone = (d?.channels ?? []).some((c) => c.signups > 0 || (c.new_customers ?? 0) > 0);
  const blank = !!d && funnelBlank && !metrics.length && !anyone;
  const loud = loudHealth(d?.health);

  const spark = (pick?: (p: GrowthPoint) => number | null) => {
    if (!pick) return undefined;
    const pts = series.map(pick).filter((v): v is number => v != null);
    return pts.length > 1 ? <Sparkline points={pts} /> : undefined;
  };
  const figures: Figure[] = tiles.map((t) => ({
    label: t.label,
    value: shown(t.fig) ? (
      <>
        {t.write(t.fig.value)}
        <Verdict v={t.fig.verdict} />
      </>
    ) : (
      <span className="gr-why">{statusWord(t.fig.status)}</span>
    ),
    sub: t.sub ?? figSub(t.fig, t.write),
    spark: spark(t.spark),
    target: `figure:${t.key}`,
  }));
  const kpiFigures: Figure[] = metrics.slice(0, 6).map((m) => ({
    label: m.name,
    value: metricValue(m) || <span className="gr-why">{statusWord('not_yet')}</span>,
    sub: m.estimate ? 'estimate' : m.vs_prior != null ? `was ${metricValue({ ...m, value: m.vs_prior })}` : '',
    target: `kpi:${m.id}`,
  }));
  const strip = (list: Figure[], label: string) =>
    list.length > 0 && (
      <div className="gr-tiles" style={{ ['--gr-n' as string]: Math.min(list.length, 6) }}>
        <FiguresStrip figures={list} label={label} />
      </div>
    );

  // A scope tab starts the board clean: no month, no channel open. No segments, no tabs.
  const tabs: PageTab[] | undefined = segs.length
    ? [
        { label: 'All', to: GROWTH, target: 'tab:all' },
        ...segs.map((s) => ({ label: s.name, to: GROWTH, target: `tab:${s.slug}`, search: { segment: s.slug } })),
      ]
    : undefined;
  const row = opened ? d?.channels.find((c) => c.slug === opened) : undefined;

  return (
    <Dashboard
      {...head}
      state={readState(read, blank)}
      onRetry={() => void read.refetch()}
      empty={{
        title: 'No numbers yet',
        action: (
          <Button variant="primary" onClick={() => void press(manager ? 'growth.askManager' : 'growth.openSources', {})}>
            Set up
          </Button>
        ),
      }}
      actions={
        <>
          <ManagerFace />
          <div className="month-pick" data-target="range:growth" data-phone>
            <Select
              label="Month"
              labelHidden
              options={months.map((m) => ({ value: m, label: m === thisMonth ? `${monthName(m)}, partial` : monthName(m) }))}
              value={month}
              onValueChange={(m) => void press('growth.setMonth', { month: m })}
            />
            {d?.partial && <span className="gr-partial">partial</span>}
          </div>
        </>
      }
      more={
        <PageMore target="growth:more">
          <MenuItem label="Sources" onSelect={() => void press('growth.openSources', {})} />
        </PageMore>
      }
      tabs={tabs}
      figures={
        d && (
          <div className="gr-top">
            {manager && <Ask />}
            {loud.length > 0 && (
              <div className="gr-loud" role="status">
                {loud.map((line) => (
                  <p key={line}>
                    <Icon name="alert" />
                    {line}
                  </p>
                ))}
              </div>
            )}
            {kpisLead ? strip(kpiFigures, 'KPIs') : strip(figures, 'Headline')}
          </div>
        )
      }
      widgets={
        d && (
          <div className="stack">
            {kpisLead && figures.length > 0 && (
              <Section name="Funnel" defaultOpen={false}>
                {strip(figures, 'Funnel')}
              </Section>
            )}
            <Channels read={d} selected={opened} phone={phone} cac={valueKind(d) !== 'retention'} />
            <Trends read={d} />
            <Retention rows={d.retention ?? []} />
            {!kpisLead ? <Kpis metrics={metrics} /> : metrics.length > 6 && <Kpis metrics={metrics.slice(6)} />}
            <HealthFoot health={d.health} quietOnly={loud.length > 0} />
          </div>
        )
      }
      panel={opened && <ChannelPanel slug={opened} month={month} row={row} />}
    />
  );
}

/** One line to the Growth Manager: the words go to its chat, led by the scope and month on screen, and the chat opens. */
function Ask() {
  const press = usePress();
  const [text, setText] = useState('');
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    if (await press('growth.askManager', { text: text.trim() })) setText('');
  };
  return (
    <form className="gr-ask" onSubmit={(e) => void send(e)} data-target="growth:ask">
      <Field label="Ask the Growth Manager" labelHidden placeholder="Ask the Growth Manager…" value={text} onValueChange={setText} lead="spark" />
      <IconButton icon="send" label="Send" type="submit" />
    </form>
  );
}

/** A channel owner's face, beside its name. */
function OwnerFace({ id }: { id: string }) {
  const a = useActor(id);
  return <Avatar kind={a?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={a?.name ?? 'Owner'} hue={a?.hue} size={16} decorative />;
}

/** Where to bet: each channel's new customers, cost and CAC, budget and shares, in the read's ranking. Unknown last, and
    always shown when it has anyone. On a phone: channel, new, CAC. A retention company has no CAC column. */
function Channels({ read, selected, phone, cac }: { read: GrowthRead; selected?: string; phone: boolean; cac: boolean }) {
  const rows = read.channels.filter((c) => c.slug !== 'unknown' || c.signups > 0 || (c.new_customers ?? 0) > 0);
  const span = (phone ? 2 : 6) + (cac ? 1 : 0);
  const word = (c: GrowthChannel) => <span className="zero">{statusWord(c.status)}</span>;
  return (
    <Section name="Channels" count={rows.filter((c) => c.slug !== 'unknown').length} target="growth:channels">
      <Table label="Channels">
        <colgroup>
          <col />
          <col className="t-n" />
          {!phone && <col className="t-col" />}
          {cac && <col className="t-col" />}
          {!phone && (
            <>
              <col className="t-col" />
              <col className="t-n" />
              <col className="t-n" />
            </>
          )}
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Channel</th>
            <th scope="col" className="num">
              New
            </th>
            {!phone && (
              <th scope="col" className="num">
                Cost
              </th>
            )}
            {cac && (
              <th scope="col" className="num">
                CAC
              </th>
            )}
            {!phone && (
              <>
                <th scope="col" className="num">
                  Budget
                </th>
                <th scope="col" className="num">
                  Cost share
                </th>
                <th scope="col" className="num">
                  New share
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && <EmptyRow span={span}>No channels yet</EmptyRow>}
          {rows.map((c) => {
            const unknown = c.slug === 'unknown';
            return (
              <tr key={c.slug} className={c.slug === selected ? 'is-selected' : undefined} data-target={`channel:${c.slug}`}>
                <td className="t-name">
                  {unknown ? (
                    'Unknown'
                  ) : (
                    <span className="gr-chan">
                      {c.owner && <OwnerFace id={c.owner} />}
                      <ActLink
                        to={GROWTH}
                        search={{ tab: undefined, panel: `channel:${c.slug}` }}
                        action="growth.openChannel"
                        input={{ channel: c.slug }}
                        className="t-link"
                      >
                        {c.name ?? c.slug}
                      </ActLink>
                    </span>
                  )}
                </td>
                <td className="num">{count(c.new_customers)}</td>
                {!phone && <td className="num">{c.status === 'no_spend' || unknown ? '' : usd(c.cost)}</td>}
                {cac && (
                  <td className="num t-fig">
                    {c.cac != null ? (
                      <>
                        {usd(c.cac, true)}
                        <Verdict v={c.verdict} />
                      </>
                    ) : c.status ? (
                      word(c)
                    ) : (
                      ''
                    )}
                  </td>
                )}
                {!phone && (
                  <>
                    <td className="num">
                      {usd(c.budget)}
                      {c.over_budget ? <span className="gr-over">{usd(c.over_budget)} over</span> : null}
                    </td>
                    <td className="num">{pct(c.cost_share)}</td>
                    <td className={unknown ? 'num t-fig' : 'num'}>{pct(c.new_share)}</td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </Table>
    </Section>
  );
}

/** The read's months as lines, two to a row: sign-ups, new customers, CAC (not for a retention company) and kept. */
function Trends({ read }: { read: GrowthRead }) {
  const s = read.series ?? [];
  const retention = valueKind(read) === 'retention';
  const all: [string, string, (p: GrowthPoint) => number | null, (v: number) => string][] = [
    ['signups', 'Sign-ups, by month', (p) => p.signups, (v) => count(v)],
    ['new', 'New customers, by month', (p) => p.new_customers, (v) => count(v)],
    ['cac', 'CAC, by month', (p) => p.cac, (v) => usd(v, true)],
    ['kept', 'Kept, by month', (p) => p.kept, (v) => pct(v)],
  ];
  const charts = all.filter(([k, , pick]) => !(retention && (k === 'new' || k === 'cac')) && s.filter((p) => pick(p) != null).length > 1);
  const pairs = Array.from({ length: Math.ceil(charts.length / 2) }, (_, i) => charts.slice(i * 2, i * 2 + 2));
  return (
    <>
      {pairs.map((pair) => (
        <div key={pair[0]![0]} className="two-col">
          {pair.map(([k, title, pick, fmt]) => (
            <Trend key={k} title={title} points={s.map((p) => [p.period, pick(p)] as const)} fmt={fmt} />
          ))}
        </div>
      ))}
    </>
  );
}

/** One figure by month as a line, named at its end. */
function Trend({ title, points, fmt }: { title: string; points: readonly (readonly [string, number | null])[]; fmt: (v: number) => string }) {
  const have = points.filter((p): p is readonly [string, number] => p[1] != null);
  if (have.length < 2) return null;
  const pts = have.map((p) => p[1]);
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
            [0, label(have[0]![0])],
            [n - 1, label(have[n - 1]![0])],
          ]}
          label={title}
        />
      </div>
    </Section>
  );
}

const WINDOWS = [
  ['kept_d30', 30],
  ['kept_d90', 90],
  ['kept_d180', 180],
] as const;

/** One retention cell: the share kept and how many were old enough to judge, or when it can first be read. */
function KeptCell({ cohort, w, days }: { cohort: GrowthCohort; w: GrowthCohort['kept_d30']; days: number }) {
  if (!w) return <td className="num" />;
  if (w.status === 'not_yet')
    return (
      <td className="num">
        <span className="zero">first read {firstRead(cohort.cohort, days)}</span>
      </td>
    );
  if (w.status === 'too_few')
    return (
      <td className="num">
        <span className="zero">
          {count(w.n)} of {count(cohort.entered)} old enough
        </span>
      </td>
    );
  return (
    <td className="num">
      {pct(w.value)}
      <span className="gr-n">{count(w.n)}</span>
    </td>
  );
}

/** Each cohort kept by day 30, 90 and 180. */
function Retention({ rows }: { rows: readonly GrowthCohort[] }) {
  if (!rows.length) return null;
  return (
    <Section name="Retention" target="growth:retention">
      <div className="gr-ret">
        <Table label="Retention">
          <colgroup>
            <col />
            <col className="t-n" />
            <col className="t-col" />
            <col className="t-col" />
            <col className="t-col" />
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
                <td className="t-name">{isYm(r.cohort) ? monthName(r.cohort) : r.cohort}</td>
                <td className="num">{count(r.entered)}</td>
                {WINDOWS.map(([k, days]) => (
                  <KeptCell key={k} cohort={r} w={r[k]} days={days} />
                ))}
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </Section>
  );
}

function Kpis({ metrics }: { metrics: readonly GrowthMetric[] }) {
  if (!metrics.length) return null;
  return (
    <Section name="KPIs" count={metrics.length} target="growth:kpis">
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
              <td className="t-name">
                {m.name}
                {m.estimate && <span className="gr-est">estimate</span>}
              </td>
              <td className="num t-fig">{metricValue(m)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Section>
  );
}

const FIGURE_NAME = { signups: 'Sign-ups', new_customers: 'New customers', cost: 'Cost' } as const;

/** The read's health, quiet: checks that match (both figures on open), checks not filled, and when nothing is loud, the
    unknown share. */
function HealthFoot({ health: h, quietOnly }: { health: GrowthHealth; quietOnly: boolean }) {
  const checks = h.checks ?? [];
  const unknown =
    !quietOnly && h.unknown_share ? `${pct(h.unknown_share)} of ${h.basis === 'signups' ? 'sign-ups' : 'new customers'} have no known channel` : '';
  if (!checks.some((c) => c.agrees !== false) && !unknown) return null;
  return (
    <div className="gr-health" data-target="growth:health">
      {checks.map((c) =>
        c.agrees === true ? (
          <details key={`${c.figure}:${c.kpi}`} className="gr-match">
            <summary>
              <Icon name="check" />
              Matches {c.kpi}
            </summary>
            <span>
              {FIGURE_NAME[c.figure]}: {count(c.growth)} here, {count(c.outside)} there
            </span>
          </details>
        ) : c.agrees === null ? (
          <p key={`${c.figure}:${c.kpi}`}>{c.kpi} not filled</p>
        ) : null,
      )}
      {unknown && <p>{unknown}</p>}
    </div>
  );
}

const HEALTH: Record<GrowthSource['health'], string> = { fresh: '', stale: 'Stale', failing: 'Sign in again', waiting: 'Waiting', not_synced: 'Not synced' };
const words = (v: string[] | string | null | undefined) =>
  (Array.isArray(v) ? v : v ? [v] : []).map((x) => x.replace(/_/g, ' ')).map((x, i) => (i ? x : x.charAt(0).toUpperCase() + x.slice(1))).join(', ');

/** Where Growth's numbers come from, and what each word means here. Reading only; the Growth Manager changes them. */
function GrowthSources({ search }: { search: GrowthSearch }) {
  const q = useQuery(growthSourcesQuery);
  const open = panelOf(search.panel);
  const picked = open?.[0] === 'source' ? open[1] : undefined;
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
      actions={<ManagerFace />}
      panel={picked && <SourcePanel id={picked} />}
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
                  <tr key={s.id} className={s.id === picked ? 'is-selected' : undefined} data-target={`source:${s.id}`}>
                    <td className="t-name">
                      <ActLink
                        to={GROWTH}
                        search={{ tab: 'sources', panel: `source:${s.id}` }}
                        action="growth.openSource"
                        input={{ source: s.id }}
                        className="t-link"
                      >
                        {s.name ?? s.system ?? s.id}
                      </ActLink>
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
