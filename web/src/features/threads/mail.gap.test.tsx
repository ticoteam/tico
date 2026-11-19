import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { MailPage } from './MailPage';
import { mailboxesQuery } from './queries';
import './actions';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

// jsdom lays nothing out: give every element a box so the virtual list has a window to fill.
const saved = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 600 });
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
afterAll(() => {
  if (saved) Object.defineProperty(Element.prototype, 'clientHeight', saved);
});

const urls: string[] = [];
function serve(messages: unknown[]) {
  vi.stubGlobal('fetch', async (url: string) => {
    urls.push(String(url));
    const body = String(url).includes('/mail/messages') ? { messages, next_cursor: null } : { items: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const letter = (extra: Record<string, unknown> = {}) => ({
  msg_id: 'l1',
  thread_id: 'th1',
  mailbox: 'help@example.test',
  epoch: Math.floor(Date.now() / 1000) - 600,
  from_header: 'Sam Lee <sam@example.test>',
  from_addr: 'sam@example.test',
  subject: 'Invoice question',
  labels: ['INBOX', 'UNREAD', 'IMPORTANT', 'CATEGORY_UPDATES'],
  has_attachment: true,
  ...extra,
});

beforeEach(() => {
  queryClient.clear();
  urls.length = 0;
  queryClient.setQueryData(mailboxesQuery.queryKey, [{ address: 'help@example.test' }]);
});

describe('Mail: search @s159', () => {
  it('asks the server for the words searched, and marks an attachment with an icon, not pills @s159', async () => {
    serve([letter()]);
    show(<MailPage box="help@example.test" q="invoice" />, '/threads/mail?box=help%40example.test&q=invoice');
    const row = await screen.findByTitle('Sam Lee: Invoice question');
    const asked = new URL(urls.find((u) => u.includes('/mail/messages'))!, 'http://x');
    expect(asked.searchParams.get('q')).toBe('invoice');
    expect(within(row).getByRole('img', { name: 'Attachment' })).toBeTruthy();
    expect(within(row).queryByText('Important')).toBeNull();
    // An opened mail keeps the words it was found by.
    expect(row.getAttribute('href')).toBe('/threads/mail?box=help%40example.test&q=invoice&open=th1');
    expect((screen.getByRole('textbox', { name: 'Find mail' }) as HTMLInputElement).value).toBe('invoice');
  });

  it('searches through the address @s159', async () => {
    serve([letter()]);
    const router = show(<MailPage box="help@example.test" />, '/threads/mail?box=help%40example.test');
    await screen.findByTitle('Sam Lee: Invoice question');
    expect((await run('mail.find', { box: 'help@example.test', q: 'refund' }, { via: 'tico' })).ok).toBe(true);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ box: 'help@example.test', q: 'refund' }));
  });

  it('says nothing matches, and keeps the search field, when a narrowing finds nothing @s159', async () => {
    serve([]);
    show(<MailPage box="help@example.test" q="nothing here" />, '/threads/mail?box=help%40example.test&q=nothing+here');
    expect(await screen.findByRole('heading', { name: 'No matches' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Find mail' })).toBeTruthy();
  });
});
