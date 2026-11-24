import { createFileRoute } from '@tanstack/react-router';
import { openCold } from '@/features/task/routing';

/* An old Flow link to a task: the task over Tasks, with its tab, ask, version and chat. */
export const Route = createFileRoute('/flow/$task')({
  beforeLoad: openCold,
  component: () => null,
});
