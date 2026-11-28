import { afterEach, describe, expect, it, vi } from 'vitest';
import { canOpenChatWindow, chatWindowWho, isChatWindow, openChatWindow, type TicoDesktop } from '@/desktop';

const ORIGIN = window.location.origin;

/** A window `window.open` hands back: a fresh one sits on about:blank until it is sent somewhere. */
function fakeWin(start = 'about:blank') {
  let href = start;
  const location = {
    loads: 0,
    get href() {
      return href;
    },
    set href(to: string) {
      href = to;
      location.loads += 1;
    },
  };
  return { location, focus: vi.fn() } as unknown as Window & { focus: ReturnType<typeof vi.fn>; location: { loads: number } };
}

afterEach(() => {
  delete (window as { tico?: TicoDesktop }).tico;
  delete (window as { ticoNative?: unknown }).ticoNative;
  vi.restoreAllMocks();
});

describe('the chat-only page', () => {
  it('is an agent’s address with ?window=1, and nothing else', () => {
    expect(isChatWindow('/team/release-owner', '?window=1')).toBe(true);
    expect(isChatWindow('/next/team/release-owner/', '?window=1')).toBe(true);
    expect(chatWindowWho('/team/release%20owner')).toBe('release owner');
    expect(isChatWindow('/team/release-owner', '')).toBe(false);
    expect(isChatWindow('/team/release-owner', '?window=0')).toBe(false);
    expect(isChatWindow('/team/release-owner/settings', '?window=1')).toBe(false);
    expect(isChatWindow('/call/abc', '?window=1')).toBe(false);
  });
});

describe('opening an agent’s chat in its own window, in a browser', () => {
  it('opens a separate window, named for the agent, on the chat-only page', async () => {
    const win = fakeWin();
    const open = vi.spyOn(window, 'open').mockReturnValue(win);
    expect(await openChatWindow('release-owner')).toBe('window');
    expect(open).toHaveBeenCalledTimes(1);
    const [, name, features] = open.mock.calls[0]!;
    expect(name).toBe('tico-chat-release-owner');
    expect(features).toContain('popup');
    expect(win.location.href).toBe(`${ORIGIN}/team/release-owner?window=1`);
    expect(win.focus).toHaveBeenCalled();
  });

  it('brings an agent’s open window forward without loading it again or opening a second', async () => {
    const win = fakeWin(`${ORIGIN}/team/release-owner?window=1`);
    const open = vi.spyOn(window, 'open').mockReturnValue(win);
    expect(await openChatWindow('release-owner')).toBe('window');
    expect(open).toHaveBeenCalledTimes(1);
    expect(win.location.loads).toBe(0);
    expect(win.focus).toHaveBeenCalled();
  });

  it('gives each agent its own window', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => fakeWin());
    await openChatWindow('release-owner');
    await openChatWindow('builder');
    expect(open.mock.calls.map((c) => c[1])).toEqual(['tico-chat-release-owner', 'tico-chat-builder']);
  });

  it('keeps a window name to safe characters', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => fakeWin());
    await openChatWindow('a b/../c');
    expect(open.mock.calls[0]![1]).toMatch(/^tico-chat-[\w-]+$/);
  });

  it('opens a new tab when the browser blocks the window', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValueOnce(null).mockReturnValueOnce(fakeWin());
    expect(await openChatWindow('release-owner')).toBe('tab');
    expect(open).toHaveBeenLastCalledWith(`${ORIGIN}/team/release-owner?window=1`, '_blank');
  });

  it('says so when the browser blocks the tab too', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    expect(await openChatWindow('release-owner')).toBe('blocked');
  });

  it('sends a reused window that a link took elsewhere back to the chat', async () => {
    const win = fakeWin(`${ORIGIN}/tasks`);
    vi.spyOn(window, 'open').mockReturnValue(win);
    expect(await openChatWindow('release-owner')).toBe('window');
    expect(win.location.href).toBe(`${ORIGIN}/team/release-owner?window=1`);
    expect(win.focus).toHaveBeenCalled();
  });

  it('sends a reused window on another site back to the chat', async () => {
    let sent = '';
    const win = {
      location: {
        get href(): string {
          throw new DOMException('cross-origin', 'SecurityError');
        },
        set href(to: string) {
          sent = to;
        },
      },
      focus: vi.fn(),
    } as unknown as Window;
    vi.spyOn(window, 'open').mockReturnValue(win);
    expect(await openChatWindow('release-owner')).toBe('window');
    expect(sent).toBe(`${ORIGIN}/team/release-owner?window=1`);
  });

  it('can open one in a browser', () => {
    expect(canOpenChatWindow()).toBe(true);
  });
});

describe('opening an agent’s chat in its own window, in the desktop app', () => {
  it('asks the app for the window and opens nothing itself', async () => {
    const chatOpen = vi.fn(async () => 'open' as const);
    (window as { tico?: unknown }).tico = { chatWindow: { open: chatOpen } };
    const open = vi.spyOn(window, 'open');
    expect(await openChatWindow('release-owner')).toBe('desktop');
    expect(chatOpen).toHaveBeenCalledWith('release-owner');
    expect(open).not.toHaveBeenCalled();
  });

  it('says so when the app already holds as many chat windows as it allows', async () => {
    (window as { tico?: unknown }).tico = { chatWindow: { open: vi.fn(async () => 'full' as const) } };
    expect(await openChatWindow('release-owner')).toBe('full');
  });

  it('can open one in an app with the call', () => {
    (window as { tico?: unknown }).tico = { chatWindow: { open: vi.fn() } };
    expect(canOpenChatWindow()).toBe(true);
  });

  // An older app hands a new window to the system browser, which has none of the app's sign-in: no window at all.
  it('cannot open one in an older app without the call, and never falls back to a browser window', async () => {
    (window as { tico?: unknown }).tico = { companies: {} };
    const open = vi.spyOn(window, 'open');
    expect(canOpenChatWindow()).toBe(false);
    expect(await openChatWindow('release-owner')).toBe('unavailable');
    expect(open).not.toHaveBeenCalled();
  });

  it('cannot open one in the Tauri shell', async () => {
    (window as { ticoNative?: unknown }).ticoNative = {};
    const open = vi.spyOn(window, 'open');
    expect(canOpenChatWindow()).toBe(false);
    expect(await openChatWindow('release-owner')).toBe('unavailable');
    expect(open).not.toHaveBeenCalled();
  });
});
