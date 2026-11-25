import { createFileRoute, redirect } from '@tanstack/react-router';
import { NeedsYouViews } from '@/features/tasks/exports';
import { PAGE_SEARCH } from '@/lib';

type View = 'all' | 'snoozed' | 'done';
const VIEWS = new Set<unknown>(['all', 'snoozed', 'done']);
const isView = (v: unknown): v is View => VIEWS.has(v);

/* Needs you is the top of Tasks. Its other views (All, Snoozed, Done) are here, from its section there; with none of
   them named (none, or an old one such as Mine), an old Needs you link opens Tasks, the shell's own words (an open
   chat) kept. */
export const Route = createFileRoute('/needs-you')({
  validateSearch: (s: Record<string, unknown>): { view?: View } => (isView(s.view) ? { view: s.view } : {}),
  beforeLoad: ({ search }) => {
    const { view, ...shell } = search as Record<string, unknown>;
    if (!isView(view)) throw redirect({ to: '/tasks', search: shell as never, replace: true });
  },
  component: Page,
});

function Page() {
  const { view } = Route.useSearch(PAGE_SEARCH);
  return isView(view) ? <NeedsYouViews view={view} /> : null;
}
