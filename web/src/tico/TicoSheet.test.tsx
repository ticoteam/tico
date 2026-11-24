import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { startLive, stopLive } from '@/live';
import { FakeEventSource, opening } from '@/live/fake-stream';
import { PhoneCards } from '@/shell/phone';
import { closeSearch, searchState } from '@/search/state';
import { ticoPowerQuery } from './queries';
import { TicoSheet } from './TicoSheet';

async function show(tree: () => ReactNode, read = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  // `read`: the power and the room come from the server (GET /api/v2/assistant), not the cache
  if (!read) {
    client.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
    client.setQueryData(ticoRoomQuery.queryKey, null);
  }
  const router = createRouter({ routeTree: createRootRoute({ component: tree }), history: createMemoryHistory({ initialEntries: ['/'] }) });
  const { container } = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(container.querySelector('.tico-head')).toBeTruthy());
  return container;
}

describe("Tico's Dot", () => {
  it('sits once in the head on a desktop', async () => {
    const page = await show(() => <TicoSheet />);
    expect(page.querySelectorAll('.tico-head .dw')).toHaveLength(1);
  });

  it('sits once on a phone: on the bar’s Tico icon, the card’s head has none of its own', async () => {
    const page = await show(() => <PhoneCards page={<p>Tasks</p>} tico={<TicoSheet />} menu={null} />);
    expect(page.querySelectorAll('.dw')).toHaveLength(1);
    expect(page.querySelectorAll('.tabbar .tab-tico .dw')).toHaveLength(1);
  });
});

describe("Tico's head", () => {
  afterEach(() => closeSearch());

  it('has search beside Tico: one click opens it, the cursor already in a field @s1190', async () => {
    const page = await show(() => <TicoSheet />);
    const head = page.querySelector<HTMLElement>('.tico-head')!;
    fireEvent.click(within(head).getByRole('button', { name: 'Search' }));
    expect(searchState().open).toBe(true);
    // the field that holds the cursor until the palette's own draws (search/state.ts)
    expect(document.activeElement?.tagName).toBe('INPUT');
  });
});

describe("Tico's sheet when its read fails", () => {
  afterEach(() => {
    stopLive();
    queryClient.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  /** GET /api/v2/assistant fails as a restart does until `back` is set, then answers `info`. */
  function assistant(info: object) {
    const server = { back: false };
    vi.stubGlobal('fetch', async (url: string) =>
      String(url) !== '/api/v2/assistant'
        ? json(404, { error: { code: 'not_found', detail: 'No.' } })
        : server.back
          ? json(200, { bot: 'assistant', room_id: null, ...info })
          : json(503, { error: { code: 'starting', detail: 'Restarting.' } }),
    );
    vi.stubGlobal('EventSource', FakeEventSource);
    return server;
  }

  it('says why in one line, logs it once, and reads again by itself once the stream is back', async () => {
    const server = assistant({ available: true, can_turn_on: false, state: 'active' });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    // the stream is down with the server: connecting, not live
    startLive();
    await show(() => <TicoSheet />, true);
    expect(await screen.findByText("Tico didn't load · server restarting")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(warn).toHaveBeenCalledTimes(1);
    // the server is back: the stream says so, and the read goes out again with no click
    server.back = true;
    act(() => FakeEventSource.last.feed(opening(1)));
    expect(await screen.findByRole('img', { name: 'Tico has no chat yet' })).toBeTruthy();
    expect(screen.queryByText(/didn't load/)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('reads the power again with the room, so a Tico that is off shows Turn on, not an empty chat', async () => {
    const server = assistant({ available: false, can_turn_on: true, state: 'archived' });
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    startLive();
    await show(() => <TicoSheet />, true);
    expect(await screen.findByText("Tico didn't load · server restarting")).toBeTruthy();
    server.back = true;
    act(() => FakeEventSource.last.feed(opening(1)));
    expect(await screen.findByRole('button', { name: 'Turn on' })).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Tico has no chat yet' })).toBeNull();
  });
});
