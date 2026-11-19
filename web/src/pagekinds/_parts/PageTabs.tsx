import { Link, useLocation, type LinkProps } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { Count } from '@/kit/Count';
import type { PageTab } from '@/pagekinds/types';

export interface PageTabsProps {
  tabs: readonly PageTab[];
  /** Names the row for a screen reader; the page's title by default. */
  label: string;
  /** At the row's end: the controls that act on the open tab (a version, Add). */
  after?: ReactNode;
}

const trim = (p: string) => (p.length > 1 ? p.replace(/\/+$/, '') : p);

/**
 * On at its address (or under it, when not exact), and while the address's search matches the tab's own for every key
 * the row's tabs use: a tab with no search of its own (Overview) is off once a sibling's `?tab=` is in the address.
 */
function isOn(t: PageTab, path: string, search: Record<string, unknown>, rowKeys: readonly string[]): boolean {
  const here = trim(path);
  const there = trim(t.to.split('?')[0] ?? t.to);
  const pathOn = (t.exact ?? true) ? here === there : here === there || here.startsWith(`${there}/`);
  return pathOn && rowKeys.every((k) => String(search[k] ?? '') === (t.search?.[k] ?? ''));
}

/**
 * The one tab row: underlined, right under the head, one line that scrolls sideways and never wraps. Never the
 * segmented pill (aaa-audit-a Top 5); a pill only switches the view of the same data. Each tab is a link.
 */
export function PageTabs({ tabs, label, after }: PageTabsProps) {
  const at = useLocation();
  const search = at.search as Record<string, unknown>;
  const rowKeys = [...new Set(tabs.flatMap((t) => Object.keys(t.search ?? {})))];
  return (
    <nav className={after ? 'page-tabs pk-tabs has-after' : 'page-tabs pk-tabs'} aria-label={label}>
      <div className="docs">
        {tabs.map((t) => {
          const on = isOn(t, at.pathname, search, rowKeys);
          return (
            <Link
              key={`${t.to}?${new URLSearchParams(t.search).toString()}`}
              className={on ? 'doc-tab is-on' : 'doc-tab'}
              aria-current={on ? 'page' : undefined}
              to={t.to as LinkProps['to']}
              search={t.search as never}
              data-target={t.target}
            >
              {t.mark}
              <span data-t={t.label}>{t.label}</span>
              {t.count !== undefined && <Count n={t.count} />}
            </Link>
          );
        })}
      </div>
      {after}
    </nav>
  );
}
