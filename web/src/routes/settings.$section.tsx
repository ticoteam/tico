import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/settings/$section')({
  component: () => <Placeholder title="Settings" />,
});
