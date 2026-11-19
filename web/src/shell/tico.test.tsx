import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { TicoSheet } from '@/tico';
import { ticoPowerQuery } from '@/tico/queries';
import { Frame } from './Frame';
import { Sheet } from './Sheet';
import type { Arrangement } from './layout';

/* The old Assistant (its page, its panel on every page, the tab on your own page) is Tico, the first sheet: drawn
   left of everything in every arrangement, holding Tico's own chat. The chat itself is the conversation module's. */

vi.mock('@/motion', () => ({ motionLevel: () => 'off' }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/shell/phone', () => ({ PhoneCards: () => <div>cards</div>, usePhoneCards: () => null }));
vi.mock('@/conversation', async (orig) => ({
  ...(await orig<object>()),
  Conversation: ({ id, variant }: { id: string; variant: string }) => <p>{`${variant} chat in ${id}`}</p>,
}));

const resize = (w: number) =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });

async function show(arrangement: Arrangement) {
  const frame = (
    <Frame
      arrangement={arrangement}
      tico={<TicoSheet />}
      menu={<nav className="pane nav" aria-label="Main" />}
      page={<Sheet kind="page" label="Tasks" />}
      chat={<Sheet kind="chat" label="The task's chat" />}
      widgets={<Sheet kind="column" label="Widgets" />}
    />
  );
  const root = createRootRoute({ component: () => frame });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return screen.findByRole('complementary', { name: 'Tico' });
}

beforeEach(() => vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline'))));
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('Tico, the first sheet @s212 @s238 @s624 @s1193', () => {
  it.each<Arrangement>(['page', 'chat', 'agent'])('sits first in the %s arrangement, holding Tico’s own chat', async (arrangement) => {
    resize(1440);
    queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-1');
    queryClient.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
    const tico = await show(arrangement);
    expect(document.querySelector('[data-sheet]')?.getAttribute('data-sheet')).toBe('tico');
    expect(document.querySelector('[data-sheet="tico"]')?.contains(tico)).toBe(true);
    expect(within(tico).getByText('Tico')).toBeTruthy();
    expect(within(tico).getByText('tico chat in room-1')).toBeTruthy();
  });

  it('says so in its place when Tico’s chat did not load; the page beside it still draws', async () => {
    resize(1440);
    queryClient.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
    queryClient.setQueryDefaults(ticoRoomQuery.queryKey, { retry: false });
    const tico = await show('page');
    expect(await within(tico).findByRole('img', { name: 'Tico did not load' })).toBeTruthy();
    expect(screen.getByRole('main', { name: 'Tasks' })).toBeTruthy();
  });

  it('shows message bones while Tico’s chat loads, then the chat', async () => {
    resize(1440);
    queryClient.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
    vi.stubGlobal('fetch', () => new Promise(() => {}));
    const tico = await show('page');
    expect(await within(tico).findByRole('status', { name: 'Loading Tico' })).toBeTruthy();
    act(() => queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-2'));
    expect(await within(tico).findByText('tico chat in room-2')).toBeTruthy();
    expect(within(tico).queryByRole('status', { name: 'Loading Tico' })).toBeNull();
  });
});
