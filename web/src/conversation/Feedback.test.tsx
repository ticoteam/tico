import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type * as Live from '@/live';
import { Conversation } from './Conversation';
import { feedbackKey } from './queries';
import type { ChatMessage } from './types';

vi.mock('@/live', async (actual) => ({ ...(await actual<typeof Live>()), useFollow: () => {}, onTopic: () => () => {} }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const T = 't1';
const ME = 'human:me';
const saved = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 600 });
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
afterAll(() => {
  if (saved) Object.defineProperty(Element.prototype, 'clientHeight', saved);
});
afterEach(() => vi.unstubAllGlobals());

const at = (min: number) => new Date(new Date().setHours(12, min, 0, 0)).toISOString();
const msg = (seq: number, sender: string, body: string): ChatMessage => ({
  id: `m${seq}`,
  seq,
  thread_id: T,
  conversation_id: T,
  sender_id: sender,
  body,
  created_at: at(seq),
});

type Reply = { status: number; body: unknown };
const calls: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(messages: ChatMessage[]) {
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['thread', T, 'timeline'], { pages: [{ messages, next_cursor: null, read_marks: {} }], pageParams: [null] });
  queryClient.setQueryData(feedbackKey(T), []);
  const root = createRootRoute({ component: () => <Conversation id={T} variant="page" /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/threads/t1'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
});

describe('A picture in a message', () => {
  it('opens full size over the page, not in a new tab', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(1, 'bot:qa', 'Here it is: ![Chart](/api/v2/files/f1)')]);
    fireEvent.click(await screen.findByRole('img', { name: 'Chart' }));
    const dialog = await screen.findByRole('dialog', { name: 'Chart' });
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/f1');
  });
});

describe('Good and Not good', () => {
  it('rates only what others said', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(1, ME, 'Mine'), msg(2, 'bot:qa', 'Theirs')]);
    await screen.findByText('Theirs');
    expect(screen.getAllByRole('group', { name: 'Rate this message' })).toHaveLength(1);
  });

  it('shows Good at once and takes it back when the server refuses it', async () => {
    let refuse!: () => void;
    serve((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/feedback')
        ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } })))
        : { status: 200, body: { items: [], counts: [] } },
    );
    show([msg(2, 'bot:qa', 'A reply')]);
    const good = await screen.findByRole('button', { name: 'Good' });
    fireEvent.click(good);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Good' }).getAttribute('aria-pressed')).toBe('true'));
    await act(async () => refuse());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Good' }).getAttribute('aria-pressed')).toBe('false'));
  });

  it('keeps Not good at once, then asks what was wrong and sends the reason to the agent', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/feedback')) {
        const body = calls[calls.length - 1]!.body as { reason?: string; tell_bot?: boolean };
        return {
          status: 200,
          body: { id: 'f1', subject_type: 'message', subject_id: 'm2', rating: 'down', reason: body.reason ?? '', outcome: 'none', note_status: body.tell_bot ? 'waiting' : null },
        };
      }
      if (url.includes('recent-reasons')) return { status: 200, body: { reasons: [{ text: 'Too long', count: 1 }] } };
      return { status: 200, body: { items: [], counts: [] } };
    });
    show([msg(2, 'bot:qa', 'A reply')]);
    fireEvent.click(await screen.findByRole('button', { name: 'Not good' }));
    await screen.findByRole('dialog', { name: 'Not good' });
    expect(calls.some((c) => c.method === 'POST' && (c.body as { rating?: string }).rating === 'down')).toBe(true);
    fireEvent.click(await screen.findByRole('button', { name: 'Too long' }));
    fireEvent.click(screen.getByRole('button', { name: 'Tell agent' }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && (c.body as { tell_bot?: boolean; reason?: string }).tell_bot && (c.body as { reason?: string }).reason === 'Too long')).toBe(true),
    );
  });
});
