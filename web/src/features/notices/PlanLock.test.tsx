import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data';
import { PlanLock } from './PlanLock';
import { checkPlan, lockOf } from './queries';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const PAY = 'https://billing.example.com/pay';
const locked = (reason: string | null = 'unpaid', billing_url: unknown = PAY) =>
  new ApiError(402, 'locked', 'Pay to continue', false, null, { cloud: { access: 'locked', reason, billing_url } });

function show(node: ReactNode) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/** A read the server refused while locked; `answer` decides what the next read gets. */
async function refuse(answer: () => unknown) {
  await queryClient.prefetchQuery({ queryKey: ['directory'], queryFn: async () => answer(), retry: false });
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
});
afterEach(() => vi.unstubAllGlobals());

describe('Your plan needs attention @s1224', () => {
  it('reads the lock from a refusal, and only from a locked one', () => {
    expect(lockOf(locked('trial_over'))).toEqual({ reason: 'trial_over', billingUrl: PAY });
    expect(lockOf(locked(null, 'http://plain.example.com'))?.billingUrl).toBe('https://app.tico.dev/billing');
    expect(lockOf(new ApiError(402, 'out_of_credit', '', false))).toBeNull();
    expect(lockOf(new ApiError(500, 'locked', '', false))).toBeNull();
  });

  it('draws nothing while the plan is fine', async () => {
    show(
      <>
        <main>App</main>
        <PlanLock />
      </>,
    );
    expect(await screen.findByRole('main')).toBeTruthy();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('covers the app with why, Check again and Open billing once a read is refused', async () => {
    await refuse(() => {
      throw locked();
    });
    show(
      <>
        <div data-testid="app">App</div>
        <PlanLock />
      </>,
    );
    const lock = await screen.findByRole('alertdialog', { name: 'Your plan needs attention' });
    expect(within(lock).getByText("This month's payment didn't go through.")).toBeTruthy();
    expect(within(lock).getByRole('button', { name: 'Check again' })).toBeTruthy();
    expect(within(lock).getByRole('button', { name: 'Open billing' })).toBeTruthy();
    expect(within(lock).getByRole('button', { name: 'Sign out' })).toBeTruthy();
    expect(within(lock).getByRole('link', { name: 'Contact Tico support' }).getAttribute('href')).toBe('https://app.tico.dev/support');
    expect(within(lock).queryByRole('button', { name: 'Download files' })).toBeNull();
    expect((screen.getByTestId('app') as HTMLElement).inert).toBe(true);
  });

  it('offers the owner the download of every file', async () => {
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    await refuse(() => {
      throw locked('trial_over');
    });
    show(<PlanLock />);
    const lock = await screen.findByRole('alertdialog');
    expect(within(lock).getByText('Your trial has ended.')).toBeTruthy();
    expect(within(lock).getByRole('button', { name: 'Download files' })).toBeTruthy();
  });

  it('Check again reads the refused data again and keeps the lock while it is still refused', async () => {
    const answer = vi.fn(() => {
      throw locked();
    });
    await refuse(answer);
    show(<PlanLock />);
    const lock = await screen.findByRole('alertdialog');
    await act(async () => {
      fireEvent.click(within(lock).getByRole('button', { name: 'Check again' }));
    });
    await waitFor(() => expect(answer).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('alertdialog')).toBeTruthy();
  });

  it('the lock lifts once the refused reads come back', async () => {
    let paid = false;
    await refuse(() => {
      if (!paid) throw locked();
      return { actors: [], byId: {} };
    });
    expect(await checkPlan()).toBe(false);
    paid = true;
    expect(await checkPlan()).toBe(true);
  });
});
