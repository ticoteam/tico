import type { ReactNode } from 'react';
import { Icon, type IconName } from '@/kit/Icon';
import { Dot } from '@/tico/dot';
import '@/pagekinds/pagekinds.css';

export interface CenteredCardProps {
  /** One line. */
  title: string;
  /** The address, for the title's `data-target="page:<path>"`. */
  path: string;
  /** Under the title: one muted line. */
  text?: ReactNode;
  /** One field, the code boxes, or what is paused (`form.q-form`). */
  body?: ReactNode;
  /** One filled button. */
  actions?: ReactNode;
  /** At the bottom of the card: the Tico support link. */
  foot?: ReactNode;
  /** Over the title; the Dot at 112 unless given. */
  dot?: ReactNode;
  /** Something stopped: this icon leads the title ("alert", "offline", "lock"). */
  fault?: IconName;
  /** For the code boxes and anything wider than one field. */
  wide?: boolean;
}

/**
 * Before or outside the app: sign in, a code, a fault. One column centered on a sheet, no menu. It is itself the
 * loading, fault and no-access answer, so it takes no `state`.
 */
export function CenteredCard({ title, path, text, body, actions, foot, dot, fault, wide = false }: CenteredCardProps) {
  return (
    <div className="q pk-q">
      <main className="pane q-sheet" aria-label={title}>
        <div className={wide ? 'q-col is-wide' : 'q-col'}>
          <div className="q-dot">{dot ?? <Dot size={112} />}</div>
          <h1 className={fault ? 'is-fault' : undefined} data-target={`page:${path}`}>
            {fault && <Icon name={fault} />}
            {title}
          </h1>
          {text && <p className="q-sub">{text}</p>}
          {body}
          {actions && <div className="q-row">{actions}</div>}
        </div>
        {foot && <footer className="q-foot">{foot}</footer>}
      </main>
    </div>
  );
}
