import type { ReactNode } from 'react';
import { Empty } from '@/kit/Empty';
import './threads.css';

export function ThreadPane({ children }: { children: ReactNode }) {
  return (
    <main className="pane page page-thread pk pk-split is-open" aria-label="Thread">
      <section className="conv" aria-label="Thread">{children}</section>
    </main>
  );
}

export function ThreadsIndex() {
  return (
    <ThreadPane>
      <div className="conv-body">
        <Empty icon="threads" title="No thread open" />
      </div>
    </ThreadPane>
  );
}
