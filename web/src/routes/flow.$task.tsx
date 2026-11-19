import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/flow/$task')({
  component: () => <Placeholder title="Flow" />,
});
