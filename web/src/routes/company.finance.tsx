import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/company/finance')({
  component: () => <Placeholder title="Finance" />,
});
