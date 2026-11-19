import { useQuery } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';
import { directoryQuery, useActor } from '@/data';
import { chatAddress, fmtAge, fmtDate, fmtMoney } from '@/lib';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { SkeletonRows } from '@/kit/Skeleton';
import { OwnerButton } from '@/kit/OwnerButton';
import { Section, SidePanel } from '@/pagekinds';
import { count, monthName, statusWord } from './model';
import { Pfig, Prow, Ptrace, usePress, Who } from './parts';
import { growthChannelQuery, growthManager, growthSourcesQuery, pageOwnersQuery, type GrowthChannel, type GrowthSource } from './queries';

/* What Growth opens beside the board (old ui/app growth-channels.ts, growth-sources.ts dialogs), read only. Changes to
   channels, customers and sources are asked of the Growth Manager in its chat. */

const usd = (v: number | null | undefined, cents = false) => (v == null ? '' : fmtMoney(v, { cents }));
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
            <Section name="Budget">
              <Ptrace name="Budget" sub="" fig={usd(c?.budget)} />
              <Ptrace name="Spend" sub={c?.status === 'no_spend' ? statusWord('no_spend') : ''} fig={usd(c?.cost ?? h?.cost.value)} />
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

const claims = (v: string[] | string | null | undefined) => (Array.isArray(v) ? v : v ? [v] : []).map((x) => x.trim().toLowerCase()).filter(Boolean);

/** The other live sources that claim a number this one is the truth for, when the read names it a conflict; '' when none. */
export function alsoClaimed(read: { sources: GrowthSource[]; conflicts?: string[] } | undefined, s: GrowthSource): string {
  const clash = new Set(claims(read?.conflicts));
  const mine = claims(s.truth_for).filter((n) => clash.has(n));
  if (!mine.length) return '';
  return (read?.sources ?? [])
    .filter((o) => o.id !== s.id && !o.retired_at && claims(o.truth_for).some((n) => mine.includes(n)))
    .map((o) => o.name ?? o.system ?? o.id)
    .join(', ');
}

/** One source: what it is the truth for, how agents reach it, and its last sync (growth-sources.ts gsrcDialog, gsrcResult). */
export function SourcePanel({ id }: { id: string }) {
  const press = usePress();
  const q = useQuery(growthSourcesQuery);
  const s = q.data?.sources.find((x) => x.id === id && !x.retired_at);
  if (!s) return null;
  const also = alsoClaimed(q.data, s);
  const reach = s.reach_kind ? (REACH[s.reach_kind] ?? words(s.reach_kind)) : '';
  return (
    <SidePanel title={s.name ?? s.system ?? s.id} target={`source:${s.id}`} onClose={() => void press('growth.closePanel', {})}>
      <div className="money">
        <Facts label="Source">
          {words(s.truth_for) && <LabelValue label="Truth for">{words(s.truth_for)}</LabelValue>}
          {also && <LabelValue label="Also claimed by">{also}</LabelValue>}
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
