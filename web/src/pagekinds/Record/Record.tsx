import type { ReactNode } from 'react';
import { Skeleton, useDelayed } from '@/kit/Skeleton';
import { Page, PageBody, PanelSlot, SidePanel, SkRows, type SidePanelProps } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface RecordProps extends PageBaseProps {
  /** About, People, Threads, Files. */
  tabs?: readonly PageTab[];
  /** At the tab row's end: the controls that act on the open tab. */
  tabsAfter?: ReactNode;
  /** Label and value (`<Facts>` from `@/kit/LabelValue`); each saves at once. */
  facts?: ReactNode;
  /** A few stats (`<FiguresStrip>`). */
  figures?: ReactNode;
  /** Its rows: rules, dates, matters, tasks (`<Section>`s). */
  sections?: ReactNode;
  /** Facts in a column at the right. On a phone they fold in under `facts`. */
  side?: ReactNode;
  /** The open row (a `<SidePanel>`): a matter, a date. Drawn in the shell's panel place. */
  panel?: ReactNode;
}

/** One thing: label and value, its own sections, a side column. Reached from a row; its crumb leads back. */
export function Record({ tabs, facts, figures, sections, side, panel, ...page }: RecordProps) {
  return (
    <Page kind="record" tabs={tabs} over={panel && <PanelSlot>{panel}</PanelSlot>} skeleton={<RecordSkeleton />} {...page}>
      <PageBody>
        <div className={side ? 'pk-rec has-side' : 'pk-rec'}>
          {facts && <div className="pk-rec-facts">{facts}</div>}
          {side && (
            <aside className="pk-rec-side" aria-label="Details">
              {side}
            </aside>
          )}
          {figures && <div className="pk-rec-figs">{figures}</div>}
          {sections && <div className="pk-rec-secs">{sections}</div>}
        </div>
      </PageBody>
    </Page>
  );
}

const FACT_W = [34, 52, 28, 46, 40];

/** Label-and-value bars, then rows. */
export function RecordSkeleton() {
  return (
    <PageBody>
      <div className="pk-rec">
        <SkFacts />
        <div className="pk-rec-secs" aria-hidden="true">
          <SkRows rows={4} />
        </div>
      </div>
    </PageBody>
  );
}

function SkFacts() {
  return (
    <div className="pk-rec-facts pk-sk-facts" aria-hidden="true">
      {FACT_W.map((w, i) => (
        <div key={i} className="pk-sk-fact">
          <Skeleton width={60} />
          <Skeleton width={w} />
        </div>
      ))}
    </div>
  );
}

/** A panel's bars in its own shape: label-and-value lines, then a few rows. A read faster than 150 ms shows none. */
function RecordPanelSkeleton() {
  const shown = useDelayed(150);
  return (
    <div className="pk-rec is-panel is-loading" role="status" aria-busy="true" aria-label="Loading">
      {shown && (
        <>
          <SkFacts />
          <div className="pk-rec-secs" aria-hidden="true">
            <SkRows rows={3} />
          </div>
        </>
      )}
    </div>
  );
}

export interface RecordPanelProps extends Omit<SidePanelProps, 'children'> {
  facts?: ReactNode;
  figures?: ReactNode;
  sections?: ReactNode;
  /** Its read is on the way: grey bars in the panel's shape stand for the slots. */
  loading?: boolean;
}

/** A record opened from its list as a side panel (Products, Risk): the same slots in one column. */
export function RecordPanel({ facts, figures, sections, loading, ...panel }: RecordPanelProps) {
  return (
    <SidePanel {...panel}>
      {loading ? (
        <RecordPanelSkeleton />
      ) : (
        <div className="pk-rec is-panel">
          {facts && <div className="pk-rec-facts">{facts}</div>}
          {figures && <div className="pk-rec-figs">{figures}</div>}
          {sections && <div className="pk-rec-secs">{sections}</div>}
        </div>
      )}
    </SidePanel>
  );
}
