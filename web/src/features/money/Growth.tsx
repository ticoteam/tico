import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useActor, useDirectory } from '@/data';
import { fmtMoney } from '@/lib';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Lines, Sparkline } from '@/kit/Chart';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Select } from '@/kit/Select';
import { EmptyRow, Table } from '@/kit/Table';
import { Dashboard, FiguresStrip, Section, type Figure, type PageTab } from '@/pagekinds';
import { GROWTH } from './head';
import { ChannelPanel, ManagerFace } from './GrowthPanels';
import { axis, count, firstRead, growthMonths, isYm, loudHealth, monShort, monthName, pct, ratio, readDay, statusWord, valueKind } from './model';
import { ActLink, usePhone, usePress } from './parts';
import {
  growthManager,
  growthReadQuery,
  pageOwnersQuery,
  segmentsQuery,
  type GrowthChannel,
  type GrowthCohort,
  type GrowthFig,
  type GrowthHealth,
  type GrowthMetric,
  type GrowthPoint,
  type GrowthRead,
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
          // cents only under $100, where they matter: six tiles leave no room for "$132.61"
          { key: 'cac', label: 'CAC', fig: h.cac, write: (v) => usd(v, v < 100), spark: (p) => p.cac },
          h.ltv_cac
            ? {
                key: 'ltv',
                label: 'LTV to CAC',
                fig: h.ltv_cac,
                write: ratio,
                sub: shown(h.payback_months) ? `${ratio(h.payback_months.value)} mo payback` : undefined,
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
  const d = useSuspenseQuery(growthReadQuery(search.segment, search.month)).data;
  const owners = useQuery(pageOwnersQuery).data;
  const manager = useDirectory((dir) => growthManager(owners, dir)) ?? null;
  const segs = segments.data?.segments ?? [];
  const series = d.series ?? [];
  const month = d.period;
  const day = readDay(d.as_of);
  const asOf = new Date(`${day}T00:00:00`);
  const months = growthMonths(month, asOf);
  const thisMonth = day.slice(0, 7);
  const open = panelOf(search.panel);
  const opened = open?.[0] === 'channel' ? open[1] : undefined;

  const tiles = tilesOf(d);
  // Before revenue (every funnel tile 0 or left out), the company's own KPIs lead and the funnel folds below them.
  const funnelBlank = tiles.every((t) => !t.fig.value);
  const metrics = d.metrics ?? [];
  const kpisLead = funnelBlank && metrics.length > 0;
  const anyone = d.channels.some((c) => c.signups > 0 || (c.new_customers ?? 0) > 0);
  const blank = funnelBlank && !metrics.length && !anyone;
  const loud = loudHealth(d.health);

  // Past four tiles a trend line leaves no room for the figure: the tiles then show figures alone (finance.js: the four
  // figures lose their trend lines in the narrower page).
  const spark = (pick?: (p: GrowthPoint) => number | null) => {
    if (!pick || (!phone && tiles.length > 4)) return undefined;
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
      <div className={list.length > 4 ? 'gr-tiles is-many' : 'gr-tiles'} style={{ ['--gr-n' as string]: Math.min(list.length, 6) }}>
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
  const row = opened ? d.channels.find((c) => c.slug === opened) : undefined;

  return (
    <Dashboard
      {...head}
      state={blank ? 'empty' : 'ready'}
      empty={{
        title: 'No numbers yet',
        // Sources are set up through the Growth Manager; with none, there is nothing to press here.
        action: manager ? (
          <Button variant="primary" onClick={() => void press('growth.askManager', {})}>
            Set up
          </Button>
        ) : undefined,
      }}
      actions={
        <>
          <div className="month-pick" data-target="range:growth" data-phone>
            <Select
              label="Month"
              labelHidden
              options={months.map((m) => ({ value: m, label: m === thisMonth ? `${monthName(m)}, partial` : monthName(m) }))}
              value={month}
              onValueChange={(m) => void press('growth.setMonth', { month: m })}
            />
            {d.partial && <span className="gr-partial">partial</span>}
          </div>
          <ManagerFace />
        </>
      }
      tabs={tabs}
      figures={
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
      }
      widgets={
        <div className="stack">
          {kpisLead && figures.length > 0 && (
            <Section name="Funnel" defaultOpen={false}>
              {strip(figures, 'Funnel')}
            </Section>
          )}
          <Channels read={d} selected={opened} phone={phone} cac={valueKind(d) !== 'retention'} />
          <Trends read={d} />
          <Retention rows={d.retention ?? []} phone={phone} />
          {!kpisLead ? <Kpis metrics={metrics} /> : metrics.length > 6 && <Kpis metrics={metrics.slice(6)} />}
          <HealthFoot health={d.health} quietOnly={loud.length > 0} />
        </div>
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
  // With a channel open beside it the table keeps channel, new, cost and CAC, so the names keep their room; the panel
  // has the rest (finance.js: tables drop columns beside a panel).
  const narrow = phone || !!selected;
  const span = (phone ? 2 : narrow ? 3 : 6) + (cac ? 1 : 0);
  const word = (c: GrowthChannel) => <span className="zero">{statusWord(c.status)}</span>;
  return (
    <Section name="Channels" count={rows.filter((c) => c.slug !== 'unknown').length} target="growth:channels">
      <Table label="Channels">
        <colgroup>
          <col />
          <col className="t-n" />
          {!phone && <col className="t-col" />}
          {/* wide enough for "no customers" */}
          {cac && <col className="t-src" />}
          {!narrow && (
            <>
              <col className="t-col" />
              {/* the shares' heads are two words: 76 less padding is narrower than "Cost share", which ran into its neighbors */}
              <col className="t-col" />
              <col className="t-col" />
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
            {!narrow && (
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
                      {/* a channel with no owner keeps the face's room, so every name starts on one line */}
                      {c.owner ? <OwnerFace id={c.owner} /> : <span className="gr-noface" aria-hidden="true" />}
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
                {!narrow && (
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
  // the fifth: a count, whose axis steps by whole numbers
  const all: [string, string, (p: GrowthPoint) => number | null, (v: number) => string, boolean][] = [
    ['signups', 'Sign-ups, by month', (p) => p.signups, (v) => count(v), true],
    ['new', 'New customers, by month', (p) => p.new_customers, (v) => count(v), true],
    ['cac', 'CAC, by month', (p) => p.cac, (v) => usd(v, true), false],
    ['kept', 'Kept, by month', (p) => p.kept, (v) => pct(v), false],
  ];
  const charts = all.filter(([k, , pick]) => !(retention && (k === 'new' || k === 'cac')) && s.filter((p) => pick(p) != null).length > 1);
  const pairs = Array.from({ length: Math.ceil(charts.length / 2) }, (_, i) => charts.slice(i * 2, i * 2 + 2));
  return (
    <>
      {pairs.map((pair) => (
        <div key={pair[0]![0]} className="two-col">
          {pair.map(([k, title, pick, fmt, whole]) => (
            <Trend key={k} title={title} points={s.map((p) => [p.period, pick(p)] as const)} fmt={fmt} whole={whole} />
          ))}
        </div>
      ))}
    </>
  );
}

/** One figure by month as a line, named at its end. */
function Trend({
  title,
  points,
  fmt,
  whole,
}: {
  title: string;
  points: readonly (readonly [string, number | null])[];
  fmt: (v: number) => string;
  whole: boolean;
}) {
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
          {...axis(pts, whole)}
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
  if (w.status === 'not_yet') {
    const day = firstRead(cohort.cohort, days);
    return (
      <td className="num">
        <span className="zero">{day ? `first read ${day}` : statusWord('not_supplied')}</span>
      </td>
    );
  }
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

/** Each cohort kept by day 30, 90 and 180; on a phone, where 180 would push the table sideways, by day 30 and 90. */
function Retention({ rows, phone }: { rows: readonly GrowthCohort[]; phone: boolean }) {
  if (!rows.length) return null;
  const windows = phone ? WINDOWS.slice(0, 2) : WINDOWS;
  return (
    <Section name="Retention" target="growth:retention">
      <div className="gr-ret">
        <Table label="Retention">
          <colgroup>
            <col />
            <col className="t-n" />
            {windows.map(([k]) => (
              <col key={k} className="t-col" />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th scope="col">First order</th>
              <th scope="col" className="num">
                Customers
              </th>
              {windows.map(([k, days]) => (
                <th key={k} scope="col" className="num">
                  {`Day ${days}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.cohort}>
                <td className="t-name">{isYm(r.cohort) ? monthName(r.cohort) : r.cohort}</td>
                <td className="num">{count(r.entered)}</td>
                {windows.map(([k, days]) => (
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

/** The read's health, quiet: the KPIs it reconciles with, checks not filled, and when nothing is loud, the unknown share. */
function HealthFoot({ health: h, quietOnly }: { health: GrowthHealth; quietOnly: boolean }) {
  const checks = h.checks ?? [];
  const matched = [...new Set(checks.filter((c) => c.agrees === true).map((c) => c.kpi))];
  const unknown =
    !quietOnly && h.unknown_share ? `${pct(h.unknown_share)} of ${h.basis === 'signups' ? 'sign-ups' : 'new customers'} have no known channel` : '';
  // When each source last pushed: one quiet line; a stale one is already said out loud above.
  const pushed = (h.sources ?? []).filter((s) => !s.stale);
  if (!checks.some((c) => c.agrees !== false) && !unknown && !pushed.length) return null;
  return (
    <div className="gr-health" data-target="growth:health">
      {matched.length > 0 && (
        <p className="gr-match">
          <Icon name="check" />
          {`Reconciled with ${matched.join(', ')}`}
        </p>
      )}
      {checks.map((c) => (c.agrees === null ? <p key={`${c.figure}:${c.kpi}`}>{c.kpi} not filled</p> : null))}
      {unknown && <p>{unknown}</p>}
      {pushed.length > 0 && (
        <p>
          Last pushed:{' '}
          {pushed.map((s, i) => (
            <span key={s.source}>
              {i > 0 && ', '}
              {s.name} <Ago at={s.pushed_at ?? s.as_of} />
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
