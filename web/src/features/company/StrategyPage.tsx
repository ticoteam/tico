import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { meQuery, useActors } from '@/data';
import { BarChart } from '@/kit/Chart';
import { Pill } from '@/kit/Pill';
import { FoldButton } from '@/kit/SectionHead';
import { Table, Td, Th } from '@/kit/Table';
import { fmtAge, fmtDate } from '@/lib';
import { Dashboard, Document, FiguresStrip, List, Section, type Figure, type PageTab } from '@/pagekinds';
import { GoalsPage, type GoalsSearch } from './GoalsPage';
import { Face, NameOf, teamPath, usdShort } from './parts';
import { brandQuery, groupsQuery, listeningStatsQuery, marketBriefingQuery, marketSpendQuery, strategyQuery, type BrandBody, type Strategy, type Team } from './queries';
import './company.css';

/* Strategy and its tabs, in the mock's order (pages/3-company/company.js strategyPage). Each tab is its own address
   under /company/strategy, so Back and a shared link restore it. Budget belongs to another area: its page comes in
   through `budget`. */

export const STRATEGY_TABS = ['strategy', 'market', 'org', 'goals', 'budget', 'brand'] as const;
export type StrategyTab = (typeof STRATEGY_TABS)[number];
const BASE = '/company/strategy';
const LABEL: Record<StrategyTab, string> = { strategy: 'Strategy', market: 'Market', org: 'Org', goals: 'Goals', budget: 'Budget', brand: 'Brand' };
export const strategyTabs: readonly PageTab[] = STRATEGY_TABS.map((t) => ({ label: LABEL[t], to: t === 'strategy' ? BASE : `${BASE}/${t}`, target: `tab:${t}` }));
const head = (tab: StrategyTab) => ({ title: 'Strategy', path: tab === 'strategy' ? BASE : `${BASE}/${tab}`, tabs: strategyTabs, className: 'co' });

export function StrategyPage({ tab, search = {}, budget }: { tab: StrategyTab; search?: GoalsSearch; budget?: ReactNode }) {
  switch (tab) {
    case 'market':
      return <MarketTab />;
    case 'org':
      return <OrgTab />;
    case 'goals':
      return <GoalsPage head={head('goals')} search={search} />;
    case 'budget':
      return budget ?? <Document {...head('budget')} state="empty" empty={{ title: 'No budget yet' }} body={null} />;
    case 'brand':
      return <BrandTab />;
    default:
      return <StrategyTabBody />;
  }
}

// ---------------------------------------------------------------- Strategy: the page the Strategy owner keeps

const PARTS: [string, string][] = [
  ['serve', 'Who we serve'],
  ['sell', 'What we sell, and for how much'],
  ['proof', 'How we know they want it'],
  ['now', 'Why now'],
  ['know', "What we know that others don't"],
  ['how', 'How we sell'],
  ['not', "What we won't do"],
];

function StrategyTabBody() {
  const q = useQuery(strategyQuery);
  const s = q.data;
  const v = s?.current;
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : v || s?.bets.length ? 'ready' : 'empty';
  return (
    <Document
      {...head('strategy')}
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No strategy yet' }}
      body={s && <StrategyText s={s} />}
    />
  );
}

function StrategyText({ s }: { s: Strategy }) {
  const v = s.current;
  const sections = v?.sections ?? {};
  const extra = Object.entries(v?.sections.extra ?? {});
  const bets = s.bets.filter((b) => b.status !== 'retired');
  return (
    <>
      {v && (
        <div className="read-by">
          <Face id={v.published_by} />
          <b>
            <NameOf id={v.published_by} />
          </b>
          <span>v{v.n}</span>
          <span className="grow" />
          {v.published_at && <span className="row-age">{fmtDate(v.published_at)}</span>}
        </div>
      )}
      {(v?.summary || v?.vision) && <p className="read-lead">{v.summary || v.vision}</p>}
      {bets.length > 0 && (
        <Part name="Bets">
          <ol className="bets">
            {bets.map((b) => {
              const owner = s.goals_by_bet?.[b.id]?.[0]?.owner_id;
              const health = s.health_by_bet?.[b.id];
              return (
                <li key={b.id} className="bet">
                  <span>{b.statement}</span>
                  {b.status === 'proposed' ? <Pill flag>Proposed</Pill> : health === 'red' ? <Pill flag>Behind</Pill> : <span />}
                  {owner ? <Face id={owner} /> : <span />}
                </li>
              );
            })}
          </ol>
        </Part>
      )}
      {PARTS.map(([k, name]) => {
        const body = sections[k];
        return typeof body === 'string' && body.trim() ? (
          <Part key={k} name={name}>
            <Paras text={body} />
          </Part>
        ) : null;
      })}
      {extra.map(([k, x]) => (
        <Part key={k} name={x.title}>
          <Paras text={x.body} />
        </Part>
      ))}
    </>
  );
}

/** A heading of the page that folds what stands under it. */
function Part({ name, children }: { name: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <h2>
        <FoldButton name={name} open={open} onToggle={() => setOpen(!open)} />
      </h2>
      {open && children}
    </>
  );
}

/** Text in paragraphs, as written: one per blank line. */
function Paras({ text }: { text: string }) {
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p, i) => <p key={i}>{p.replace(/^#+\s*/, '')}</p>);
}

// ---------------------------------------------------------------- Market: figures, buyer posts a day, what changed

function MarketTab() {
  const spend = useQuery(marketSpendQuery);
  const brief = useQuery(marketBriefingQuery);
  const posts = useQuery(listeningStatsQuery);
  const segs = spend.data?.segments ?? [];
  const players = segs.flatMap((sg) => (sg.players ?? []).map((p) => ({ ...p, sells: sg.name, us: !!sg.self })));
  const days = (posts.data?.daily ?? []).map((d) => ({ day: d.day, n: Object.values(d.sources ?? {}).reduce((a, b) => a + b, 0) }));
  const items = brief.data?.briefing?.items ?? [];
  const figures: Figure[] = [];
  const total = spend.data?.total?.spend_usd;
  if (total != null) figures.push({ label: 'Market', value: usdShort(total), sub: spend.data?.name ?? undefined, target: 'figure:market' });
  if (players.length) figures.push({ label: 'Organizations', value: players.length, target: 'figure:organizations' });
  if (days.length) figures.push({ label: 'Buyer posts', value: days.at(-1)?.n ?? 0, sub: 'today', target: 'figure:posts' });
  const pending = spend.isPending && brief.isPending;
  const failed = spend.isError && brief.isError;
  const state = pending ? 'loading' : failed ? 'fault' : figures.length || items.length ? 'ready' : 'empty';
  return (
    <Dashboard
      {...head('market')}
      state={state}
      onRetry={() => void Promise.all([spend.refetch(), brief.refetch()])}
      empty={{ title: 'No market read yet' }}
      figures={figures.length > 0 && <FiguresStrip figures={figures} label="Market figures" />}
      chart={days.length > 1 && <PostsChart days={days} />}
      widgets={
        <>
          {items.length > 0 && (
            <Section name="Changes" count={items.length}>
              {items.map((it, i) => (
                <div key={i} className="row chg2">
                  <span className="row-title">{it.text}</span>
                  <span className="row-age">{brief.data?.briefing?.created_at ? fmtAge(brief.data.briefing.created_at) : ''}</span>
                </div>
              ))}
            </Section>
          )}
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
                      <tr key={`${p.entity_id ?? p.name}:${i}`} className={p.us ? 'is-us' : undefined}>
                        <Td>{p.name}</Td>
                        <td className="dim">{p.sells}</td>
                        <Td num>{typeof fig?.value === 'number' ? usdShort(fig.value) : (fig?.value ?? '')}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            </Section>
          )}
        </>
      }
    />
  );
}

/** Buyer posts a day over the last two weeks, from Listening. */
function PostsChart({ days }: { days: { day: string; n: number }[] }) {
  const [width, setWidth] = useState(640);
  const max = Math.max(4, ...days.map((d) => d.n));
  const top = Math.ceil(max / 4) * 4;
  return (
    <section className="stage pk-sec" aria-label="Buyer posts a day">
      <header className="stage-head">
        <h2 className="sec-name">Buyer posts a day</h2>
        <span className="grow" />
        <span className="sec-note">Posts, 14 days</span>
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
          points={days.map((d) => d.n)}
          width={width}
          max={top}
          ticks={[0, top / 2, top]}
          fmt={(v) => String(Math.round(v))}
          xLabels={[
            [0, fmtDate(days[0]?.day ?? '')],
            [days.length - 1, 'Today'],
          ]}
          label="Buyer posts a day"
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- Org: who reports to whom, as a light chart

function OrgTab() {
  const q = useQuery(groupsQuery);
  const me = useQuery(meQuery).data?.id;
  const actors = useActors();
  const all = q.data ?? [];
  const teams = all.filter((g) => !g.parent).sort((a, b) => a.order - b.order);
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : teams.length ? 'ready' : 'empty';
  return (
    <List
      {...head('org')}
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No teams yet' }}
      sections={
        <div className="oc" role="tree" aria-label="Org">
          <header className="oc-root">
            {me && (
              <Link to={teamPath(me) as never} className="oc-me">
                <Face id={me} size={36} />
                <span>
                  <NameOf id={me} />
                </span>
              </Link>
            )}
            <span className="grow" />
            <span className="stage-n">{actors.length} teammates</span>
          </header>
          {teams.map((t) => (
            <OrgTeam key={t.id} team={t} subs={all.filter((g) => g.parent === t.id).sort((a, b) => a.order - b.order)} />
          ))}
        </div>
      }
    />
  );
}

const members = (g: Team) => [...g.people.map((p) => (p.includes(':') ? p : `human:${p}`)), ...g.bot_slugs.map((b) => `bot:${b}`)];

function OrgTeam({ team, subs }: { team: Team; subs: Team[] }) {
  const [open, setOpen] = useState(true);
  const lead = team.lead ?? null;
  const mates = members(team).filter((m) => m !== lead);
  const n = mates.length + subs.reduce((a, s) => a + members(s).length, 0);
  return (
    <section className="oc-team" role="treeitem" aria-expanded={open} aria-label={team.name}>
      <header className="oc-node">
        {lead ? (
          <Link to={teamPath(lead) as never} className="oc-face" aria-label={`${team.name} lead`}>
            <Face id={lead} size={36} />
          </Link>
        ) : (
          <span className="oc-face" />
        )}
        <div className="oc-id">
          <h2>
            <FoldButton name={team.name} count={n} open={open} onToggle={() => setOpen(!open)} />
          </h2>
          {lead && (
            <Link to={teamPath(lead) as never} className="oc-who">
              <NameOf id={lead} />
            </Link>
          )}
        </div>
      </header>
      {open && (
        <div className={subs.length ? 'oc-kids has-subs' : 'oc-kids'} role="group">
          {mates.length > 0 && <Mates ids={mates} />}
          {subs.map((s) => (
            <OrgSub key={s.id} team={s} />
          ))}
        </div>
      )}
    </section>
  );
}

function OrgSub({ team }: { team: Team }) {
  const [open, setOpen] = useState(true);
  const ids = members(team);
  return (
    <>
      <header className="oc-sub">
        <h3>
          <FoldButton name={team.name} count={ids.length} open={open} onToggle={() => setOpen(!open)} />
        </h3>
      </header>
      {open && ids.length > 0 && <Mates ids={ids} />}
    </>
  );
}

function Mates({ ids }: { ids: string[] }) {
  return (
    <div className="oc-row">
      {ids.map((id) => (
        <Mate key={id} id={id} />
      ))}
    </div>
  );
}

function Mate({ id }: { id: string }) {
  return (
    <Link to={teamPath(id) as never} className="oc-mate" role="treeitem">
      <span className="oc-in">
        <Face id={id} size={28} />
        <span className="oc-name">
          <NameOf id={id} />
        </span>
      </span>
    </Link>
  );
}

// ---------------------------------------------------------------- Brand: the published book, in a frame

function BrandTab() {
  const q = useQuery(brandQuery);
  const book = q.data?.published;
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : book ? 'ready' : 'empty';
  return (
    <Document
      {...head('brand')}
      framed
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No brand book yet' }}
      body={
        book && (
          <div className="frame is-inset brand-frame">
            <iframe title="Brand book" srcDoc={brandDoc(book.body)} sandbox="" />
          </div>
        )
      }
    />
  );
}

const esc = (s: string | null | undefined) => (s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const isHex = (s: string | null | undefined) => !!s && /^#[0-9a-f]{3,8}$/i.test(s);

/** The brand book as its own small page. It keeps its own look: its colors come from the book. */
export function brandDoc(b: BrandBody): string {
  const sec = (name: string, inner: string) => (inner ? `<h2>${esc(name)}</h2>${inner}` : '');
  const list = (rows: string[]) => (rows.length ? `<ul>${rows.join('')}</ul>` : '');
  const colors = (b.visual?.colors ?? []).filter((c) => isHex(c.hex));
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:32px 40px;font:15px/1.55 ${(b.visual?.fonts ?? []).map((f) => `"${esc(f)}"`).join(',') || 'system-ui'},system-ui,sans-serif}
h1{font-size:28px;margin:0 0 8px}h2{font-size:15px;margin:28px 0 8px}ul{padding-left:18px;margin:0}li{margin:4px 0}
.sw{display:inline-flex;align-items:center;gap:8px;margin:0 16px 8px 0}.sw i{width:20px;height:20px;border-radius:50%;display:inline-block}
</style></head><body>
<h1>${esc(b.name)}</h1>${b.positioning ? `<p>${esc(b.positioning)}</p>` : ''}
${sec('Voice', list((b.voice ?? []).map((v) => `<li><b>${esc(v.trait)}</b>${v.do ? `. Do: ${esc(v.do)}` : ''}${v.dont ? `. Don't: ${esc(v.dont)}` : ''}</li>`)))}
${sec('Tone', list((b.tone ?? []).map((t) => `<li><b>${esc(t.situation)}</b>: ${esc(t.guide)}</li>`)))}
${sec('Words we use', list((b.words?.use ?? []).map((w) => `<li>${esc(w.word)}${w.not ? `, not ${esc(w.not)}` : ''}</li>`)))}
${sec('Words we avoid', list((b.words?.avoid ?? []).map((w) => `<li>${esc(w.word)}${w.why ? `: ${esc(w.why)}` : ''}</li>`)))}
${sec('Naming', list((b.naming ?? []).map((n) => `<li><b>${esc(n.name)}</b>${n.rule ? `: ${esc(n.rule)}` : ''}</li>`)))}
${sec('Colors', colors.map((c) => `<span class="sw"><i style="background:${c.hex}"></i>${esc(c.name ?? c.hex)}</span>`).join(''))}
${sec('Imagery', b.visual?.imagery ? `<p>${esc(b.visual.imagery)}</p>` : '')}
${sec('Claims we can make', list((b.claims?.allowed ?? []).map((c) => `<li>${esc(c.claim)}${c.proof ? `: ${esc(c.proof)}` : ''}</li>`)))}
${sec('Claims we never make', list((b.claims?.never ?? []).map((c) => `<li>${esc(c)}</li>`)))}
</body></html>`;
}
