import { createFileRoute } from '@tanstack/react-router';
import { openCold } from '@/features/task/routing';

/* `/tasks/<pipeline>/<n>`: the task over its board, with the board's list beside it. */
export const Route = createFileRoute('/tasks/$pipeline/$task/')({
  beforeLoad: openCold,
  component: () => null,
});
