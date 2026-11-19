import { useLocation, useRouter, type ErrorComponentProps } from '@tanstack/react-router';
import { ApiError } from '@/api/client';
import { List } from '@/pagekinds';

/** A page that broke while drawing or loading (the router's default error component): the page kinds' fault in the
    page's place, so the menu and Tico stay usable. Try again draws it afresh and reads its data again. A refusal (403)
    is the no-access state instead, without Try again: reading again would only be refused again. */
export function PageFault({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  const path = useLocation({ select: (l) => l.pathname });
  if (error instanceof ApiError && error.status === 403) return <List title="This page" path={path} state="no-access" sections={null} />;
  const retry = () => {
    reset();
    void router.invalidate();
  };
  return <List title="This page" path={path} state="fault" onRetry={retry} sections={null} />;
}
