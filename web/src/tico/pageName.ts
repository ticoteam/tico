import { useLocation } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

/* The open page's name, as the person reads it: the h1 in the page's sheet (a record's name, "Tasks", a thread's
   title). It is what Tico's chip shows and what a message to Tico says the person was looking at. */
const H1 = '.pane.page h1';

export const pageName = (): string => document.querySelector(H1)?.textContent?.trim() ?? '';

/** The page's name, kept current while the page loads and when it changes. */
export function usePageName(): string {
  const path = useLocation({ select: (l) => l.pathname });
  const [name, setName] = useState(pageName);
  useEffect(() => {
    const read = () => setName(pageName());
    read();
    // the h1 arrives with the page's data, after the address changed; a rename while open changes its text
    const watch = new MutationObserver(read);
    watch.observe(document.getElementById('root') ?? document.body, { subtree: true, childList: true, characterData: true });
    return () => watch.disconnect();
  }, [path]);
  return name;
}
