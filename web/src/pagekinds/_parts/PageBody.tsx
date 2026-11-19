import type { ReactNode } from 'react';

export interface PageBodyProps {
  children?: ReactNode;
  /** Extra classes: the page's own body class from the mock (`tasks-body`, `rec-body`). */
  className?: string;
  /** Names the scroller when a page has more than one. */
  label?: string;
}

/**
 * The page's scroller. Text stands on `--page-x` left and right; marks and fold chevrons hang into that margin. What
 * runs past the bottom edge fades instead of being cut. `data-scroll` lets the phone keep the typed-in line in view.
 * The router remembers its place per page (Back returns there) and opens any other page at its top (router.tsx).
 */
export function PageBody({ children, className, label }: PageBodyProps) {
  return (
    <div
      className={className ? `page-body pk-body ${className}` : 'page-body pk-body'}
      data-scroll
      data-scroll-restoration-id="page-body"
      aria-label={label}
      role={label ? 'region' : undefined}
    >
      {children}
    </div>
  );
}
