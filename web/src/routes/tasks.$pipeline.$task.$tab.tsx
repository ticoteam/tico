import { createFileRoute } from '@tanstack/react-router';
import { openCold } from '@/features/task/routing';

/* `/tasks/<pipeline>/<n>/<tab>`: the task over its board, on that tab (Details' file, a version, an ask kept). */
export const Route = createFileRoute('/tasks/$pipeline/$task/$tab')({
  beforeLoad: openCold,
  component: () => null,
});
