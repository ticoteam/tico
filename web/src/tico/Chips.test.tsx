import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import { liveQuery, timelineQuery } from '@/conversation';
import { catchUpQuery } from '@/features/threads/routing';
import { chipsFor, TicoChips } from './Chips';

vi.mock('@/actions', async (orig) => ({ ...(await orig<object>()), run: vi.fn() }));
afterEach(() => vi.clearAllMocks());

// Read with node's fs (tests load no CSS, not even ?raw), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const menuCss = fs.readFileSync(new URL('../shell/Menu.css', here), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const texts = (path: string, news = false) => chipsFor(path, news).map((c) => c.text);
const ROOM = 'room-1';

async function mount(path: string, news = 0) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(catchUpQuery.queryKey, { count: news } as never);
  client.setQueryData(timelineQuery(ROOM).queryKey, { pages: [{ messages: [], next_cursor: null }], pageParams: [null] } as never);
  const root = createRootRoute({ component: () => <TicoChips room={ROOM} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findAllByRole('button');
  return client;
}

describe("Tico's chips", () => {
  it('follow the open page, a few at most', () => {
    expect(texts('/next/threads')).toEqual(['What needs me?']);
    expect(texts('/next/flow', true)).toEqual(['Catch up', 'What needs me?']);
    expect(texts('/next/tasks/p1/t1')).toEqual(['Summarize this', "What's next?"]);
    expect(texts('/next/flow/t1')).toEqual(['Summarize this', "What's next?"]);
    expect(texts('/next/tasks/p1/edit')).toEqual(['What needs me?']);
    expect(texts('/next/threads/th1', true)).toEqual(['Summarize thread']);
    expect(texts('/next/threads/mail')).toEqual(['What needs me?']);
    expect(texts('/next/company')).toEqual(['What changed?']);
    expect(texts('/next/company/finance')).toEqual(['What changed?']);
    expect(texts('/next/company/growth')).toEqual(['What changed?']);
    expect(texts('/next/company/routines')).toEqual(['What needs me?']);
  });

  it('send their words to Tico, then go until the page is left', async () => {
    await mount('/tasks/p1/t1');
    fireEvent.click(screen.getByRole('button', { name: 'Summarize this' }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('tico.ask', { text: 'Summarize this' }, { via: 'click' });
    expect(screen.queryByRole('button', { name: 'Summarize this' })).toBeNull();
    expect(screen.getByRole('button', { name: "What's next?" })).toBeTruthy();
  });

  it('show Catch up only while something is new, and hide while Tico answers', async () => {
    const client = await mount('/threads', 3);
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Catch up', 'What needs me?']);
    act(() => void client.setQueryData(liveQuery(ROOM).queryKey, [{ run: 'r1', ended: false } as never]));
    await waitFor(() => expect(screen.queryAllByRole('button')).toHaveLength(0));
  });

  it('fade away while the chat is scrolled up (the foot is data-up), and come back with a fade', () => {
    const rule = (sel: string) => menuCss.match(new RegExp(`(^|\\n)${sel.replace(/[.[\]]/g, '\\$&')}\\{([^}]*)\\}`))?.[2] ?? '';
    expect(rule('.chat-foot[data-up] .qchips')).toMatch(/display:none;opacity:0;transition:opacity var\(--dur-leave\)/);
    expect(rule('.qchips')).toMatch(/transition:opacity var\(--dur-enter\)/);
    expect(menuCss).toMatch(/@starting-style\{\.qchips\{opacity:0\}\}/);
  });
});
