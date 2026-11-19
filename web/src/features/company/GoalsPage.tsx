import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { fmtAge, fmtDate } from '@/lib';
import { List, SidePanel, type PageTab } from '@/pagekinds';
import { amount, Face, NameOf, PanelLink, Trend, useDo } from './parts';
import { goalQuery, goalsTreeQuery, kpiQuery, type TreeGoal } from './queries';
import type { Kpi, KpiDetail } from '@/api/schema';

/* Goals (pages/3-company/company.js goalsBody): a List of the goal tree, a goal sits under the goal it feeds and each
   KPI is a quiet line under its goal's title. A goal, a KPI, and the KPI and goal forms open in the panel:
   `?panel=goal:<id>`, `kpi:<id>`, `kpi-new:<goal>`, `kpi-edit:<kpi>`, `goal-new`. */

export interface GoalsSearch {
  panel?: string;
}
type Head = { title: string; path: string; tabs: readonly PageTab[]; className: string };

const FLAGS: Record<string, string> = { red: 'Behind', yellow: 'At risk' };
const flag = (status: string | null | undefined) => (status ? FLAGS[status] : undefined);
const KPIS_SHOWN = 3;

export function GoalsPage({ head, search }: { head: Head; search: GoalsSearch }) {
  const q = useQuery(goalsTreeQuery);
  const open = useDo('company.openPanel');
  const goals = q.data?.goals ?? [];
  const panel = search.panel ?? '';
  const at = panel.indexOf(':');
  const kind = at < 0 ? panel : panel.slice(0, at);
  const ref = panel.slice(at + 1);
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : goals.length ? 'ready' : 'empty';
  const add = (
    <Button variant={panel ? 'plain' : 'primary'} icon="plus" onClick={() => void open.run({ panel: 'goal-new' })}>
      Add goal
    </Button>
  );
  return (
    <List
      {...head}
      actions={add}
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No goals yet', action: add }}
      sections={<GoalTreeView goals={goals} selected={kind === 'goal' ? ref : null} />}
      panel={
        panel === 'goal-new' ? (
          <GoalForm goals={goals} />
        ) : kind === 'goal' ? (
          <GoalPanel gid={ref} />
        ) : kind === 'kpi' ? (
          <KpiPanel kid={ref} />
        ) : kind === 'kpi-new' ? (
          <KpiForm goal={ref} goals={goals} />
        ) : kind === 'kpi-edit' ? (
          <KpiEdit kid={ref} goals={goals} />
        ) : null
      }
    />
  );
}

// ---------------------------------------------------------------- the tree

type Placed = { goal: TreeGoal; depth: number; last: boolean };

/** Goals in tree order: each under the goal it feeds, in rank order. */
export function placeGoals(goals: readonly TreeGoal[]): Placed[] {
  const ids = new Set(goals.map((g) => g.id));
  const kids = new Map<string | null, TreeGoal[]>();
  for (const g of goals) {
    const p = g.parent_id && ids.has(g.parent_id) ? g.parent_id : null;
    kids.set(p, [...(kids.get(p) ?? []), g]);
  }
  const rank = (a: TreeGoal, b: TreeGoal) => (a.rank ?? 1e9) - (b.rank ?? 1e9) || a.created_at.localeCompare(b.created_at);
  const out: Placed[] = [];
  const walk = (p: string | null, depth: number) => {
    const list = [...(kids.get(p) ?? [])].sort(rank);
    list.forEach((g, i) => {
      out.push({ goal: g, depth, last: i === list.length - 1 });
      walk(g.id, depth + 1);
    });
  };
  walk(null, 0);
  return out;
}

function GoalTreeView({ goals, selected }: { goals: readonly TreeGoal[]; selected: string | null }) {
  return (
    <div className="gt" role="tree" aria-label="Goals">
      {placeGoals(goals).map(({ goal: g, depth, last }) => {
        const more = g.kpis.length - KPIS_SHOWN;
        return (
          <div key={g.id} className={g.id === selected ? 'gt-row is-selected' : 'gt-row'} role="treeitem" aria-level={depth + 1} aria-selected={g.id === selected}>
            <span className="gt-g">{depth > 0 && <i className={last ? 'e' : 't'} />}</span>
            {g.owner_id === 'company' ? <span className="av-none" /> : <Face id={g.owner_id} />}
            <div className="gt-main">
              <PanelLink panel={`goal:${g.id}`} className="gt-title" data-target={`goal:${g.id}`}>
                <span>{g.title}</span>
                {flag(g.status) && <Pill flag>{flag(g.status)}</Pill>}
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
              {!g.id.startsWith('tmp:') && (
                <PanelLink panel={`kpi-new:${g.id}`} className="gk gk-add">
                  <Icon name="plus" />
                  Add KPI
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

function GoalPanel({ gid }: { gid: string }) {
  const tree = useQuery(goalsTreeQuery);
  const detail = useQuery({ ...goalQuery(gid), enabled: !gid.startsWith('tmp:') });
  const close = useDo('company.closePanel');
  const update = useDo('goals.update');
  const shut = useDo('goals.close');
  const owners = useOwners();
  const [renaming, setRenaming] = useState<string | null>(null);
  const fromTree = tree.data?.goals.find((g) => g.id === gid);
  const g = fromTree ?? detail.data?.goal;
  if (!g) return null;
  const kpis = fromTree?.kpis ?? detail.data?.goal.kpis ?? [];
  const parent = tree.data?.goals.find((x) => x.id === g.parent_id);
  const checkins = detail.data?.goal.checkins ?? [];
  const done = async (status: 'done' | 'dropped') => {
    if (await shut.run({ goal: gid, status })) void close.run({});
  };
  const rename = async () => {
    const title = renaming?.trim();
    setRenaming(null);
    if (title && title !== g.title) await update.run({ goal: gid, title });
  };
  return (
    <SidePanel
      title={g.title}
      target={`goal:${gid}`}
      lead={g.owner_id !== 'company' && <Face id={g.owner_id} size={28} />}
      actions={<IconButton icon="edit" label="Rename" quiet size="sm" onClick={() => setRenaming(g.title)} />}
      onClose={() => void close.run({})}
      foot={
        <>
          <Button onClick={() => void done('done')} busy={shut.pending}>
            Mark done
          </Button>
          <Button variant="quiet" onClick={() => void done('dropped')}>
            Archive
          </Button>
        </>
      }
    >
      <section className="rail-sec">
        {renaming != null && (
          <form
            className="kf-rename"
            onSubmit={(e) => {
              e.preventDefault();
              void rename();
            }}
          >
            <Field label="Title" labelHidden value={renaming} onValueChange={setRenaming} autoFocus />
          </form>
        )}
        {flag(g.status) && (
          <div className="pnl-fig">
            <Pill flag>{flag(g.status)}</Pill>
          </div>
        )}
        <div className="fact">
          <span>Owner</span>
          <span className="fact-v">
            <Select label="Owner" labelHidden look="quiet" options={owners} value={g.owner_id} onValueChange={(owner) => void update.run({ goal: gid, owner })} />
          </span>
        </div>
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
      </section>
      <section className="rail-sec" aria-label="KPIs">
        <h3>
          KPIs<span>{kpis.length || ''}</span>
        </h3>
        {kpis.map((k) => (
          <PanelLink key={k.id} panel={`kpi:${k.id}`} className="kr" data-target={`kpi:${k.id}`}>
            <span className="kr-name">
              {k.name}
              {flag(k.status) && <Pill flag>{flag(k.status)}</Pill>}
            </span>
            <KpiFig k={k} className="gt-fig" />
            <span className="kr-src">
              {k.latest?.actor_id && <Face id={k.latest.actor_id} size={16} />}
              <span>{k.latest ? <ReadBy r={k.latest} /> : <NameOf id={k.owner_id} />}</span>
              {k.latest && <i>{fmtAge(k.latest.created_at ?? k.latest.period_end)}</i>}
            </span>
            <span className="kr-by">{k.link?.deadline ? `by ${fmtDate(k.link.deadline)}` : ''}</span>
          </PanelLink>
        ))}
        <PanelLink panel={`kpi-new:${gid}`} className="kr-add">
          <Icon name="plus" />
          Add KPI
        </PanelLink>
      </section>
      {checkins.length > 0 && (
        <section className="rail-sec" aria-label="Check-ins">
          <h3>Check-ins</h3>
          {checkins.map((c, i) => (
            <div key={i} className="checkin">
              <span className="row-age">{fmtDate(c.created_at)}</span>
              <p>{c.body}</p>
            </div>
          ))}
        </section>
      )}
    </SidePanel>
  );
}

/** Who or what recorded a reading: a teammate by name, else the name it came with, else its source. */
function ReadBy({ r }: { r: { actor_id?: string | null; actor_name?: string; source?: string } }) {
  if (r.actor_id) return <NameOf id={r.actor_id} />;
  return <>{r.actor_name || r.source || 'Unknown'}</>;
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
      foot={
        d.may_edit && (
          <>
            <Button onClick={() => void open.run({ panel: `kpi-edit:${kid}` })}>Edit</Button>
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
            <span>
              {fmtDate(r.period_end)}, <ReadBy r={r} />
            </span>
            <span className="row-fig">{amount(r.value, k.unit)}</span>
          </div>
        ))}
      </section>
    </SidePanel>
  );
}

/** The readings over time with the target as a dashed line (company.js kpiChart). */
function KpiChart({ d }: { d: KpiDetail }) {
  const pts = [...d.readings].reverse().map((r) => r.value);
  if (pts.length < 2) return null;
  const W = 312;
  const H = 124;
  const target = d.links[0]?.target ?? null;
  const all = target != null ? [...pts, target] : pts;
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const y = (v: number) => (hi === lo ? H / 2 : 8 + (1 - (v - lo) / (hi - lo)) * (H - 24));
  const x = (i: number) => 4 + (i * (W - 48)) / (pts.length - 1);
  const last = pts.length - 1;
  const now = pts[last] ?? 0;
  return (
    <div className="chart pnl-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${d.kpi.name} over time`}>
        {target != null && <line className="lim" x1="0" x2={W} y1={y(target)} y2={y(target)} />}
        <path className="ln" d={pts.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')} />
        <circle className="pt-now" cx={x(last)} cy={y(now)} r="3" />
        <text className="now" x={x(last) + 8} y={y(now) + 4}>
          {amount(now, d.kpi.unit)}
        </text>
      </svg>
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
          <Button variant="primary" busy={busy} onClick={() => void save()}>
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
          <Button variant="primary" busy={create.pending} onClick={() => void save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={() => void close.run({})}>
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

