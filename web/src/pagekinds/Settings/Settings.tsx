import type { ReactNode } from 'react';
import { Skeleton } from '@/kit/Skeleton';
import { Page, SkRows } from '@/pagekinds/_parts';
import type { PageBaseProps } from '@/pagekinds/types';

export interface SettingsProps extends PageBaseProps {
  /** The section list, General to Changelog: links styled `a.row.set-link`, the open one `aria-current="page"`. */
  nav: ReactNode;
  /** The open section: folding sections of label and value. */
  body: ReactNode;
  /** Rare: a destructive action, named. */
  foot?: ReactNode;
  /** A section is open. A phone shows the section list until then, and the section alone after. */
  open?: boolean;
}

/** A section list on the left, the open section's label and value rows on the right. */
export function Settings({ nav, body, foot, open = false, className, ...page }: SettingsProps) {
  const cls = `${open ? 'is-open' : 'is-list'}${className ? ` ${className}` : ''}`;
  return (
    <Page kind="settings" className={cls} skeleton={<SettingsSkeleton />} {...page}>
      <div className="split">
        <nav className="set-nav" aria-label="Sections" data-scroll>
          {nav}
        </nav>
        <div className="set-body" data-scroll>
          {body}
          {foot && <footer className="pk-set-foot">{foot}</footer>}
        </div>
      </div>
    </Page>
  );
}

const NAV_W = [58, 44, 66, 50, 38, 62];

/** The section list as bars, rows beside it. */
export function SettingsSkeleton() {
  return (
    <div className="split" aria-hidden="true">
      <div className="set-nav">
        {NAV_W.map((w, i) => (
          <div key={i} className="row">
            <Skeleton width={w} />
          </div>
        ))}
      </div>
      <div className="set-body">
        <SkRows rows={6} />
      </div>
    </div>
  );
}
