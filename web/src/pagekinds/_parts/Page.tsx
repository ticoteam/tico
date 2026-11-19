import type { ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Skeleton, useDelayed } from '@/kit/Skeleton';
import '@/pagekinds/pagekinds.css';
import type { PageBaseProps, PageTab, StateProps } from '@/pagekinds/types';
import { PageBody } from './PageBody';
import { PageHead } from './PageHead';
import { PageTabs } from './PageTabs';

export interface StateBodyProps extends StateProps {
  /** The page's name, for "<title> did not load". */
  title: string;
  /** The kind's grey bars: a whole body in the kind's own shape. */
  skeleton: ReactNode;
  /** The body when ready. */
  children: ReactNode;
}

/** The body for each state: the slots when ready, the kind's bars while loading, one line and one action otherwise. */
export function StateBody({ state = 'ready', title, empty, onRetry, noAccess, skeleton, children }: StateBodyProps) {
  if (state === 'loading') return <Loading>{skeleton}</Loading>;
  if (state === 'ready') return children;
  return (
    <PageBody className="is-empty">
      {state === 'empty' ? (
        <Empty title={empty?.title ?? 'Nothing here yet'}>{empty?.action}</Empty>
      ) : state === 'fault' ? (
        <Empty icon="alert" title={`${title} did not load`} text="Check your connection and try again.">
          {onRetry && (
            <Button variant="primary" onClick={onRetry}>
              Try again
            </Button>
          )}
        </Empty>
      ) : (
        <Empty icon="lock" title="You don't have access to this page" text={noAccess?.text}>
          {noAccess?.actions}
        </Empty>
      )}
    </PageBody>
  );
}

/** A read faster than 150 ms never shows bars (architecture.md 8). No spinner, ever. */
function Loading({ children }: { children: ReactNode }) {
  const shown = useDelayed(150);
  return (
    <div className="pk-loading" role="status" aria-busy="true" aria-label="Loading">
      {shown && children}
    </div>
  );
}

const WIDTHS = [62, 44, 71, 53, 38, 66, 48, 57];

/** Task-shaped bars for inside a kind's skeleton; `SkeletonRows` is not used there because it times and announces itself. */
export function SkRows({ rows = 6 }: { rows?: number }) {
  return Array.from({ length: rows }, (_, i) => (
    <div key={i} className="row task is-skeleton" aria-hidden="true">
      <span className="row-mark" />
      <Skeleton width={WIDTHS[i % WIDTHS.length]} />
      <span />
      <Skeleton className="sk-av" />
      <Skeleton className="sk-age" />
    </div>
  ));
}

export type PageKind ='list' | 'board' | 'split' | 'record' | 'document' | 'dashboard' | 'settings' | 'calendar';

export interface PageProps extends PageBaseProps {
  kind: PageKind;
  tabs?: readonly PageTab[];
  /** At the tab row's end: the controls that act on the open tab. */
  tabsAfter?: ReactNode;
  /** Under the head and tabs while ready: the filter row. */
  under?: ReactNode;
  skeleton: ReactNode;
  /** Laid over the page when no shell place holds it: the inline side panel. */
  over?: ReactNode;
  children: ReactNode;
}

/** The page sheet every kind but the centered card stands in: head, tab row, the state's body. */
export function Page({ kind, className, tabs, tabsAfter, under, skeleton, over, children, state, empty, onRetry, noAccess, ...head }: PageProps) {
  const cls = `pane page pk pk-${kind}${className ? ` ${className}` : ''}`;
  if (state === 'no-access') {
    // The mock draws no head here: nothing of the page shows (15-start, noaccess).
    return (
      <main className={cls} aria-label={head.title}>
        <StateBody state={state} title={head.title} noAccess={noAccess} skeleton={null}>
          {null}
        </StateBody>
      </main>
    );
  }
  return (
    <main className={cls} aria-label={head.title}>
      <PageHead {...head} />
      {tabs && tabs.length > 0 && <PageTabs tabs={tabs} label={`${head.title} pages`} after={tabsAfter} />}
      {(state ?? 'ready') === 'ready' && under}
      <StateBody state={state} title={head.title} empty={empty} onRetry={onRetry} skeleton={skeleton}>
        {children}
      </StateBody>
      {(state ?? 'ready') === 'ready' && over}
    </main>
  );
}
