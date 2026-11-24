import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data/query-client';
import { FakeEventSource, opening } from './fake-stream';
import { faultReason, useReadFault } from './fault';
import { liveStatus, restartLive, startLive, stopLive } from './stream';

const FIVE_MIN = 5 * 60_000;
const starting = () => new ApiError(503, 'starting', 'Tico is restarting', true);

/** One read: failed with `error`, or read (null). */
function Read({ error, retry }: { error: unknown; retry: () => unknown }) {
  return <p>{useReadFault('Thing', error ? [{ error, refetch: retry }] : [], !error) ?? 'fine'}</p>;
}

let visible: DocumentVisibilityState = 'visible';

let warn: MockInstance<typeof console.warn>;
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  // the stream's "who is this" on a resumed stream: the same person
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ actor: 'human:a', email: '', role: 'human' })));
  warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  // a test's own onLine (an own property over jsdom's)
  delete (navigator as { onLine?: boolean }).onLine;
  delete (document as { visibilityState?: string }).visibilityState;
  visible = 'visible';
  stopLive();
  queryClient.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('a read that failed', () => {
  it('says why in one line', () => {
    render(<Read error={starting()} retry={() => undefined} />);
    expect(screen.getByText("Thing didn't load · server restarting")).toBeTruthy();
  });

  it('names the reason plainly; an HTTP failure is never "offline"', () => {
    let online = true;
    Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => online });
    expect(faultReason(new ApiError(503, 'busy', 'Busy', true))).toBe('server busy');
    expect(faultReason(new ApiError(503, 'storage_unavailable', 'No storage', true))).toBe('server error');
    expect(faultReason(new ApiError(502, 'http_502', 'Bad Gateway', true))).toBe('server error');
    expect(faultReason(new ApiError(0, 'network', 'No answer', true))).toBe('no connection');
    expect(faultReason(new TypeError('x is undefined'))).toBe('app error');
    online = false;
    expect(faultReason(new ApiError(0, 'network', 'No answer', true))).toBe('offline');
    expect(faultReason(starting())).toBe('server restarting');
  });

  it('is read again when a stream that dropped mid-session is back', async () => {
    const retry = vi.fn();
    startLive();
    FakeEventSource.last.feed(opening(900));
    render(<Read error={starting()} retry={retry} />);
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(250);
    FakeEventSource.last.feed(opening(905, 900));
    await vi.waitFor(() => expect(retry).toHaveBeenCalledTimes(1));
    // joining a read already on its way (the stream's own read of quiet areas), never cancelling it for another
    expect(retry).toHaveBeenCalledWith({ cancelRefetch: false });
  });

  it('is read again when the page comes back from the browser’s page cache, though the status stayed live', async () => {
    const retry = vi.fn();
    startLive();
    FakeEventSource.last.feed(opening(900));
    render(<Read error={starting()} retry={retry} />);
    dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
    expect(liveStatus()).toBe('live');
    FakeEventSource.last.feed(opening(905, 900));
    await vi.waitFor(() => expect(retry).toHaveBeenCalledTimes(1));
  });

  it('a refusal is never read again by itself: only Try again reads it', () => {
    const retry = vi.fn();
    startLive();
    render(<Read error={new ApiError(403, 'forbidden', 'No.')} retry={retry} />);
    expect(screen.getByText("Thing didn't load · no access")).toBeTruthy();
    FakeEventSource.last.feed(opening(900));
    vi.advanceTimersByTime(FIVE_MIN * 2);
    expect(retry).not.toHaveBeenCalled();
  });

  it('meanwhile is read again every 5 minutes only while the stream is live', () => {
    const retry = vi.fn();
    startLive();
    render(<Read error={starting()} retry={retry} />);
    vi.advanceTimersByTime(FIVE_MIN);
    expect(retry).not.toHaveBeenCalled();
    FakeEventSource.last.feed(opening(900));
    expect(retry).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(FIVE_MIN);
    expect(retry).toHaveBeenCalledTimes(2);
  });

  it('logs once however often it fails again, and again after a read succeeds', () => {
    const { rerender } = render(<Read error={starting()} retry={() => undefined} />);
    rerender(<Read error={starting()} retry={() => undefined} />);
    rerender(<Read error={starting()} retry={() => undefined} />);
    expect(warn).toHaveBeenCalledTimes(1);
    rerender(<Read error={null} retry={() => undefined} />);
    expect(screen.getByText('fine')).toBeTruthy();
    rerender(<Read error={starting()} retry={() => undefined} />);
    expect(warn).toHaveBeenCalledTimes(2);
  });

  it('is not read again when a live stream only reconnects for new follows', async () => {
    const retry = vi.fn();
    startLive();
    FakeEventSource.last.feed(opening(900));
    render(<Read error={starting()} retry={retry} />);
    // a page's follows changed: the same stream opens again with the new scope, and was never down
    restartLive();
    vi.advanceTimersByTime(30);
    FakeEventSource.last.feed(opening(905, 900));
    // then a real drop and return, which does read again: once, so the reconnect before it did not
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(250);
    FakeEventSource.last.feed(opening(910, 905));
    await vi.waitFor(() => expect(retry).toHaveBeenCalled());
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('a stopped company is never read again by itself, on reconnect or on the backstop', () => {
    const retry = vi.fn();
    startLive();
    render(<Read error={new ApiError(503, 'stopped', 'This Tico company is stopped')} retry={retry} />);
    expect(screen.getByText("Thing didn't load · company stopped")).toBeTruthy();
    FakeEventSource.last.feed(opening(900));
    vi.advanceTimersByTime(FIVE_MIN * 2);
    expect(retry).not.toHaveBeenCalled();
  });

  it('a hidden tab skips the backstop, and it resumes once the tab shows again', () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visible });
    const retry = vi.fn();
    startLive();
    FakeEventSource.last.feed(opening(900));
    render(<Read error={starting()} retry={retry} />);
    visible = 'hidden';
    vi.advanceTimersByTime(FIVE_MIN);
    expect(retry).not.toHaveBeenCalled();
    visible = 'visible';
    vi.advanceTimersByTime(FIVE_MIN);
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('reads nothing once its view is gone', () => {
    const retry = vi.fn();
    startLive();
    const { unmount } = render(<Read error={starting()} retry={retry} />);
    unmount();
    act(() => FakeEventSource.last.feed(opening(900)));
    vi.advanceTimersByTime(FIVE_MIN * 2);
    expect(retry).not.toHaveBeenCalled();
  });
});
