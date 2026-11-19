import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { BarChart } from '@/kit/Chart';
import { Field } from '@/kit/Field';
import { Markdown } from '@/kit/Markdown';
import { MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Table, Td, Th, Tr } from '@/kit/Table';
import { fmtAge, fmtDate } from '@/lib';
import { Dashboard, FiguresStrip, PageMore, Section, SidePanel, type Figure, type PageTab } from '@/pagekinds';
import { Go, PanelLink, useDo, usdShort } from './parts';
import {
  listeningStatsQuery,
  marketBriefingQuery,
  marketCountQuery,
  marketNoteQuery,
  marketSetupQuery,
  marketSpendQuery,
  valueChainQuery,
  type MarketAnswer,
  type MarketBriefing,
  type MarketEstimate,
  type MarketFigure,
  type MarketParam,
  type MarketSegment,
  type MarketSetupState,
  type MarketSpend,
  type ValueChain,
} from './queries';

/* Market (pages/3-company/company.js marketPage; the old ui/app/redesign-market.ts): a Dashboard. Its figures, buyer
   posts a day, what each segment spends, what changed this week, how the market reaches buyers, and the organizations
   in it. A row opens its panel: `?panel=segment:<id>`, `player:<entity id>` (an organization's note), `briefing` (the
   week's briefing in full) and `definition` (what the market counts, and the owner's sentence that sizes it). A market
   never set up asks the owner Market's four questions instead. The map and its lenses are gone: nothing links there. */

type Head = { title: string; path: string; tabs: readonly PageTab[]; className: string };

const METHODS: Record<string, string> = { top_down: 'Top down', bottom_up: 'Bottom up', player_sum: 'Player sum', owner: 'Owner' };
const KINDS: Record<string, string> = { reported: 'Reported', estimated: 'Estimated', claimed: 'Claimed' };
const METRICS: Record<string, string> = { revenue: 'Revenue', arr: 'ARR', customers: 'Customers', seats: 'Seats' };
const CONFIDENCE: Record<string, string> = { high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence' };
const OPEN_TASK = (status: unknown) => typeof status === 'string' && !['done', 'closed', 'declined'].includes(status);

const range = (r: { low_usd?: number | null; high_usd?: number | null }) => (r.low_usd != null && r.high_usd != null ? `${usdShort(r.low_usd)} to ${usdShort(r.high_usd)}` : '');
const spendOf = (s: MarketSegment) => s.size?.spend_usd ?? s.rolled_usd?.spend_usd ?? null;

export function MarketTab({ head, search }: { head: Head; search: { panel?: string } }) {
  const owner = useQuery(meQuery).data?.role === 'owner';
  const spend = useQuery(marketSpendQuery);
  const brief = useQuery(marketBriefingQuery);
  const posts = useQuery(listeningStatsQuery);
  const chain = useQuery(valueChainQuery);
  const count = useQuery(marketCountQuery);
  const setup = useQuery({ ...marketSetupQuery, enabled: owner });
  const open = useDo('company.openPanel');
  const close = useDo('company.closePanel');
  const segs = spend.data?.segments ?? [];
  const players = segs.flatMap((sg) => (sg.players ?? []).map((p) => ({ ...p, sells: sg.name, us: !!sg.self })));
  const daily = posts.data?.daily ?? [];
  const total = spend.data?.total?.spend_usd;
  const items = brief.data?.briefing?.items ?? [];
  const panel = search.panel ?? '';
  const at = panel.indexOf(':');
  const kind = at < 0 ? panel : panel.slice(0, at);
  const ref = panel.slice(at + 1);
  // Never set up: nothing in the market, nothing sized and no briefing yet.
  const blank = count.data === 0 && !segs.length && !items.length;
  const none = <span className="none">None yet</span>;
  // The mock's four figures, each kept with its empty line until the server has it, so the page keeps its shape.
  const figures: Figure[] = [
    { label: 'Market', value: total != null ? usdShort(total) : none, sub: spend.data?.name ?? undefined, target: 'figure:market' },
    { label: 'Reachable', value: none, target: 'figure:reachable' },
    { label: 'Our share', value: none, target: 'figure:share' },
    { label: 'Buyer posts', value: daily.length ? sum(daily.at(-1)?.sources) : none, sub: daily.length ? 'today' : undefined, target: 'figure:posts' },
  ];
  const pending = (spend.isPending && brief.isPending) || (blank && owner && setup.isPending);
  const failed = spend.isError && brief.isError;
  const state = pending ? 'loading' : failed ? 'fault' : blank && !owner ? 'empty' : 'ready';
  const seg = kind === 'segment' ? segs.find((s) => s.id === ref) : undefined;
  return (
    <Dashboard
      {...head}
      more={
        <PageMore target="market:more">
          <MenuItem label="Definition" onSelect={() => void open.run({ panel: 'definition' })} />
        </PageMore>
      }
      state={state}
      onRetry={() => void Promise.all([spend.refetch(), brief.refetch()])}
      empty={{ title: 'Nothing here yet' }}
      figures={!blank && <FiguresStrip figures={figures} label="Market figures" />}
      chart={!blank && daily.length > 1 && <PostsChart daily={daily} />}
      widgets={
        blank ? (
          setup.data && <SetUp setup={setup.data} />
        ) : (
          <>
            {segs.length > 0 && <Segments segs={segs} open={seg?.id} />}
            {items.length > 0 && (
              <Section name="Changes" count={items.length} target="market:changes">
                {items.map((it, i) => (
                  <PanelLink key={i} panel="briefing" className="row chg2">
                    <span className="row-title">{it.text}</span>
                    <span className="row-age">{brief.data?.briefing?.created_at ? fmtAge(brief.data.briefing.created_at) : ''}</span>
                  </PanelLink>
                ))}
              </Section>
            )}
            <Chain chain={chain.data} money={money(spend.data)} />
            {players.length > 0 && (
              <Section name="Organizations" count={players.length}>
                <Table label="Organizations">
                  <thead>
                    <tr>
                      <Th>Organization</Th>
                      <Th>Sells</Th>
                      <Th num>Figure</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {players.map((p, i) => {
                      const fig = p.figures?.find((f) => f.value != null);
                      return (
                        <Tr key={`${p.entity_id ?? p.name}:${i}`} className={p.us ? 'is-us' : undefined} linked={!!p.entity_id} selected={kind === 'player' && p.entity_id === ref}>
                          <Td>{p.entity_id ? <PanelLink panel={`player:${p.entity_id}`}>{p.name}</PanelLink> : p.name}</Td>
                          <td className="dim">{p.sells}</td>
                          <Td num>{typeof fig?.value === 'number' ? usdShort(fig.value) : (fig?.value ?? '')}</Td>
                        </Tr>
                      );
                    })}
                  </tbody>
                </Table>
              </Section>
            )}
          </>
        )
      }
      panel={
        seg ? (
          <SegmentPanel seg={seg} parent={segs.find((s) => s.id === seg.parent)} year={spend.data?.year} owner={owner} onClose={() => void close.run({})} />
        ) : kind === 'player' && ref ? (
          <NotePanel id={ref} onClose={() => void close.run({})} />
        ) : panel === 'briefing' && brief.data?.briefing ? (
          <BriefingPanel briefing={brief.data.briefing} onClose={() => void close.run({})} />
        ) : panel === 'definition' && spend.data ? (
          <DefinitionPanel spend={spend.data} owner={owner} onClose={() => void close.run({})} />
        ) : null
      }
    />
  );
}

const sum = (sources: Record<string, number> | undefined) => Object.values(sources ?? {}).reduce((a, b) => a + b, 0);

/** The money on each value-chain node: a segment's spend, an organization's revenue (redesign-market.ts rdqChainMoney). */
function money(m: MarketSpend | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const s of m?.segments ?? []) {
    const usd = spendOf(s);
    if (usd != null && usd > 0) out.set(s.id, usd);
    for (const p of s.players ?? []) {
      const rev = p.figures?.find((f) => f.metric === 'revenue' && typeof f.value === 'number')?.value;
      if (p.entity_id && typeof rev === 'number' && rev > 0) out.set(p.entity_id, rev);
    }
  }
  return out;
}

// ---------------------------------------------------------------- segments: what each spends

function Segments({ segs, open }: { segs: MarketSegment[]; open?: string }) {
  return (
    <Section name="Segments" count={segs.length} target="market:segments">
      <Table label="Segments">
        <thead>
          <tr>
            <Th>Segment</Th>
            <Th num>Spend</Th>
            <Th num>Growth</Th>
          </tr>
        </thead>
        <tbody>
          {segs.map((s) => {
            const usd = spendOf(s);
            const growth = s.size?.growth_pct;
            return (
              <Tr key={s.id} linked selected={s.id === open} className={s.self ? 'is-us' : undefined}>
                <Td>
                  <PanelLink panel={`segment:${s.id}`} data-target={`segment:${s.id}`}>
                    {s.name}
                  </PanelLink>
                </Td>
                <Td num>{usd != null ? usdShort(usd) : <span className="none">Not sized</span>}</Td>
                <Td num>{typeof growth === 'number' && Number.isFinite(growth) ? `${trim(growth)}%` : ''}</Td>
              </Tr>
            );
          })}
        </tbody>
      </Table>
    </Section>
  );
}

const trim = (n: number) => String(Math.round(n * 10) / 10);

/** A segment opened: how sure the size is, its figures, each estimate with its arithmetic, and the organizations in it.
    The owner accepts an estimate and changes its parameters (market-spend.ts fillPanel). */
function SegmentPanel({ seg, parent, year, owner, onClose }: { seg: MarketSegment; parent?: MarketSegment; year?: string | number | null; owner: boolean; onClose: () => void }) {
  const z = seg.size;
  const estimates = seg.estimates ?? [];
  const overlaps = estimates.map((e) => e.overlaps).find(Boolean) ?? z?.overlaps;
  const spread = estimates.map((e) => e.spread).find(Boolean) ?? z?.spread;
  const growth = z?.growth_pct;
  return (
    <SidePanel title={seg.name} target={`segment:${seg.id}`} onClose={onClose} className="mkt-pnl">
      <section className="rail-sec">
        <div className="mkt-pills">
          {z ? <Pill>{CONFIDENCE[z.confidence ?? ''] ?? 'Sized'}</Pill> : <Pill>Not sized yet</Pill>}
          {seg.disagree && <Pill flag>Disagree</Pill>}
          {seg.self && <Pill>Us</Pill>}
          {z && !z.accepted && <Pill>Proposed</Pill>}
        </div>
        {seg.summary && <p className="mkt-p">{seg.summary}</p>}
        {z?.error && <p className="mkt-p">{z.error}</p>}
        {parent && <Fact k="Within" v={<PanelLink panel={`segment:${parent.id}`}>{parent.name}</PanelLink>} />}
        {z?.spend_usd != null && <Fact k="Spend" v={usdShort(z.spend_usd)} />}
        {z && range(z) && <Fact k="Range" v={range(z)} />}
        {typeof growth === 'number' && Number.isFinite(growth) && <Fact k="Growth" v={`${trim(growth)}%`} />}
        {year != null && z && <Fact k="Year" v={String(year)} />}
      </section>
      {estimates.length > 0 && (
        <section className="rail-sec" aria-label="Estimates">
          <h3>Estimates</h3>
          {estimates.map((e) => (
            <Estimate key={e.id} e={e} accepted={e.status === 'accepted' || (z?.id === e.id && !!z.accepted)} owner={owner} />
          ))}
        </section>
      )}
      {overlaps && <Block name="Overlaps" text={overlaps} />}
      {spread && <Block name="Spread" text={spread} />}
      {(seg.players ?? []).length > 0 && (
        <section className="rail-sec" aria-label="Organizations">
          <h3>Organizations</h3>
          {(seg.players ?? []).map((p, i) => (
            <div key={`${p.entity_id ?? p.name}:${i}`} className="mkt-who">
              {p.entity_id ? <PanelLink panel={`player:${p.entity_id}`}>{p.name}</PanelLink> : <span>{p.name}</span>}
              {(p.figures ?? []).map((f, j) => (
                <span key={j} className="mkt-fig">
                  {figure(f)}
                </span>
              ))}
            </div>
          ))}
        </section>
      )}
    </SidePanel>
  );
}

/** A figure in words: "Revenue $48M, Reported, 2025". */
function figure(f: MarketFigure): string {
  const money = f.metric === 'revenue' || f.metric === 'arr';
  const n = typeof f.value === 'number' ? (money ? usdShort(f.value) : f.value.toLocaleString('en-US')) : null;
  const what = n == null ? (f.text ?? '') : `${METRICS[f.metric ?? ''] ?? f.metric ?? ''} ${n}`.trim();
  return [what, f.kind ? (KINDS[f.kind] ?? f.kind) : '', f.as_of ?? ''].filter(Boolean).join(', ');
}

function Estimate({ e, accepted, owner }: { e: MarketEstimate; accepted: boolean; owner: boolean }) {
  const accept = useDo('market.acceptSize');
  const params = Object.entries(e.params ?? {});
  const evidence = (e.evidence ?? []).filter((x) => x.source_url || x.quote);
  return (
    <div className="mkt-est" data-target={`estimate:${e.id}`}>
      <div className="mkt-est-h">
        <b>{METHODS[e.method] ?? e.method}</b>
        <span className="row-fig">{e.spend_usd != null ? usdShort(e.spend_usd) : (e.error ?? '')}</span>
        <Pill>{accepted ? 'Accepted' : 'Proposed'}</Pill>
        {owner && !accepted && (
          <Button variant="quiet" busy={accept.pending} onClick={() => void accept.run({ size: e.id })}>
            Accept
          </Button>
        )}
      </div>
      {e.spend_usd != null && range(e) && <p className="mkt-arith">{range(e)}</p>}
      {e.arithmetic && <p className="mkt-arith">{e.arithmetic}</p>}
      {e.formula && (
        <p className="mkt-arith">
          <code>{e.formula}</code>
        </p>
      )}
      {params.map(([name, p]) => (
        <Param key={name} size={e.id} name={name} p={p} owner={owner} />
      ))}
      {evidence.map((x, i) => (
        <p key={i} className="mkt-ev">
          {x.source_url && (
            <a href={x.source_url} target="_blank" rel="noreferrer">
              {host(x.source_url)}
            </a>
          )}
          {x.quote && <q>{x.quote}</q>}
        </p>
      ))}
    </div>
  );
}

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
};
const paramValue = (p: MarketParam) => (p && typeof p === 'object' ? p.value : p);

/** One parameter of an estimate: the owner changes its number, which works the estimate out again. */
function Param({ size, name, p, owner }: { size: string; name: string; p: MarketParam; owner: boolean }) {
  const set = useDo('market.setParam');
  const v = paramValue(p);
  const [draft, setDraft] = useState(v == null ? '' : String(v));
  const extra = p && typeof p === 'object' ? p : null;
  const save = () => {
    const value = Number(draft);
    if (draft.trim() === '' || !Number.isFinite(value)) return;
    void set.run({ size, name, value });
  };
  return (
    <div className="mkt-param">
      <code>{name}</code>
      {owner && typeof v === 'number' ? (
        <form
          className="mkt-param-f"
          onSubmit={(ev) => {
            ev.preventDefault();
            save();
          }}
        >
          <Field label={name} labelHidden type="number" value={draft} onValueChange={setDraft} wide={false} />
          <Button type="submit" variant="quiet" busy={set.pending}>
            Save
          </Button>
        </form>
      ) : (
        <span>{v == null ? '' : String(v)}</span>
      )}
      {extra?.judgement && <Pill>Judgement</Pill>}
      {extra?.source && (
        <a href={extra.source} target="_blank" rel="noreferrer" className="mkt-src">
          {host(extra.source)}
        </a>
      )}
    </div>
  );
}

function Fact({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="fact">
      <span>{k}</span>
      <span className="fact-v">{v}</span>
    </div>
  );
}

function Block({ name, text, items }: { name: string; text?: string; items?: readonly string[] | null }) {
  if (!text && !items?.length) return null;
  return (
    <section className="rail-sec" aria-label={name}>
      <h3>{name}</h3>
      {text && <p className="mkt-p">{text}</p>}
      {items && items.length > 0 && (
        <ul className="mkt-list">
          {items.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- what the market counts, and sizing it

/** The market's Definition: its name, then what it counts (market-spend.ts mktDefinitionHtml). For the owner, the
    sentence that asks the Librarian to define and size it, and the sizing task while it is open. */
function DefinitionPanel({ spend, owner, onClose }: { spend: MarketSpend; owner: boolean; onClose: () => void }) {
  const def = spend.definition;
  const size = useDo('market.size');
  const sized = (spend.segments ?? []).some((s) => spendOf(s) != null);
  const [text, setText] = useState(def?.description ?? '');
  const [bad, setBad] = useState(false);
  const params = Object.entries(def?.params ?? {});
  const sizing = spend.sizing;
  const save = () => {
    if (!text.trim()) return setBad(true);
    setBad(false);
    void size.run({ description: text.trim() });
  };
  return (
    <SidePanel
      title="Definition"
      target="panel:definition"
      onClose={onClose}
      foot={
        owner && (
          <Button variant="primary" busy={size.pending} onClick={save}>
            {sized || sizing ? 'Update' : 'Size it'}
          </Button>
        )
      }
    >
      <section className="rail-sec">
        {spend.name && <Fact k="Market" v={spend.name} />}
        {!def && <p className="none">Not defined yet</p>}
        {def?.description && !owner && <p className="mkt-p">{def.description}</p>}
        {def?.year != null && <Fact k="Year" v={String(def.year)} />}
        {def?.geography && <Fact k="Where" v={def.geography} />}
        {def?.currency && <Fact k="Currency" v={def.currency} />}
        {sizing && (
          <Fact
            k="Sizing"
            v={
              <Go to={`/task/${encodeURIComponent(sizing.id)}`} className="fact-clip">
                {sizing.ask?.line || (sizing.turn === 'person' ? 'Waits on you' : 'Under way')}
              </Go>
            }
          />
        )}
      </section>
      {owner && (
        <form
          className="rail-sec"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Field label="The market in a sentence" value={text} onValueChange={setText} placeholder="What small offices spend on cleaning" error={bad ? 'Describe it' : undefined} wide />
        </form>
      )}
      <Block name="Includes" items={def?.includes} />
      <Block name="Excludes" items={def?.excludes} />
      <Block name="Counting rules" items={def?.counting_rules} />
      {params.length > 0 && (
        <section className="rail-sec" aria-label="Shared parameters">
          <h3>Shared parameters</h3>
          {params.map(([name, p]) => (
            <Param key={name} size="" name={name} p={p} owner={false} />
          ))}
        </section>
      )}
    </SidePanel>
  );
}

// ---------------------------------------------------------------- the week's briefing, an organization's note

/** The week's briefing in full: the bets it touches, then its text (market-briefing.ts briefingOpen). */
function BriefingPanel({ briefing, onClose }: { briefing: NonNullable<MarketBriefing['briefing']>; onClose: () => void }) {
  return (
    <SidePanel title={briefing.title} target="panel:briefing" onClose={onClose}>
      <section className="rail-sec">
        {briefing.week && <Fact k="Week" v={briefing.week} />}
        {briefing.created_at && <Fact k="Written" v={fmtDate(briefing.created_at)} />}
        {(briefing.touched ?? []).map((b) => (
          <Fact key={b.id} k={b.n != null ? `Bet ${b.n}` : 'Bet'} v={<span className="fact-clip">{b.statement}</span>} />
        ))}
      </section>
      {briefing.body ? (
        <section className="rail-sec mkt-read">
          <Markdown text={briefing.body} compact />
        </section>
      ) : (
        <section className="rail-sec" aria-label="Changes">
          {(briefing.items ?? []).map((it, i) => (
            <p key={i} className="mkt-p">
              {it.text}
            </p>
          ))}
        </section>
      )}
    </SidePanel>
  );
}

/** An organization's note: what it is, what else it is called, how it relates to others, and what we read about it
    (market-page.ts renderEntity). A relation opens the other organization's note; nothing opens the map. */
function NotePanel({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery(marketNoteQuery(id));
  const n = q.data;
  const e = n?.entity;
  const names = new Map((n?.neighbors ?? []).map((x) => [x.id, x.name]));
  const rels = Object.entries(n?.edges ?? {}).flatMap(([rel, b]) =>
    [...(b.out ?? []), ...(b.in ?? [])].map((edge) => {
      const other = edge.src === id ? edge.dst : edge.src;
      return { rel: rel.replaceAll('_', ' '), other, name: names.get(other) ?? other };
    }),
  );
  const aliases = (e?.aliases ?? []).filter((a) => a && a !== e?.name);
  return (
    <SidePanel title={e?.name ?? 'Organization'} target={`player:${id}`} onClose={onClose}>
      <section className="rail-sec">
        {q.isPending && <p className="none">Loading</p>}
        {q.isError && <p className="none">The note did not load</p>}
        {e && [e.type, e.tier].some(Boolean) && <p className="mkt-kick">{[e.type, e.tier].filter(Boolean).join(', ')}</p>}
        {e?.summary && <p className="mkt-p">{e.summary}</p>}
        {aliases.length > 0 && <Fact k="Also called" v={<span className="fact-clip">{aliases.join(', ')}</span>} />}
      </section>
      {rels.length > 0 && (
        <section className="rail-sec" aria-label="Relations">
          <h3>Relations</h3>
          {rels.map((r, i) => (
            <div key={i} className="went">
              <span>{r.rel}</span>
              <PanelLink panel={`player:${r.other}`}>{r.name}</PanelLink>
            </div>
          ))}
        </section>
      )}
      {(n?.evidence ?? []).length > 0 && (
        <section className="rail-sec" aria-label="Evidence">
          <h3>Evidence</h3>
          {(n?.evidence ?? []).map((x, i) => (
            <blockquote key={i} className="mkt-quote">
              {x.our_read && <p>{x.our_read}</p>}
              {x.quote && <p>{x.quote}</p>}
            </blockquote>
          ))}
        </section>
      )}
    </SidePanel>
  );
}

// ---------------------------------------------------------------- how the market reaches buyers

/** The value chain as a list, layer by layer in order (suppliers, platforms, us and rivals, channels, buyers), each
    organization with its money where the page knows it. No diagram: a row opens its note. */
function Chain({ chain, money }: { chain: ValueChain | undefined; money: Map<string, number> }) {
  const layers = (chain?.layers ?? []).filter((l) => (l.nodes ?? []).length > 0);
  if (!layers.length) return null;
  return (
    <Section name="How it reaches buyers" target="market:chain">
      <ol className="mkt-chain">
        {layers.map((l) => (
          <li key={l.key} aria-label={l.title}>
            <h3 className="mkt-layer">{l.title}</h3>
            {(l.nodes ?? []).map((n) => (
              <PanelLink key={n.id} panel={`player:${n.id}`} className={n.self ? 'row mkt-node is-us' : 'row mkt-node'} data-target={`player:${n.id}`}>
                <span className="row-title">{n.name}</span>
                <span className="row-fig">{money.get(n.id) ? usdShort(money.get(n.id) ?? 0) : ''}</span>
              </PanelLink>
            ))}
          </li>
        ))}
      </ol>
    </Section>
  );
}

// ---------------------------------------------------------------- the four set-up questions

const QUESTIONS: { key: MarketAnswer; q: string; hint: string; eg: string }[] = [
  { key: 'sell', q: 'What do you sell?', hint: 'Your website, or a sentence or two.', eg: 'https://example.com' },
  { key: 'buyers', q: 'Who buys it?', hint: 'The kinds of companies or people, and where they are.', eg: 'Dog groomers in Ohio' },
  { key: 'competitors', q: 'Who do you compete with?', hint: 'Names or websites, one per line.', eg: 'Groomly' },
  { key: 'watch', q: 'What should Market watch?', hint: 'Where your buyers talk, topics, rules or people to follow.', eg: 'r/doggrooming' },
];

/** A market never set up: the owner answers four questions (only the first is needed; the research finds the rest),
    one at a time, and Start research files it to the Librarian. Set aside, it waits with a Continue; filed, it says
    the research is under way with its task (market-page.ts marketBlankHtml). */
function SetUp({ setup }: { setup: MarketSetupState }) {
  const run = useDo('market.setUp');
  const [answers, setAnswers] = useState(setup.answers);
  const [bad, setBad] = useState(false);
  const step = Math.min(Math.max(setup.step, 0), QUESTIONS.length - 1);
  const task = setup.task as { id?: string; status?: string } | null;
  const go = (to: number, action: 'save' | 'skip' | 'finish') => void run.run({ answers, step: to, action });
  if (task?.id && OPEN_TASK(task.status))
    return (
      <Section name="Set up Market" target="market:setup">
        <div className="row mkt-setup" role="status">
          <span className="row-title">Researching your market</span>
          <Go to={`/task/${encodeURIComponent(task.id)}`}>View task</Go>
        </div>
      </Section>
    );
  if (setup.skipped) {
    const answered = QUESTIONS.filter((x) => setup.answers[x.key]?.trim()).length;
    return (
      <Section name="Set up Market" target="market:setup">
        <div className="mkt-ask">
          <p className="mkt-hint">
            {answered} of {QUESTIONS.length} answered
          </p>
          <div className="mkt-acts">
            <Button variant="primary" busy={run.pending} onClick={() => go(step, 'save')}>
              Continue
            </Button>
          </div>
        </div>
      </Section>
    );
  }
  const x = QUESTIONS[step]!;
  const last = step === QUESTIONS.length - 1;
  const next = () => {
    if (!last) return go(step + 1, 'save');
    if (!answers.sell.trim()) return setBad(true);
    go(step, 'finish');
  };
  return (
    <Section name="Set up Market" target="market:setup">
      <form
        className="mkt-ask"
        onSubmit={(e) => {
          e.preventDefault();
          next();
        }}
      >
        <Field
          key={x.key}
          label={x.q}
          area
          value={answers[x.key]}
          onValueChange={(v) => setAnswers({ ...answers, [x.key]: v })}
          placeholder={x.eg}
          autoFocus
          wide
          error={bad && last && !answers.sell.trim() ? 'Say what you sell' : undefined}
        />
        <p className="mkt-hint">
          {x.hint} {step + 1} of {QUESTIONS.length}
        </p>
        <div className="mkt-acts">
          <Button type="submit" variant="primary" busy={run.pending}>
            {last ? 'Start research' : 'Next'}
          </Button>
          {step > 0 && (
            <Button variant="quiet" onClick={() => go(step - 1, 'save')}>
              Back
            </Button>
          )}
          <Button variant="quiet" onClick={() => go(step, 'skip')}>
            Skip
          </Button>
        </div>
      </form>
    </Section>
  );
}

// ---------------------------------------------------------------- buyer posts a day

/** Buyer posts a day over the last two weeks, from Listening, stacked by source: the two biggest sources, then the
    rest together (company.js postsSVG and its legend). */
function PostsChart({ daily }: { daily: { day: string; sources?: Record<string, number> }[] }) {
  const [width, setWidth] = useState(640);
  const totals = new Map<string, number>();
  for (const d of daily) for (const [k, n] of Object.entries(d.sources ?? {})) totals.set(k, (totals.get(k) ?? 0) + n);
  const ranked = [...totals.keys()].sort((a, b) => (totals.get(b) ?? 0) - (totals.get(a) ?? 0));
  const named = ranked.length > 3 ? ranked.slice(0, 2) : ranked;
  const names = ranked.length > 3 ? [...named, 'Other'] : named;
  const stack = (src: Record<string, number> | undefined) => {
    const parts = named.map((k) => src?.[k] ?? 0);
    return ranked.length > 3 ? [...parts, sum(src) - parts.reduce((a, b) => a + b, 0)] : parts;
  };
  const max = Math.max(4, ...daily.map((d) => sum(d.sources)));
  const top = Math.ceil(max / 4) * 4;
  return (
    <section className="stage pk-sec" aria-label="Buyer posts a day">
      <header className="stage-head">
        <h2 className="sec-name">Buyer posts a day</h2>
        <span className="grow" />
        <span className="legend">
          {names.map((n, i) => (
            <span key={n}>
              <i className={`c${i + 1}`} />
              {sourceName(n)}
            </span>
          ))}
        </span>
      </header>
      <div
        className="chart"
        ref={(el) => {
          if (!el) return;
          const ro = new ResizeObserver(([e]) => e && setWidth(Math.max(240, Math.round(e.contentRect.width))));
          ro.observe(el);
          return () => ro.disconnect();
        }}
      >
        <BarChart
          points={daily.map((d) => stack(d.sources))}
          width={width}
          max={top}
          ticks={[0, top / 2, top]}
          fmt={(v) => String(Math.round(v))}
          xLabels={[
            [0, fmtDate(daily[0]?.day ?? '')],
            [daily.length - 1, 'Today'],
          ]}
          label={`Buyer posts a day, by source: ${names.map(sourceName).join(', ')}`}
        />
      </div>
    </section>
  );
}

/** A Listening source as a name: `reddit` is Reddit. */
const sourceName = (k: string) => (k ? k[0]!.toUpperCase() + k.slice(1).replace(/_/g, ' ') : k);
