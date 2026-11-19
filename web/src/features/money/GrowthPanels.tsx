import { useQuery } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';
import { useActor, useDirectory } from '@/data';
import { chatAddress, fmtDate, fmtMoney } from '@/lib';
import { Button } from '@/kit/Button';
import { Lines } from '@/kit/Chart';
import { Empty } from '@/kit/Empty';
import { SkeletonRows } from '@/kit/Skeleton';
import { OwnerButton } from '@/kit/OwnerButton';
import { Section, SidePanel } from '@/pagekinds';
import { axis, count, monShort, monthName, ratio, statusWord } from './model';
import { Pfig, Prow, Ptrace, usePress } from './parts';
import { growthChannelQuery, growthManager, pageOwnersQuery, type GrowthChannel, type GrowthPoint, type GrowthRead } from './queries';

/* What Growth opens beside the board (old ui/app growth-channels.ts), read only. Changes to channels and customers are
   asked of the Growth Manager in its chat. */

const usd = (v: number | null | undefined, cents = false) => (v == null ? '' : fmtMoney(v, { cents }));
/** The Growth Manager's face in the head: it opens its chat beside the page. Nothing when the company has none. */
export function ManagerFace() {
  const owners = useQuery(pageOwnersQuery).data;
  const bot = useDirectory((dir) => growthManager(owners, dir)) ?? null;
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

const CAMPAIGN: Record<string, string> = { none: 'No campaign', other: 'Other' };

/** One channel, opened (`?panel=channel:<slug>`): its budget and spend, its months and its campaigns by name, from its
    own read. `row` is the channel as the board shows it, when the board has it. */
export function ChannelPanel({ slug, month, row }: { slug: string; month: string; row?: GrowthChannel }) {
  const press = usePress();
  const q = useQuery(growthChannelQuery(slug, month));
  const c = row ?? q.data?.channels.find((x) => x.slug === slug);
  const h = q.data?.headline;
  const cac = c?.cac ?? h?.cac.value ?? null;
  const months = [...(q.data?.series ?? [])].filter((p) => p.new_customers != null || p.cost != null).reverse();
  const camps = q.data?.campaigns?.[slug] ?? [];
  return (
    <SidePanel title={c?.name ?? (slug === 'unknown' ? 'Unknown' : slug)} target={`channel:${slug}`} onClose={() => void press('growth.closePanel', {})}>
      <div className="money">
        {q.isPending ? (
          <SkeletonRows rows={4} />
        ) : q.isError ? (
          <Failed what="This channel" retry={q.refetch} />
        ) : (
          <>
            <Pfig big={cac != null ? usd(cac, true) : statusWord(c?.status ?? h?.cac.status)} sub={`for a new customer, ${monthName(month)}`} />
            {q.data && <Origin read={q.data} c={c} />}
            <CostChart name={c?.name ?? slug} series={q.data?.series ?? []} />
            <Section name="Budget">
              <Ptrace name="Budget" sub="" fig={usd(c?.budget)} />
              {c?.over_budget ? <Ptrace name="Over" sub="" fig={usd(c.over_budget)} /> : null}
            </Section>
            <Section name="Months" count={months.length}>
              {months.length ? (
                months.map((m) => (
                  <Prow
                    key={m.period}
                    name={monthName(m.period.slice(0, 7))}
                    sub={m.cac != null ? `${usd(m.cac, true)} each` : ''}
                    fig={count(m.new_customers)}
                  />
                ))
              ) : (
                <p className="row-sub">No months yet</p>
              )}
            </Section>
            {camps.length > 0 && (
              <Section name="Campaigns" count={camps.length}>
                {camps.map((a) => (
                  <Prow
                    key={a.campaign}
                    name={CAMPAIGN[a.campaign] ?? a.campaign}
                    sub={`${count(a.new_customers)} new${a.cac != null ? `, ${usd(a.cac, true)} each` : ''}`}
                    fig={usd(a.cost)}
                  />
                ))}
              </Section>
            )}
          </>
        )}
      </div>
    </SidePanel>
  );
}

/** Where the channel's numbers come from (finance.js channelPanel), from the read itself: when it was read and which
    sources are stale, the sign-ups the new customers came from, and, for a revenue company, LTV and LTV to cost. */
function Origin({ read: d, c }: { read: GrowthRead; c?: GrowthChannel }) {
  const h = d.headline;
  const stale = d.health.stale_sources;
  const cost = c?.cost ?? h.cost.value;
  const fresh = stale.length ? `Stale: ${stale.join(', ')}` : `As of ${fmtDate(d.as_of)}`;
  const days = h.ltv?.window ? Number(h.ltv.window.replace(/\D/g, '')) : 0;
  const ltv = h.ltv?.value;
  const cac = c?.cac ?? h.cac.value;
  return (
    <Section name="Where this comes from">
      <Ptrace name="Spend" sub={c?.status === 'no_spend' ? statusWord('no_spend') : fresh} fig={usd(cost)} />
      <Ptrace name="New customers" sub={c?.signups != null ? `of ${count(c.signups)} sign-ups` : statusWord(h.new_customers.status)} fig={count(c?.new_customers ?? h.new_customers.value)} />
      {ltv != null && <Ptrace name="LTV" sub={days ? `Net revenue per customer, ${days} days` : 'Net revenue per customer'} fig={usd(ltv)} />}
      {h.ltv_cac?.value != null && <Ptrace name="LTV to cost" sub={ltv != null && cac != null ? `${usd(ltv)} over ${usd(cac, true)}` : ''} fig={ratio(h.ltv_cac.value)} />}
    </Section>
  );
}

/** The channel's cost per customer by month, from its read's series; nothing under two months. */
function CostChart({ name, series }: { name: string; series: readonly GrowthPoint[] }) {
  const have = series.filter((p): p is typeof p & { cac: number } => p.cac != null);
  if (have.length < 2) return null;
  const pts = have.map((p) => p.cac);
  const n = pts.length;
  const fmt = (v: number) => usd(v, v % 1 !== 0);
  return (
    <Section name="Cost per customer, by month">
      <div className="chart-sec">
        <Lines
          series={[{ name: usd(pts[n - 1], true), end: '', pts, cls: 'l1' }]}
          width={304}
          height={132}
          left={36}
          right={56}
          {...axis(pts)}
          fmt={fmt}
          xl={[
            [0, monShort(have[0]!.period.slice(0, 7))],
            [n - 1, monShort(have[n - 1]!.period.slice(0, 7))],
          ]}
          label={`${name}, cost per customer by month`}
        />
      </div>
    </Section>
  );
}
