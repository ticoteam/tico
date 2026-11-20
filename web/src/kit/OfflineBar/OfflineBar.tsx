import type { ComponentProps, ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import './OfflineBar.css';

export type NavNoteProps = {
  icon: ComponentProps<typeof Icon>['name'];
  /** The note's one line, in bold. */
  title: string;
  /** Under the title: a line of detail, or one action. */
  children?: ReactNode;
  /** A close mark at the right, for a note the person may put away. */
  onDismiss?: () => void;
};

/** A note at the foot of the menu (pages/15-start/start.css .nav-note): the offline bar, or one notice for the app. */
export function NavNote({ icon, title, children, onDismiss }: NavNoteProps) {
  return (
    <div className="nav-note" role="status">
      <Icon name={icon} />
      <div>
        <b>{title}</b>
        {children}
      </div>
      {onDismiss && <IconButton icon="close" label="Dismiss" size="sm" quiet onClick={onDismiss} />}
    </div>
  );
}

export type OfflineBarProps = {
  /** Changes made while offline that will send on reconnect. */
  held: number;
};

/** "Offline", and how many changes wait to send. The shell shows it while `useLiveStatus()` is 'offline'. */
export function OfflineBar({ held }: OfflineBarProps) {
  return (
    <NavNote icon="offline" title="Offline">
      {held > 0 && <span>{held === 1 ? '1 change is held and will send.' : `${held} changes are held and will send.`}</span>}
    </NavNote>
  );
}
