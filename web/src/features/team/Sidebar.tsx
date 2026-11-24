import type { MouseEvent, ReactNode } from 'react';
import { run } from '@/actions';
import type { Card, Place } from '@/api/schema';
import { PageCard } from '@/conversation';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Row, TaskRow } from '@/kit/Row';
import { fmtMoney } from '@/lib';
import { Section } from '@/pagekinds';
import type { SidebarWidget } from './queries';

/* An agent's sidebar as it arranged it (backend/bot_task_tabs.py widgets): its built-ins, and the pages and cards it
   pinned (GET /api/v2/places/bot:<slug>), each in its stored order, hidden ones left out, a set height its most. */

/** The built-ins in the order the page drew them before an agent could arrange them: the server's default too. */
export const DEFAULT_WIDGETS: SidebarWidget[] = ['updates', 'tasks', 'files'].map((id) => ({ id, hidden: false, height: null }));

/** Its sidebar in order: the stored one, or the default when there is none (a read that has not come, or failed). */
export const sidebarOf = (widgets: SidebarWidget[] | undefined): SidebarWidget[] => (widgets?.length ? widgets : DEFAULT_WIDGETS);

/** A pinned page or card, not a built-in. */
export const isPinned = (id: string) => id.startsWith('page:') || id.startsWith('card:');

export type Pin = Place['pins'][number];

/** Whether a pin shows this reader anything: a page they may read (the place leaves the rest out), a card not hidden. */
export const pinShows = (pin: Pin | undefined): pin is Pin => !!pin && (!!pin.page || (!!pin.card && !pin.card.hidden));

/** Its height when it has one set: the most it grows to, then it scrolls. */
export function Sized({ height, children }: { height: number | null; children: ReactNode }) {
  if (!height) return children;
  return (
    <div className="side-sized" style={{ maxHeight: height }}>
      {children}
    </div>
  );
}

const plainClick = (e: MouseEvent) => e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
const fileHref = (id: string): `/api/${string}` => `/api/v2/files/${encodeURIComponent(id)}`;
const openFile = (id: string) => (e: MouseEvent) => {
  if (!plainClick(e)) return;
  e.preventDefault();
  void run('files.preview', { file: id }, { via: 'click' });
};

/** A pinned page or card, drawn for this reader; nothing when it shows them nothing (or has not been read yet). */
export function PinnedItem({ pin }: { pin: Pin | undefined }) {
  if (!pinShows(pin)) return null;
  if (pin.page) {
    const { file_id: id, title } = pin.page;
    return (
      <section className="stage pk-sec side-page" aria-label={title} data-target={`page:${id}`}>
        <PageCard src={fileHref(id)} title={title} onOpen={() => void run('files.preview', { file: id }, { via: 'click' })} />
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
interface CardData {
  count?: number;
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
}

const taskPath = (id: string) => `/task/${encodeURIComponent(id)}`;

/** A row that is only words: a Needs you item names its task when it has one. */
function WordsRow({ title, href }: { title: string; href?: string }) {
  return (
    <Row className="task" title={title} href={href}>
      <span className="row-mark" />
      <span className="row-title">{title}</span>
      <span className="row-pills" />
      <span className="av-gap" />
      <span />
    </Row>
  );
}

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
            <WordsRow key={it.id ?? i} title={it.title ?? ''} href={it.task_id ? taskPath(it.task_id) : undefined} />
          ))}
        </Section>
      );
    case 'my_tasks':
      return (
        <Section name={name} count={d.count || undefined}>
          {(d.tasks ?? []).map((t) => (
            <TaskRow key={t.id} title={t.title ?? ''} href={taskPath(t.id ?? '')} at={t.updated_at ?? new Date(0).toISOString()} />
          ))}
        </Section>
      );
    case 'unread_threads':
      return (
        <Section name={name} count={d.count || undefined}>
          {(d.threads ?? []).map((t) => (
            <TaskRow key={t.id} title={t.title ?? ''} href={`/threads/${encodeURIComponent(t.id ?? '')}`} at={t.updated_at ?? new Date(0).toISOString()} />
          ))}
        </Section>
      );
    case 'updates':
      return (
        <Section name={name}>
          {(d.updates ?? []).map((u) => (
            <TaskRow key={u.id} title={u.headline ?? ''} href="/updates" at={u.created_at ?? new Date(0).toISOString()} />
          ))}
        </Section>
      );
    case 'latest_pages':
      return (
        <Section name={name}>
          {(d.pages ?? []).map((p) => (
            <TaskRow
              key={p.file_id}
              title={p.title ?? ''}
              href={fileHref(p.file_id ?? '')}
              onClick={openFile(p.file_id ?? '')}
              at={p.updated_at ?? new Date(0).toISOString()}
            />
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
      return (
        <Section name={name}>
          <Facts label={name}>
            <LabelValue label="Runs today">{d.total ?? 0}</LabelValue>
            <LabelValue label="Failed">{d.failed ?? 0}</LabelValue>
          </Facts>
        </Section>
      );
    default:
      return <Section name={name}>{null}</Section>;
  }
}
