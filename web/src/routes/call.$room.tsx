import { createFileRoute } from '@tanstack/react-router';
import { CallPage } from '@/features/calls/exports';

/** One call's window. Nothing joins until the person presses Join; leaving the page hangs up. */
export const Route = createFileRoute('/call/$room')({
  component: Page,
});

function Page() {
  return <CallPage room={Route.useParams().room} />;
}
