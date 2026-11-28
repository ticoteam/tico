import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as Client from '@/api/client';

const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(async () => ({ kept: 1 }));
vi.mock('@/api/client', async (real) => ({ ...(await real<typeof Client>()), api: { post: (path: string, body: unknown) => post(path, body) } }));

const { chatWindowOpened, chatWindowSent, forgetChatWindow } = await import('./chatWindowUsage');
const bodies = () => post.mock.calls.map(([, b]) => b as Record<string, string>);

beforeEach(() => {
  post.mockClear();
  forgetChatWindow();
  delete (window as { tico?: unknown }).tico;
});

describe("an agent's chat window's usage events", () => {
  it('sends opened once per window, with where, the agent and the window, and a reload sends nothing', () => {
    chatWindowOpened('qa');
    chatWindowOpened('qa'); // the same window loading again (a reload, a second effect run)
    expect(bodies()).toEqual([{ event: 'chat_window.opened', surface: 'web', agent: 'qa', window: expect.stringMatching(/^[\w-]{8,64}$/) }]);
  });

  it('sends sent with the first message only, under the same window id', () => {
    chatWindowOpened('qa');
    chatWindowSent('qa');
    chatWindowSent('qa');
    const [opened, sent, ...rest] = bodies();
    expect(rest).toEqual([]);
    expect(sent).toEqual({ ...opened, event: 'chat_window.sent' });
  });

  it('a new window is a new id', () => {
    chatWindowOpened('qa');
    forgetChatWindow(); // another window: its own sessionStorage
    chatWindowOpened('qa');
    const [a, b] = bodies();
    expect(a?.window).not.toBe(b?.window);
  });

  it('says desktop in the desktop app', () => {
    (window as { tico?: unknown }).tico = { chatWindow: { open: vi.fn() } };
    chatWindowOpened('qa');
    expect(bodies()[0]?.surface).toBe('desktop');
  });

  it('counts the first send when the browser cannot write session storage (quota full), under the opened window id', () => {
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError');
    });
    try {
      chatWindowOpened('qa');
      chatWindowOpened('qa'); // the same window again: still one opened
      chatWindowSent('qa');
      chatWindowSent('qa'); // the second message: no second sent
    } finally {
      write.mockRestore();
    }
    const [opened, sent, ...rest] = bodies();
    expect(opened?.event).toBe('chat_window.opened');
    expect(sent).toEqual({ ...opened, event: 'chat_window.sent' });
    expect(rest).toEqual([]);
  });

  it('a page that is no chat window (no mark) never sends sent, and a failed send throws nothing', () => {
    chatWindowSent('qa');
    expect(post).not.toHaveBeenCalled();
    post.mockRejectedValueOnce(new Error('offline'));
    expect(() => chatWindowOpened('qa')).not.toThrow();
  });
});
