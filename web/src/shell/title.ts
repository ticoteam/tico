import { useEffect } from 'react';
import { usePageName } from '@/tico/pageName';

/** The browser tab's title for a page name: "<name> - Tico", or "Tico" while the page has none. */
export const tabTitle = (name: string) => (name ? `${name} - Tico` : 'Tico');

/** Keeps the tab's title on the open page's name, the one Tico's chip shows (`usePageName`). */
export function useTabTitle() {
  const name = usePageName();
  useEffect(() => {
    document.title = tabTitle(name);
  }, [name]);
  useEffect(
    () => () => {
      document.title = 'Tico';
    },
    [],
  );
}
