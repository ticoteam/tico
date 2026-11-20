import { useLocation } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { Skeleton } from '@/kit/Skeleton';
import { Page, PageBody, PanelSlot, SkRows } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface ListProps extends PageBaseProps {
  /** The underlined tab row under the head. */
  tabs?: readonly PageTab[];
  /** At the tab row's end: the controls that act on the open tab. */
  tabsAfter?: ReactNode;
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

/* The pages under an area that are named by their own step (the menu's Company and Library rows): their bars carry
   the page's name, so the title does not change when the page arrives. */
const NAMED = new Set(['strategy', 'finance', 'growth', 'products', 'risk', 'automations', 'listening', 'rules', 'usage', 'files', 'docs', 'tools', 'skills', 'contacts']);
const word = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The title a page has before its reads arrive: its own name where the address says it ("/library/docs" is Docs),
    else its area's ("/settings/people" is Settings). */
export function pendingTitle(path: string): string {
  const [, area = '', step = ''] = path.split('/');
  if ((area === 'company' || area === 'library') && NAMED.has(step)) return word(step);
  return area ? word(area) : 'Tico';
}

/** The router's page while a new page's reads are on their way. The router holds the old page for 150 ms first
    (`defaultPendingMs`), so the bars show at once here. */
export function PagePending() {
  const path = useLocation({ select: (l) => l.pathname });
  return (
    <Page kind="list" title={pendingTitle(path)} path={path} skeleton={null}>
      <div className="pk-loading" role="status" aria-busy="true" aria-label="Loading">
        <ListSkeleton />
      </div>
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
