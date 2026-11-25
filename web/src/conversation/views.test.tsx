import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type { TimelineView } from '@/live';
import { dispatch, installPatchers } from '@/live/patch';
import { Conversation } from './Conversation';
import { sendMessage, type TaskSend } from './queries';
import type { ChatMessage } from './types';

/* A task's thread seen through its views (comments, one agent's session): the real stream patchers, a stand-in server. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const ME = 'human:me';
const T = 'th1';
const at = (min: number) => new Date(new Date().setHours(12, min, 0, 0)).toISOString();
const msg = (seq: number, sender: string, body: string, extra: Partial<ChatMessage> & { session_agent_id?: string | null } = {}): ChatMessage =>
  ({ id: `m${seq}`, seq, thread_id: T, conversation_id: T, sender_id: sender, body, created_at: at(seq), ...extra }) as ChatMessage;

/** What the stand-in server answers: the timeline per view (by its address), who a Reply reaches, a posted comment. */
let pages: { comments: ChatMessage[]; session: ChatMessage[] };
let reach: { actor: string }[];
/** The thread's read mark the timeline reads with. */
let mark: number;
let calls: { url: string; method: string; body: unknown }[];
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

function serve() {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    if (url.includes('/timeline')) {
      const messages = url.includes('comments=true') ? pages.comments : url.includes('session=') ? pages.session : [...pages.comments, ...pages.session];
      return json({ messages, next_cursor: null, read_marks: { [T]: mark } });
    }
    if (url.includes('/reach')) return json({ thread_id: T, reaches: reach, outside: [] });
    if (url.includes('/comments')) {
      const body = JSON.parse(String(init?.body));
      return json({ comment: msg(90, ME, body.text, { in_reply_to: body.in_reply_to ?? null }) });
    }
    return json({});
  });
}

const actors = [
  { id: 'bot:qa', kind: 'agent', name: 'QA agent' },
  { id: 'bot:build', kind: 'agent', name: 'Build agent' },
  { id: 'human:ana', kind: 'person', name: 'Ana Lee' },
];

function show(view: TimelineView, task: TaskSend = { id: 't1' }) {
  return render(<Conversation id={T} variant="task" title="QA agent" view={view} task={task} {...('comments' in view && { compose: 'Add a comment' })} />, {
    wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
  });
}

const frame = (m: ChatMessage) => act(() => dispatch('messages', [{ id: m.id, conversation_id: T, message: m }]));
const timelines = () => calls.filter((c) => c.url.includes('/timeline'));

/* jsdom lays nothing out: every box is 1000 tall and every row 50, so the whole chat is drawn. */
const layout: Record<string, PropertyDescriptor> = {
  clientHeight: { get: () => 1000 },
  clientWidth: { get: () => 400 },
  offsetHeight: { get: () => 50 },
  scrollHeight: {
    get(this: HTMLElement) {
      return parseFloat((this.querySelector('.vl-space') as HTMLElement | null)?.style.height ?? '') || 1000;
    },
  },
  scrollTop: {
    get(this: HTMLElement & { _top?: number }) {
      return Math.max(0, Math.min(this._top ?? 0, this.scrollHeight - 1000));
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

beforeAll(() => {
  notifyManager.setScheduler((cb) => cb());
  for (const [k, d] of Object.entries(layout)) Object.defineProperty(HTMLElement.prototype, k, { configurable: true, ...d });
  installPatchers();
});
afterAll(() => {
  for (const [k, d] of saved) if (d) Object.defineProperty(HTMLElement.prototype, k, d);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
});
beforeEach(() => {
  calls = [];
  reach = [{ actor: 'bot:qa' }];
  mark = 1;
  pages = {
    comments: [
      msg(1, 'human:ana', 'Ship it Friday?'),
      msg(2, 'keeper', 'Moved to Review'),
      msg(4, 'bot:qa', 'Checked, it passes', { in_reply_to: 'm1', session_agent_id: 'bot:qa' }),
      msg(5, 'human:ana', 'Thanks', { in_reply_to: 'm4' }),
      msg(6, 'bot:build', 'Build is green'),
    ],
    session: [msg(3, 'bot:qa', 'Running the checks', { session_agent_id: 'bot:qa' })],
  };
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  serve();
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("a task's comments", () => {
  it('reads only the comments, and puts each reply under its comment, a reply to a reply under the same one', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    expect(timelines()[0]?.url).toContain('comments=true');
    expect(screen.queryByText('Running the checks')).toBeNull();
    const order = [...document.querySelectorAll('[data-target^="message:"]')].map((e) => e.getAttribute('data-target'));
    // m4 answers m1 and m5 answers m4: both under m1, ahead of m2 (a Tico line written between them), as replies
    expect(order).toEqual(['message:m1', 'message:m4', 'message:m5', 'message:m2', 'message:m6']);
    const under = [...document.querySelectorAll('.msg-under [data-target]')].map((e) => e.getAttribute('data-target'));
    expect(under).toEqual(['message:m4', 'message:m5']);
  });

  it('draws the New line once, before the first unread message as the list reads: a reply under an older comment', async () => {
    // read up to m4: unread are m5 (a reply under m1, drawn before m2) and m6
    mark = 4;
    show({ comments: true });
    await screen.findByText('Thanks');
    const order = [...document.querySelectorAll('[data-target^="message:"], .day.is-new')].map((e) => e.getAttribute('data-target') ?? 'New');
    expect(order).toEqual(['message:m1', 'message:m4', 'New', 'message:m5', 'message:m2', 'message:m6']);
  });

  it('has Reply on a person’s and an agent’s comment, never on a Tico line', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    const replyOn = (id: string) => !!document.querySelector(`[data-target="message:${id}"] button[aria-label="Reply"]`);
    expect(replyOn('m1')).toBe(true);
    expect(replyOn('m4')).toBe(true);
    expect(replyOn('m2')).toBe(false);
  });

  it('replies in place under the comment with the chat’s box, says who it reaches, and sends in_reply_to as a task comment', async () => {
    reach = [{ actor: 'bot:build' }];
    show({ comments: true });
    await screen.findByText('Build is green');
    fireEvent.click(document.querySelector('[data-target="message:m6"] button[aria-label="Reply"]')!);
    const box = await screen.findByRole('group', { name: 'Reply to Build agent' });
    // its words once the reach is read, not only the line that will hold them
    await waitFor(() => expect(within(box).getByRole('status').textContent).toBe('Reaches Build agent'));
    expect(calls.some((c) => c.url === `/api/v2/threads/${T}/reach?in_reply_to=m6`)).toBe(true);
    const text = within(box).getByRole('textbox', { name: 'Reply to Build agent' });
    fireEvent.change(text, { target: { value: 'Which run?' } });
    fireEvent.keyDown(text, { key: 'Enter' });
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toEqual({ url: '/api/v2/tasks/t1/comments', method: 'POST', body: { text: 'Which run?', in_reply_to: 'm6' } }));
    // sent: the box goes, the reply shows under the comment it answers
    expect(screen.queryByRole('group', { name: 'Reply to Build agent' })).toBeNull();
    await waitFor(() => expect(document.querySelector('.msg-under [data-target="message:m90"]')).toBeTruthy());
  });

  it('says "Reaches nobody" before sending a Reply that reaches no one (an agent that cannot be reached)', async () => {
    reach = [];
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    fireEvent.click(document.querySelector('[data-target="message:m4"] button[aria-label="Reply"]')!);
    const box = await screen.findByRole('group', { name: 'Reply to QA agent' });
    expect(await within(box).findByText('Reaches nobody')).toBeTruthy();
    // a reply to a reply opens under the same comment: after the last reply there
    const order = [...document.querySelectorAll('[data-target^="message:"], .reply-box')].map((e) => e.getAttribute('data-target') ?? 'box');
    expect(order).toEqual(['message:m1', 'message:m4', 'message:m5', 'box', 'message:m2', 'message:m6']);
  });

  it('takes a comment from the stream, drops a session message, and reads again for an answer it cannot place', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    frame(msg(7, 'human:ana', 'Also the copy'));
    expect(screen.getByText('Also the copy')).toBeTruthy();
    frame(msg(8, 'bot:qa', 'Looking at the logs', { session_agent_id: 'bot:qa' }));
    expect(screen.queryByText('Looking at the logs')).toBeNull();
    // an agent's answer to a comment on screen: shown under it
    frame(msg(9, 'bot:build', 'Copy fixed', { session_agent_id: 'bot:build', in_reply_to: 'm7' }));
    expect(document.querySelector('.msg-under [data-target="message:m9"]')).toBeTruthy();
    // its answer to a message this view does not hold: only the server can say, so the view reads again
    const before = timelines().length;
    frame(msg(10, 'bot:build', 'Done there', { session_agent_id: 'bot:build', in_reply_to: 'gone' }));
    expect(screen.queryByText('Done there')).toBeNull();
    await waitFor(() => expect(timelines().length).toBe(before + 1));
    expect(timelines().at(-1)?.url).toContain('comments=true');
  });

  it('keeps Reply a tap away on a touch screen, where other message actions wait for a long press, never over the words', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    expect(document.querySelector('.chat-body.is-comments')).toBeTruthy();
    // Read with node's fs (tests load no CSS), and web's types leave node out.
    const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
    const here = import.meta.url;
    const css = fs.readFileSync(new URL('./conversation.css', here), 'utf8');
    const touch = /@media \(hover:none\)\{\s*\.chat-body\.is-comments \.msg-acts\{display:flex;opacity:1\}([^@]*)\}/.exec(css)?.[1] ?? '';
    // a comment keeps room above its author row for the 44px target, which ends level with that row
    expect(touch).toContain('.chat-body.is-comments .msg:not(.is-mine):has(> .msg-acts){padding-top:var(--s-6)}');
    expect(touch).toContain('.chat-body.is-comments .msg:not(.is-mine) > .msg-acts{top:0}');
  });

  it('gives each comment its author row, one after another from the same author too (where a phone’s Reply sits)', async () => {
    pages.comments = [...pages.comments, msg(7, 'bot:build', 'Lint is green too')];
    show({ comments: true });
    const next = (await screen.findByText('Lint is green too')).closest('.msg')!;
    expect(next.classList.contains('is-cont')).toBe(false);
    expect(within(next as HTMLElement).getByText('Build agent')).toBeTruthy();
  });

  it('drops a Reply with Escape', async () => {
    show({ comments: true });
    await screen.findByText('Build is green');
    fireEvent.click(document.querySelector('[data-target="message:m6"] button[aria-label="Reply"]')!);
    const box = await screen.findByRole('group', { name: 'Reply to Build agent' });
    // only the Reply: the phone card around the chat sees it taken, the frame (on the document) never sees it
    const frame = vi.fn();
    document.addEventListener('keydown', frame);
    try {
      expect(fireEvent.keyDown(within(box).getByRole('textbox'), { key: 'Escape' })).toBe(false);
      expect(frame).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('keydown', frame);
    }
    expect(screen.queryByRole('group', { name: 'Reply to Build agent' })).toBeNull();
  });

  it('takes files with a plain comment', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    expect(within(document.querySelector('.chat-foot')!).queryByRole('button', { name: 'Attach a file' })).toBeTruthy();
  });

  it('sends no files with a message to an agent, whatever the box held (the comment route carries no upload yet)', async () => {
    await sendMessage({ id: T, variant: 'task', text: 'To you', files: [new File(['x'], 'ledger.csv')], me: ME, task: { id: 't1', to: 'bot:qa' }, view: { session: 'bot:qa' } });
    expect(calls.filter((c) => c.method === 'POST')).toEqual([{ url: '/api/v2/tasks/t1/comments', method: 'POST', body: { text: 'To you', to: 'bot:qa' } }]);
  });

  it('shows a deleted reply as a reload would: no longer under what it answered, its own replies under it', async () => {
    const quote = { id: 'm4', author: 'bot:qa', author_name: 'QA agent', excerpt: 'Checked, it passes' };
    pages.comments = pages.comments.map((m) =>
      m.id === 'm4'
        ? { ...m, recipient_id: 'human:ana', refs: { attachments: [{ id: 'f1', name: 'report.pdf' }] }, reply_to: { id: 'm1', excerpt: 'Ship it Friday?' } }
        : m.id === 'm5'
          ? { ...m, reply_to: quote }
          : m,
    );
    show({ comments: true });
    await screen.findByText('Thanks');
    const order = () =>
      [...document.querySelectorAll('[data-target^="message:"]')].map((e) => `${e.closest('.msg-under') ? '  ' : ''}${e.getAttribute('data-target')}`);
    act(() => dispatch('messages', [{ id: 'm4', conversation_id: T, deleted: true, revision: 2 }]));
    const live = order();
    // m4 answers nothing now: it takes its own place in time, m5 still under it
    expect(live).toEqual(['message:m1', 'message:m2', 'message:m4', '  message:m5', 'message:m6']);
    // what it was, as the rows hold it: no words, files, recipient or quote, and the reply under it quotes nothing
    const held = () => {
      const rows = queryClient.getQueryData<{ pages: { messages: ChatMessage[] }[] }>(['thread', T, 'timeline', { comments: true }])!.pages[0]!.messages;
      const of = (id: string) => rows.find((m) => m.id === id)!;
      const { body, refs, recipient_id, in_reply_to, reply_to } = of('m4') as ChatMessage & { recipient_id?: string | null };
      return { m4: { body, refs, recipient_id, in_reply_to, reply_to: reply_to ?? null }, m5: of('m5').reply_to };
    };
    const liveRows = held();
    expect(liveRows).toEqual({ m4: { body: null, refs: {}, recipient_id: null, in_reply_to: null, reply_to: null }, m5: { ...quote, excerpt: '' } });
    // read again, as after a reconnect: the server's tombstone (threads._message_out), and a quote of it that says nothing
    pages.comments = pages.comments.map((m) =>
      m.id === 'm4'
        ? { ...m, deleted: true, body: null, refs: {}, recipient_id: null, in_reply_to: null, reply_to: null, revision: 2 }
        : m.id === 'm5'
          ? { ...m, reply_to: { ...quote, excerpt: '' } }
          : m,
    );
    const before = timelines().length;
    await act(async () => queryClient.invalidateQueries());
    await waitFor(() => expect(timelines().length).toBe(before + 1));
    expect(order()).toEqual(live);
    expect(held()).toEqual(liveRows);
  });

  it('reads the comments again on a reconnect, still only the comments', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    pages.comments = [...pages.comments, msg(20, 'human:ana', 'Missed while away')];
    pages.session = [...pages.session, msg(21, 'bot:qa', 'Session words while away', { session_agent_id: 'bot:qa' })];
    // the stream could not replay: everything is read again (live/stream.ts `reset`)
    act(() => {
      void queryClient.invalidateQueries(undefined, { cancelRefetch: false });
      dispatch('reset', [{ seq: 1 }]);
    });
    expect(await screen.findByText('Missed while away')).toBeTruthy();
    expect(timelines().at(-1)?.url).toContain('comments=true');
    expect(screen.queryByText('Session words while away')).toBeNull();
  });

  it('keeps its own draft apart from each agent’s session', async () => {
    const view = show({ comments: true });
    fireEvent.change(await screen.findByRole('textbox', { name: 'Add a comment' }), { target: { value: 'For everyone' } });
    view.unmount();
    show({ session: 'bot:qa' }, { id: 't1', to: 'bot:qa' });
    const box = (await screen.findByRole('textbox', { name: 'Message QA agent' })) as HTMLTextAreaElement;
    expect(box.value).toBe('');
  });

  it('never marks the thread read by itself', async () => {
    show({ comments: true });
    await screen.findByText('Ship it Friday?');
    frame(msg(7, 'human:ana', 'Also the copy'));
    expect(calls.some((c) => c.url.endsWith('/read'))).toBe(false);
  });
});

describe("an agent's session", () => {
  it('reads only that session, writes to that agent, and keeps its own live messages and edits', async () => {
    show({ session: 'bot:qa' }, { id: 't1', to: 'bot:qa' });
    await screen.findByText('Running the checks');
    expect(timelines()[0]?.url).toContain('session=bot%3Aqa');
    expect(screen.queryByText('Ship it Friday?')).toBeNull();
    frame(msg(11, 'bot:build', 'Build log', { session_agent_id: 'bot:build' }));
    frame(msg(12, 'human:ana', 'Plain note'));
    expect(screen.queryByText('Build log')).toBeNull();
    expect(screen.queryByText('Plain note')).toBeNull();
    frame(msg(3, 'bot:qa', 'Running the checks again', { session_agent_id: 'bot:qa', revision: 2 }));
    expect(screen.getByText('Running the checks again')).toBeTruthy();
    // an @mention of it is in its session only by the server's word: the view reads again
    const before = timelines().length;
    frame(msg(13, 'human:ana', '@qa look at this'));
    await waitFor(() => expect(timelines().length).toBe(before + 1));

    const text = screen.getByRole('textbox', { name: 'Message QA agent' });
    fireEvent.change(text, { target: { value: 'Run them on staging' } });
    fireEvent.keyDown(text, { key: 'Enter' });
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toEqual({ url: '/api/v2/tasks/t1/comments', method: 'POST', body: { text: 'Run them on staging', to: 'bot:qa' } }));
  });

  it('reads again for another agent’s message that may @mention its agent, and drops one that cannot', async () => {
    show({ session: 'bot:qa' });
    await screen.findByText('Running the checks');
    const before = timelines().length;
    frame(msg(14, 'bot:build', 'Build log only', { session_agent_id: 'bot:build' }));
    expect(screen.queryByText('Build log only')).toBeNull();
    expect(timelines().length).toBe(before);
    pages.session = [...pages.session, msg(15, 'bot:build', '@qa the build is ready for you', { session_agent_id: 'bot:build' })];
    frame(msg(15, 'bot:build', '@qa the build is ready for you', { session_agent_id: 'bot:build' }));
    expect(await screen.findByText('@qa the build is ready for you')).toBeTruthy();
    expect(timelines().length).toBe(before + 1);
  });

  it('has no Reply (a reply lives in the comments) and takes no files (a file would not reach the agent)', async () => {
    show({ session: 'bot:qa' }, { id: 't1', to: 'bot:qa' });
    await screen.findByText('Running the checks');
    expect(document.querySelector('button[aria-label="Reply"]')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Attach a file' })).toBeNull();
  });

  it('shows only its own agent’s reply being written', async () => {
    show({ session: 'bot:qa' });
    await screen.findByText('Running the checks');
    const working = (bot: string, run: string) => ({ actor: bot, name: bot, state: 'working', detail: 'Thinking', since: '', run_id: run, fix: null, message_id: 'm1' });
    act(() => {
      dispatch('runs', [{ run_id: 'r1', conversation_id: T, bot_id: 'build', state: 'running' }]);
      dispatch('threads', [{ kind: 'bot_status', thread_id: T, bots: [working('bot:build', 'r1')] }]);
    });
    expect(document.querySelector('.msg.is-live')).toBeNull();
    act(() => {
      dispatch('runs', [{ run_id: 'r2', conversation_id: T, bot_id: 'qa', state: 'running' }]);
      dispatch('threads', [{ kind: 'bot_status', thread_id: T, bots: [working('bot:build', 'r1'), working('bot:qa', 'r2')] }]);
    });
    expect(document.querySelector('.msg.is-live')).toBeTruthy();
  });

  it('shows the box but sends nothing when the person may not message the agent', async () => {
    render(<Conversation id={T} variant="task" title="QA agent" view={{ session: 'bot:qa' }} task={{ id: 't1', to: 'bot:qa' }} canSend={false} />, {
      wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
    });
    const text = await screen.findByRole('textbox', { name: 'Message QA agent' });
    fireEvent.change(text, { target: { value: 'Hello' } });
    fireEvent.keyDown(text, { key: 'Enter' });
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });
});
