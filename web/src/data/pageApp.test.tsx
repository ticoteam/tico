import { act, render } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type * as Client from '@/api/client';
import { APP_FRAMES, bridge, useActSheet, useAppBridge, useAppSlot, type ActSheet } from './pageApp';

vi.mock('@/api/client', async (real) => ({ ...(await real<typeof Client>()), api: { post: vi.fn() } }));
const post = vi.mocked(api.post);

const fakePort = () => ({ onmessage: null as ((e: MessageEvent) => void) | null, postMessage: vi.fn(), close: vi.fn() });
const hello = (source: Window | null, port: ReturnType<typeof fakePort>, data: unknown = { ticoApp: 'key-1' }) => {
  const e = new MessageEvent('message', { data, source });
  Object.defineProperty(e, 'ports', { value: [port] });
  window.dispatchEvent(e);
};
const flush = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  document.body.innerHTML = '';
  post.mockReset();
});

describe('a page app’s bridge', () => {
  it('takes one port from its own frame only, sends its calls as this page and version, and closes on a new page', async () => {
    const frame = document.body.appendChild(document.createElement('iframe'));
    const other = document.body.appendChild(document.createElement('iframe'));
    const opened: string[] = [];
    const stop = bridge(frame, { page: 'file-1', version: 3 }, (href) => opened.push(href));

    // a sibling frame's handshake is not heard
    const stranger = fakePort();
    hello(other.contentWindow, stranger);
    const mine = fakePort();
    hello(frame.contentWindow, mine);
    // a second handshake from the same frame (a page that navigated there) gets nothing
    const second = fakePort();
    hello(frame.contentWindow, second, { ticoApp: 'key-2' });
    expect(stranger.onmessage).toBeNull();
    expect(second.onmessage).toBeNull();

    post.mockResolvedValueOnce({ result: { rows: [{ id: 't1' }], next: null } });
    mine.onmessage?.({ data: { id: 1, op: 'query', args: { kind: 'tasks' } } } as MessageEvent);
    await flush();
    expect(post).toHaveBeenCalledWith('/api/v2/pages/file-1/app', { version: 3, nonce: 'key-1', op: 'query', args: { kind: 'tasks' } }, { once: true });
    expect(mine.postMessage).toHaveBeenCalledWith({ id: 1, result: { rows: [{ id: 't1' }], next: null } });

    // open takes the app there, never the frame
    post.mockResolvedValueOnce({ result: { href: '/task/t1' } });
    mine.onmessage?.({ data: { id: 2, op: 'open', args: { ref: 'task:t1' } } } as MessageEvent);
    await flush();
    expect(opened).toEqual(['/task/t1']);

    // the page loads once; a second load is a new page in the frame: its port is closed
    frame.dispatchEvent(new Event('load'));
    expect(mine.close).not.toHaveBeenCalled();
    frame.dispatchEvent(new Event('load'));
    expect(mine.close).toHaveBeenCalled();
    stop();
  });

  it('binds to a frame drawn after it runs (the large view in its portal), and again to a new frame in its place', () => {
    function App({ shown, n }: { shown: boolean; n: number }) {
      const ref = useAppBridge({ page: 'file-1', version: 3 });
      return shown ? <iframe key={n} ref={ref} title="app" /> : null;
    }
    const { container, rerender } = render(<App shown={false} n={0} />);
    rerender(<App shown n={0} />);
    const first = fakePort();
    hello(container.querySelector('iframe')!.contentWindow, first);
    expect(first.onmessage).not.toBeNull();
    rerender(<App shown n={1} />);
    expect(first.close).toHaveBeenCalled();
    const second = fakePort();
    hello(container.querySelector('iframe')!.contentWindow, second);
    expect(second.onmessage).not.toBeNull();
  });

  it('holds an act for the viewer’s tap outside the frame: the key never reaches the page, and closing cancels', async () => {
    let sheet: ActSheet | null = null;
    function Watch() {
      sheet = useActSheet();
      return null;
    }
    render(<Watch />);
    const frame = document.body.appendChild(document.createElement('iframe'));
    const stop = bridge(frame, { page: 'file-1', version: 3 });
    const port = fakePort();
    hello(frame.contentWindow, port);
    const lines = [{ kind: 'comment', label: 'Comment on #12 Spring launch', detail: 'Ship it' }];
    const asked = { actions: [{ kind: 'comment', payload: { task: 't1', text: 'Ship it' } }] };

    post.mockResolvedValueOnce({ result: { key: 'held-1', page: 'Creative library', lines } });
    port.onmessage?.({ data: { id: 1, op: 'act', args: asked } } as MessageEvent);
    // a second ask before the server answers the first is refused: the sheet never changes under the viewer
    port.onmessage?.({ data: { id: 9, op: 'act', args: asked } } as MessageEvent);
    expect(port.postMessage).toHaveBeenCalledWith({ id: 9, error: { code: 'busy', detail: expect.any(String) } });
    port.postMessage.mockClear();
    await act(flush);
    expect(post).toHaveBeenCalledWith('/api/v2/pages/file-1/app', { version: 3, nonce: 'key-1', op: 'act', args: asked }, { once: true });
    expect(sheet).toMatchObject({ page: 'Creative library', lines });
    expect(port.postMessage).not.toHaveBeenCalled(); // nothing goes back to the page until the tap
    expect(post).toHaveBeenCalledTimes(1);

    // a second ask while one waits is refused, not stacked
    port.onmessage?.({ data: { id: 2, op: 'act', args: asked } } as MessageEvent);
    expect(port.postMessage).toHaveBeenCalledWith({ id: 2, error: { code: 'busy', detail: expect.any(String) } });

    post.mockResolvedValueOnce({ status: 'done', items: [{ ok: true, error: null }] });
    await act(() => sheet!.answer(true));
    expect(post).toHaveBeenLastCalledWith('/api/v2/pages/file-1/app/act', { key: 'held-1', confirm: true }, { once: true });
    expect(port.postMessage).toHaveBeenLastCalledWith({ id: 1, result: { status: 'done', items: [{ ok: true, error: null }] } });
    expect(sheet).toBeNull();
    expect(JSON.stringify(port.postMessage.mock.calls)).not.toContain('held-1');

    // the page going away while a sheet waits cancels it
    post.mockResolvedValueOnce({ result: { key: 'held-2', page: 'Creative library', lines } });
    port.onmessage?.({ data: { id: 3, op: 'act', args: asked } } as MessageEvent);
    await act(flush);
    post.mockReturnValueOnce(new Promise(() => undefined)); // the cancel is still on its way
    act(() => stop());
    expect(post).toHaveBeenLastCalledWith('/api/v2/pages/file-1/app/act', { key: 'held-2', confirm: false }, { once: true });
    expect(sheet).toBeNull(); // gone at once, and another page may ask
    const next = document.body.appendChild(document.createElement('iframe'));
    const stopNext = bridge(next, { page: 'file-2', version: 1 });
    const other = fakePort();
    hello(next.contentWindow, other);
    post.mockResolvedValueOnce({ result: { key: 'held-3', page: 'Other', lines } });
    other.onmessage?.({ data: { id: 4, op: 'act', args: asked } } as MessageEvent);
    await act(flush);
    expect(other.postMessage).not.toHaveBeenCalled();
    expect(sheet).toMatchObject({ page: 'Other' });
    post.mockResolvedValueOnce({ status: 'cancelled', items: [] });
    await act(async () => {
      stopNext();
      await flush();
    });
  });

  it('runs at most APP_FRAMES app frames at once; the next one runs when one goes', () => {
    function Slot({ n }: { n: number }) {
      const box = useRef<HTMLDivElement>(null);
      return <div ref={box}>{useAppSlot(box, true) ? `run ${n}` : `still ${n}`}</div>;
    }
    const shown = (n: number) => Array.from({ length: n }, (_, i) => <Slot key={i} n={i} />);
    const { container, rerender } = render(<>{shown(APP_FRAMES + 1)}</>);
    expect(container.textContent).toContain(`still ${APP_FRAMES}`);
    expect(container.textContent?.match(/run/g)).toHaveLength(APP_FRAMES);
    act(() => rerender(<>{shown(APP_FRAMES + 1).slice(1)}</>));
    expect(container.textContent?.match(/run/g)).toHaveLength(APP_FRAMES);
    expect(container.textContent).toContain(`run ${APP_FRAMES}`);
  });
});
