import { Link, type LinkProps } from '@tanstack/react-router';
import { Count } from '@/kit/Count';
import type { PageTab } from '@/pagekinds/types';

export interface PageTabsProps {
  tabs: readonly PageTab[];
  /** Names the row for a screen reader; the page's title by default. */
  label: string;
}

const ON = { className: 'is-on', 'aria-current': 'page' as const };

/**
 * The one tab row: underlined, right under the head, one line that scrolls sideways and never wraps. Never the
 * segmented pill (aaa-audit-a Top 5); a pill only switches the view of the same data. Each tab is a link.
 */
export function PageTabs({ tabs, label }: PageTabsProps) {
  return (
    <nav className="page-tabs pk-tabs" aria-label={label}>
      <div className="docs">
        {tabs.map((t) => (
          <Link
            key={t.to}
            className="doc-tab"
            to={t.to as LinkProps['to']}
            activeOptions={{ exact: t.exact ?? true, includeSearch: false }}
            activeProps={ON}
            data-target={t.target}
          >
            <span data-t={t.label}>{t.label}</span>
            {t.count !== undefined && <Count n={t.count} />}
          </Link>
        ))}
      </div>
    </nav>
  );
}
