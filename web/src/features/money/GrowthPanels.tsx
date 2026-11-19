import { useQuery } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';
import { directoryQuery, useActor, type Directory } from '@/data';
import { chatAddress, fmtAge, fmtDate, fmtMoney } from '@/lib';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import { OwnerButton } from '@/kit/OwnerButton';
import { Section, SidePanel } from '@/pagekinds';
import { GROWTH } from './head';
import { count, monthName, pct, statusWord } from './model';
import { ActLink, Pfig, Prow, Ptrace, usePress, Who } from './parts';
import {
  channelMonthsQuery,
  growthCustomersQuery,
  growthManager,
  growthSourcesQuery,
  pageOwnersQuery,
  type GrowthCohortMonth,
  type GrowthOrigin,
  type GrowthSource,
} from './queries';

/* What Growth opens beside the board (old ui/app growth-channels.ts, growth-retention.ts, growth-sources.ts dialogs),
   read only. Changes to channels, customers and sources are asked of the Growth Manager in its chat. */

const usd = (v: number | null | undefined, cents = false) => (v == null ? '' : fmtMoney(v, { cents }));
/** /cohorts and /computed hold kept and margin as percents, 0 to 100. */
const of100 = (v: number | null | undefined) => (v == null ? '' : pct(v / 100));

/** The Growth Manager's face in the head: it opens its chat beside the page. Nothing when the company has none. */
export function ManagerFace() {
  const owners = useQuery(pageOwnersQuery);
  const dir = useQuery(directoryQuery);
  const bot = growthManager(owners.data, dir.data);
  const who = useActor(bot);
  const press = usePress();
  const chat = (useSearch({ strict: false }) as { chat?: string }).chat;
  if (!bot) return null;
  const addr = chatAddress(bot, 'agent');
  return (
    <span className="gm-ask" data-phone>
      <OwnerButton
        name={who?.name ?? 'Growth Manager'}
        kind="agent"
        hue={who?.hue}
        live={who?.working}
        on={chat === addr}
        target={`chat:${addr}`}
        onPress={() => void press('growth.askManager', {})}
      />
    </span>
  );
}

/** A small read's fault inside a panel: what did not load, and Try again. */
function Failed({ what, retry }: { what: string; retry: () => unknown }) {
  return (
    <Empty title={`${what} did not load`} icon="alert">
      <Button variant="quiet" onClick={() => void retry()}>
        Try again
      </Button>
    </Empty>
  );
}

/** A channel's months, newest first, each opening its numbers and where they came from (growth-channels.ts gcMonths). */
export function ChannelMonths({ channel }: { channel: string }) {
  const q = useQuery(channelMonthsQuery(channel));
  const rows = [...(q.data?.cohorts ?? [])].sort((a, b) => b.month.localeCompare(a.month));
  return (
    <Section name="Months" count={q.data ? rows.length : undefined}>
      {q.isPending ? (
        <SkeletonRows rows={3} />
      ) : q.isError ? (
        <Failed what="Months" retry={q.refetch} />
      ) : rows.length === 0 ? (
        <p className="row-sub">No months yet</p>
      ) : (
        rows.map((c) => (
          <Prow
            key={c.month}
            name={
              <ActLink
                to={GROWTH}
                search={{ tab: undefined, panel: `month:${channel}:${c.month}` }}
                action="growth.openMonth"
                input={{ channel, month: c.month }}
                className="t-link"
                data-target={`month:${channel}:${c.month}`}
              >
                {monthName(c.month)}
              </ActLink>
            }
            sub={c.quality === 'estimate' ? 'estimate' : ''}
            fig={count(c.new_customers)}
          />
        ))
      )}
    </Section>
  );
}

type Field = 'spend' | 'bot_cost' | 'new_customers' | 'kept_d30' | 'kept_d90' | 'kept_d180' | 'revenue_month' | 'margin_pct';
const FIELDS: readonly [Field, string, (v: number | null) => string][] = [
  ['spend', 'Spend', (v) => usd(v)],
  ['bot_cost', 'Agent cost', (v) => usd(v)],
  ['new_customers', 'New customers', count],
  ['kept_d30', 'Kept at day 30', of100],
  ['kept_d90', 'Kept at day 90', of100],
  ['kept_d180', 'Kept at day 180', of100],
  ['revenue_month', 'Revenue per customer, monthly', (v) => usd(v, true)],
  ['margin_pct', 'Margin', of100],
];

/** Where one number came from, in words (growth-channels.ts gcOriginHint). */
function originWords(o: GrowthOrigin | undefined, dir: Directory | undefined, write: (v: number | null) => string): string {
  if (!o) return '';
  const at = o.at ? `, ${fmtAge(o.at)}` : '';
  const said =
    o.origin === 'synced'
      ? `${o.source ?? 'A source'}${at}`
      : o.origin === 'computed'
        ? `From ${count(o.computed_from ?? 0)} customers${o.sources?.length ? `, ${o.sources.map((s) => s.source).join(', ')}` : ''}`
        : `Typed${o.by ? ` by ${dir?.byId[o.by]?.name ?? 'a teammate'}` : ''}${o.at ? `, ${fmtDate(o.at)}` : ''}`;
  const est = o.quality === 'estimate' ? ', estimate' : '';
  const differs = o.conflict ? `; ${o.conflict.source ?? 'a source'} says ${write(o.conflict.value)}` : '';
  return `${said}${est}${differs}`;
}

/** One channel's month: each number and where it came from (growth-channels.ts gcCohortDialog, read only). */
export function MonthPanel({ channel, name, month }: { channel: string; name: string; month: string }) {
  const press = usePress();
  const q = useQuery(channelMonthsQuery(channel));
  const dir = useQuery(directoryQuery).data;
  const c: GrowthCohortMonth | undefined = q.data?.cohorts.find((r) => r.month === month);
  const known = c ? FIELDS.filter(([f]) => c[f] != null || c.status?.[f]) : [];
  return (
    <SidePanel title={name} target={`month:${channel}:${month}`} onClose={() => void press('growth.openChannel', { channel })}>
      <div className="money">
        {q.isPending ? (
          <SkeletonRows rows={4} />
        ) : q.isError ? (
          <Failed what="This month" retry={q.refetch} />
        ) : !c || known.length === 0 ? (
          <Empty title="Nothing for this month" />
        ) : (
          <>
            <Pfig big={count(c.new_customers) || '0'} sub={`new customers, ${monthName(month)}${c.quality === 'estimate' ? ', estimate' : ''}`} />
            <Section name="Where this comes from">
              {known.map(([f, label, write]) => (
                <Ptrace key={f} name={label} sub={originWords(c.origins?.[f], dir, write) || statusWord(c.status?.[f])} fig={write(c[f])} />
              ))}
            </Section>
          </>
        )}
      </div>
    </SidePanel>
  );
}

/** Who a retention figure counts: still customers and churned ones (growth-retention.ts grxOpen). Keys, never names. */
export function CustomersPanel({ segment, month, day }: { segment?: string; month: string; day: '30' | '90' | '180' }) {
  const press = usePress();
  const q = useQuery(growthCustomersQuery(segment, month));
  const cohort = q.data?.cohorts.find((r) => r.period.startsWith(month));
  const f = cohort?.[`kept_d${day}`];
  const list = f?.customers;
  return (
    <SidePanel title={`${monthName(month)}, day ${day}`} target={`kept:${month}:${day}`} onClose={() => void press('growth.closePanel', {})}>
      <div className="money">
        {q.isPending ? (
          <SkeletonRows rows={4} />
        ) : q.isError ? (
          <Failed what="Customers" retry={q.refetch} />
        ) : !f ? (
          <Empty title="No customer list" />
        ) : (
          <>
            <Pfig
              big={f.value != null ? of100(f.value) : statusWord(f.status)}
              sub={f.denominator ? `${count(f.count ?? null) || 0} of ${count(f.denominator)} kept` : 'kept'}
            />
            {list ? (
              <>
                <Keys name="Still customers" rows={list.still.map((k) => ({ key: k, when: '' }))} />
                <Keys name="Churned" rows={list.churned.map((c) => ({ key: c.key, when: c.churned ? fmtDate(c.churned) : '' }))} />
              </>
            ) : null}
          </>
        )}
      </div>
    </SidePanel>
  );
}

/** A list of customer keys; a long one scrolls in place. */
function Keys({ name, rows }: { name: string; rows: { key: string; when: string }[] }) {
  if (!rows.length) return null;
  const row = (r: { key: string; when: string }) => <Prow name={r.key} fig={r.when} />;
  return (
    <Section name={name} count={rows.length}>
      {rows.length > 50 ? (
        <VirtualList items={rows} getKey={(r) => r.key} estimateSize={36} renderItem={row} className="cust-list" label={name} />
      ) : (
        rows.map((r) => <div key={r.key}>{row(r)}</div>)
      )}
    </Section>
  );
}

const REACH: Record<string, string> = { mcp: 'MCP', credential: 'Sign-in', url: 'Address', person: 'A teammate' };
const MAY: Record<string, string> = { read: 'Read', write: 'Write notes', act: 'Act within rules' };
const LAST: Partial<Record<GrowthSource['health'], string>> = { fresh: 'Synced', stale: 'Stale', failing: 'Failing', waiting: 'Waiting' };
const words = (v: string[] | string | null | undefined) =>
  (Array.isArray(v) ? v : v ? v.split(',') : [])
    .map((x) => x.trim().replace(/_/g, ' '))
    .filter(Boolean)
    .map((x, i) => (i ? x : x.charAt(0).toUpperCase() + x.slice(1)))
    .join(', ');
const host = (ref: string) => {
  try {
    return new URL(ref).hostname;
  } catch {
    return ref;
  }
};

/** One source: what it is the truth for, how agents reach it, and its last sync (growth-sources.ts gsrcDialog, gsrcResult). */
export function SourcePanel({ id }: { id: string }) {
  const press = usePress();
  const q = useQuery(growthSourcesQuery);
  const s = q.data?.sources.find((x) => x.id === id && !x.retired_at);
  if (!s) return null;
  const reach = s.reach_kind ? (REACH[s.reach_kind] ?? words(s.reach_kind)) : '';
  return (
    <SidePanel title={s.name ?? s.system ?? s.id} target={`source:${s.id}`} onClose={() => void press('growth.closePanel', {})}>
      <div className="money">
        <Facts label="Source">
          {words(s.truth_for) && <LabelValue label="Truth for">{words(s.truth_for)}</LabelValue>}
          {words(s.fills) && <LabelValue label="Fills">{words(s.fills)}</LabelValue>}
          {reach && (
            <LabelValue label="Reached by">
              {s.reach_kind === 'person' && s.reach_ref ? (
                <Who id={s.reach_ref} />
              ) : (
                [reach, s.reach_ref && (s.reach_kind === 'url' ? host(s.reach_ref) : s.reach_ref)].filter(Boolean).join(', ')
              )}
            </LabelValue>
          )}
          {s.may && (
            <LabelValue label="Agents may">
              {s.may
                .split(',')
                .map((m) => MAY[m.trim()] ?? m.trim())
                .join(', ')}
            </LabelValue>
          )}
          {s.owner_ref && (
            <LabelValue label="Owner">
              <Who id={s.owner_ref} />
            </LabelValue>
          )}
        </Facts>
        {LAST[s.health] && (
          <Section name="Last sync">
            <Facts label="Last sync">
              <LabelValue label="Status">{LAST[s.health]}</LabelValue>
              {s.last_run_at && <LabelValue label="Last run">{fmtAge(s.last_run_at)}</LabelValue>}
              {s.last_good_run && s.last_good_run !== s.last_run_at && <LabelValue label="Last good run">{fmtAge(s.last_good_run)}</LabelValue>}
              {s.health !== 'fresh' && s.health_since && <LabelValue label="Since">{fmtDate(s.health_since)}</LabelValue>}
              {s.last_error && <LabelValue label="Error">{s.last_error}</LabelValue>}
            </Facts>
          </Section>
        )}
      </div>
    </SidePanel>
  );
}
