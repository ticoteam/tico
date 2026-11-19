import { createFileRoute } from '@tanstack/react-router';
import { CalendarPage, calendarSearch } from '@/features/calls/exports';

/** Calendar: `?view=` day, 3day, week, month or list; `?date=` the day it shows; `?panel=event:<id>` or `meeting:<id>` beside. */
export const Route = createFileRoute('/calendar')({
  validateSearch: calendarSearch,
  component: CalendarPage,
});
