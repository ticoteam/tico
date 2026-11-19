import { useLocation, useRouter, type ErrorComponentProps } from '@tanstack/react-router';
import { List } from '@/pagekinds';

/** A page that broke while drawing (the router's default error component): the page kinds' fault in the page's place,
    so the menu and Tico stay usable. Try again draws it afresh and reads its data again. */
export function PageFault({ reset }: ErrorComponentProps) {
  const router = useRouter();
  const path = useLocation({ select: (l) => l.pathname });
  const retry = () => {
    reset();
    void router.invalidate();
  };
  return <List title="This page" path={path} state="fault" onRetry={retry} sections={null} />;
}
