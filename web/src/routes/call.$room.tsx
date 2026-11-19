import { createFileRoute } from '@tanstack/react-router';
import { CallPage } from '@/features/calls/exports';
import { callQuery } from '@/features/calls/routing';

/** One call's window. Nothing joins until the person presses Join; leaving the page keeps the call on in the bar. */
export const Route = createFileRoute('/call/$room')({
  loader: ({ context, params }) => {
    void context.queryClient.prefetchQuery(callQuery(params.room));
  },
  component: Page,
});

function Page() {
  return <CallPage room={Route.useParams().room} />;
}
