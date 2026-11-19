import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { directoryQuery, meQuery, useActor } from '@/data';
import { Sparkline } from '@/kit/Chart';
import { FlowMark } from '@/kit/FlowMark';
import { FoldButton } from '@/kit/SectionHead';
import { LatePill } from '@/kit/Pill';
import { chatAddress, fmtAge } from '@/lib';
import { Dashboard, FiguresStrip, SidePanel, type Figure } from '@/pagekinds';
import { Face, Go, PanelLink, usd } from './parts';
import { floorQuery, goalsTreeQuery, needsQuery, revenueQuery, spendQuery, stageTasksQuery, type Pipeline, type Stage } from './queries';
import './company.css';

/* Company (pages/3-company/company.js companyPage): a Dashboard, Floor variant. Figures across the top, each from its
   own area and linking to its page; then one hall per team with its pipelines, every stage a stack of tasks per owner.
   A stage opens its tasks in a panel (`?panel=stage:<pipeline>:<stage>`). */

export interface CompanySearch {
  panel?: string;
}

const CAP = 7;
type Factory = { id: string | null; manager_id: string | null; name: string; pipelines: Pipeline[] };

export function CompanyPage({ search }: { search: CompanySearch }) {
  const floor = useQuery(floorQuery);
  const navigate = useNavigate();
  const open = parseStage(search.panel);
  const close = () => void navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, panel: undefined }) as never });
  const halls = (floor.data?.factories ?? []) as Factory[];
  const state = floor.isPending ? 'loading' : floor.isError ? 'fault' : halls.length ? 'ready' : 'empty';
  const pipeline = open && halls.flatMap((h) => h.pipelines).find((p) => p.id === open.pipeline);
  const stage = pipeline && pipeline.stages.find((s) => s.id === open.stage);
  return (
    <Dashboard
      title="Company"
      path="/company"
      className="co"
      state={state}
      onRetry={() => void floor.refetch()}
      empty={{ title: 'Nothing is running yet' }}
      figures={<CompanyFigures />}
      chart={
        <div className="floor">
          {halls.map((h) => (
            <Hall key={h.id ?? h.name} hall={h} open={open} />
          ))}
        </div>
      }
      panel={pipeline && stage && <StagePanel pipeline={pipeline} stage={stage} onClose={close} />}
    />
  );
}

function parseStage(panel: string | undefined) {
  const [, pipeline, stage] = panel?.match(/^stage:([^:]+):(.+)$/) ?? [];
  return pipeline && stage ? { pipeline, stage } : null;
}

/** Four figures, each read from its own area and opening that area's page. One a person may not read is left out. */
function CompanyFigures() {
  const revenue = useQuery(revenueQuery);
  const goals = useQuery(goalsTreeQuery);
  const needs = useQuery(needsQuery);
  const spend = useQuery(spendQuery);
  const figures: Figure[] = [];
  const rev = revenue.data?.value;
  if (typeof rev === 'number')
    figures.push({ label: 'Revenue', value: <Go to="/company/finance">{usd(revenue.data?.unit === 'minor' ? rev / 100 : rev)}</Go>, sub: 'last 7 days', target: 'figure:revenue' });
  if (goals.data) {
    const all = goals.data.goals;
    figures.push({
      label: 'Goals behind',
      value: <Go to="/company/strategy/goals">{all.filter((g) => g.status === 'red').length}</Go>,
      sub: `of ${all.length}`,
      target: 'figure:goals',
    });
  }
  if (needs.data) figures.push({ label: 'Needs you', value: <Go to="/flow">{needs.data.total}</Go>, sub: `${needs.data.this_week} this week`, target: 'figure:needs' });
  if (spend.data) {
    const days = (spend.data.rows ?? []).map((r) => Number((r as { est_cost_usd?: number | null }).est_cost_usd ?? 0));
    figures.push({
      label: 'Agent spend',
      value: <Go to="/company/usage">{usd(spend.data.totals.est_cost_usd ?? 0)}</Go>,
      sub: 'last 30 days',
      spark: days.length > 1 ? <Sparkline points={days} /> : undefined,
      target: 'figure:spend',
    });
  }
  return figures.length ? <FiguresStrip figures={figures} label="Company figures" /> : null;
}

/** A hall's manager is who you talk to about that hall: their face opens their chat beside the Floor (company.js
    companyChat), and Back closes it. */
function ManagerChat({ who }: { who: string }) {
  const actor = who.includes(':') ? who : `bot:${who}`;
  const name = useActor(actor)?.name ?? 'the manager';
  return (
    <Link to="." search={((prev: Record<string, unknown>) => ({ ...prev, chat: chatAddress(actor) })) as never} className="crew-face" aria-label={`Chat with ${name}`} title={`Chat with ${name}`} data-target={`chat:${chatAddress(actor)}`}>
      <Face id={actor} />
    </Link>
  );
}

/** One team: its name (folds the hall), its crew's faces, then its pipelines. */
function Hall({ hall, open }: { hall: Factory; open: { pipeline: string; stage: string } | null }) {
  const [shown, setShown] = useState(true);
  const dir = useQuery(directoryQuery);
  const crew = (dir.data?.actors ?? []).filter((a) => a.team === hall.name && a.id !== hall.manager_id);
  return (
    <section className={shown ? 'hall' : 'hall is-folded'} aria-label={hall.name}>
      <header className="hall-head">
        <h2>
          <FoldButton name={hall.name} open={shown} onToggle={() => setShown(!shown)} />
        </h2>
        <div className="crew" aria-label={`${hall.name} team`}>
          {hall.manager_id && <ManagerChat who={hall.manager_id} />}
          {crew.slice(0, 8).map((a) => (
            <Face key={a.id} id={a.id} />
          ))}
        </div>
      </header>
      {shown && (
        <div className="pipes">
          {hall.pipelines.map((p) => (
            <Pipe key={p.id} pipe={p} open={open?.pipeline === p.id ? open.stage : null} />
          ))}
        </div>
      )}
    </section>
  );
}

/** A pipeline: its name, tasks in a day, its stages on one line, tasks out a day. */
function Pipe({ pipe, open }: { pipe: Pipeline; open: string | null }) {
  return (
    <div className="pipe">
      <span className="pipe-name" title={pipe.name}>
        {pipe.name}
      </span>
      <span className="rate" title="In a day">
        {pipe.in.day}/day
      </span>
      <div className="line">
        {pipe.stages.map((st) => (
          <PanelLink
            key={st.id}
            panel={`stage:${pipe.id}:${st.id}`}
            className={['st', st.needs_you ? 'needs' : '', st.id === open ? 'is-open' : ''].filter(Boolean).join(' ')}
            title={`${st.name}: ${st.count}`}
            data-target={`stage:${st.id}`}
          >
            {st.needs_you > 0 && <FlowMark size={12} className="st-flow" />}
            <span className="decks">
              {st.owners.length ? st.owners.map((o) => <Deck key={o.actor} actor={o.actor} n={o.count} />) : <span className="deck-none" />}
            </span>
            <span className="node" />
            <span className="st-name">{st.name}</span>
          </PanelLink>
        ))}
      </div>
      <span className="rate rate-out" title="Out a day">
        {pipe.out.day}/day
      </span>
    </div>
  );
}

function Deck({ actor, n }: { actor: string; n: number }) {
  return (
    <span className={n > CAP ? 'deck is-capped' : 'deck'} aria-hidden="true">
      {Array.from({ length: Math.min(n, CAP) }, (_, i) => (
        <i key={i} />
      ))}
      <span hidden>{actor}</span>
    </span>
  );
}

/** A stage, opened: every open task in it, the ones that wait on you first. */
function StagePanel({ pipeline, stage, onClose }: { pipeline: Pipeline; stage: Stage; onClose: () => void }) {
  const tasks = useQuery(stageTasksQuery(pipeline.id, stage.id));
  const me = useQuery(meQuery).data?.id;
  const mine = (t: { turn: { state: string; who: string | null } | null }) => t.turn?.state === 'person' && t.turn.who === me;
  const list = [...(tasks.data?.tasks ?? [])].sort((a, b) => Number(mine(b)) - Number(mine(a)));
  return (
    <SidePanel
      title={stage.name}
      target={`stage:${stage.id}`}
      onClose={onClose}
      foot={
        <Go to={`/tasks/${encodeURIComponent(pipeline.id)}`} className="btn">
          All tasks
        </Go>
      }
    >
      <section className="rail-sec">
        <div className="fact">
          <span>Pipeline</span>
          <span className="fact-v">{pipeline.name}</span>
        </div>
        <div className="fact">
          <span>Tasks</span>
          <span className="fact-v">{stage.count}</span>
        </div>
        {stage.needs_you > 0 && (
          <div className="fact">
            <span>Waits on you</span>
            <span className="fact-v">
              <FlowMark size={12} />
              {stage.needs_you}
            </span>
          </div>
        )}
      </section>
      <section className="rail-sec" aria-label="Tasks">
        {tasks.isError && <p className="pt-none">Tasks did not load</p>}
        {list.map((t) => (
          <Go key={t.id} to={t.number != null ? `/tasks/${encodeURIComponent(pipeline.id)}/${t.number}` : `/task/${encodeURIComponent(t.id)}`} className="pt" data-target={`task:${t.number ?? t.id}`}>
            <span className="pt-mark">{mine(t) && <FlowMark size={10} />}</span>
            <span className="pt-title">
              {t.title}
              {t.late && <LatePill />}
            </span>
            <span className="row-age">{t.stage_since ? fmtAge(t.stage_since) : ''}</span>
          </Go>
        ))}
      </section>
    </SidePanel>
  );
}
