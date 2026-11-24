import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LivePage, MAX_HEIGHT, MIN_HEIGHT, WAIT_MS, liveSrc } from './LivePage';

const VISUAL = 'http://visuals.test';
const FILES = 'https://acme-files.example';

let n = 0;
const show = (src: string | null, config: object = { visual_origin: VISUAL, visual_files: null }) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(['config'], config);
  // each case its own file version: the height cache is per version
  const id = `f${++n}@1`;
  return render(
    <QueryClientProvider client={client}>
      <LivePage src={src} title="Report" id={id} fallback={<p>plain card</p>} />
    </QueryClientProvider>,
  );
};
const height = (c: HTMLElement) => (c.querySelector('.lpage-view') as HTMLElement).style.height;
const report = (source: Window | null, h: unknown) =>
  act(() => window.dispatchEvent(new MessageEvent('message', { data: { ticoVisualHeight: h }, source })));

afterEach(() => vi.useRealTimers());

describe('an agent’s page live in a chat', () => {
  it('runs only from this install’s own visual addresses; any other address is the plain card', () => {
    const cfg = { visual_origin: VISUAL, visual_files: FILES };
    expect(liveSrc(`${VISUAL}/visuals/abc.def`, cfg)).toBe(`${VISUAL}/visuals/abc.def`);
    expect(liveSrc(`${FILES}/visuals/abc`, cfg)).toBe(`${FILES}/visuals/abc`);
    expect(liveSrc('https://evil.example/visuals/abc', cfg)).toBeNull();
    expect(liveSrc(`${VISUAL}/api/v2/files/1/raw`, cfg)).toBeNull();
    expect(liveSrc(`${VISUAL}/visuals/abc?x=1`, cfg)).toBeNull();
    // Tico file hosting: the app's own route, which only ever sends the frame on to the files host
    expect(liveSrc('/visuals/abc', cfg)).toBe(`${location.origin}/visuals/abc`);
    expect(liveSrc('/visuals/abc', { visual_origin: VISUAL, visual_files: null })).toBeNull();
    expect(liveSrc(`${VISUAL}/visuals/abc`, undefined)).toBeNull();

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
    report(window, 300);
    expect(height(container)).toBe('240px');
    report(frame.contentWindow, 300);
    expect(height(container)).toBe('300px');
    report(frame.contentWindow, 20);
    expect(height(container)).toBe(`${MIN_HEIGHT}px`);
    expect(container.querySelector('.lpage-fade')).toBeNull();
    report(frame.contentWindow, 2000);
    expect(height(container)).toBe(`${MAX_HEIGHT}px`);
    expect(container.querySelector('.lpage-fade')).toBeTruthy();
    report(frame.contentWindow, 'tall');
    expect(height(container)).toBe(`${MAX_HEIGHT}px`);
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

  it('a height in time keeps the page', () => {
    vi.useFakeTimers();
    const { container } = show(`${VISUAL}/visuals/tok`);
    report(container.querySelector('iframe')!.contentWindow, 200);
    act(() => vi.advanceTimersByTime(WAIT_MS * 2));
    expect(container.querySelector('iframe')).toBeTruthy();
  });

  it('expands to the whole page with a way out to its own tab, and a press inside never reaches the message', async () => {
    const press = vi.fn();
    const client = new QueryClient();
    client.setQueryData(['config'], { visual_origin: VISUAL, visual_files: null });
    render(
      <QueryClientProvider client={client}>
        <div onClick={press} onContextMenu={press}>
          <LivePage src={`${VISUAL}/visuals/tok`} title="Report" id="big@1" fallback={null} />
        </div>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    press.mockClear();
    const dialog = await screen.findByRole('dialog', { name: 'Report' });
    expect(dialog.querySelector('iframe')?.getAttribute('src')).toBe(`${VISUAL}/visuals/tok`);
    expect(screen.getByRole('link', { name: 'Open in new tab' }).getAttribute('href')).toBe(`${VISUAL}/visuals/tok`);
    fireEvent.contextMenu(dialog);
    fireEvent.click(dialog);
    expect(press).not.toHaveBeenCalled();
  });
});
