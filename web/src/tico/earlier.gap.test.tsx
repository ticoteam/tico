import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import { bind } from '@/actions/bind';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { readEarlier } from './earlier';
import { ticoPowerQuery, type TicoPower } from './queries';
import { TicoSheet } from './TicoSheet';
import './actions';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/conversation', async (orig) => ({
  ...(await orig<object>()),
  Conversation: ({ id, variant, canPost = true }: { id: string; variant: string; canPost?: boolean }) => (
    <p>{`${variant} chat in ${id}${canPost ? '' : ', read only'}`}</p>
  ),
}));

const owner: TicoPower = {
  available: true,
  bot: 'assistant',
  can_turn_on: true,
  state: 'active',
  earlier: { room_id: 'old-1', bot: 'strategy', name: 'Strategy Owner' },
};

function show() {
  const root = createRootRoute({ component: () => <TicoSheet /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks'] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline'))));
afterEach(() => {
  act(() => readEarlier(null));
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("Tico's earlier chats @s214", () => {
  it('lists the earlier chat under ⋯, opens it read only in the sheet, and Back returns to Tico @s214', async () => {
    queryClient.setQueryData(ticoPowerQuery.queryKey, owner);
    queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-1');
    show();
    expect(await screen.findByText('tico chat in room-1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Earlier chats' })).toBeTruthy();

    expect((await run('tico.openEarlier', { room: 'old-1' }, { via: 'click' })).ok).toBe(true);
    expect(await screen.findByText('task chat in old-1, read only')).toBeTruthy();
    expect(screen.getByText('Strategy Owner')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Back to Tico' }));
    expect(await screen.findByText('tico chat in room-1')).toBeTruthy();
  });

  it('has no ⋯ for someone with no earlier chat, and opens no chat that is not theirs @s214', async () => {
    queryClient.setQueryData(ticoPowerQuery.queryKey, { ...owner, earlier: null });
    queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-1');
    show();
    expect(await screen.findByText('tico chat in room-1')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect((await run('tico.openEarlier', { room: 'old-1' }, { via: 'tico' })).ok).toBe(false);
  });

  it('offers Try again when Tico’s chat did not load, and reads it again @s214', async () => {
    queryClient.setQueryData(ticoPowerQuery.queryKey, { ...owner, earlier: null });
    let asked = 0;
    vi.stubGlobal('fetch', async () => {
      asked += 1;
      return asked === 1
        ? new Response(JSON.stringify({ detail: 'down' }), { status: 500, headers: { 'Content-Type': 'application/json' } })
        : new Response(JSON.stringify({ room_id: 'room-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    show();
    expect(await screen.findByRole('img', { name: 'Tico did not load' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('tico chat in room-1')).toBeTruthy();
  });
});
