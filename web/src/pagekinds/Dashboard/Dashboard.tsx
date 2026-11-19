import type { ReactNode } from 'react';
import { Skeleton } from '@/kit/Skeleton';
import { Page, PageBody, PanelSlot, SkRows } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface DashboardProps extends PageBaseProps {
  /** Scope tabs: All and each segment. */
  tabs?: readonly PageTab[];
  /** At the tab row's end: the controls that act on the open tab. */
  tabsAfter?: ReactNode;
  /** Four stats across, each with a sparkline (`<FiguresStrip>`). */
  figures?: ReactNode;
  /** One wide chart. */
  chart?: ReactNode;
  /** Sections side by side, two to a row: funnels, a table, waits on you (`<Section>`s). One column on a phone. */
  widgets?: ReactNode;
  /** A row opened, as a sheet (`<SidePanel>`). */
  panel?: ReactNode;
}

/** Figures across the top, one wide chart, then sections side by side. Read-mostly; a row opens a panel. */
export function Dashboard({ tabs, figures, chart, widgets, panel, ...page }: DashboardProps) {
  return (
    <Page kind="dashboard" tabs={tabs} over={panel && <PanelSlot>{panel}</PanelSlot>} skeleton={<DashboardSkeleton />} {...page}>
      <PageBody>
        {figures}
        {chart && <div className="pk-chart">{chart}</div>}
        {widgets && <div className="pk-widgets">{widgets}</div>}
      </PageBody>
    </Page>
  );
}

/** Four figure bars, a chart block, two columns of rows. */
export function DashboardSkeleton() {
  return (
    <PageBody>
      <div className="widgets pk-figs" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="widget">
            <div className="widget-main pk-sk-fig">
              <Skeleton width={50} />
              <Skeleton className="sk-title" />
            </div>
          </div>
        ))}
      </div>
      <div className="pk-chart pk-sk-chart" aria-hidden="true" />
      <div className="pk-widgets" aria-hidden="true">
        <div>
          <SkRows rows={4} />
        </div>
        <div>
          <SkRows rows={4} />
        </div>
      </div>
    </PageBody>
  );
}
