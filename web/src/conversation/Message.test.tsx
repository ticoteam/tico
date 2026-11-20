import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type * as Live from '@/live';
import { Conversation } from './Conversation';
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

describe('Message actions, on a phone @s181', () => {
  const writeText = vi.fn(async (_text: string) => {});
  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  });
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  it('a long press on what an agent said opens Copy as a sheet', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(2, 'bot:qa', 'The plan is ready')]);
    const said = await screen.findByText('The plan is ready');
    act(() => {
      fireEvent.contextMenu(said);
    });
    const sheet = await screen.findByRole('menu');
    expect(sheet.className).toContain('is-sheet');
    expect(screen.getByRole('menuitem', { name: 'Copy' })).toBeTruthy();
  });

  it('Copy takes the words as written', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(1, ME, 'Send it **today**')]);
    const mine = (await screen.findByText('today')).closest('.msg')!;
    act(() => {
      fireEvent.contextMenu(mine);
    });
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

describe('Who said it, when the message names no one', () => {
  const BOT = 'bot:01a1d25e-6716-71f9-8c8a-06486add5ed1';

  it('an agent the directory knows is named by it, never by its id', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    const actors = [{ id: BOT, kind: 'agent', name: 'Legal Owner' }];
    queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
    show([msg(1, BOT, 'The letter is queued')]);
    expect(await screen.findByText('Legal Owner')).toBeTruthy();
    expect(screen.queryByText(/01a1d25e/)).toBeNull();
  });

  it('one it does not know reads "Agent"', async () => {
    serve(() => ({ status: 200, body: { items: [], counts: [] } }));
    show([msg(1, BOT, 'The letter is queued')]);
    expect(await screen.findByText('Agent')).toBeTruthy();
    expect(screen.queryByText(/01a1d25e/)).toBeNull();
  });
});
