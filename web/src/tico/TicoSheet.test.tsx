import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { ticoRoomQuery } from '@/conversation';
import { PhoneCards } from '@/shell/phone';
import { closeSearch, searchState } from '@/search/state';
import { ticoPowerQuery } from './queries';
import { TicoSheet } from './TicoSheet';

async function show(tree: () => ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
  client.setQueryData(ticoRoomQuery.queryKey, null);
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

  it('sits once on the phone card: the card draws it, the head has none of its own', async () => {
    const page = await show(() => <PhoneCards page={<p>Tasks</p>} tico={<TicoSheet />} menu={null} />);
    const card = page.querySelector('.ly-tico')!;
    expect(card.querySelectorAll('.dw')).toHaveLength(1);
    expect(card.querySelectorAll('.tico-head .dw')).toHaveLength(0);
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
