import { Icon } from '@/kit/Icon';
import './OfflineBar.css';

export type OfflineBarProps = {
  /** Changes made while offline that will send on reconnect. */
  held: number;
};

/** "Offline", and how many changes wait to send. The shell shows it while `useLiveStatus()` is 'offline'. */
export function OfflineBar({ held }: OfflineBarProps) {
  return (
    <div className="nav-note" role="status">
      <Icon name="offline" />
      <div>
        <b>Offline</b>
        {held > 0 && <span>{held === 1 ? '1 change is held and will send.' : `${held} changes are held and will send.`}</span>}
      </div>
    </div>
  );
}
