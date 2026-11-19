import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

function show(messages: ChatMessage[], role: 'person' | 'owner' = 'person') {
  queryClient.setQueryData(['me'], { id: ME, email: '', role, slug: null });
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

describe('A picture in a message @s197', () => {
  it('opens full size over the page, not in a new tab', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(1, 'bot:qa', 'Here it is: ![Chart](/api/v2/files/f1)')]);
    fireEvent.click(await screen.findByRole('img', { name: 'Chart' }));
    const dialog = await screen.findByRole('dialog', { name: 'Chart' });
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/f1');
  });
});

describe('A picture attached to a chat message @s218', () => {
  it('opens full size from its file chip, with Download', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    const shot = { id: 'f2', name: 'shot.png', content_type: 'image/png' };
    show([{ ...msg(1, 'bot:qa', 'The new header'), refs: { attachments: [shot] } } as ChatMessage]);
    fireEvent.click(await screen.findByRole('link', { name: /shot\.png/ }));
    const dialog = await screen.findByRole('dialog', { name: 'shot.png' });
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/f2');
    expect(within(dialog).getByRole('link', { name: 'Download' }).getAttribute('href')).toBe('/api/v2/files/f2');
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

describe('Message actions, on a phone @s181', () => {
  const writeText = vi.fn(async (_text: string) => {});
  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  });
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  it('a long press on what an agent said opens Copy, Good and Not good; Not good asks what was wrong', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/feedback'))
        return { status: 200, body: { id: 'f1', subject_type: 'message', subject_id: 'm2', rating: 'down', reason: '', outcome: 'none', note_status: null } };
      if (url.includes('recent-reasons')) return { status: 200, body: { reasons: [] } };
      return { status: 200, body: { items: [], counts: [] } };
    });
    show([msg(2, 'bot:qa', 'The plan is ready')]);
    const said = await screen.findByText('The plan is ready');
    act(() => {
      fireEvent.contextMenu(said);
    });
    const sheet = await screen.findByRole('menu');
    expect(sheet.className).toContain('is-sheet');
    expect(screen.getByRole('menuitem', { name: 'Copy' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Good' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not good' }));
    await screen.findByRole('dialog', { name: 'Not good' });
    expect(calls.some((c) => c.method === 'POST' && (c.body as { rating?: string }).rating === 'down')).toBe(true);
  });

  it('Copy takes the words as written', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(1, ME, 'Send it **today**')]);
    const mine = (await screen.findByText('today')).closest('.msg')!;
    act(() => {
      fireEvent.contextMenu(mine);
    });
    expect(screen.queryByRole('menuitem', { name: 'Good' })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Send it **today**'));
  });

  it('on a computer a right click is left to the browser', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(2, 'bot:qa', 'The plan is ready')]);
    expect(fireEvent.contextMenu(await screen.findByText('The plan is ready'))).toBe(true);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('A message that came from a Slack thread @s190', () => {
  const fromSlack = (): ChatMessage => ({
    ...msg(3, 'human:ana', 'Can we ship Friday?'),
    refs: {
      slack: { channel: 'C1', channel_name: 'launch', ts: '1789800000.000200', thread_ts: '1789800000.000100', permalink: 'https://example.slack.com/archives/C1/p1' },
      routing: { recipients: [{ bot: 'helper', confidence: 0.9 }] },
    },
  });

  it('the owner reads the whole Slack thread over the page, each message in full', async () => {
    const long = 'Line one of a long note.\nLine two, still there.';
    serve((url) =>
      url.includes('/slack-thread')
        ? {
            status: 200,
            body: {
              messages: [
                { event_id: 'e1', ts: '1789800000.000100', author: 'Sam Rivera', text: long, deleted: false, url: 'https://example.slack.com/archives/C1/p1' },
                { event_id: 'e2', ts: '1789800000.000200', author: 'Ana Lima', text: 'Can we ship Friday?', deleted: false, url: 'https://example.slack.com/archives/C1/p2' },
              ],
            },
          }
        : { status: 200, body: { items: [], counts: [] } },
    );
    show([fromSlack()], 'owner');
    fireEvent.click(await screen.findByRole('button', { name: 'Slack #launch' }));
    const dialog = await screen.findByRole('dialog', { name: 'Slack thread' });
    await waitFor(() => expect(dialog.textContent).toContain('Sam Rivera'));
    expect(dialog.textContent).toContain(long);
    expect(calls.some((c) => c.url.includes('/api/v2/messaging/bots/helper/slack-thread?channel=C1&thread_ts=1789800000.000100'))).toBe(true);
    expect(screen.getByRole('link', { name: 'Open in Slack' }).getAttribute('href')).toBe('https://example.slack.com/archives/C1/p1');
  });

  it('anyone else gets the link to the message in Slack', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([fromSlack()]);
    const link = await screen.findByRole('link', { name: 'Slack #launch' });
    expect(link.getAttribute('href')).toBe('https://example.slack.com/archives/C1/p1');
    expect(screen.queryByRole('button', { name: 'Slack #launch' })).toBeNull();
  });

  it('a message carried from a linked Slack channel (its channel and time there, no link, no agent) still says so', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([{ ...msg(4, 'human:ana', 'Shipping Friday'), refs: { slack: { channel: 'C1', ts: '1789800000.000300' } } }], 'owner');
    expect(await screen.findByText('Slack')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Slack' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Slack' })).toBeNull();
  });
});
