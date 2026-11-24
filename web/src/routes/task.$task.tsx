import { createFileRoute } from '@tanstack/react-router';
import { openCold } from '@/features/task/routing';

/* `/task/<n>` (its tab as `?tab=`): the task over Tasks, with Needs you beside it. */
export const Route = createFileRoute('/task/$task')({
  beforeLoad: openCold,
  component: () => null,
});
