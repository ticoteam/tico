import type { ReactNode } from 'react';
import { Icon, type IconName } from '@/kit/Icon';
import './Empty.css';

export type EmptyProps = {
  /** One line: "Nothing in Review", "Finance did not load". */
  title: string;
  /** A failed or locked state shows its icon in a well; a plain empty list shows the stacked bars. */
  icon?: IconName;
  /** At most one short sentence, only when the title is not enough. */
  text?: string;
  /** The buttons: one filled at most. */
  children?: ReactNode;
};

/** An empty, failed or no-access state, centered in its sheet (parts.css `.empty`). */
export function Empty({ title, icon, text, children }: EmptyProps) {
  return (
    <div className="empty">
      {icon ? (
        <span className="empty-ico">
          <Icon name={icon} />
        </span>
      ) : (
        <div className="empty-art" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </div>
      )}
      <h2>{title}</h2>
      {text && <p>{text}</p>}
      {children && <div className="q-row">{children}</div>}
    </div>
  );
}
