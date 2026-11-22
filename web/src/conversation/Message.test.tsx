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

function show(messages: ChatMessage[], { role = 'person', variant = 'page' }: { role?: 'person' | 'owner'; variant?: Variant } = {}) {
  queryClient.setQueryData(['me'], { id: ME, email: '', role, slug: null });
  if (!queryClient.getQueryData(keys.directory()))
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

describe('A picture in a message @s197', () => {
  it('opens full size over the page, not in a new tab', async () => {
    serve();
    show([msg(1, 'bot:qa', 'Here it is: ![Chart](/api/v2/files/f1)')]);
    fireEvent.click(await screen.findByRole('img', { name: 'Chart' }));
    const dialog = await screen.findByRole('dialog', { name: 'Chart' });
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/f1');
  });
});

describe('A picture attached to a chat message @s218', () => {
  it('shows as a picture in the message, and opens full size from it, with Download', async () => {
    serve();
    const shot = { id: 'f2', name: 'shot.png', content_type: 'image/png' };
    show([{ ...msg(1, 'bot:qa', 'The new header'), refs: { attachments: [shot] } } as ChatMessage]);
    const pic = await screen.findByRole('button', { name: 'shot.png' });
    expect(pic.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/f2');
    fireEvent.click(pic);
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

  it('a long press on what an agent said opens Copy as a sheet', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve();
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
    serve();
    show([msg(1, ME, 'Send it **today**')]);
    const mine = (await screen.findByText('today')).closest('.msg')!;
    act(() => {
      fireEvent.contextMenu(mine);
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Send it **today**'));
  });

  it('on a computer a right click is left to the browser', async () => {
    serve();
    show([msg(2, 'bot:qa', 'The plan is ready')]);
    expect(fireEvent.contextMenu(await screen.findByText('The plan is ready'))).toBe(true);
    expect(screen.queryByRole('menu')).toBeNull();
  });

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
        : undefined,
    );
    show([fromSlack()], { role: 'owner' });
    fireEvent.click(await screen.findByRole('button', { name: 'Slack #launch' }));
    const dialog = await screen.findByRole('dialog', { name: 'Slack thread' });
    await waitFor(() => expect(dialog.textContent).toContain('Sam Rivera'));
    expect(dialog.textContent).toContain(long);
    expect(calls.some((c) => c.url.includes('/api/v2/messaging/bots/helper/slack-thread?channel=C1&thread_ts=1789800000.000100'))).toBe(true);
    expect(screen.getByRole('link', { name: 'Open in Slack' }).getAttribute('href')).toBe('https://example.slack.com/archives/C1/p1');
  });

  it('anyone else gets the link to the message in Slack', async () => {
    serve();
    show([fromSlack()]);
    const link = await screen.findByRole('link', { name: 'Slack #launch' });
    expect(link.getAttribute('href')).toBe('https://example.slack.com/archives/C1/p1');
    expect(screen.queryByRole('button', { name: 'Slack #launch' })).toBeNull();
  });

  it('a message carried from a linked Slack channel (its channel and time there, no link, no agent) still says so', async () => {
    serve();
    show([{ ...msg(4, 'human:ana', 'Shipping Friday'), refs: { slack: { channel: 'C1', ts: '1789800000.000300' } } }], { role: 'owner' });
    expect(await screen.findByText('Slack')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Slack' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Slack' })).toBeNull();
  });
});

describe('Who said it, when the message names no one', () => {
  const BOT = 'bot:01a1d25e-6716-71f9-8c8a-06486add5ed1';

  it('an agent the directory knows is named by it, never by its id', async () => {
    serve();
    const actors = [{ id: BOT, kind: 'agent', name: 'Legal Owner' }];
    queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
    show([msg(1, BOT, 'The letter is queued')]);
    expect(await screen.findByText('Legal Owner')).toBeTruthy();
    expect(screen.queryByText(/01a1d25e/)).toBeNull();
  });

  it('one it does not know reads "Agent"', async () => {
    serve();
    show([msg(1, BOT, 'The letter is queued')]);
    expect(await screen.findByText('Agent')).toBeTruthy();
    expect(screen.queryByText(/01a1d25e/)).toBeNull();
  });
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
    show([msg(2, 'bot:tico', 'Done')], { variant: 'tico' });
    await screen.findByText('Done');
    expect(screen.queryByRole('button', { name: 'Reply' })).toBeNull();
  });
});

describe('What a message holds besides words (thread-content.html)', () => {
  it('draws a bare web address as a chip naming the thing, opening in a new tab', async () => {
    serve();
    show([msg(1, 'bot:qa', 'Merged https://github.com/fernhill/shop/pull/412 today')]);
    const chip = await screen.findByRole('link', { name: 'shop #412' });
    expect(chip.className).toContain('lchip');
    expect(chip.getAttribute('href')).toBe('https://github.com/fernhill/shop/pull/412');
    expect(chip.getAttribute('target')).toBe('_blank');
  });

  it('keeps words someone chose as a link, and opens an app link beside this thread', async () => {
    serve();
    show([msg(1, 'bot:qa', 'Read [the brief](https://fernhill.example/brief) and [Fix the cart](#/task/t7)')]);
    expect((await screen.findByRole('link', { name: 'the brief' })).className).toBe('lnk');
    const task = screen.getByRole('link', { name: 'Fix the cart' });
    expect(task.className).toContain('lchip');
    expect(task.getAttribute('href')).toBe('/task/t7?chat=thread%3At1&via=thread');
  });

  it('names a page ref by its title, and shows the page small and live under the words', async () => {
    serve((url) => {
      if (url.endsWith('/info')) return { status: 200, body: { file: { id: 'file-0123456789abcdef', title: 'Spring catalog', name: 'catalog.html', mime: 'text/html', library_kind: 'page', size: 10 } } };
      if (url.includes('/versions/2')) return { status: 200, body: '<h1>Spring catalog</h1>' };
      return undefined;
    });
    show([msg(1, 'bot:qa', 'Spec: page:file-0123456789abcdef@2')]);
    const chip = await screen.findByRole('link', { name: 'Spring catalog' });
    expect(chip.getAttribute('href')).toBe('/api/v2/files/file-0123456789abcdef/versions/2');
    const card = await screen.findByRole('button', { name: 'Spring catalog' });
    const frame = await waitFor(() => {
      const f = card.closest('.pcard')?.querySelector('iframe');
      expect(f).toBeTruthy();
      return f!;
    });
    expect(frame.getAttribute('sandbox')).toBe('');
  });

  it('shows a page the reply links the way the Assistant writes it (#/file/<id>) as a page card, not a bare link', async () => {
    serve((url) => {
      if (url.endsWith('/info')) return { status: 200, body: { file: { id: 'file-0123456789abcdef', title: 'Weekly review', name: 'weekly.html', mime: 'text/html', library_kind: 'page', size: 10 } } };
      if (url.includes('/files/file-0123456789abcdef')) return { status: 200, body: '<h1>Weekly review</h1>' };
      return undefined;
    });
    show([msg(1, 'bot:assistant', 'Made it: [Weekly review](#/file/file-0123456789abcdef)')]);
    const card = await screen.findByRole('button', { name: 'Weekly review' });
    expect(card.closest('.pcard')).toBeTruthy();
  });

  it('shows a picture that does not load as its name', async () => {
    serve();
    show([msg(1, 'bot:qa', 'Shot', { refs: { attachments: [{ id: 'f3', name: 'gone.png', content_type: 'image/png' }] } })]);
    const img = (await screen.findByRole('button', { name: 'gone.png' })).querySelector('img')!;
    fireEvent.error(img);
    expect((await screen.findByRole('link', { name: 'gone.png' })).getAttribute('href')).toBe('/api/v2/files/f3');
  });

  it('a long message offers Read, which shows it full screen', async () => {
    serve();
    show([msg(1, 'bot:qa', `Report. ${'Beds sold well this week. '.repeat(80)}`)]);
    fireEvent.click(await screen.findByRole('button', { name: 'Read' }));
    const dialog = await screen.findByRole('dialog', { name: 'Qa' });
    expect(dialog.textContent).toContain('Beds sold well');
  });

  it('a table expands full screen; code copies', async () => {
    const writeText = vi.fn(async (_t: string) => {});
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    serve();
    show([msg(1, 'bot:qa', '| Bed | Sold |\n| --- | --- |\n| Cedar | 40 |\n\n```\nmake ship\n```')]);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('make ship');
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    const dialog = await screen.findByRole('dialog', { name: 'Table' });
    expect(within(dialog).getByText('Cedar')).toBeTruthy();
  });
});

describe('A question that shows a live browser', () => {
  it('has Open browser, which opens that account\'s browser in place', async () => {
    serve((url) => (url.includes('/browser') ? { status: 200, body: { ok: true } } : undefined));
    const asked = { ...msg(1, 'bot:finance', 'Sign in to Stripe for the Wells import.\n\nOpen the browser below, finish there, then reply Done.'), refs: { shows: ['browser:stripe/acct-9'], question: { id: 'q1', to: ME } } };
    show([asked as ChatMessage]);
    fireEvent.click(await screen.findByRole('button', { name: 'Open browser' }));
    expect(await screen.findByRole('dialog', { name: 'Sign in to Stripe for the Wells import.' })).toBeTruthy();
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('stripe') && c.url.includes('acct-9'))).toBe(true));
  });

  it('shows nothing once the question is answered, or when it is someone else\'s', async () => {
    serve();
    const said = (n: number, question?: { id: string; to: string }) => ({ ...msg(n, 'bot:finance', `Sign in ${n}`), refs: { shows: ['browser:stripe/acct-9'], ...(question && { question }) } }) as ChatMessage;
    show([said(1), said(2, { id: 'q2', to: ANA })]);
    await screen.findByText('Sign in 2');
    expect(screen.queryByRole('button', { name: 'Open browser' })).toBeNull();
  });

  it('shows nothing for a message that shows no browser', async () => {
    serve();
    show([{ ...msg(1, 'bot:qa', 'See the page'), refs: { shows: ['page:f1@2'] } } as ChatMessage]);
    await screen.findByText('See the page');
    expect(screen.queryByRole('button', { name: 'Open browser' })).toBeNull();
  });
});
