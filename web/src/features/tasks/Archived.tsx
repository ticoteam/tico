import { useSuspenseQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Pill } from '@/kit/Pill';
import { useCards } from '@/kit/Popover';
import { Row } from '@/kit/Row';
import { Section } from '@/pagekinds';
import { Capped, LIST_CAP, LIST_SHOW, PHONE_ROW_H, RowMore, ROW_H } from './capped';
import { Face, taskHref } from './parts';
import { loadMoreOf, shelfParams, shelfQuery, shelfTotal, type Filters, type Pipeline, type Shelf, type TaskItem, type TaskPage } from './queries';

const SHELVES: readonly [Shelf, string][] = [
  ['snoozed', 'Snoozed'],
  ['done', 'Done'],
  ['closed', 'Closed'],
];

/** The three shelves, which the route's loader has read. */
export function useShelves(pipeline: string, f: Filters): Record<Shelf, TaskPage> {
  return {
    snoozed: useSuspenseQuery(shelfQuery(pipeline, 'snoozed', f)).data,
    done: useSuspenseQuery(shelfQuery(pipeline, 'done', f)).data,
    closed: useSuspenseQuery(shelfQuery(pipeline, 'closed', f)).data,
  };
}

/** Archived (list.js `archivedBody`): Snoozed, Done, Closed, each a section of rows that name their stage. */
export function ArchivedSections({ p, filters, shelves }: { p: Pipeline; filters: Filters; shelves: ReturnType<typeof useShelves> }) {
  const cards = useCards();
  const names = new Map<string, string>([...p.stages, ...(p.done ? [p.done] : []), ...(p.closed ? [p.closed] : [])].map((s) => [s.id, s.name]));
  return SHELVES.map(([shelf, name]) => {
    const page = shelves[shelf];
    if (!page.tasks.length) return null;
    const cut = !!page.next_after;
    return (
      <Section key={shelf} name={name} count={shelfTotal(page)} target={`archived:${shelf}`}>
        <Capped
          list={page.tasks}
          cap={LIST_CAP}
          show={LIST_SHOW}
          height={cards ? PHONE_ROW_H : ROW_H}
          measure={cards}
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
      {/* Paid shots to reconcile (backend/paid_renders.py) still wait on a person on a card that ended. */}
      {t.reconcile && <Pill>{t.reconcile.line}</Pill>}
      {t.owner_id ? <Face actor={t.owner_id} /> : <span className="av-gap" />}
      <Ago at={t.updated_at} className="row-age" />
    </Row>
  );
}
