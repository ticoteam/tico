import { createFileRoute } from '@tanstack/react-router';
import { CalendarPage } from '@/features/calls/exports';
import { calendarSearch, loadCalendar } from '@/features/calls/routing';

/** Calendar: `?view=` day, 3day, week, month or list; `?date=` the day it shows; `?panel=event:<id>` or `meeting:<id>` beside. */
export const Route = createFileRoute('/calendar')({
  validateSearch: calendarSearch,
  loaderDeps: ({ search: { view, date, review, q, has, panel } }) => ({ view, date, review, q, has, panel }),
  loader: ({ context, deps }) => loadCalendar(context.queryClient, deps),
  component: CalendarPage,
});
