import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/company/strategy')({
  component: () => <Placeholder title="Strategy" />,
});
