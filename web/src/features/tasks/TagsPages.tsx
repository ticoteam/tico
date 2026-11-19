import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { meQuery } from '@/data';
import { useFollow } from '@/live/follow';
import { Ago } from '@/kit/Ago';
import { TaskRow } from '@/kit/Row';
import { Table, Td, Th } from '@/kit/Table';
import { List, Section, type PageState } from '@/pagekinds';
import { Face, markOf, taskHref } from './parts';
import { pipelinesQuery, tagQuery, tagsQuery, type Pipeline, type TaskItem } from './queries';
import './tasks.css';

/** Tags: every tag and when it was last used (more.js tagsPage). A tag opens its tasks. */
export function TagsPage() {
  const tags = useQuery(tagsQuery);
  const state: PageState = tags.isError ? 'fault' : !tags.data ? 'loading' : !tags.data.length ? 'empty' : 'ready';
  const list = [...(tags.data ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return (
    <List
      title="Tags"
      path="/tags"
      state={state}
      empty={{ title: 'No tags yet' }}
      onRetry={() => void tags.refetch()}
      className="tasks-page"
      sections={
        <Table label="Tags">
          <thead>
            <tr>
              <Th>Tag</Th>
              <Th num sorted="descending">
                Last used
              </Th>
            </tr>
          </thead>
          <tbody>
            {list.map((t) => (
              <tr key={t.id}>
                <Td>
                  <Link to="/tags/$tag" params={{ tag: t.id }} data-target={`tag:${t.id}`}>
                    {t.label || t.key}
                  </Link>
                </Td>
                <Td num>
                  <Ago at={t.updated_at} />
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      }
    />
  );
}

interface Group {
  pipe: Pipeline | undefined;
  rows: TaskItem[];
}

/** One tag: its open tasks grouped by pipeline, each with its stage; Done is a section drawn shut (more.js tagPage). */
export function TagPage({ tag }: { tag: string }) {
  useFollow({ allTasks: true });
  const detail = useQuery(tagQuery(tag));
  const pipes = useQuery(pipelinesQuery);
  const me = useQuery(meQuery).data?.id;
  const name = detail.data ? detail.data.tag.label || detail.data.tag.key : 'Tag';
  const tasks = (detail.data?.tasks ?? []) as TaskItem[];
  const open = tasks.filter((t) => t.status === 'open');
  const done = tasks.filter((t) => t.status !== 'open');
  const groups: Group[] = [];
  for (const t of open) {
    let g = groups.find((x) => (x.pipe?.id ?? null) === t.pipeline_id);
    if (!g) groups.push((g = { pipe: pipes.data?.find((p) => p.id === t.pipeline_id), rows: [] }));
    g.rows.push(t);
  }
  const stageName = (t: TaskItem, p: Pipeline | undefined) =>
    p?.stages.find((s) => s.id === t.stage_id)?.name ?? (p?.done?.id === t.stage_id ? p?.done?.name : p?.closed?.name) ?? '';
  const row = (t: TaskItem) => {
    const p = pipes.data?.find((x) => x.id === t.pipeline_id);
    return (
      <TaskRow
        key={t.id}
        title={t.title}
        href={p ? taskHref(p.id, t) : `/task/${t.number ?? encodeURIComponent(t.id)}`}
        target={`task:${t.number ?? t.id}`}
        mark={markOf(t, me)}
        late={!!t.late}
        pills={<span className="row-stage">{stageName(t, p)}</span>}
        avatar={<Face actor={t.owner_id} />}
        at={t.updated_at}
      />
    );
  };
  const state: PageState = detail.isError ? 'fault' : !detail.data ? 'loading' : !tasks.length ? 'empty' : 'ready';
  return (
    <List
      title={name}
      path={`/tags/${tag}`}
      crumb={[{ label: 'Tags', to: '/tags' }]}
      state={state}
      empty={{ title: `Nothing tagged ${name}` }}
      onRetry={() => void detail.refetch()}
      className="tasks-page"
      sections={
        <>
          {groups.map((g) => (
            <Section key={g.pipe?.id ?? 'none'} name={g.pipe?.name ?? 'Tasks'} count={g.rows.length}>
              {g.rows.map(row)}
            </Section>
          ))}
          {done.length > 0 && (
            <Section name="Done" count={done.length} defaultOpen={false}>
              {done.map(row)}
            </Section>
          )}
        </>
      }
    />
  );
}
