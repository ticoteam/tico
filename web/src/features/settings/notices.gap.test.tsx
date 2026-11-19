import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NoticeSwitch } from './Notices';

// The person's notifications switch: the browser asks permission only from the press, never on load (platforms.md 2).

const { run } = vi.hoisted(() => ({ run: vi.fn(async () => ({ ok: true })) }));
vi.mock('@/actions', () => ({ run, refusal: () => ({ text: 'No' }) }));

function stubNotification(permission: NotificationPermission, answer: NotificationPermission = permission) {
  const requestPermission = vi.fn(async () => answer);
  vi.stubGlobal('Notification', { permission, requestPermission });
  return requestPermission;
}

afterEach(() => {
  vi.unstubAllGlobals();
  run.mockClear();
});

describe('notifications switch', () => {
  it('asks the browser only when pressed, then turns it on', async () => {
    const ask = stubNotification('default', 'granted');
    render(<NoticeSwitch on={false} />);
    expect(ask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('switch', { name: 'Notifications' }));
    expect(ask).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(run).toHaveBeenCalledWith('settings.notify', { field: 'notify_browser', on: true }, { via: 'click' }));
  });

  it('stays off when the browser blocks it', async () => {
    const ask = stubNotification('denied');
    render(<NoticeSwitch on={false} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Notifications' }));
    expect(ask).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
  });

  it('turns off without asking', async () => {
    const ask = stubNotification('granted');
    render(<NoticeSwitch on />);
    fireEvent.click(screen.getByRole('switch', { name: 'Notifications' }));
    expect(ask).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(run).toHaveBeenCalledWith('settings.notify', { field: 'notify_browser', on: false }, { via: 'click' }));
  });
});
