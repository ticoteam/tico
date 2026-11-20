import { QueryClientProvider } from '@tanstack/react-query';
import { Link as RouterLink, RouterProvider } from '@tanstack/react-router';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { queryClient } from '@/data/query-client';
import { setLinkComponent, type LinkProps } from '@/kit/Link';
import { appHref, router } from '@/router';
import { markDesktop } from '@/shell';
import '@/styles/index.css';

/* The kit's links move through the router (no page load). An address is app-relative ("/tasks/engineering?view=list"),
   or an old app address (`/#/task/12`) that opens its new page; the router reads the search the way its routes do. */
function AppLink({ href, ...rest }: LinkProps) {
  const u = new URL(appHref(href), location.origin);
  return (
    <RouterLink
      {...rest}
      to={u.pathname as never}
      search={router.options.parseSearch(u.search) as never}
      hash={u.hash.slice(1) || undefined}
    />
  );
}
setLinkComponent(AppLink);
// the desktop preload marks the page before the first paint; a page it marked late is marked here
markDesktop();

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no #root');

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
