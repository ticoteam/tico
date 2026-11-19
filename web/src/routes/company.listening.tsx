import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/company/listening')({
  component: () => <Placeholder title="Listening" />,
});
