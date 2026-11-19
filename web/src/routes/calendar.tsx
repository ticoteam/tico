import { createFileRoute } from '@tanstack/react-router';
import { CalendarPage } from '@/features/calls/exports';
import { calendarSearch } from '@/features/calls/routing';

/** Calendar: `?view=` day, 3day, week, month or list; `?date=` the day it shows; `?panel=event:<id>` or `meeting:<id>` beside. */
export const Route = createFileRoute('/calendar')({
  validateSearch: calendarSearch,
  component: CalendarPage,
});
