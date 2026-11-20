import { useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useFollow } from '@/live/follow';
import { FlowMark } from '@/kit/FlowMark';
import { useCards } from '@/kit/Popover';
import { Icon } from '@/kit/Icon';
import { TaskRow } from '@/kit/Row';
import { List, Section } from '@/pagekinds';
import { Capped, LIST_CAP, LIST_SHOW, RowMore, ROW_H } from './capped';
import { Blocks, Face, FlowButton } from './parts';
import { flowGroups, flowQuery, pipelinesQuery, type FlowGroup, type FlowItem } from './queries';
import './tasks.css';

function FlowRow({ it }: { it: FlowItem }) {
  return (
    <TaskRow
      title={it.title}
      href={`/flow/${it.number ?? encodeURIComponent(it.id)}`}
      target={`task:${it.number ?? it.id}`}
      mark={<FlowMark />}
      late={it.late}
      pills={<Blocks t={it} />}
      avatar={<Face actor={it.owner_id} />}
      at={it.since}
    />
  );
}

/** A section named by its pipeline, then its stage (list.js path()); its name folds it like every section. Under a
    pipeline's own head (a phone) it is named by its stage alone. */
function FlowSection({ g, under }: { g: FlowGroup; under?: boolean }) {
  return (
    <Section
      name={
        under ? (
          g.stage
        ) : (
          <>
            {g.pipe}
            <Icon name="chevron" className="sec-sep" />
            {g.stage}
          </>
        )
      }
      label={`${g.pipe}, ${g.stage}`}
      count={g.items.length}
      target={`stage:${g.key}`}
    >
      <Capped
        list={g.items}
        cap={LIST_CAP}
        show={LIST_SHOW}
        height={ROW_H}
        more={undefined}
        draw={(it) => <FlowRow key={it.id} it={it} />}
        moreRow={(n, open) => <RowMore key="more" n={n} onClick={open} />}
      />
    </Section>
  );
}

/** Flow: every task that waits on you, in every pipeline, one after another. A row opens it in Flow. It is Tasks with
    the Flow button pressed (list.js flow). */
export function FlowPage() {
  // The always-followed needs topic keeps the Flow query current across pages.
  useFollow({ allTasks: true });
  const flow = useSuspenseQuery(flowQuery).data;
  const pipes = useSuspenseQuery(pipelinesQuery).data;
  const cards = useCards();
  const groups = flowGroups(flow.items, pipes);
  return (
    <List
      title="Tasks"
      path="/flow"
      actions={<FlowButton on count={flow.count} />}
      state={flow.items.length ? 'ready' : 'empty'}
      empty={{
        title: 'Nothing waits on you',
        action: (
          <Link to="/tasks" className="btn">
            Open Tasks
          </Link>
        ),
      }}
      className="tasks-page"
      sections={cards ? <ByPipeline groups={groups} /> : groups.map((g) => <FlowSection key={g.key} g={g} />)}
    />
  );
}

/** A phone keeps a pipeline's name over its stages (list.js pipeSec): the pipeline's head and how many wait in it, then
    a section a stage. */
function ByPipeline({ groups }: { groups: readonly FlowGroup[] }) {
  const pipes: { name: string; groups: FlowGroup[] }[] = [];
  for (const g of groups) {
    const last = pipes.at(-1);
    if (last?.name === g.pipe) last.groups.push(g);
    else pipes.push({ name: g.pipe, groups: [g] });
  }
  return pipes.map((p) => (
    <section key={p.groups[0]?.key} className="pipe" aria-label={p.name}>
      <header className="pipe-head">
        <h2>{p.name}</h2>
        <span className="stage-n">{p.groups.reduce((n, g) => n + g.items.length, 0)}</span>
      </header>
      {p.groups.map((g) => (
        <FlowSection key={g.key} g={g} under />
      ))}
    </section>
  ));
}
