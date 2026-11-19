import type { ReactNode } from 'react';
import { Skeleton } from '@/kit/Skeleton';
import { Page, PageBody, PanelSlot } from '@/pagekinds/_parts';
import type { PageBaseProps } from '@/pagekinds/types';

export interface CalendarProps extends PageBaseProps {
  /** Hours down, days across (or a month). Put the range and its steps in `after`, the view switch in `actions`. */
  grid: ReactNode;
  /** The same events as a list by day. A phone shows it in place of the grid in Week and Month. */
  agenda?: ReactNode;
  /** The event open (`<SidePanel>`). A raised card on a phone. */
  panel?: ReactNode;
}

/** A time grid: hours down, days across. Steps through dates; an event opens as a panel. */
export function Calendar({ grid, agenda, panel, ...page }: CalendarProps) {
  return (
    <Page kind="calendar" over={panel && <PanelSlot>{panel}</PanelSlot>} skeleton={<CalendarSkeleton />} {...page}>
      <PageBody className={agenda ? 'pk-cal has-agenda' : 'pk-cal'}>
        <div className="pk-cal-grid">{grid}</div>
        {agenda && <div className="pk-cal-agenda">{agenda}</div>}
      </PageBody>
    </Page>
  );
}

/** Seven day columns, a few event bars in each. */
export function CalendarSkeleton() {
  return (
    <PageBody className="pk-cal">
      <div className="pk-sk-cal" aria-hidden="true">
        {[0, 1, 2, 3, 4, 5, 6].map((d) => (
          <div key={d} className="pk-sk-day">
            <Skeleton width={40} />
            {d % 3 !== 2 && <Skeleton className="pk-sk-event" />}
            {d % 2 === 0 && <Skeleton className="pk-sk-event" />}
          </div>
        ))}
      </div>
    </PageBody>
  );
}
