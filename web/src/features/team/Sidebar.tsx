import { Fragment, type ReactNode } from 'react';
import { run } from '@/actions';
import type { Card, Place } from '@/api/schema';
import { PageCard } from '@/conversation';
import { Ago } from '@/kit/Ago';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Pill } from '@/kit/Pill';
import { Row, TaskRow } from '@/kit/Row';
import { fmtMoney } from '@/lib';
import { Section } from '@/pagekinds';
import { taskHref } from './parts';
import { readable, type SidebarWidget } from './queries';

/* An agent's sidebar as it arranged it (backend/bot_task_tabs.py widgets): its built-ins, and the pages and cards it
   pinned (GET /api/v2/places/bot:<slug>), each in its stored order, hidden ones left out, a set height its most. */

/** The built-ins in the order the page drew them before an agent could arrange them: the server's default too. */
export const DEFAULT_WIDGETS: SidebarWidget[] = ['updates', 'tasks', 'files'].map((id) => ({ id, hidden: false, height: null }));

/** Its sidebar in order: the stored one, or the default when there is none (a read that has not come, or failed). */
export const sidebarOf = (widgets: SidebarWidget[] | undefined): SidebarWidget[] => (widgets?.length ? widgets : DEFAULT_WIDGETS);

export type Pin = Place['pins'][number];

/** Its pins as this reader may see them now: none once a read of them is refused, else the last read's. */
export const pinsOf = (read: { data?: Place; error: unknown }): Pin[] => readable(read)?.pins ?? [];

/** Whether a pin shows this reader anything: a page they may read (the place leaves the rest out), a card not hidden. */
export const pinShows = (pin: Pin | undefined): pin is Pin => !!pin && (!!pin.page || (!!pin.card && !pin.card.hidden));

/** Live previews drawn at once, as under a message (conversation/Rich.tsx PAGE_REFS); a page past them is its row. */
export const LIVE_PAGES = 3;

/** Its height when it has one set: the most it grows to, then it scrolls. */
export function Sized({ height, children }: { height: number | null; children: ReactNode }) {
  if (!height) return children;
  return (
    <div className="side-sized" style={{ maxHeight: height }}>
      {children}
    </div>
  );
}

const fileHref = (id: string): `/api/${string}` => `/api/v2/files/${encodeURIComponent(id)}`;
/** A file's page in Files (its viewer: a page shown, never downloaded), as Files links it. */
const viewerHref = (id: string) => `/library/files?${new URLSearchParams({ file: id }).toString()}`;

/** A row of words: its age when it has one, an empty cell when not. A file's row opens it in Files in a new tab. */
function CardRow({ title, href, file, at, pills }: { title: string; href?: string; file?: string; at?: string | null; pills?: ReactNode }) {
  const cells = (
    <>
      <span className="row-mark" />
      <span className="row-title">{title}</span>
      <span className="row-pills">{pills}</span>
      <span className="av-gap" />
      {at ? <Ago at={at} className="row-age" /> : <span />}
    </>
  );
  if (file)
    return (
      <Link className="row task" title={title} href={viewerHref(file)} target="_blank" rel="noreferrer">
        {cells}
      </Link>
    );
  return (
    <Row className="task" title={title} href={href}>
      {cells}
    </Row>
  );
}

/** A pinned page or card, drawn for this reader; nothing when it shows them nothing (or has not been read yet). A page
    is its live preview when `live`, else its row. */
export function PinnedItem({ pin, live = true }: { pin: Pin | undefined; live?: boolean }) {
  if (!pinShows(pin)) return null;
  if (pin.page) {
    const { file_id: id, title, updated_at, version } = pin.page;
    // its address changes with each version, so a new one is read, never the cached last one
    const src: `/api/${string}` = version ? `${fileHref(id)}?v=${version}` : `${fileHref(id)}?at=${encodeURIComponent(updated_at)}`;
    return (
      <section className="stage pk-sec side-page" aria-label={title} data-target={`page:${id}`}>
        {live ? (
          <PageCard src={src} title={title} onOpen={() => void run('files.preview', version ? { file: id, version } : { file: id }, { via: 'click' })} />
        ) : (
          <CardRow title={title} file={id} at={updated_at} />
        )}
      </section>
    );
  }
  return pin.card ? <CardSection card={pin.card} /> : null;
}

/* ---------------------------------------------------------------- cards (backend/cards.py DATA) */

interface Line {
  id?: string | null;
  title?: string;
  headline?: string;
  task_id?: string | null;
  file_id?: string;
  updated_at?: string | null;
  created_at?: string;
}
/** A stage of Tasks by stage: its open tasks the reader may read, counted, and the first few. */
interface StageLine {
  id: string;
  name: string;
  count: number;
  tasks: { id: string; number: number | null; pipeline_id: string | null; title: string; updated_at: string | null }[];
}
interface CardData {
  count?: number;
  stages?: StageLine[];
  items?: Line[];
  tasks?: Line[];
  threads?: Line[];
  updates?: Line[];
  pages?: Line[];
  counts?: { open: number; done: number };
  weeks?: { week: string; done: number }[];
  spent?: number;
  budget?: number | null;
  name?: string;
  unit?: string;
  value?: number | null;
  total?: number;
  failed?: number;
  running?: number;
  runs?: { id: string; name?: string; status: string; started_at?: string | null; finished_at?: string | null }[];
}

const taskPath = (id: string) => `/task/${encodeURIComponent(id)}`;

/** A card as the reader's own read of it: its rows, or its figures. */
export function CardSection({ card }: { card: Card }) {
  const d = (card.data ?? {}) as CardData;
  const name = card.heading;
  switch (card.template) {
    case 'needs_you':
    case 'needs_attention':
      return (
        <Section name={name} count={d.count || undefined}>
          {(d.items ?? []).map((it, i) => (
            <CardRow key={it.id ?? i} title={it.title ?? ''} href={it.task_id ? taskPath(it.task_id) : undefined} />
          ))}
        </Section>
      );
    case 'my_tasks':
      return (
        <Section name={name} count={d.count || undefined}>
          {(d.tasks ?? []).map((t) => (
            <CardRow key={t.id} title={t.title ?? ''} href={taskPath(t.id ?? '')} at={t.updated_at} />
          ))}
        </Section>
      );
    case 'unread_threads':
      return (
        <Section name={name} count={d.count || undefined}>
          {(d.threads ?? []).map((t) => (
            <CardRow key={t.id} title={t.title ?? ''} href={`/threads/${encodeURIComponent(t.id ?? '')}`} at={t.updated_at} />
          ))}
        </Section>
      );
    case 'updates':
      return (
        <Section name={name}>
          {(d.updates ?? []).map((u) => (
            <CardRow key={u.id} title={u.headline ?? ''} href="/updates" at={u.created_at} />
          ))}
        </Section>
      );
    case 'latest_pages':
      return (
        <Section name={name}>
          {(d.pages ?? []).map((p) => (
            <CardRow key={p.file_id} title={p.title ?? ''} file={p.file_id} at={p.updated_at} />
          ))}
        </Section>
      );
    case 'tasks_by_stage':
      return (
        <Section name={name} count={d.count || undefined}>
          {(d.stages ?? []).map((s) => (
            <Fragment key={s.id}>
              <div className="stage-fold">
                <span className="fold-label">{s.name}</span>
                <span>{s.count}</span>
              </div>
              {s.tasks.map((t) => (
                <TaskRow key={t.id} title={t.title} href={taskHref(t)} target={`task:${t.number ?? t.id}`} at={t.updated_at ?? new Date(0).toISOString()} />
              ))}
            </Fragment>
          ))}
        </Section>
      );
    case 'tasks_by_status':
      return (
        <Section name={name}>
          <Facts label={name}>
            <LabelValue label="Open">{d.counts?.open ?? 0}</LabelValue>
            <LabelValue label="Done this week">{d.counts?.done ?? 0}</LabelValue>
          </Facts>
        </Section>
      );
    case 'done_per_week': {
      const weeks = d.weeks ?? [];
      return (
        <Section name={name}>
          <Facts label={name}>
            <LabelValue label="This week">{weeks.at(-1)?.done ?? 0}</LabelValue>
            <LabelValue label={`Last ${weeks.length} weeks`}>{weeks.reduce((n, w) => n + w.done, 0)}</LabelValue>
          </Facts>
        </Section>
      );
    }
    case 'spend':
      return (
        <Section name={name}>
          <Facts label={name}>
            <LabelValue label="Spent">{fmtMoney(d.spent ?? 0, { cents: true })}</LabelValue>
            {d.budget != null && <LabelValue label="Budget">{fmtMoney(d.budget)}</LabelValue>}
          </Facts>
        </Section>
      );
    case 'kpi':
      return (
        <Section name={name}>
          <Facts label={name}>
            <LabelValue label={d.name ?? 'Value'}>{d.value == null ? 'None yet' : `${d.value}${d.unit ? ` ${d.unit}` : ''}`}</LabelValue>
          </Facts>
        </Section>
      );
    case 'automation_runs':
      // the server counts the last 24 hours (backend/cards.py automation_runs), its failed runs first
      return (
        <Section name={name}>
          <Facts label={name}>
            <LabelValue label="Last 24 hours">{d.total ?? 0}</LabelValue>
            <LabelValue label="Failed">{d.failed ?? 0}</LabelValue>
          </Facts>
          {(d.runs ?? [])
            .filter((r) => r.status === 'failed')
            .map((r) => (
              <CardRow key={r.id} title={r.name ?? ''} at={r.finished_at ?? r.started_at} pills={<Pill flag>Failed</Pill>} />
            ))}
        </Section>
      );
    default:
      return <Section name={name}>{null}</Section>;
  }
}
