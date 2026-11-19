import { toast } from '@/kit/Toast';
import { SwitchLine, act } from './parts';

/* The person's own notifications switch (the old app's "Browser notifications", ui/app/person-page.ts): a system
   notification for a new ask while Tico is out of sight. The browser asks permission only from this press. */

async function turn(on: boolean): Promise<void> {
  if (on) {
    if (!('Notification' in window)) {
      toast({ text: 'This browser has no notifications', kind: 'failed' });
      return;
    }
    const allowed = Notification.permission === 'granted' || (Notification.permission !== 'denied' && (await Notification.requestPermission()) === 'granted');
    if (!allowed) {
      toast({ text: 'Notifications are blocked for Tico', kind: 'failed' });
      return;
    }
  }
  await act('settings.notify', { field: 'notify_browser', on });
}

export function NoticeSwitch({ on }: { on: boolean }) {
  return <SwitchLine label="Notifications" target="setting:notify_browser" checked={on} onChange={(next) => void turn(next)} />;
}
