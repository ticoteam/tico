import { createFileRoute } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';

export const Route = createFileRoute('/tasks/$pipeline/$task/')({
  component: () => <Placeholder title="Task" />,
});
