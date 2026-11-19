import type { ReactNode } from 'react';
import { Skeleton } from '@/kit/Skeleton';
import { Page, PageBody } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface DocumentProps extends PageBaseProps {
  /** The thing's pages (Spec, QA report), right under the head. */
  tabs?: readonly PageTab[];
  /** At the tab row's end: the controls that act on the open tab. */
  tabsAfter?: ReactNode;
  /** Reading text, or a page that keeps its own look (`framed`). */
  body: ReactNode;
  /** The body is a page in a frame (`.frame`): it fills the width and keeps no reading measure. */
  framed?: boolean;
  /** Outline (a doc) or facts (a skill, a file), in a column at the right. */
  side?: ReactNode;
  /** Approve or send back, when it waits on you. Stays under the body. */
  foot?: ReactNode;
}

/** Something to read and decide on: a spec, a QA report, an update, a skill. Comes with a small crumb trail. */
export function Document({ tabs, body, framed = false, side, foot, ...page }: DocumentProps) {
  return (
    <Page kind="document" tabs={tabs} skeleton={<DocumentSkeleton />} {...page}>
      <div className={side ? 'pk-doc has-side' : 'pk-doc'}>
        <PageBody className={framed ? 'pk-doc-body is-framed' : 'read-body pk-doc-body'}>
          {framed ? body : <article className="read">{body}</article>}
        </PageBody>
        {side && (
          <aside className="pk-doc-side" aria-label="Outline">
            {side}
          </aside>
        )}
      </div>
      {foot && <footer className="pk-foot">{foot}</footer>}
    </Page>
  );
}

const LINES = [92, 86, 95, 64, 0, 88, 90, 72, 0, 84, 58];

/** Lines of reading text, with paragraph gaps. */
export function DocumentSkeleton() {
  return (
    <PageBody className="read-body">
      <div className="read pk-sk-read" aria-hidden="true">
        {LINES.map((w, i) => (w ? <Skeleton key={i} width={w} /> : <span key={i} className="pk-sk-gap" />))}
      </div>
    </PageBody>
  );
}
