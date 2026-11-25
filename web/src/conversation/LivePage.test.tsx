import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LivePage, MAX_HEIGHT, MIN_HEIGHT, WAIT_MS, liveSrc } from './LivePage';

const VISUAL = 'http://visuals.test';
const FILES = 'https://acme-files.example';

const client = (config: object) => {
  const made = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  made.setQueryData(['config'], config);
  return made;
};
const page = (src: string | null, id: string, config: object = { visual_origin: VISUAL, visual_files: null }) => (
  <QueryClientProvider client={client(config)}>
    <LivePage src={src} title="Report" id={id} fallback={<p>plain card</p>} />
  </QueryClientProvider>
);
let n = 0;
// each case its own file version unless it says otherwise: the height cache is per version
const show = (src: string | null, config?: object) => render(page(src, `f${++n}@1`, config));
const height = (c: HTMLElement) => (c.querySelector('.lpage-view') as HTMLElement).style.height;
const report = (source: Window | null, data: object) => act(() => window.dispatchEvent(new MessageEvent('message', { data, source })));
const tall = (source: Window | null, h: unknown) => report(source, { ticoVisualHeight: h });

afterEach(() => vi.useRealTimers());

describe('an agent’s page live in a chat', () => {
  it('runs only from this install’s own visual addresses, never on the app’s own origin; anything else is the plain card', () => {
    const cfg = { visual_origin: VISUAL, visual_files: FILES };
    expect(liveSrc(`${VISUAL}/visuals/abc.def`, cfg)).toBe(`${VISUAL}/visuals/abc.def`);
    expect(liveSrc(`${FILES}/visuals/abc`, cfg)).toBe(`${FILES}/visuals/abc`);
    expect(liveSrc('https://evil.example/visuals/abc', cfg)).toBeNull();
    expect(liveSrc(`${VISUAL}/api/v2/files/1/raw`, cfg)).toBeNull();
    expect(liveSrc(`${VISUAL}/visuals/abc?x=1`, cfg)).toBeNull();
    expect(liveSrc(`${VISUAL}/visuals/abc`, undefined)).toBeNull();
    // Tico file hosting: the app's own route, which only ever sends the frame on to the files host
    expect(liveSrc('/visuals/abc', { visual_origin: null, visual_files: FILES })).toBe(`${location.origin}/visuals/abc`);
    expect(liveSrc('/visuals/abc', { visual_origin: VISUAL, visual_files: null })).toBeNull();
    // a visual address that is the app's own origin (its loopback twin, the app opened there): the page would run
    // beside the app, so it never runs live
    const twin = { visual_origin: location.origin, visual_files: FILES };
    expect(liveSrc(`${location.origin}/visuals/abc`, twin)).toBeNull();
    expect(liveSrc('/visuals/abc', twin)).toBeNull();
    // another port of the app's own host name shares its cookies: never live there either
    const port = `${location.protocol}//${location.hostname}:${location.port === '9001' ? '9002' : '9001'}`;
    expect(liveSrc(`${port}/visuals/abc`, { visual_origin: port, visual_files: null })).toBeNull();
    expect(liveSrc(`${port}/visuals/abc`, { visual_origin: null, visual_files: port })).toBeNull();
    expect(liveSrc('/visuals/abc', { visual_origin: null, visual_files: port })).toBeNull();

    const { container } = show('https://evil.example/visuals/abc');
    expect(container.querySelector('iframe')).toBeNull();
    expect(screen.getByText('plain card')).toBeTruthy();
  });

  it('opens the page with its own storage and sizes to what its own frame reports, between the bounds', () => {
    const { container } = show(`${VISUAL}/visuals/tok`);
    const frame = container.querySelector('iframe')!;
    expect(frame.getAttribute('src')).toBe(`${VISUAL}/visuals/tok#tico-view=card&tico-size=medium`);
    expect(frame.getAttribute('sandbox')).toContain('allow-same-origin');
    // another window's report (another card, the app) never sizes it
    tall(window, 300);
    expect(height(container)).toBe('240px');
    tall(frame.contentWindow, 300);
    expect(height(container)).toBe('300px');
    tall(frame.contentWindow, 20);
    expect(height(container)).toBe(`${MIN_HEIGHT}px`);
    expect(container.querySelector('.lpage-fade')).toBeNull();
    tall(frame.contentWindow, 2000);
    expect(height(container)).toBe(`${MAX_HEIGHT}px`);
    expect(container.querySelector('.lpage-fade')).toBeTruthy();
    tall(frame.contentWindow, 'tall');
    expect(height(container)).toBe(`${MAX_HEIGHT}px`);
  });

  it('frames the card unscrollable (what holds it takes the wheel), the large view scrollable', async () => {
    const { container } = show(`${VISUAL}/visuals/tok`);
    expect(container.querySelector('iframe')!.getAttribute('scrolling')).toBe('no');
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    const dialog = await screen.findByRole('dialog', { name: 'Report' });
    expect(dialog.querySelector('iframe')!.hasAttribute('scrolling')).toBe(false);
  });

  it('falls back to the plain card when no height comes (an edge that does not serve the page yet)', () => {
    vi.useFakeTimers();
    const { container } = show(`${VISUAL}/visuals/tok`);
    act(() => vi.advanceTimersByTime(WAIT_MS - 1));
    expect(container.querySelector('iframe')).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(container.querySelector('iframe')).toBeNull();
    expect(screen.getByText('plain card')).toBeTruthy();
  });

  it('keeps an open large view when the card falls back (a page whose own policy blocks the height reporter)', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    render(page(`${VISUAL}/visuals/tok`, 'kept@1'));
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    await act(async () => {});
    const inside = screen.getByRole('dialog', { name: 'Report' }).querySelector('iframe');
    expect(inside).toBeTruthy();
    act(() => vi.advanceTimersByTime(WAIT_MS));
    expect(screen.getByText('plain card')).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Report' }).querySelector('iframe')).toBe(inside);
  });

  it('keeps a page that reports in time, a page fixed to the viewport (height 0) included', () => {
    vi.useFakeTimers();
    const { container } = show(`${VISUAL}/visuals/tok`);
    tall(container.querySelector('iframe')!.contentWindow, 0);
    act(() => vi.advanceTimersByTime(WAIT_MS * 2));
    expect(container.querySelector('iframe')).toBeTruthy();
    expect(height(container)).toBe(`${MIN_HEIGHT}px`);
  });

  it('drawn again, opens at its last size but still waits for its page; a past fallback does not stick', () => {
    vi.useFakeTimers();
    const src = `${VISUAL}/visuals/tok`;
    const first = render(page(src, 'again@1'));
    tall(first.container.querySelector('iframe')!.contentWindow, 300);
    first.unmount();
    const second = render(page(src, 'again@1'));
    expect(height(second.container)).toBe('300px');
    act(() => vi.advanceTimersByTime(WAIT_MS));
    expect(second.container.querySelector('iframe')).toBeNull(); // this frame never answered: the plain card
    second.unmount();
    const third = render(page(src, 'again@1'));
    expect(third.container.querySelector('iframe')).toBeTruthy();
  });

  it('a renewed address never reloads the page or its large view; a new version is a new frame', async () => {
    const view = render(page(`${VISUAL}/visuals/one`, 'doc@1'));
    const card = () => view.container.querySelector('.lpage iframe')!.getAttribute('src');
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    const dialog = await screen.findByRole('dialog', { name: 'Report' });
    view.rerender(page(`${VISUAL}/visuals/two`, 'doc@1'));
    expect(card()).toBe(`${VISUAL}/visuals/one#tico-view=card&tico-size=medium`);
    expect(dialog.querySelector('iframe')!.getAttribute('src')).toBe(`${VISUAL}/visuals/one`);
    expect(screen.getByRole('link', { name: 'Open in new tab' }).getAttribute('href')).toBe(`${VISUAL}/visuals/one`);
    view.rerender(page(`${VISUAL}/visuals/three`, 'doc@2'));
    expect(card()).toBe(`${VISUAL}/visuals/three#tico-view=card&tico-size=medium`);
  });

  it('expands to the whole page, closes on Escape inside it, and a press inside never reaches the message', async () => {
    const press = vi.fn();
    render(
      <QueryClientProvider client={client({ visual_origin: VISUAL, visual_files: null })}>
        <div onClick={press} onContextMenu={press}>
          <LivePage src={`${VISUAL}/visuals/tok`} title="Report" id="big@1" fallback={null} />
        </div>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    press.mockClear();
    const dialog = await screen.findByRole('dialog', { name: 'Report' });
    const inside = dialog.querySelector('iframe')!;
    expect(inside.getAttribute('src')).toBe(`${VISUAL}/visuals/tok`);
    fireEvent.contextMenu(dialog);
    fireEvent.click(dialog);
    expect(press).not.toHaveBeenCalled();
    // Escape from any other window (the card's own page, another page) is not the large view's
    report(window, { ticoVisualKey: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeTruthy();
    report(inside.contentWindow, { ticoVisualKey: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
