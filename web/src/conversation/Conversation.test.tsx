import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient, type Actor } from '@/data';
import { upsertMessage, type TimelineData } from '@/live';
import type * as Live from '@/live';
import { Composer } from './Composer';
import { Conversation } from './Conversation';
import { Message } from './Message';
import { showPageName } from '@/tico/pageName';
import type { Session } from './Session';
import type { ChatMessage } from './types';

/* The stream, stood in for: handlers register as they do in the app; `emit` applies one frame. A `messages` frame
   first does what live/patch.ts's built-in patcher does (upsert by id into the timeline), then the module's own. */
const handlers = vi.hoisted(() => new Map<string, Set<(batch: unknown[]) => void>>());
vi.mock('@/live', async (actual) => ({
  ...(await actual<typeof Live>()),
  useFollow: () => {},
  onTopic: (topic: string, h: (batch: unknown[]) => void) => {
    const set = handlers.get(topic) ?? new Set();
    handlers.set(topic, set.add(h));
    return () => set.delete(h);
  },
}));
const ran = vi.hoisted(() => vi.fn(async () => ({ ok: true })));
// The run module, not `@/actions`: the registry loads the conversation while `@/actions` itself is still loading.
vi.mock('@/actions/run', async (actual) => ({ ...(await actual<Record<string, unknown>>()), run: ran }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const ME = 'human:me';
const T = 't1';
const key = ['thread', T, 'timeline'];

function emit(topic: string, batch: Record<string, unknown>[]) {
  act(() => {
    if (topic === 'messages')
      for (const e of batch) {
        const data = queryClient.getQueryData<TimelineData>(key);
        if (data && e.message) queryClient.setQueryData(key, upsertMessage(data, T, e.message as ChatMessage));
      }
    for (const h of handlers.get(topic) ?? []) h(batch);
  });
}

// Noon today, a minute apart: the same day whenever the test runs.
const at = (min: number) => new Date(new Date().setHours(12, min, 0, 0)).toISOString();
const msg = (seq: number, sender: string, body: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: `m${seq}`, seq, thread_id: T, conversation_id: T, sender_id: sender, body, created_at: at(seq), ...extra,
});

function seed(messages: ChatMessage[]) {
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(key, { pages: [{ messages, next_cursor: null, read_marks: { [T]: 2 } }], pageParams: [null] });
}

type Reply = { status: number; body: unknown } | Promise<{ status: number; body: unknown }>;
let posts: { url: string; body: unknown }[] = [];
const sends = () => posts.filter((p) => p.url.endsWith('/messages'));
function serve(reply: (url: string) => Reply) {
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    if (init?.method === 'POST') posts.push({ url, body: typeof init.body === 'string' ? JSON.parse(init.body) : init.body });
    const r = await reply(url);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

const chat = (id: string) => <Conversation id={id} variant="side" title="Ana" actor="bot:qa" page={{ path: '/tasks/engineering', title: 'Engineering' }} />;
const show = () =>
  render(chat(T), { wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider> });

const type = (text: string) => {
  const box = screen.getByRole('textbox', { name: 'Message Ana' });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: 'Enter' });
};

/* jsdom lays nothing out. A stand-in: every box is 300 tall, every row 50, a scroller is as tall as the virtual
   list's spacer says, its place never passes its end, and scrolling tells its listeners a moment later, as a
   browser does. */
const ROW = 50;
const VIEW = 300;
const layout: Record<string, PropertyDescriptor> = {
  clientHeight: { get: () => VIEW },
  clientWidth: { get: () => 400 },
  offsetHeight: { get: () => ROW },
  scrollHeight: {
    get(this: HTMLElement) {
      return parseFloat((this.querySelector('.vl-space') as HTMLElement | null)?.style.height ?? '') || VIEW;
    },
  },
  scrollTop: {
    get(this: HTMLElement & { _top?: number }) {
      return Math.max(0, Math.min(this._top ?? 0, this.scrollHeight - VIEW));
    },
    set(this: HTMLElement & { _top?: number }, v: number) {
      this._top = v;
    },
  },
  scrollTo: {
    value(this: HTMLElement, o: ScrollToOptions) {
      this.scrollTop = o.top ?? 0;
      queueMicrotask(() => this.dispatchEvent(new Event('scroll')));
    },
  },
};
const saved = Object.keys(layout).map((k) => [k, Object.getOwnPropertyDescriptor(HTMLElement.prototype, k)] as const);

// The cache tells React at once (the app waits a tick), so each step's act() draws it.
beforeAll(() => {
  notifyManager.setScheduler((cb) => cb());
  for (const [k, d] of Object.entries(layout)) Object.defineProperty(HTMLElement.prototype, k, { configurable: true, ...d });
});
afterAll(() => {
  for (const [k, d] of saved) if (d) Object.defineProperty(HTMLElement.prototype, k, d);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
});
beforeEach(() => {
  posts = [];
  seed([msg(1, 'bot:qa', 'Morning'), msg(2, ME, 'Hi')]);
});
afterEach(() => {
  ran.mockClear();
  queryClient.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('Conversation', () => {
  it('shows a sent message at once, with the page it came from, and never twice when the stream echoes it', async () => {
    let answer!: (r: { status: number; body: unknown }) => void;
    serve(() => new Promise((res) => (answer = res)));
    show();
    type('Can you check the label?');
    expect(screen.getByText('Can you check the label?')).toBeTruthy();
    expect(sends()[0]).toEqual({ url: `/api/v2/threads/${T}/messages`, body: { text: 'Can you check the label?', refs: { page: { path: '/tasks/engineering', title: 'Engineering' } } } });

    const real = msg(3, ME, 'Can you check the label?');
    emit('messages', [{ id: real.id, conversation_id: T, message: real }]);
    expect(screen.getAllByText('Can you check the label?')).toHaveLength(1);

    await act(async () => answer({ status: 200, body: real }));
    expect(screen.getAllByText('Can you check the label?')).toHaveLength(1);
  });

  it('grows a streamed reply in place, then hands it to the posted message', () => {
    show();
    emit('runs', [{ run_id: 'r1', conversation_id: T, bot_id: 'qa', state: 'running' }]);
    const row = document.querySelector('.msg.is-live');
    expect(row?.textContent).toContain('Thinking');

    emit('runs', [{ run_id: 'r1', conversation_id: T, output: { kind: 'delta', payload: { text: 'Checking the' } } }]);
    emit('runs', [{ run_id: 'r1', conversation_id: T, output: { kind: 'delta', payload: { text: ' label service' } } }]);
    expect(document.querySelector('.msg.is-live')).toBe(row);
    expect(row?.textContent).toContain('Checking the label service');

    const reply = msg(3, 'bot:qa', 'Checking the label service again.', { refs: { turn_id: 'r1' } });
    emit('messages', [{ id: reply.id, conversation_id: T, message: reply }]);
    expect(document.querySelector('.msg.is-live')).toBeNull();
    expect(screen.getAllByText('Checking the label service again.')).toHaveLength(1);
  });

  it('keeps a failed send on screen, marked, and sends it again on Retry', async () => {
    let fail = true;
    serve(() => (fail ? { status: 500, body: { error: { code: 'boom', detail: 'Broke' } } } : { status: 200, body: msg(3, ME, 'Ship it') }));
    show();
    type('Ship it');
    expect(await screen.findByText('Not sent')).toBeTruthy();
    expect(screen.getByText('Ship it')).toBeTruthy();

    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('Not sent')).toBeNull());
    expect(screen.getAllByText('Ship it')).toHaveLength(1);
    expect(sends()).toHaveLength(2);
  });

  it('follows and reads new messages at the bottom, and waits for the reader to scroll back otherwise', async () => {
    const settle = () => act(async () => {});
    const reads = () => posts.filter((p) => p.url === `/api/v2/threads/${T}/read`).map((p) => p.body);
    serve(() => ({ status: 200, body: {} }));
    seed(Array.from({ length: 20 }, (_, i) => msg(i + 1, 'bot:qa', `Line ${i + 1}`)));
    show();
    await settle();
    const log = screen.getByRole('log', { name: 'Chat with Ana' });
    const bottom = () => log.scrollHeight - VIEW;
    expect(log.scrollTop).toBe(bottom());

    emit('messages', [{ id: 'm21', conversation_id: T, message: msg(21, 'bot:qa', 'One more thing') }]);
    await settle();
    expect(log.scrollTop).toBe(bottom());
    expect(reads()).toContainEqual({ seq: 21 });

    log.scrollTo({ top: 200 });
    await settle();
    emit('messages', [{ id: 'm22', conversation_id: T, message: msg(22, 'bot:qa', 'And another') }]);
    await settle();
    expect(log.scrollTop).toBe(200);
    expect(bottom()).toBeGreaterThan(200);
    expect(reads()).not.toContainEqual({ seq: 22 });

    log.scrollTo({ top: bottom() });
    await settle();
    expect(reads()).toContainEqual({ seq: 22 });
  });

  it('draws a "New" line before the first message from someone else past the read mark', () => {
    seed([msg(1, 'bot:qa', 'Old'), msg(2, ME, 'Mine'), msg(3, 'bot:qa', 'Fresh')]);
    show();
    const line = screen.getByText('New').parentElement;
    expect(line?.className).toBe('day is-new');
    expect(line?.closest('.vl-item')?.nextElementSibling?.textContent).toContain('Fresh');
  });

  it('uses each thread’s read mark when the timeline includes a sub-thread', async () => {
    const branch = { thread_id: 'child', conversation_id: 'child' };
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(key, {
      pages: [{
        messages: [
          msg(1, 'bot:qa', 'Already read', branch),
          msg(3, 'bot:qa', 'Unread reply', branch),
          msg(4, 'bot:qa', 'Read in the root'),
          msg(5, 'bot:qa', '', { ...branch, hidden: true }),
        ],
        next_cursor: null,
        read_marks: { [T]: 4, child: 1 },
      }],
      pageParams: [null],
    });
    show();
    const line = screen.getByText('New');
    expect(line.closest('.vl-item')?.nextElementSibling?.textContent).toContain('Unread reply');
    await waitFor(() => expect(posts.filter((p) => p.url.endsWith('/read'))).toEqual([
      { url: '/api/v2/threads/child/read', body: { seq: 3 } },
    ]));
  });

  it('opens the newest messages when switching to a cached conversation', async () => {
    const other = 't2';
    const messages = Array.from({ length: 20 }, (_, i) => msg(i + 1, 'bot:qa', `Line ${i + 1}`));
    seed(messages);
    queryClient.setQueryData(['thread', other, 'timeline'], {
      pages: [{ messages: messages.map((m) => ({ ...m, id: `other-${m.id}`, thread_id: other, conversation_id: other })), next_cursor: null }],
      pageParams: [null],
    });
    serve(() => ({ status: 200, body: {} }));
    const { rerender } = show();
    const log = () => screen.getByRole('log', { name: 'Chat with Ana' });
    await act(async () => {});
    log().scrollTo({ top: 100 });
    await act(async () => {});
    rerender(chat(other));
    await act(async () => {});
    expect(log().scrollTop).toBe(log().scrollHeight - VIEW);
  });
});

describe('Message', () => {
  it('draws what `card` gives in place of the text, mine or another\'s, and the text when it gives nothing', () => {
    seed([msg(1, 'bot:qa', 'Call', { refs: { call: { id: 'c1', card: true } } }), msg(2, ME, 'Call', { refs: { call: { id: 'c2', card: true } } }), msg(3, 'bot:qa', 'Plain words')]);
    const card = (m: ChatMessage) => {
      const c = m.refs?.call as { id?: string } | undefined;
      return c?.id ? <div role="group" aria-label={`Card ${c.id}`} /> : null;
    };
    render(
      <QueryClientProvider client={queryClient}>
        <Conversation id={T} variant="page" title="Ana" card={card} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('group', { name: 'Card c1' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Card c2' })).toBeTruthy();
    expect(screen.queryByText('Call')).toBeNull();
    expect(screen.getByText('Plain words')).toBeTruthy();
  });

  it('opens a file over the page on a plain click, and leaves a modified click to the browser', () => {
    const files = [{ id: 'f1', name: 'brief.pdf', content_type: 'application/pdf' }];
    render(
      <QueryClientProvider client={queryClient}>
        <Message m={msg(1, 'bot:qa', 'See this', { refs: { attachments: files } })} variant="page" me={ME} />
      </QueryClientProvider>,
    );
    const link = screen.getByRole('link', { name: /brief\.pdf/ });
    expect(link.getAttribute('href')).toBe('/api/v2/files/f1');
    expect(fireEvent.click(link)).toBe(false);
    expect(ran).toHaveBeenCalledExactlyOnceWith('files.view', { file: 'f1' }, { via: 'click' });
    expect(fireEvent.click(link, { metaKey: true })).toBe(true);
    expect(fireEvent.click(link, { button: 1 })).toBe(true);
    expect(ran).toHaveBeenCalledTimes(1);
  });
});

describe('Conversation loading @s157', () => {
  it('keeps an unsent draft editable when the chat fails to load and enables sending after retry', async () => {
    queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
    queryClient.removeQueries({ queryKey: key });
    let fail = true;
    serve((url) =>
      url.includes('/timeline') || url.includes('/messages')
        ? fail
          ? { status: 500, body: { detail: 'down' } }
          : { status: 200, body: { messages: [msg(1, 'bot:qa', 'Back again')], next_cursor: null, read_marks: {} } }
        : { status: 200, body: [] },
    );
    show();
    expect(await screen.findByRole('heading', { name: 'This chat did not load' })).toBeTruthy();
    const box = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Ana' });
    const send = screen.getByRole<HTMLButtonElement>('button', { name: 'Send' });
    fireEvent.change(box, { target: { value: 'My draft' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(box.value).toBe('My draft');
    expect(send.disabled).toBe(true);
    expect(sends()).toHaveLength(0);
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Back again')).toBeTruthy();
    expect(send.disabled).toBe(false);
  });
});

describe('Composer', () => {
  it('keeps a draft when the box is closed immediately, and clears it once sent', () => {
    const onSend = vi.fn();
    const box = () => screen.getByRole('textbox', { name: 'Message Tico' }) as HTMLTextAreaElement;
    const first = render(<Composer id={T} me={ME} to="Tico" onSend={onSend} />);
    fireEvent.change(box(), { target: { value: 'Half a thought' } });
    first.unmount();

    render(<Composer id={T} me={ME} to="Tico" onSend={onSend} />);
    expect(box().value).toBe('Half a thought');

    fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Half a thought', []);
    expect(box().value).toBe('');
  });
});

describe('the page chip', () => {
  it('names the page beside the chat once its head does, not the window title', async () => {
    seed([]);
    serve(() => ({ status: 200, body: {} }));
    render(<Conversation id={T} variant="side" title="Ana" actor="bot:qa" />, {
      wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
    });
    act(() => showPageName('Records production'));
    expect((await screen.findByText('Records production')).className).toBe('look-text');
    act(() => showPageName(''));
    await waitFor(() => expect(screen.queryByText('Records production')).toBeNull());
  });
});

describe('the head', () => {
  it('says under the name which chat this is: a task’s own, to the task, or the agent’s main chat', () => {
    serve(() => ({ status: 200, body: {} }));
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
    const head = (session: Session) => (
      <Conversation id={T} variant="side" title="Legal Owner" actor="bot:qa" session={session} />
    );
    const { rerender } = render(head({ task: 'k9', title: 'Randazzo: reply letter' }), { wrapper });
    expect(screen.getByRole('link', { name: 'Randazzo: reply letter' }).getAttribute('href')).toBe('/task/k9');
    expect(screen.queryByText('Main chat')).toBeNull();
    // The box still names only the agent.
    expect(screen.getByRole('textbox', { name: 'Message Legal Owner' })).toBeTruthy();

    rerender(head('main'));
    expect(screen.getByText('Main chat')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Randazzo: reply letter' })).toBeNull();
  });
});

describe('a chat head', () => {
  const scout: Actor = { id: 'bot:0190-sc', kind: 'agent', name: 'Scout', slug: 'scout' };
  const head = (actor: string | undefined, title: string) => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(['directory'], { actors: [scout], byId: { [scout.id]: scout } });
    render(<Conversation id={T} variant="side" title={title} actor={actor} page={null} />, {
      wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
    });
  };

  it("links an agent's name to its page, by slug", () => {
    head(scout.id, 'Scout');
    expect(screen.getByRole('link', { name: 'Scout' }).getAttribute('href')).toBe('/team/scout');
  });

  it("links a person's name to their page, by id", () => {
    head('human:p7', 'Kim Roe');
    expect(screen.getByRole('link', { name: 'Kim Roe' }).getAttribute('href')).toBe('/team/p7');
  });

  it('draws a plain name when the chat names no teammate', () => {
    head(undefined, 'Launch');
    expect(screen.getByText('Launch')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Launch' })).toBeNull();
  });
});
