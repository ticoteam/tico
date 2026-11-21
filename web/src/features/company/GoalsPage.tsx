import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { configQuery, useActor, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { LineChart } from '@/kit/Chart';
import { DatePick } from '@/kit/DatePick';
import { Field, SettledSearch } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { fmtAge, fmtDate } from '@/lib';
import { List, RecordPanel, SidePanel, type PageTab } from '@/pagekinds';
import { amount, Face, Go, NameOf, PanelLink, PanelSection, Trend, useDo } from './parts';
import {
  goalNeedsQuery,
  goalQuery,
  goalsTreeQuery,
  kpiQuery,
  placedKpisQuery,
  type GoalNeed,
  type GrowthRole,
  type PlacedKpi,
  type Proposal,
  type ProposalCard,
  type RecoveryOption,
  type TreeGoal,
} from './queries';
import type { Kpi, KpiDetail, KpiReadingView } from '@/api/schema';

/* Goals (pages/3-company/company.js goalsBody): a List of the goal tree, the company's top goal as its headline; a
   goal sits under the goal it feeds and each KPI is a quiet line under its goal's title. A goal, a KPI, and the KPI and goal forms open in the panel:
   `?panel=goal:<id>`, `kpi:<id>`, `kpi-new:<goal>`, `kpi-edit:<kpi>`, `kpi-place:<kpi>`, `goal-new`. */

export interface GoalsSearch {
  panel?: string;
}
type Head = { title: string; path: string; tabs: readonly PageTab[]; className: string };

const FLAGS: Record<string, string> = { red: 'Behind', yellow: 'At risk' };
const flag = (status: string | null | undefined) => (status ? FLAGS[status] : undefined);
const KPIS_SHOWN = 3;

export function GoalsPage({ head, search }: { head: Head; search: GoalsSearch }) {
  const tree = useSuspenseQuery(goalsTreeQuery).data;
  const needs = useQuery(goalNeedsQuery).data ?? [];
  const open = useDo('company.openPanel');
  const [find, setFind] = useState('');
  const goals = tree.goals;
  const panel = search.panel ?? '';
  const at = panel.indexOf(':');
  const kind = at < 0 ? panel : panel.slice(0, at);
  const ref = panel.slice(at + 1);
  const top = topGoal(goals);
  const words = find.trim().toLowerCase();
  const shown = words ? goals.filter((g) => [g.title, ...g.kpis.map((k) => k.name)].some((t) => t.toLowerCase().includes(words))) : goals;
  const add = (
    <Button variant={panel ? 'plain' : 'primary'} onClick={() => open.run({ panel: 'goal-new' })}>
      Add goal
    </Button>
  );
  const blank = !goals.length && !needs.length;
  return (
    <List
      {...head}
      // No goals yet: the head keeps only the owner and the empty state holds Add goal (company.js goals, state empty).
      search={!blank && <SettledSearch label="Search goals" placeholder="Search" wait={0} value={find} onSettle={setFind} />}
      actions={
        <>
          {!blank && add}
        </>
      }
      state={blank ? 'empty' : 'ready'}
      empty={{ title: 'No goals yet', action: add }}
      sections={
        <>
          <NeedsLine open={panel === 'needs'} />
          {top && !words && <TopGoal g={top} />}
          <GoalTreeView goals={shown} top={words ? undefined : top?.id} waiting={waiting(tree.proposals)} selected={kind === 'goal' ? ref : null} />
        </>
      }
      panel={
        panel === 'needs' ? (
          <NeedsPanel />
        ) : panel === 'goal-new' ? (
          <GoalForm goals={goals} />
        ) : kind === 'goal' ? (
          <GoalPanel key={ref} gid={ref} />
        ) : kind === 'kpi' ? (
          <KpiPanel key={ref} kid={ref} />
        ) : kind === 'kpi-new' ? (
          <KpiForm key={ref} goal={ref} goals={goals} />
        ) : kind === 'kpi-edit' ? (
          <KpiEdit key={ref} kid={ref} goals={goals} />
        ) : kind === 'kpi-place' ? (
          <KpiPlace key={ref} kid={ref} />
        ) : null
      }
    />
  );
}

// ---------------------------------------------------------------- the tree

type Placed = { goal: TreeGoal; depth: number; last: boolean };

const byRank = (a: TreeGoal, b: TreeGoal) => (a.rank ?? 1e9) - (b.rank ?? 1e9) || a.created_at.localeCompare(b.created_at);

/** The company's own first goal, at the top of the tree: the page's headline (company.js goalLead). */
export function topGoal(goals: readonly TreeGoal[]): TreeGoal | undefined {
  const first = goals.filter((g) => !g.parent_id || !goals.some((x) => x.id === g.parent_id)).sort(byRank)[0];
  return first?.owner_id === 'company' ? first : undefined;
}

/** Goals in tree order: each under the goal it feeds, in rank order. The headline (`top`) is left out and the goals
    that feed it start the tree. */
export function placeGoals(goals: readonly TreeGoal[], top?: string): Placed[] {
  const ids = new Set(goals.map((g) => g.id));
  const kids = new Map<string | null, TreeGoal[]>();
  for (const g of goals) {
    if (g.id === top) continue;
    const p = g.parent_id && ids.has(g.parent_id) && g.parent_id !== top ? g.parent_id : null;
    kids.set(p, [...(kids.get(p) ?? []), g]);
  }
  const out: Placed[] = [];
  const walk = (p: string | null, depth: number) => {
    const list = [...(kids.get(p) ?? [])].sort(byRank);
    list.forEach((g, i) => {
      out.push({ goal: g, depth, last: i === list.length - 1 });
      walk(g.id, depth + 1);
    });
  };
  walk(null, 0);
  return out;
}

/** The headline: the company's name, its top goal, and its first KPI's reading of the target on a thin meter. */
function TopGoal({ g }: { g: TreeGoal }) {
  const company = useQuery(configQuery).data?.company_name;
  const k = g.kpis[0];
  const now = k?.latest?.value;
  const target = k?.link?.target;
  const pct = now != null && target ? Math.min(100, Math.max(0, (now / target) * 100)) : null;
  return (
    <PanelLink panel={`goal:${g.id}`} className="goal-lead" data-target={`goal:${g.id}`}>
      <span className="goal-name">
        {company && <span className="goal-co">{company}</span>}
        <span className="goal-title">{g.title}</span>
      </span>
      {k && now != null && (
        <span className="goal-fig">
          <span>
            <b>{amount(now, k.unit)}</b>
            {target != null && `of ${amount(target, k.unit)}`}
          </span>
          {pct != null && (
            <span className="meter" style={{ '--pct': `${pct}%` } as CSSProperties}>
              <i />
            </span>
          )}
        </span>
      )}
    </PanelLink>
  );
}

function GoalTreeView({ goals, top, waiting: mine, selected }: { goals: readonly TreeGoal[]; top?: string; waiting: readonly Proposal[]; selected: string | null }) {
  const needs = new Set(mine.map((p) => p.goal_id));
  return (
    <div className="gt" aria-label="Goals">
      {placeGoals(goals, top).map(({ goal: g, depth, last }) => {
        const more = g.kpis.length - KPIS_SHOWN;
        return (
          <div key={g.id} className={g.id === selected ? 'gt-row is-selected' : 'gt-row'}>
            <span className="gt-g">{depth > 0 && <i className={last ? 'e' : 't'} />}</span>
            {g.owner_id === 'company' ? <span className="av-none" /> : <Face id={g.owner_id} />}
            <div className="gt-main">
              <PanelLink panel={`goal:${g.id}`} className="gt-title" data-target={`goal:${g.id}`}>
                <span>{g.title}</span>
                {flag(g.status) && <Pill flag>{flag(g.status)}</Pill>}
                {needs.has(g.id) && <FlowMark size={10} />}
              </PanelLink>
              {g.kpis.slice(0, KPIS_SHOWN).map((k) => (
                <PanelLink key={k.id} panel={`kpi:${k.id}`} className="gk" data-target={`kpi:${k.id}`}>
                  <span className="gk-name">{k.name}</span>
                  <KpiFig k={k} className="gk-fig" />
                  <Trend points={k.spark} />
                </PanelLink>
              ))}
              {more > 0 && (
                <PanelLink panel={`goal:${g.id}`} className="gk gk-more">
                  {more} more
                </PanelLink>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** "<b>52%</b> of 65%": the latest reading of the target, or the reading alone. */
function KpiFig({ k, className, target: t }: { k: Kpi; className: string; target?: number | null }) {
  const now = k.latest ? amount(k.latest.value, k.unit) : '';
  const target = t !== undefined ? t : k.link?.target;
  return (
    <span className={className}>
      {now ? <b>{now}</b> : <span>No reading</span>}
      {target != null && ` of ${amount(target, k.unit)}`}
    </span>
  );
}

// ---------------------------------------------------------------- one goal

function useOwners() {
  const actors = useActors();
  return [
    { value: 'company', label: 'Company' },
    ...actors.filter((a) => a.kind === 'person').map((a) => ({ value: a.id, label: a.name })),
    ...actors.filter((a) => a.kind === 'agent').map((a) => ({ value: a.id, label: a.name, meta: 'Agent' })),
  ];
}

/** A goal opened (company.js goal panel): its owner in the head, its title and flag, what it feeds, then its KPIs and
    check-ins as sections that fold. Rename and a new owner are under the panel's ⋯, Mark done too; Archive is the foot. */
function GoalPanel({ gid }: { gid: string }) {
  const tree = useQuery(goalsTreeQuery);
  const detail = useQuery({ ...goalQuery(gid), enabled: !gid.startsWith('tmp:') });
  const close = useDo('company.closePanel');
  const update = useDo('goals.update');
  const shut = useDo('goals.close');
  const owners = useOwners();
  const [editing, setEditing] = useState<{ title: string } | 'owner' | null>(null);
  const fromTree = tree.data?.goals.find((g) => g.id === gid);
  const g = fromTree ?? detail.data?.goal;
  const ownerName = useActor(g?.owner_id)?.name;
  if (!g) return null;
  const kpis = fromTree?.kpis ?? detail.data?.goal.kpis ?? [];
  const parent = tree.data?.goals.find((x) => x.id === g.parent_id);
  const checkins = detail.data?.goal.checkins ?? [];
  const asks = waiting(tree.data?.proposals).filter((p) => p.goal_id === gid);
  const onKpi = (kid: string) => asks.filter((p) => p.kpi_id === kid && p.kind !== 'goal_kpi');
  const newKpis = asks.filter((p) => p.kind === 'goal_kpi');
  const onGoal = asks.filter((p) => !p.kpi_id && p.kind !== 'goal_kpi');
  const done = async (status: 'done' | 'dropped') => {
    if (await shut.run({ goal: gid, status })) void close.run({});
  };
  const rename = async (title: string) => {
    setEditing(null);
    if (title.trim() && title.trim() !== g.title) await update.run({ goal: gid, title: title.trim() });
  };
  const company = g.owner_id === 'company';
  return (
    <RecordPanel
      title={company ? 'Company' : (ownerName ?? 'Goal')}
      target={`goal:${gid}`}
      lead={!company && <Face id={g.owner_id} size={28} />}
      actions={
        <Menu trigger={<IconButton icon="more" label="More" quiet size="sm" />}>
          <MenuItem label="Rename" onSelect={() => setEditing({ title: g.title })} />
          <MenuItem label="Change owner" onSelect={() => setEditing('owner')} />
          <MenuItem label="Mark done" onSelect={() => void done('done')} />
        </Menu>
      }
      onClose={() => void close.run({})}
      foot={
        <Button variant="quiet" busy={shut.pending} onClick={() => done('dropped')}>
          Archive
        </Button>
      }
      facts={
        <div className="rec-flds">
          {editing && typeof editing === 'object' ? (
            <form
              className="kf-rename"
              onSubmit={(e) => {
                e.preventDefault();
                void rename(editing.title);
              }}
            >
              <Field label="Title" labelHidden value={editing.title} onValueChange={(title) => setEditing({ title })} autoFocus />
            </form>
          ) : (
            <h2 className="pnl-title">
              {g.title}
              {flag(g.status) && <Pill flag>{flag(g.status)}</Pill>}
            </h2>
          )}
          {editing === 'owner' && (
            <div className="fact">
              <span>Owner</span>
              <span className="fact-v">
                <Select
                  label="Owner"
                  labelHidden
                  look="quiet"
                  options={owners}
                  value={g.owner_id}
                  onValueChange={(owner) => {
                    setEditing(null);
                    void update.run({ goal: gid, owner });
                  }}
                />
              </span>
            </div>
          )}
          {parent && (
            <div className="fact">
              <span>Feeds</span>
              <span className="fact-v">
                <PanelLink panel={`goal:${parent.id}`} className="fact-clip">
                  {parent.owner_id !== 'company' && <Face id={parent.owner_id} />}
                  <span>{parent.title}</span>
                </PanelLink>
              </span>
            </div>
          )}
          {onGoal.map((p) => (
            <ProposalCardView key={p.id} p={p} />
          ))}
        </div>
      }
      sections={
        <>
          <PanelSection name="KPIs" count={kpis.length + newKpis.length} target={`goal:${gid}:kpis`}>
            {kpis.map((k) => (
              <KpiWithAsks key={k.id} asks={onKpi(k.id)}>
                <PanelLink panel={`kpi:${k.id}`} className="kr" data-target={`kpi:${k.id}`}>
                  <span className="kr-name">
                    {onKpi(k.id).length > 0 && <FlowMark size={10} />}
                    {k.name}
                    {flag(k.status) && <Pill flag>{flag(k.status)}</Pill>}
                  </span>
                  <KpiFig k={k} className="gt-fig" />
                  {k.latest ? (
                    <From r={k.latest} when={fmtAge(k.latest.measured_at ?? k.latest.created_at ?? k.latest.period_end)} />
                  ) : (
                    <span className="kr-src">
                      <Face id={k.owner_id} size={16} />
                      <span>
                        <NameOf id={k.owner_id} />
                      </span>
                    </span>
                  )}
                  <span className="kr-by">{k.link?.deadline ? `by ${fmtDate(k.link.deadline)}` : ''}</span>
                </PanelLink>
              </KpiWithAsks>
            ))}
            {newKpis.map((p) => (
              <KpiWithAsks key={p.id} asks={[p]}>
                <div className="kr">
                  <span className="kr-name">
                    <FlowMark size={10} />
                    {card(p).measure?.name}
                  </span>
                  <span className="gt-fig">{card(p).target_label}</span>
                  <span className="kr-src" />
                  <span className="kr-by">
                    <Pill flag>Proposed</Pill>
                  </span>
                </div>
              </KpiWithAsks>
            ))}
            <PanelLink panel={`kpi-new:${gid}`} className="kr-add">
              <Icon name="plus" />
              Add KPI
            </PanelLink>
          </PanelSection>
          {checkins.length > 0 && (
            <PanelSection name="Check-ins" count={checkins.length} target={`goal:${gid}:checkins`}>
              {checkins.map((c, i) => (
                <div key={i} className="checkin">
                  <span className="row-age">{fmtDate(c.created_at)}</span>
                  <p>{c.body}</p>
                </div>
              ))}
            </PanelSection>
          )}
        </>
      }
    />
  );
}

// ---------------------------------------------------------------- what waits on you: proposals as answer cards

/** The proposals this reader decides, still open. */
const waiting = (all: readonly Proposal[] | undefined) => (all ?? []).filter((p) => p.status === 'pending' && p.may_decide && ask(p));
const card = (p: Proposal) => (p.card ?? {}) as ProposalCard;

/** What a proposal asks, and its two answers (company.js propose: "Move the target from $22 to $24?", Keep $22, Set $24). */
function ask(p: Proposal): { q: string; no: string; yes: string } | null {
  const c = card(p);
  const said = (k: string) => {
    const v = (p.payload ?? {})[k];
    return typeof v === 'string' ? v : '';
  };
  switch (p.kind) {
    case 'kpi_target': {
      const to = c.target_label;
      if (!to) return null;
      return c.now_label ? { q: `Move the target from ${c.now_label} to ${to}?`, no: `Keep ${c.now_label}`, yes: `Set ${to}` } : { q: `Set the target to ${to}?`, no: 'Leave it', yes: `Set ${to}` };
    }
    case 'goal_kpi':
      return c.measure?.name ? { q: `Add ${c.measure.name}${c.target_label ? `, ${c.target_label}` : ''}?`, no: 'Leave it', yes: 'Add' } : null;
    case 'kpi_definition':
    case 'goal_wording': {
      const ch = c.changes ?? [];
      if (!ch.length) return null;
      const one = ch.length === 1 ? ch[0] : undefined;
      return {
        q: one ? `Change ${one.label.toLowerCase()} to “${one.proposed}”?` : `Change ${ch.map((x) => x.label.toLowerCase()).join(', ')}?`,
        no: 'Keep',
        yes: 'Change',
      };
    }
    case 'goal_why':
      return said('why') ? { q: `Use “${said('why')}” as the why?`, no: 'Leave it', yes: 'Use it' } : null;
    case 'flag':
      return said('issue') ? { q: `Flag this goal as ${said('issue')}?`, no: 'Leave it', yes: 'Flag' } : null;
    default:
      return null;
  }
}

/** A row and, under it, the cards about it. */
function KpiWithAsks({ asks, children }: { asks: readonly Proposal[]; children: ReactNode }) {
  return (
    <>
      {children}
      {asks.map((p) => (
        <ProposalCardView key={p.id} p={p} />
      ))}
    </>
  );
}

/** A proposal that waits on you: an answer card under the row it is about, with who asks and why. */
function ProposalCardView({ p }: { p: Proposal }) {
  const decide = useDo('goals.decide');
  const a = ask(p);
  if (!a) return null;
  const answer = (decision: 'confirm' | 'reject') => void decide.run({ proposal: p.id, decision });
  return (
    <section className="answer is-ask kp-ask" aria-label={a.q} data-target={`proposal:${p.id}`}>
      <p className="an-q">
        <FlowMark />
        <span>{a.q}</span>
      </p>
      {p.reason && (
        <p className="kp-why">
          <Face id={p.proposed_by} size={16} />
          <span>
            <b>
              <NameOf id={p.proposed_by} />
            </b>{' '}
            {p.reason}
          </span>
        </p>
      )}
      <div className="an-row">
        <Button disabled={decide.pending} onClick={() => answer('reject')}>
          {a.no}
        </Button>
        {/* plain: a goal can hold several proposals, and a sheet has one filled button */}
        <Button disabled={decide.pending} onClick={() => answer('confirm')}>
          {a.yes}
        </Button>
      </div>
    </section>
  );
}

/** Who or what recorded a reading (company.js srcMark, srcSays): a teammate's face and name, or, for Tico's own
    readings from a connected tool, the tool's mark and name. */
function From({ r, when }: { r: KpiReadingView; when: string }) {
  const a = useActor(r.actor_id);
  // A teammate's id is 'human:<id>' or 'bot:<slug>'; Tico's own readings carry its bare system id.
  const mate = a ?? (r.actor_id?.includes(':') ? { name: r.actor_name || r.actor_id.slice(r.actor_id.indexOf(':') + 1), kind: r.actor_id.startsWith('bot:') ? 'agent' : 'person' } : null);
  const name = mate?.name ?? (r.source || r.actor_name || 'Tico');
  const says = mate ? `${mate.name}, ${mate.kind === 'agent' ? 'at its check-in' : 'by hand'}` : name;
  return (
    <span className="kr-src" title={`Reads it: ${says}`}>
      {mate ? (
        <Face id={r.actor_id} size={16} />
      ) : (
        <span className="src-tool" aria-hidden="true">
          <Icon name="link" />
        </span>
      )}
      <span>{name}</span>
      <i>{when}</i>
    </span>
  );
}

// ---------------------------------------------------------------- one KPI

function KpiPanel({ kid }: { kid: string }) {
  const q = useQuery({ ...kpiQuery(kid), enabled: !kid.startsWith('tmp:') });
  const close = useDo('company.closePanel');
  const archive = useDo('kpis.archive');
  const add = useDo('kpis.addReading');
  const open = useDo('company.openPanel');
  const [draft, setDraft] = useState<string | null>(null);
  const d = q.data;
  if (!d) return null;
  const k = d.kpi;
  const link = d.links[0];
  const save = async () => {
    const value = Number(draft);
    if (draft == null || draft.trim() === '' || !Number.isFinite(value)) return;
    setDraft(null);
    await add.run({ kpi: kid, value });
  };
  return (
    <SidePanel
      title={k.name}
      target={`kpi:${kid}`}
      lead={
        link && (
          <PanelLink panel={`goal:${link.goal_id}`} className="ib ib-quiet pnl-back" title="Back to the goal">
            <Icon name="chevron" />
          </PanelLink>
        )
      }
      onClose={() => void close.run({})}
      actions={
        d.may_edit && (
          <Menu trigger={<IconButton icon="more" label="More" quiet />}>
            <MenuItem label="Place in tree" onSelect={() => void open.run({ panel: `kpi-place:${kid}` })} />
          </Menu>
        )
      }
      foot={
        d.may_edit && (
          <>
            <Button onClick={() => open.run({ panel: `kpi-edit:${kid}` })}>Edit</Button>
            <Button
              variant="quiet"
              onClick={async () => {
                if (await archive.run({ kpi: kid })) void (link ? open.run({ panel: `goal:${link.goal_id}` }) : close.run({}));
              }}
            >
              Archive
            </Button>
          </>
        )
      }
    >
      <section className="rail-sec">
        <div className="pnl-fig">
          <b>{k.latest ? amount(k.latest.value, k.unit) : 'No reading'}</b>
          {link?.target != null && <span>of {amount(link.target, k.unit)}</span>}
          {flag(k.status) && <Pill flag>{flag(k.status)}</Pill>}
        </div>
        <KpiChart d={d} />
        {link && (
          <div className="fact">
            <span>Goal</span>
            <span className="fact-v">
              <PanelLink panel={`goal:${link.goal_id}`} className="fact-clip">
                <span>{link.goal_title}</span>
              </PanelLink>
            </span>
          </div>
        )}
        {link?.target_label && (
          <div className="fact">
            <span>Target</span>
            <span className="fact-v">{link.target_label}</span>
          </div>
        )}
        {link?.deadline && (
          <div className="fact">
            <span>By</span>
            <span className="fact-v">{fmtDate(link.deadline)}</span>
          </div>
        )}
        <div className="fact">
          <span>Reads it</span>
          <span className="fact-v fact-clip">
            <Face id={k.owner_id} />
            <NameOf id={k.owner_id} />
          </span>
        </div>
      </section>
      <section className="rail-sec" aria-label="Readings">
        <h3>
          Readings
          {d.may_log && <IconButton icon="plus" label="Add reading" quiet size="sm" onClick={() => setDraft('')} />}
        </h3>
        {draft != null && (
          <form
            className="went"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <Field label="Reading" labelHidden type="number" value={draft} onValueChange={setDraft} autoFocus wide={false} />
            <Button type="submit" variant="primary" busy={add.pending}>
              Save
            </Button>
          </form>
        )}
        {d.readings.map((r) => (
          <div key={r.id} className="went">
            <From r={r} when={fmtDate(r.period_end)} />
            <span className="row-fig">{amount(r.value, k.unit)}</span>
          </div>
        ))}
      </section>
    </SidePanel>
  );
}

/** The readings over time with the target as a dashed line (company.js readings()). */
function KpiChart({ d }: { d: KpiDetail }) {
  const rs = [...d.readings].reverse();
  if (rs.length < 2) return null;
  const target = d.links[0]?.target ?? null;
  const short = (r: (typeof rs)[number] | undefined) => (r ? fmtDate(r.period_end) : undefined);
  return (
    <div className="pnl-chart">
      <LineChart
        points={rs.map((r) => r.value)}
        target={target}
        fmt={(v) => amount(v, d.kpi.unit)}
        from={short(rs[0])}
        to={short(rs.at(-1))}
        label={target != null ? `${d.kpi.name} over time, against ${amount(target, d.kpi.unit)}` : `${d.kpi.name} over time`}
      />
    </div>
  );
}

// ---------------------------------------------------------------- the forms

const UNITS = [
  { value: 'none', label: 'Number' },
  { value: '$', label: 'Dollars' },
  { value: '%', label: 'Percent' },
];
const DIRECTIONS = [
  { value: 'up', label: 'At least' },
  { value: 'down', label: 'At most' },
];
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

function KpiEdit({ kid, goals }: { kid: string; goals: readonly TreeGoal[] }) {
  const q = useQuery(kpiQuery(kid));
  if (!q.data) return null;
  return <KpiForm goal={q.data.links[0]?.goal_id ?? ''} goals={goals} kpi={q.data} />;
}

/** Add a KPI to a goal, or change one: name, goal, unit, target and its direction, by when, who reads it. */
function KpiForm({ goal, goals, kpi }: { goal: string; goals: readonly TreeGoal[]; kpi?: KpiDetail }) {
  const k = kpi?.kpi;
  const link = kpi?.links[0];
  const actors = useActors();
  const create = useDo('kpis.create');
  const update = useDo('kpis.update');
  const close = useDo('company.closePanel');
  const open = useDo('company.openPanel');
  const [name, setName] = useState(k?.name ?? '');
  const [goalId, setGoalId] = useState(goal);
  const [unit, setUnit] = useState(k?.unit || 'none');
  const [direction, setDirection] = useState<'up' | 'down'>(k?.direction === 'down' ? 'down' : 'up');
  const [target, setTarget] = useState(link?.target != null ? String(link.target) : '');
  const [deadline, setDeadline] = useState<Date | null>(link?.deadline ? new Date(`${link.deadline}T12:00:00`) : null);
  const [owner, setOwner] = useState<string | null>(k?.owner_id || null);
  const [bad, setBad] = useState(false);
  const busy = create.pending || update.pending;
  const save = async () => {
    const t = target.trim() === '' ? null : Number(target);
    if (!name.trim() || !goalId || (t != null && !Number.isFinite(t))) return setBad(true);
    const fields = { name: name.trim(), unit: unit === 'none' ? '' : unit, direction, target: t, deadline: deadline ? ymd(deadline) : null, owner };
    if (k) await update.run({ kpi: k.id, goal: goalId, ...fields });
    else await create.run({ goal: goalId, ...fields });
  };
  const back = () => void (k ? open.run({ panel: `kpi:${k.id}` }) : goal ? open.run({ panel: `goal:${goal}` }) : close.run({}));
  return (
    <SidePanel
      title={k ? 'Edit KPI' : 'New KPI'}
      target="panel:kpi-form"
      onClose={() => void close.run({})}
      foot={
        <>
          <Button variant="primary" busy={busy} onClick={() => save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={back}>
            Cancel
          </Button>
        </>
      }
    >
      <form
        className="rail-sec kf"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <span className="kf-l">Name</span>
        <div className="kf-c">
          <Field label="Name" labelHidden value={name} onValueChange={setName} autoFocus error={bad && !name.trim() ? 'Name it' : undefined} />
        </div>
        <span className="kf-l">Goal</span>
        <div className="kf-c">
          <Select label="Goal" labelHidden options={goals.filter((g) => !g.id.startsWith('tmp:')).map((g) => ({ value: g.id, label: g.title }))} value={goalId || null} onValueChange={setGoalId} />
        </div>
        <span className="kf-l">Unit</span>
        <div className="kf-c">
          <Select label="Unit" labelHidden options={UNITS.some((u) => u.value === unit) ? UNITS : [...UNITS, { value: unit, label: unit }]} value={unit} onValueChange={setUnit} />
        </div>
        <span className="kf-l">Target</span>
        <div className="kf-c">
          <Select label="Direction" labelHidden options={DIRECTIONS} value={direction} onValueChange={(v) => setDirection(v === 'down' ? 'down' : 'up')} />
          <span className="kf-num">
            <Field label="Target" labelHidden type="number" value={target} onValueChange={setTarget} wide={false} />
          </span>
        </div>
        <span className="kf-l">By</span>
        <div className="kf-c">
          <DatePick label="By" labelHidden withTime={false} value={deadline} onValueChange={setDeadline} />
        </div>
        <span className="kf-l">Reads it</span>
        <div className="kf-c">
          <Select
            label="Reads it"
            labelHidden
            options={actors.map((a) => ({ value: a.id, label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined }))}
            value={owner}
            onValueChange={setOwner}
          />
        </div>
      </form>
    </SidePanel>
  );
}

const ROLES: { value: GrowthRole | ''; label: string }[] = [
  { value: '', label: 'None' },
  { value: 'north_star', label: 'North Star' },
  { value: 'input', label: 'Input' },
  { value: 'guardrail', label: 'Guardrail' },
  { value: 'check_signups', label: 'Check: sign-ups' },
  { value: 'check_new_customers', label: 'Check: new customers' },
  { value: 'check_cost', label: 'Check: cost' },
];

/** The KPIs under `kid` in the KPI tree, and itself: what it may not feed. */
function below(kpis: readonly PlacedKpi[], kid: string): Set<string> {
  const out = new Set([kid]);
  for (let grew = true; grew; ) {
    grew = false;
    for (const k of kpis)
      if (k.parent_kpi_id && out.has(k.parent_kpi_id) && !out.has(k.id)) {
        out.add(k.id);
        grew = true;
      }
  }
  return out;
}

/** A KPI's place in the KPI tree: what it feeds (never itself or anything under it) and its role (goals-kpi-views.ts
    gkvPlace). Loop, Step and the funnel stage went with the Loops and Journey views. */
function KpiPlace({ kid }: { kid: string }) {
  const q = useQuery(placedKpisQuery);
  const place = useDo('kpis.place');
  const open = useDo('company.openPanel');
  const close = useDo('company.closePanel');
  const kpis = q.data ?? [];
  const k = kpis.find((x) => x.id === kid);
  const [feeds, setFeeds] = useState<string | null>(null);
  const [role, setRole] = useState<GrowthRole | '' | null>(null);
  const shownFeeds = feeds ?? k?.parent_kpi_id ?? '';
  const shownRole = role ?? k?.growth_role ?? '';
  const out = below(kpis, kid);
  const back = () => void open.run({ panel: `kpi:${kid}` });
  return (
    <SidePanel
      title={k?.name ?? 'Place KPI'}
      target="panel:kpi-place"
      onClose={() => void close.run({})}
      foot={
        k && (
          <>
            <Button variant="primary" busy={place.pending} onClick={() => place.run({ kpi: kid, feeds: shownFeeds || null, role: shownRole || null })}>
              Save
            </Button>
            <Button variant="quiet" onClick={back}>
              Cancel
            </Button>
          </>
        )
      }
    >
      {q.isPending && <p className="rail-sec pt-none">Loading</p>}
      {q.isError && <p className="rail-sec pt-none">The KPI tree did not load</p>}
      {q.data && !k && <p className="rail-sec pt-none">Not in the KPI tree</p>}
      {k && (
        <div className="rail-sec kf">
          <span className="kf-l">Feeds</span>
          <div className="kf-c">
            <Select label="Feeds" labelHidden options={[{ value: '', label: 'None' }, ...kpis.filter((x) => !out.has(x.id)).map((x) => ({ value: x.id, label: x.name }))]} value={shownFeeds} onValueChange={setFeeds} />
          </div>
          <span className="kf-l">Role</span>
          <div className="kf-c">
            <Select label="Role" labelHidden options={ROLES} value={shownRole} onValueChange={setRole} />
          </div>
        </div>
      )}
    </SidePanel>
  );
}

/** A new goal: what it is, whose it is, which goal it feeds. */
function GoalForm({ goals }: { goals: readonly TreeGoal[] }) {
  const owners = useOwners();
  const create = useDo('goals.create');
  const close = useDo('company.closePanel');
  const [title, setTitle] = useState('');
  const [owner, setOwner] = useState('company');
  const [parent, setParent] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  const save = async () => {
    if (!title.trim()) return setBad(true);
    if (await create.run({ title: title.trim(), owner, ...(parent ? { parent } : {}) })) void close.run({});
  };
  return (
    <SidePanel
      title="New goal"
      target="panel:goal-new"
      onClose={() => void close.run({})}
      foot={
        <>
          <Button variant="primary" busy={create.pending} onClick={() => save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={() => close.run({})}>
            Cancel
          </Button>
        </>
      }
    >
      <form
        className="rail-sec kf"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <span className="kf-l">Goal</span>
        <div className="kf-c">
          <Field label="Goal" labelHidden value={title} onValueChange={setTitle} autoFocus error={bad && !title.trim() ? 'Name it' : undefined} />
        </div>
        <span className="kf-l">Owner</span>
        <div className="kf-c">
          <Select label="Owner" labelHidden options={owners} value={owner} onValueChange={setOwner} />
        </div>
        <span className="kf-l">Feeds</span>
        <div className="kf-c">
          <Select
            label="Feeds"
            labelHidden
            placeholder="None"
            options={goals.filter((g) => !g.id.startsWith('tmp:')).map((g) => ({ value: g.id, label: g.title }))}
            value={parent}
            onValueChange={setParent}
          />
        </div>
      </form>
    </SidePanel>
  );
}


// ---------------------------------------------------------------- Needs you: what waits on the reader in Goals

/** What a need says on its own: the server's line, or for a suggestion, what it suggests. */
function needSays(n: GoalNeed): string {
  const p = (n.proposal ?? {}) as { kind?: string; payload?: { bets?: { id?: string }[] } };
  if (n.kind === 'recovery') return `A recovery for ${n.goal_title || 'a goal'}`;
  if (n.kind !== 'proposal') return n.title;
  const bets = p.payload?.bets ?? [];
  if (p.kind === 'strategy_draft') return bets.length === 1 && !bets[0]?.id ? 'A new bet for the strategy' : 'A change to the strategy';
  if (p.kind === 'strategy_realign') return 'Goals moved to new bets';
  if (p.kind === 'brand_draft') return 'A change to the brand book';
  if (p.kind === 'goal_kpi') return `A KPI for ${n.goal_title || 'a goal'}`;
  if (p.kind === 'kpi_target') return `A target for ${n.kpi_name || 'a KPI'}`;
  if (p.kind === 'kpi_definition') return `A change to ${n.kpi_name || 'a KPI'}`;
  return n.title;
}

/** One quiet line over the tree when something waits on the reader (goals.ts goalNeedsLineHtml); it opens the list. */
function NeedsLine({ open }: { open: boolean }) {
  const items = useQuery(goalNeedsQuery).data ?? [];
  if (!items.length) return null;
  const asks = items.every((n) => n.kind === 'proposal' || n.kind === 'recovery');
  const n = items.length;
  const first = items[0];
  const says = n === 1 && first ? needSays(first) : asks ? `${n} suggestions` : `${n} waiting on you`;
  return (
    <PanelLink panel="needs" className={open ? 'gt-needs is-selected' : 'gt-needs'} data-target="panel:needs">
      <FlowMark size={10} />
      <span>{says}</span>
    </PanelLink>
  );
}

/** Everything that waits on the reader in Goals, each answered in place: suggestions and recoveries get their answer,
    the rest open where they are done (goals-kpis.ts goalNeedsHtml). */
function NeedsPanel() {
  const q = useQuery(goalNeedsQuery);
  const close = useDo('company.closePanel');
  const items = q.data ?? [];
  return (
    <SidePanel title="Needs you" target="panel:needs" onClose={() => void close.run({})}>
      {q.isSuccess && !items.length && <p className="rail-sec pt-none">Nothing waits on you</p>}
      {items.map((n) => (
        <NeedItem key={n.id} n={n} />
      ))}
    </SidePanel>
  );
}

const str = (v: unknown) => (typeof v === 'string' ? v : '');

function NeedItem({ n }: { n: GoalNeed }) {
  const answer = useDo('goals.answer');
  const p = (n.proposal ?? {}) as { id?: string; reason?: string; proposed_by?: string; payload?: Record<string, unknown> };
  const say = (decision: 'confirm' | 'reject', option?: number) => p.id && void answer.run({ proposal: p.id, decision, ...(option != null && { option }) });
  if (n.kind === 'recovery') {
    const options = (n.options ?? []) as unknown as RecoveryOption[];
    return (
      <section className="answer is-ask kp-ask" aria-label={needSays(n)} data-target={`proposal:${p.id}`}>
        <p className="an-q">
          <FlowMark />
          <span>{needSays(n)}</span>
        </p>
        {str(p.payload?.evidence) && <p className="kp-why">{str(p.payload?.evidence)}</p>}
        {p.reason && <p className="kp-why">{p.reason}</p>}
        {options.map((o, i) => (
          <div key={i} className="rec-opt">
            <p>
              <b>{o.label}</b>
              {o.pick && <span className="none"> Recommended</span>}
            </p>
            {(o.actions ?? []).length > 0 && (
              <ul>
                {(o.actions ?? []).map((a, j) => (
                  <li key={j}>{a.note ? `${a.text}: ${a.note}` : a.text}</li>
                ))}
              </ul>
            )}
            <Button disabled={answer.pending} onClick={() => say('confirm', i)}>
              {options.length > 1 ? `Approve ${i + 1}` : 'Approve'}
            </Button>
          </div>
        ))}
        <div className="an-row">
          <Button variant="quiet" disabled={answer.pending} onClick={() => say('reject')}>
            Reject
          </Button>
        </div>
      </section>
    );
  }
  if (n.kind === 'proposal') {
    const kind = str((n.proposal as { kind?: unknown } | undefined)?.kind);
    const open = kind === 'brand_draft' ? { to: '/company/strategy/brand', search: { edit: true } } : kind.startsWith('strategy') ? { to: '/company/strategy', search: {} } : null;
    return (
      <section className="answer is-ask kp-ask" aria-label={needSays(n)} data-target={`proposal:${p.id}`}>
        <p className="an-q">
          <FlowMark />
          <span>{needSays(n)}</span>
        </p>
        {p.reason && (
          <p className="kp-why">
            <Face id={p.proposed_by} size={16} />
            <span>
              <b>
                <NameOf id={p.proposed_by} />
              </b>{' '}
              {p.reason}
            </span>
          </p>
        )}
        <div className="an-row">
          {open && (
            <Link to={open.to as never} search={open.search as never} className="btn btn-quiet">
              Open
            </Link>
          )}
          <Button disabled={answer.pending} onClick={() => say('reject')}>
            Reject
          </Button>
          <Button disabled={answer.pending} onClick={() => say('confirm')}>
            Accept
          </Button>
        </div>
      </section>
    );
  }
  if (n.kind === 'vision')
    return (
      <Go to="/company/strategy" className="row need-row">
        <span className="row-title">{n.title}</span>
      </Go>
    );
  if (n.kind === 'why') {
    const goals = (n.goals ?? []) as { goal_id: string; goal_title: string }[];
    return (
      <section className="rail-sec" aria-label={n.title}>
        <h3>{n.title}</h3>
        {goals.map((g) => (
          <PanelLink key={g.goal_id} panel={`goal:${g.goal_id}`} className="row need-row">
            <span className="row-title">{g.goal_title}</span>
          </PanelLink>
        ))}
      </section>
    );
  }
  const kpis = n.kind === 'kpi_stale' ? ((n.kpis ?? []) as { kpi_id: string; kpi_name: string; reason?: string }[]) : n.kpi_id ? [{ kpi_id: n.kpi_id, kpi_name: n.kpi_name ?? '', reason: n.reason }] : [];
  return (
    <>
      {kpis.map((k) => (
        <PanelLink key={k.kpi_id} panel={`kpi:${k.kpi_id}`} className="row need-row" data-target={`kpi:${k.kpi_id}`}>
          <span className="row-title">
            {k.kpi_name}
            {n.kind === 'kpi_red' && n.goal_title ? ` on ${n.goal_title}` : ''}
          </span>
          {n.kind === 'kpi_red' && <Pill flag>Behind</Pill>}
          {k.reason && <span className="row-age">{k.reason}</span>}
        </PanelLink>
      ))}
    </>
  );
}
