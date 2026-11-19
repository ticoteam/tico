import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/call/$room')({
  component: () => <Placeholder title="Call" />,
});
