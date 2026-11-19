import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, actionsCatalog } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { sendToSupport, ticketsQuery, type SupportList } from './queries';
import { SupportChat } from './SupportChat';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const at = (min: number) => new Date(new Date().setHours(12, min, 0, 0)).toISOString();
const OPEN: SupportList = {
  enabled: true,
  unread: 1,
  tickets: [
    {
      id: 't1',
      status: 'answered',
      message: 'Labels fail since this morning',
      created_at: at(1),
      unread: 1,
      messages: [{ id: 1, from: 'staff', body: 'We see it. Try the new key.', created_at: at(2) }],
    },
  ],
};
const CLOSED: SupportList = { enabled: true, unread: 0, tickets: [{ ...OPEN.tickets[0]!, status: 'closed', unread: 0 }] };

type Reply = { status: number; body: unknown };
let calls: { url: string; method: string; body: unknown; key: string | null }[] = [];
let list: SupportList = OPEN;
let reply: (url: string, method: string) => Reply | undefined = () => undefined;
const posts = () => calls.filter((c) => c.method !== 'GET' && !c.url.endsWith('/refresh'));

function serve() {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body, key: new Headers(init?.headers).get('Idempotency-Key') });
    const r: Reply = reply(url, method) ??
      (url === '/api/v2/support/tickets' || url.endsWith('/refresh')
        ? { status: 200, body: list }
        : url === '/api/v2/support/compose'
          ? { status: 200, body: { email: 'ana@example.test', version: '0.6.30', install_id: 'i1' } }
          : url === '/api/v2/config'
            ? { status: 200, body: { company_name: 'Fernhill Test' } }
            : url === '/api/v2/me'
              ? { status: 200, body: { actor: 'human:p1', email: 'ana@example.test', role: 'member' } }
              : url.endsWith('/read')
                ? { status: 200, body: { ...list.tickets[0], unread: 0 } }
                : { status: 200, body: { id: 't2', status: 'open', message: body?.message ?? '', created_at: at(5), unread: 0, messages: [] } });
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show() {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <SupportChat />
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks?chat=support'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const say = async (text: string) => {
  const box = await screen.findByRole('textbox', { name: 'Message Tico support' });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: 'Enter' });
};

/* jsdom lays nothing out: every box 300 tall and every row 50, so the virtual list draws its rows. */
const layout: Record<string, PropertyDescriptor> = {
  clientHeight: { get: () => 300 },
  clientWidth: { get: () => 400 },
  offsetHeight: { get: () => 50 },
};
const saved = Object.keys(layout).map((k) => [k, Object.getOwnPropertyDescriptor(HTMLElement.prototype, k)] as const);
beforeAll(() => {
  notifyManager.setScheduler((cb) => cb());
  for (const [k, d] of Object.entries(layout)) Object.defineProperty(HTMLElement.prototype, k, { configurable: true, ...d });
});
afterAll(() => {
  for (const [k, d] of saved) if (d) Object.defineProperty(HTMLElement.prototype, k, d);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
});
beforeEach(() => {
  calls = [];
  list = OPEN;
  reply = () => undefined;
  serve();
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('Tico support', () => {
  it('draws the tickets as one conversation and reads the reply', async () => {
    show();
    expect(await screen.findByText('We see it. Try the new key.')).toBeTruthy();
    expect(screen.getByText('Labels fail since this morning')).toBeTruthy();
    expect(calls.some((c) => c.method === 'GET' && c.url === '/api/v2/support/tickets')).toBe(true);
    await waitFor(() => expect(posts().map((c) => c.url)).toContain('/api/v2/support/tickets/t1/read'));
  });

  it('sends to the open ticket @s1123', async () => {
    show();
    await screen.findByText('We see it. Try the new key.');
    await say('Pasted the new key');
    expect(screen.getByText('Pasted the new key')).toBeTruthy();
    await waitFor(() => expect(posts().find((c) => c.url.endsWith('/messages'))).toBeTruthy());
    const sent = posts().find((c) => c.url.endsWith('/messages'))!;
    expect(sent.url).toBe('/api/v2/support/tickets/t1/messages');
    expect(sent.body).toEqual({ message: 'Pasted the new key', diagnostics: '' });
  });

  it('starts a new ticket when none is open, with the reply email and a key @s1123', async () => {
    list = CLOSED;
    show();
    await screen.findByText('We see it. Try the new key.');
    await waitFor(() => expect(calls.some((c) => c.url === '/api/v2/support/compose')).toBe(true));
    await say('Another question');
    await waitFor(() => expect(posts().find((c) => c.url === '/api/v2/support/tickets')).toBeTruthy());
    const made = posts().find((c) => c.url === '/api/v2/support/tickets')!;
    expect(made.body).toEqual({ message: 'Another question', email: 'ana@example.test', include_ids: true, diagnostics: '' });
    expect(made.key).toMatch(/.{8,}/);
  });

  it('a refused follow-up starts a new ticket once, under the same key', async () => {
    reply = (url) => (url.endsWith('/messages') ? { status: 409, body: { error: { code: 'support_closed', detail: 'Closed' } } } : undefined);
    show();
    await screen.findByText('We see it. Try the new key.');
    await say('Still failing');
    await waitFor(() => expect(posts().find((c) => c.url === '/api/v2/support/tickets')).toBeTruthy());
    const first = posts().find((c) => c.url.endsWith('/messages'))!;
    const made = posts().find((c) => c.url === '/api/v2/support/tickets')!;
    expect(made.key).toBe(`${first.key}-new`);
    expect(made.body).toMatchObject({ message: 'Still failing' });
  });

  it('a refused send stays in the list as not sent', async () => {
    reply = (url) => (url.endsWith('/messages') ? { status: 422, body: { error: { code: 'validation', detail: 'Too long' } } } : undefined);
    show();
    await screen.findByText('We see it. Try the new key.');
    await say('Hello');
    expect(await screen.findByText('Not sent. Too long')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('keeps a later reply available to retry when an earlier reply finishes first', async () => {
    const ticket = OPEN.tickets[0]!;
    queryClient.setQueryData(ticketsQuery.queryKey, OPEN);
    let finishFirst!: (response: Response) => void;
    let finishSecond!: (response: Response) => void;
    const first = new Promise<Response>((resolve) => (finishFirst = resolve));
    const second = new Promise<Response>((resolve) => (finishSecond = resolve));
    vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second));
    const sent = sendToSupport({ message: 'First reply', to: ticket.id, key: 'first' });
    const failed = sendToSupport({ message: 'Second reply', to: ticket.id, key: 'second' });

    finishFirst(Response.json({
      ...ticket,
      messages: [...(ticket.messages ?? []), { id: 2, from: 'person', body: 'First reply', created_at: at(3) }],
    }));
    await sent;
    const refused = expect(failed).rejects.toMatchObject({ status: 422 });
    finishSecond(Response.json({ error: { code: 'validation', detail: 'Too long' } }, { status: 422 }));
    await refused;

    expect(queryClient.getQueryData(ticketsQuery.queryKey)?.tickets[0]?.messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ body: 'First reply' }),
      expect.objectContaining({ body: 'Second reply', pending: 'failed' }),
    ]));
  });

  it('shows starters when there is nothing yet, and says when support is off', async () => {
    list = { enabled: true, unread: 0, tickets: [] };
    show();
    expect(await screen.findByRole('button', { name: 'An agent is stuck' })).toBeTruthy();
    queryClient.setQueryData(['support', 'tickets'], { enabled: false, unread: 0, tickets: [] });
    expect(await screen.findByText('Tico support is off on this server')).toBeTruthy();
  });
});

describe('Tico support actions', () => {
  it('Tico may write to support but never send diagnostics or delete the history', async () => {
    const ids = actionsCatalog().map((a) => a.id);
    expect(ids).toEqual(expect.arrayContaining(['support.open', 'support.send', 'support.newTicket', 'support.markRead']));
    expect(ids).not.toContain('support.sendDiagnostics');
    expect(ids).not.toContain('support.deleteHistory');
    const r = await run('support.sendDiagnostics', { message: 'Hi', diagnostics: 'd'.repeat(64) }, { via: 'tico' });
    expect(r).toEqual({ ok: false, error: 'not_allowed' });
    expect(posts()).toHaveLength(0);
  });
});
