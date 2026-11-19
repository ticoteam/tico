import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Frame } from '@/shell/Frame';
import { Sheet } from '@/shell/Sheet';
import { MenuLip } from './MenuLip';

vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/motion', () => ({ motionLevel: () => 'off' }));

const page = (
  <Sheet kind="page" label="Finance">
    <h1>Finance</h1>
  </Sheet>
);

function show(node: ReactNode) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/company/finance'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const phone = () =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });

afterEach(() => {
  queryClient.clear();
  Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
});

describe("The phone's menu lip @s1191", () => {
  it('shows the company, the page behind and what waits on you in Flow, passed through the frame @s1191', async () => {
    phone();
    show(<Frame arrangement="page" tico={<p>Tico</p>} menu={<nav aria-label="Main" />} page={page} lips={{ menu: <MenuLip company="Fernhill" flow={4} /> }} />);
    const lip = await screen.findByRole('button', { name: 'Open Menu' });
    expect(within(lip).getByText('Fernhill')).toBeTruthy();
    expect(within(lip).getByText('F')).toBeTruthy();
    expect(await within(lip).findByText('Finance')).toBeTruthy();
    expect(within(lip).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(within(lip).getByText('4')).toBeTruthy();
  });

  it('leaves the count out when nothing waits on you @s1191', async () => {
    phone();
    show(<Frame arrangement="page" tico={<p>Tico</p>} menu={<nav aria-label="Main" />} page={page} lips={{ menu: <MenuLip company="Fernhill" /> }} />);
    const lip = await screen.findByRole('button', { name: 'Open Menu' });
    expect(within(lip).getByText('Fernhill')).toBeTruthy();
    expect(within(lip).queryByRole('img', { name: 'Waits on you' })).toBeNull();
  });
});
