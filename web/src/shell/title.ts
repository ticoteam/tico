import { useEffect } from 'react';
import { pageName } from '@/tico/pageName';

/** The browser tab's title for a page name: "<name> - Tico", or "Tico" while the page has none. */
export const tabTitle = (name: string) => (name ? `${name} - Tico` : 'Tico');

/** Keeps the tab's title on the open page's name. The name is the page's h1 (as Tico reads it), which arrives with the
    page's data and changes on a rename, so the title follows the page's text rather than the address. */
export function useTabTitle() {
  useEffect(() => {
    const put = () => {
      const t = tabTitle(pageName());
      if (document.title !== t) document.title = t;
    };
    put();
    const watch = new MutationObserver(put);
    watch.observe(document.body, { subtree: true, childList: true, characterData: true });
    return () => {
      watch.disconnect();
      document.title = 'Tico';
    };
  }, []);
}
