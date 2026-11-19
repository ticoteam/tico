import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/flow/')({
  component: () => <Placeholder title="Flow" />,
});
