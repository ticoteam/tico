import type { ReactNode } from 'react';
import { Skeleton } from '@/kit/Skeleton';
import { Page, PageBody, PanelSlot, SkRows } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface ListProps extends PageBaseProps {
  /** The underlined tab row under the head. */
  tabs?: readonly PageTab[];
  /** The filter row (`<FilterRow>`), while a filter is set. Folds into the head's filter button on a phone. */
  filter?: ReactNode;
  /** A strip of figures over the sections (`<FiguresStrip>`): Team, Budget. */
  figures?: ReactNode;
  /** Named groups of rows (`<Section>`), or a table. Every named section folds. */
  sections: ReactNode;
  /** The row opened, as its own sheet right of the page (`<SidePanel>`). */
  panel?: ReactNode;
}

/** Named sections of rows, read top to bottom. A row opens a side panel, a record or a task. Most pages are this. */
export function List({ tabs, filter, figures, sections, panel, ...page }: ListProps) {
  return (
    <Page kind="list" tabs={tabs} under={filter} over={panel && <PanelSlot>{panel}</PanelSlot>} skeleton={<ListSkeleton />} {...page}>
      <PageBody>
        {figures}
        {sections}
      </PageBody>
    </Page>
  );
}

/** A section's name and task-shaped rows under it. */
export function ListSkeleton() {
  return (
    <PageBody>
      <section className="stage pk-sec is-skeleton" aria-hidden="true">
        <header className="stage-head">
          <Skeleton className="sk-head" width={18} />
        </header>
        <SkRows rows={7} />
      </section>
    </PageBody>
  );
}
