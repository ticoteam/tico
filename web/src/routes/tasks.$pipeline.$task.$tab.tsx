import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/tasks/$pipeline/$task/$tab')({
  component: () => <Placeholder title="Task" />,
});
