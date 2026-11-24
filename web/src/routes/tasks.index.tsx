import { createFileRoute } from '@tanstack/react-router';
import { TasksHome, TasksHomePending } from '@/features/tasks/exports';
import { flowQuery, pipelinesQuery } from '@/features/tasks/routing';

/** Tasks: Needs you on top, then the pipelines. The pipelines are waited for; Needs you's read starts here and its
    section shows it as it comes (or fails), never holding the page. */
export const Route = createFileRoute('/tasks/')({
  loader: ({ context: { queryClient } }) => {
    void queryClient.prefetchQuery(flowQuery);
    return queryClient.ensureQueryData(pipelinesQuery);
  },
  pendingComponent: TasksHomePending,
  component: TasksHome,
});
