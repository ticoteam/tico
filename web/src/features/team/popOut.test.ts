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

describe("the press that opens an agent's chat window", () => {
  // the window sends its own usage event when it first loads (chatWindowUsage.test.ts): a press that only brings an open
  // window forward is no new window, so the press itself sends none
  it.each(['desktop', 'window', 'tab'] as const)('one that opened (%s) says so and sends no usage event', async (how) => {
    opened.mockResolvedValueOnce(how);
    expect(await press('release-owner')).toBe(how);
    expect(post).not.toHaveBeenCalled();
    expect(popOutChat.did?.({ who: 'release-owner' }, how)).toBe('Opened the chat in a new window');
  });

  it.each(['full', 'blocked', 'unavailable'] as const)('one that did not open (%s) sends nothing and says so', async (how) => {
    opened.mockResolvedValueOnce(how);
    expect(await press('release-owner')).toBe(how);
    expect(post).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ kind: 'failed' }));
    expect(popOutChat.did?.({ who: 'release-owner' }, how)).toBe('No new window opened');
  });
});
