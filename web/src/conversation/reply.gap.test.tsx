import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import { bind } from '@/actions/bind';
import { keys, queryClient } from '@/data';
import { toast } from '@/kit/Toast';
import type * as Live from '@/live';
import type * as ToastModule from '@/kit/Toast';
import { Conversation } from './Conversation';
import { timelineKey } from './queries';
import type { ChatMessage, Variant } from './types';

vi.mock('@/live', async (actual) => ({ ...(await actual<typeof Live>()), useFollow: () => {}, onTopic: () => () => {} }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/kit/Toast', async (actual) => ({ ...(await actual<typeof ToastModule>()), toast: vi.fn() }));

const T = 't1';
const ME = 'human:me';
const ANA = 'human:ana';
const saved = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
const savedScroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTo');
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 600 });
  // jsdom draws no layout: sending scrolls the chat to its end.
  Object.defineProperty(Element.prototype, 'scrollTo', { configurable: true, writable: true, value: () => {} });
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
afterAll(() => {
  if (saved) Object.defineProperty(Element.prototype, 'clientHeight', saved);
  if (savedScroll) Object.defineProperty(Element.prototype, 'scrollTo', savedScroll);
  else delete (Element.prototype as { scrollTo?: unknown }).scrollTo;
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.layout;
});

const at = (min: number) => new Date(new Date().setHours(12, min, 0, 0)).toISOString();
const msg = (seq: number, sender: string, body: string, more: Partial<ChatMessage> = {}): ChatMessage => ({
  id: `m${seq}`,
  seq,
  thread_id: T,
  conversation_id: T,
  sender_id: sender,
  body,
  created_at: at(seq),
  ...more,
});

type Reply = { status: number; body: unknown };
const calls: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const r = (await reply(String(url), method)) ?? { status: 200, body: { items: [], counts: [] } };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(messages: ChatMessage[], variant: Variant = 'page') {
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(keys.directory(), {
    actors: [{ id: ANA, kind: 'person', name: 'Ana Lima' }],
    byId: { [ANA]: { id: ANA, kind: 'person', name: 'Ana Lima' } },
  });
  queryClient.setQueryData(timelineKey(T), { pages: [{ messages, next_cursor: null, read_marks: {} }], pageParams: [null] });
  const root = createRootRoute({ component: () => <Conversation id={T} variant={variant} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/threads/t1'] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  vi.mocked(toast).mockClear();
});

describe('Reply with quote @s157', () => {
  it('Tico may reply for the person', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith(`/threads/${T}/messages`) ? { status: 200, body: msg(3, ME, 'On it', { in_reply_to: 'm2' }) } : undefined,
    );
    show([msg(2, ANA, 'Shipping Friday')]);
    await screen.findByText('Shipping Friday');
    expect((await run('conversation.reply', { chat: T, message: 'm2', text: 'On it' }, { via: 'tico' })).ok).toBe(true);
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith(`/threads/${T}/messages`));
    expect(post?.body).toMatchObject({ text: 'On it', in_reply_to: 'm2' });
  });

  it('Reply puts the message above the box, sends in_reply_to, and shows the quote above the reply', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith(`/threads/${T}/messages`)
        ? { status: 200, body: msg(3, ME, 'Yes, Friday', { in_reply_to: 'm2', reply_to: { id: 'm2', author: ANA, author_name: 'Ana Lima', excerpt: 'Can we ship Friday?' } }) }
        : undefined,
    );
    show([msg(2, ANA, 'Can we **ship** Friday?')]);
    fireEvent.click(await screen.findByRole('button', { name: 'Reply' }));
    const above = screen.getByRole('group', { name: 'Replying to' });
    expect(above.textContent).toContain('Ana Lima');
    expect(above.textContent).toContain('Can we ship Friday?');
    const box = screen.getByRole('textbox', { name: /^Message/ });
    fireEvent.change(box, { target: { value: 'Yes, Friday' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ text: 'Yes, Friday', in_reply_to: 'm2' }));
    expect(screen.queryByRole('group', { name: 'Replying to' })).toBeNull();
    const mine = (await screen.findByText('Yes, Friday')).closest('.msg')!;
    expect(within(mine as HTMLElement).getByRole('button', { name: /Ana Lima/ }).textContent).toContain('Can we ship Friday?');
  });

  it('the quote above the box can be dropped', async () => {
    serve();
    show([msg(2, ANA, 'Can we ship Friday?')]);
    fireEvent.click(await screen.findByRole('button', { name: 'Reply' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel reply' }));
    expect(screen.queryByRole('group', { name: 'Replying to' })).toBeNull();
  });

  it('pressing a quote goes to the original when it is loaded', async () => {
    serve();
    show([
      msg(1, ANA, 'Can we ship Friday?'),
      msg(2, 'bot:qa', 'Yes', { in_reply_to: 'm1' }),
      msg(3, 'bot:qa', 'And the beds', { reply_to: { id: 'm0', author: ANA, author_name: 'Ana Lima', excerpt: 'An older one' } }),
    ]);
    fireEvent.click(await screen.findByRole('button', { name: /Can we ship Friday\?/ }));
    expect(toast).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /An older one/ }));
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ text: 'That message is not loaded' }));
  });

  it("Tico's own chat offers no Reply", async () => {
    serve();
    show([msg(2, 'bot:tico', 'Done')], 'tico');
    await screen.findByText('Done');
    expect(screen.queryByRole('button', { name: 'Reply' })).toBeNull();
  });
});

describe('Message actions, on a phone @s181', () => {
  it('Reply from the long press puts the quote above the box', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve();
    show([msg(2, 'bot:qa', 'The plan is ready')]);
    const said = await screen.findByText('The plan is ready');
    act(() => {
      fireEvent.contextMenu(said);
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reply' }));
    expect((await screen.findByRole('group', { name: 'Replying to' })).textContent).toContain('The plan is ready');
  });
});
