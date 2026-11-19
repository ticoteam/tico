import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import { googleQuery, lettersQuery, lettersSettingsQuery, mailQuery, sendingQuery, toolsQuery } from './queries';
import { teamsQuery, vaultQuery } from './toolKeys';
import { ToolsPage } from './ToolsPage';

/* Browser run 6 on Tools: the phone's head, a mailbox the index lists without its own record, and the Number and
   Letters sections when their read fails. Made-up tools and addresses only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({ BrowserDialog: () => null }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(teamsQuery.queryKey, []);
  queryClient.setQueryData(vaultQuery.queryKey, { credentials: [], can_manage: false, people: [], bots: [] } as never);
});
afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.layout;
  vi.unstubAllGlobals();
});

type Reply = { status: number; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const refused = (status: number, code: string): Reply => ({ status, body: { error: { code, detail: 'Made-up refusal' } } });

/** Answers each call with `reply`; returns every path asked for. */
function serve(reply: (url: string) => Reply | undefined) {
  const asked: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => {
    asked.push(String(url));
    const r = reply(String(url)) ?? ok({});
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return asked;
}

function at(tool: string | undefined, node: ReactNode) {
  const path = tool ? `/library/tools?tool=${encodeURIComponent(tool)}` : '/library/tools';
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: '/library/tools', component: () => node });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const indexRow = (o: Record<string, unknown>) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who: [], who_total: 0, detail: '', way: '', needs_you: false, status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;
const tools = (rows: ToolIndexRow[] = []) => queryClient.setQueryData(toolsQuery.queryKey, { rows } as never);

/** The head's controls a phone draws besides its ⋯: in the actions only those marked `data-phone` (pagekinds.css hides
    the rest). */
const controls = (head: HTMLElement) =>
  [...head.querySelectorAll<HTMLElement>('a, button')]
    .filter((el) => el.getAttribute('aria-label') !== 'More')
    .filter((el) => {
      const kept = [...head.querySelectorAll('.pk-acts > *')].find((c) => c.contains(el));
      return !kept || kept.hasAttribute('data-phone');
    })
    .map((el) => el.getAttribute('aria-label') ?? el.textContent);

describe('Tools head', () => {
  it('keeps Filter and the ⋯ on a phone; Add tool and the settings pages are in the ⋯', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve(() => undefined);
    tools([indexRow({ id: 'api:a1', name: 'Sample API' })]);
    const router = at(undefined, <ToolsPage search={{}} />);
    const head = (await screen.findByRole('heading', { name: 'Tools' })).closest('header') as HTMLElement;
    expect(controls(head)).toEqual(['Filter tools']);
    expect(within(head).queryByRole('button', { name: 'Add tool' })).toBeNull();
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add tool' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'API' }));
    expect(await screen.findByRole('dialog', { name: 'Add API' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Number requests' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tool: 'number' }));
  });

  it('keeps Add tool beside Filter on a wide screen, with the ⋯ last', async () => {
    serve(() => undefined);
    tools([indexRow({ id: 'api:a1', name: 'Sample API' })]);
    at(undefined, <ToolsPage search={{}} />);
    const head = (await screen.findByRole('heading', { name: 'Tools' })).closest('header') as HTMLElement;
    const labels = within(head)
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label'));
    expect(labels).toEqual(['Filter tools', 'Add tool', 'More']);
  });
});

describe('a mailbox with no record of its own', () => {
  it('shows what it can do and how it sends, as a mailbox’s page does', async () => {
    serve(() => undefined);
    tools([indexRow({ id: 'mailbox:sales', kind: 'mailbox', name: 'sales@example.test', detail: 'sales@example.test' })]);
    queryClient.setQueryData(googleQuery.queryKey, { can_admin: false, workspace: null, pending: null, me: null, permissions: { organize_mail: false, send_mail: true, change_calendar: false } });
    queryClient.setQueryData(sendingQuery.queryKey, { effective: { mode: 'ask_first', daily_cap: 20, mailbox_daily_cap: 50, company_daily_cap: 200 }, company: {} } as never);
    at('mailbox:sales', <ToolsPage search={{ tool: 'mailbox:sales' }} />);
    const box = await screen.findByRole('region', { name: 'sales@example.test' });
    const may = await within(box).findByRole('region', { name: 'What it can do' });
    expect(within(may).getByText('Send mail')).toBeTruthy();
    const sending = within(box).getByRole('region', { name: 'Sending' });
    expect(within(sending).getByText('Ask first')).toBeTruthy();
  });
});

describe('a section whose read fails', () => {
  const requests = '/api/v2/inbound/phone-requests';

  it('on Phone numbers says the requests did not load, and Try again reads them again', async () => {
    let answer = refused(409, 'gateway');
    const asked = serve((url) => (url.startsWith(requests) ? answer : undefined));
    tools();
    at('number', <ToolsPage search={{ tool: 'number' }} />);
    expect(await screen.findByText('Requests did not load')).toBeTruthy();
    expect(screen.queryByText('No requests')).toBeNull();
    const before = asked.filter((u) => u.startsWith(requests)).length;
    answer = ok({ phone_requests: [], next: null });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No requests')).toBeTruthy();
    expect(asked.filter((u) => u.startsWith(requests)).length).toBe(before + 1);
  });

  it('says a 403 is not shared, with nothing to try again', async () => {
    serve((url) => (url.startsWith(requests) ? refused(403, 'forbidden') : undefined));
    tools();
    at('number', <ToolsPage search={{ tool: 'number' }} />);
    expect(await screen.findByText('Not shared with you')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(screen.queryByText('No requests')).toBeNull();
  });

  it('on the mailing address says the addresses did not load, beside the rest of the page', async () => {
    serve((url) =>
      url === '/api/v2/postal/addresses'
        ? refused(503, 'rehearsal')
        : url.startsWith('/api/v2/postal/settings')
          ? ok({ settings: { legal_bot_id: null } })
          : url.startsWith('/api/v2/letters?') || url.startsWith('/api/v2/postal/items')
            ? ok({ items: [] })
            : undefined,
    );
    tools([indexRow({ id: 'address', kind: 'address', name: 'Mailing address' })]);
    queryClient.setQueryData(lettersQuery.queryKey, []);
    queryClient.setQueryData(mailQuery.queryKey, []);
    queryClient.setQueryData(lettersSettingsQuery.queryKey, { mode: 'ask_first', monthly_cap_micros: null, return_address: null, availability: 'ready' } as never);
    at('address', <ToolsPage search={{ tool: 'address' }} />);
    const addresses = await screen.findByRole('region', { name: 'Addresses' });
    expect(within(addresses).getByText('Addresses did not load')).toBeTruthy();
    expect(within(addresses).getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Sending letters' })).toBeTruthy();
  });

  it('says how letters go out did not load, rather than leaving it out', async () => {
    serve((url) => (url.startsWith('/api/v2/letters/settings') ? refused(500, 'boom') : url.startsWith('/api/v2/letters?') ? ok({ items: [] }) : undefined));
    tools([indexRow({ id: 'address', kind: 'address', name: 'Mailing address' })]);
    queryClient.setQueryData(lettersQuery.queryKey, []);
    at('address', <ToolsPage search={{ tool: 'address' }} />);
    const out = await screen.findByRole('region', { name: 'Sending letters' });
    expect(await within(out).findByText('Sending letters did not load')).toBeTruthy();
  });
});
