import type { QueryClient } from '@tanstack/react-query';
import { Outlet, createRootRouteWithContext } from '@tanstack/react-router';
import { Placeholder } from '@/kit/placeholder/Placeholder';
import { Frame } from '@/shell';

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: Root,
  notFoundComponent: () => <Placeholder title="Not found" />,
});

function Root() {
  return <Frame tico={null} menu={null} page={<Outlet />} />;
}
