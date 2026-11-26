import { useLocation, useMatches, useRouter, type ErrorComponentProps } from '@tanstack/react-router';
import { useEffect } from 'react';
import { ApiError } from '@/api/client';
import { forgetView, pageKey } from '@/data';
import { reportError } from '@/features/support/routing';
import { List } from '@/pagekinds';

/** A page that broke while drawing or loading (the router's default error component): the page kinds' fault in the
    page's place, so the menu and Tico stay usable, saying why. Reading again (by itself once the stream is back when
    that could help, else Try again) draws it afresh and reads its data again. A refusal (403) is the no-access state
    instead: reading again would only be refused again. Each fault but a refusal is reported
    (features/support/errors.ts), under the page's route template. */
export function PageFault({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  const path = useLocation({ select: (l) => l.pathname });
  const route = useMatches({ select: (m) => m.at(-1)?.routeId ?? '' });
  const refused = error instanceof ApiError && error.status === 403;
  // a page refused or not found is no recent view (data/recentViews.ts)
  const gone = error instanceof ApiError && (error.status === 403 || error.status === 404);
  useEffect(() => {
    if (gone) forgetView(pageKey(path));
  }, [gone, path]);
  useEffect(() => {
    if (!refused) reportError(error, { source: 'page_fault', route });
  }, [error, refused, route]);
  if (refused) return <List title="This page" path={path} state="no-access" sections={null} />;
  const retry = () => {
    reset();
    void router.invalidate();
  };
  return <List title="This page" path={path} state="fault" reads={[{ error, refetch: retry }]} sections={null} />;
}
