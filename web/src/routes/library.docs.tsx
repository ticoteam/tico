import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/library/docs')({
  component: () => <Placeholder title="Docs" />,
});
