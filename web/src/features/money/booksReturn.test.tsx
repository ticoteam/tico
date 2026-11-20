import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { BOOKS_WHY } from './booksReturn';
import { FinancePage } from './Finance';
import { BOOKS_REASONS, validateFinanceSearch } from './search';

/* A QuickBooks or Xero sign-in's return: said once from the server's read, then taken out of the address. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

// Made-up sources only.
const BOOKS_ROW = { id: 'c1', kind: 'connection', name: 'Sample books', provider: 'quickbooks', sync: { status: 'active' } };
function serve(books: boolean) {
  const reads: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => {
    reads.push(String(url));
    const body = String(url).includes('/finance/sources')
      ? { groups: [{ group: 'accounting', name: 'Books', count: books ? 1 : 0, rows: books ? [BOOKS_ROW] : [] }] }
      : String(url).endsWith('/finance/access') ? { teammates: [] } : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return reads;
}

function show(at: string) {
  const Finance = () => <FinancePage search={validateFinanceSearch(useSearch({ strict: false }))} />;
  const router = createRouter({ routeTree: createRootRoute({ component: Finance }), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
const query = (router: ReturnType<typeof show>) => Object.fromEntries(new URL(router.state.location.href, 'http://x').searchParams);

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the books return', () => {
  it('keeps only known words: every reason, and anything else as unknown', () => {
    for (const r of BOOKS_REASONS) {
      expect(validateFinanceSearch({ books: 'error', reason: r })).toEqual({ books: 'error', reason: r });
      expect(BOOKS_WHY[r]).toBeTruthy();
    }
    expect(validateFinanceSearch({ books: 'connected' })).toEqual({ books: 'connected' });
    expect(validateFinanceSearch({ books: '<b>hi</b>', reason: 'javascript:alert(1)' })).toEqual({ books: 'other', reason: 'unknown' });
  });

  it('says connected only when the sources read again hold the books, then cleans the address', async () => {
    const reads = serve(true);
    const router = show('/company/finance?tab=sources&books=connected');
    expect(await screen.findByText((_, el) => el?.tagName === 'SPAN' && el.textContent === 'Connected: QuickBooks')).toBeTruthy();
    await waitFor(() => expect(query(router)).toEqual({ tab: 'sources' }));
    expect(reads.some((u) => u.includes('/finance/sources'))).toBe(true);
    expect(screen.queryByRole('complementary', { name: 'Books not connected' })).toBeNull();
  });

  it('does not take "connected" from the address when the server holds no books', async () => {
    serve(false);
    const router = show('/company/finance?tab=sources&books=connected');
    expect(await screen.findByText(BOOKS_WHY.unknown)).toBeTruthy();
    expect(screen.queryByText(/^Connected/)).toBeNull();
    await waitFor(() => expect(query(router)).toEqual({ tab: 'sources' }));
  });

  it('opens the failure beside Sources, in plain words with the next step', async () => {
    serve(false);
    const router = show('/company/finance?tab=sources&books=error&reason=access_denied');
    expect(await screen.findByRole('complementary', { name: 'Books not connected' })).toBeTruthy();
    expect(screen.getByText(BOOKS_WHY.access_denied)).toBeTruthy();
    await waitFor(() => expect(query(router)).toEqual({ tab: 'sources' }));
  });

  it('never shows a reason it does not know', async () => {
    serve(false);
    show('/company/finance?tab=sources&books=error&reason=%3Cimg%20src%3Dx%3E');
    expect(await screen.findByText(BOOKS_WHY.unknown)).toBeTruthy();
    expect(document.body.innerHTML).not.toContain('img src');
  });

  it('ignores an unknown word and only cleans the address', async () => {
    const reads = serve(true);
    const router = show('/company/finance?tab=transactions&books=maybe');
    await waitFor(() => expect(query(router)).toEqual({ tab: 'transactions' }));
    expect(screen.queryByText(/^Connected/)).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'Books not connected' })).toBeNull();
    expect(reads.some((u) => u.includes('/finance/sources'))).toBe(false);
  });
});
