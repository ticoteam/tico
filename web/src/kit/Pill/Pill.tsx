import type { ReactNode } from 'react';
import './Pill.css';

export type PillProps = {
  children: ReactNode;
  /** A state that needs a look (Offline, Stopped, Failed, Signed out, No reply): its words one step up in ink-2. */
  flag?: boolean;
  /** A small mark before the words, such as `<Fault size={12} />` or an Icon. */
  mark?: ReactNode;
  title?: string;
};

/** The one pill: an outline in greys (parts.css `.pill`, `.pill-flag`). Never filled. */
export function Pill({ children, flag, mark, title }: PillProps) {
  return (
    <span className={flag ? 'pill pill-flag' : 'pill'} title={title}>
      {mark}
      {children}
    </span>
  );
}
