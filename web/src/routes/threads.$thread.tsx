import { createFileRoute, redirect } from '@tanstack/react-router';
import { CHAT_TAB } from '@/features/task/routing';
import { ThreadsPage } from '@/features/threads/exports';
import { loadThreads, ownTaskOf, threadQuery } from '@/features/threads/routing';

/* One thread. New message, Mail and Messaging have routes of their own, which win over this one. A task's own chat
   opens as the task, on its chat: there the person's words are comments on it, and the phone's bar has its agent. */
export const Route = createFileRoute('/threads/$thread')({
  loader: async ({ params, context: { queryClient }, preload }) => {
    await loadThreads(queryClient, { thread: params.thread, preload });
    const task = ownTaskOf(queryClient.getQueryData(threadQuery(params.thread).queryKey));
    if (task) throw redirect({ to: '/task/$task', params: { task }, search: { tab: CHAT_TAB } as never, replace: true });
  },
  component: ThreadRoute,
});

function ThreadRoute() {
  const { thread } = Route.useParams();
  return <ThreadsPage pane="thread" thread={thread} />;
}
