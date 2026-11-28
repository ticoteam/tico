import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as Client from '@/api/client';
import type * as Desktop from '@/desktop';
import type { Ctx } from '@/actions';

const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(async () => ({ kept: 1 }));
const opened = vi.fn<(agent: string) => Promise<Desktop.ChatWindowOpened>>();
const toast = vi.fn();
vi.mock('@/api/client', async (real) => ({ ...(await real<typeof Client>()), api: { post: (path: string, body: unknown) => post(path, body) } }));
vi.mock('@/desktop', async (real) => ({ ...(await real<typeof Desktop>()), openChatWindow: (agent: string) => opened(agent) }));
vi.mock('@/kit/Toast', async (real) => ({ ...(await real<Record<string, unknown>>()), toast: (t: unknown) => toast(t) }));

const { popOutChat } = await import('./actions');
const press = (who: string) => popOutChat.run({} as Ctx, { who });

beforeEach(() => {
  post.mockClear();
  toast.mockClear();
});

describe("opening an agent's chat in its own window records the usage event", () => {
  it.each([
    ['desktop', 'desktop'],
    ['window', 'web'],
    ['tab', 'web'],
  ] as const)('a chat that opened (%s) sends chat_window.opened with %s', async (how, surface) => {
    opened.mockResolvedValueOnce(how);
    expect(await press('release-owner')).toBe(how);
    expect(post).toHaveBeenCalledWith('/api/v2/web-events', { event: 'chat_window.opened', surface });
    expect(popOutChat.did?.({ who: 'release-owner' }, how)).toBe('Opened the chat in a new window');
  });

  it.each(['full', 'blocked', 'unavailable'] as const)('one that did not open (%s) sends nothing and says so', async (how) => {
    opened.mockResolvedValueOnce(how);
    expect(await press('release-owner')).toBe(how);
    expect(post).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ kind: 'failed' }));
    expect(popOutChat.did?.({ who: 'release-owner' }, how)).toBe('No new window opened');
  });

  it('a failed send never fails the press', async () => {
    opened.mockResolvedValueOnce('window');
    post.mockRejectedValueOnce(new Error('offline'));
    expect(await press('release-owner')).toBe('window');
  });
});
