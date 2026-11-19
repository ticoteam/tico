import { useQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Row } from '@/kit/Row';
import { Section } from '@/pagekinds';
import { Capped, LIST_CAP, LIST_SHOW, RowMore, ROW_H } from './capped';
import { Face, taskHref } from './parts';
import { loadMoreOf, shelfParams, shelfQuery, shelfTotal, type Filters, type Pipeline, type Shelf, type TaskItem } from './queries';

const SHELVES: readonly [Shelf, string][] = [
  ['snoozed', 'Snoozed'],
  ['done', 'Done'],
  ['closed', 'Closed'],
];

/** The three shelves' reads, for the page's state: loading until all are in, a fault if any failed. */
export function useShelves(pipeline: string, f: Filters) {
  const snoozed = useQuery(shelfQuery(pipeline, 'snoozed', f));
  const done = useQuery(shelfQuery(pipeline, 'done', f));
  const closed = useQuery(shelfQuery(pipeline, 'closed', f));
  const all = [snoozed, done, closed];
  return {
    reads: { snoozed, done, closed },
    loading: all.some((q) => !q.data),
    fault: all.some((q) => q.isError),
    empty: all.every((q) => q.data && !q.data.tasks.length),
    retry: () => all.forEach((q) => q.isError && void q.refetch()),
  };
}

/** Archived (list.js `archivedBody`): Snoozed, Done, Closed, each a section of rows that name their stage. */
export function ArchivedSections({ p, filters, shelves }: { p: Pipeline; filters: Filters; shelves: ReturnType<typeof useShelves> }) {
  const names = new Map<string, string>([...p.stages, ...(p.done ? [p.done] : []), ...(p.closed ? [p.closed] : [])].map((s) => [s.id, s.name]));
  return SHELVES.map(([shelf, name]) => {
    const page = shelves.reads[shelf].data;
    if (!page?.tasks.length) return null;
    const cut = !!page.next_after;
    return (
      <Section key={shelf} name={name} count={shelfTotal(page)} target={`archived:${shelf}`}>
        <Capped
          list={page.tasks}
          cap={LIST_CAP}
          show={LIST_SHOW}
          height={ROW_H}
          more={cut ? () => void loadMoreOf(shelfParams(p.id, shelf, filters)) : undefined}
          draw={(t) => <ArchRow key={t.id} t={t} p={p} stage={t.stage_id ? names.get(t.stage_id) : undefined} />}
          moreRow={(n, open) => <RowMore key="more" n={n} cut={cut} onClick={open} />}
        />
      </Section>
    );
  });
}

function ArchRow({ t, p, stage }: { t: TaskItem; p: Pipeline; stage: string | undefined }) {
  return (
    <Row className="task is-arch" href={taskHref(p.id, t)} title={t.title} target={`task:${t.number ?? t.id}`}>
      <span className="row-mark" />
      <span className="row-title">{t.title}</span>
      <span className="row-stage">{stage}</span>
      {t.owner_id ? <Face actor={t.owner_id} /> : <span className="av-gap" />}
      <Ago at={t.updated_at} className="row-age" />
    </Row>
  );
}
