import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/task/$task')({
  component: () => <Placeholder title="Task" />,
});
