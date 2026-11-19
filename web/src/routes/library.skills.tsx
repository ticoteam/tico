import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/library/skills')({
  component: () => <Placeholder title="Skills" />,
});
