import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '@/conversation';
import { queryClient } from '@/data';
import { askedLine, ChatQuestions, questionsIn } from './ChatQuestions';

/* A ❓ an agent put to the person in a direct chat, pinned over the chat's message box as the Question card: read from
   the chat's own messages (`refs.question`, with the answers it offers), answered by a post in that chat naming the ❓
   (`ask`). Made-up rows only. */

const ME = 'human:me';
const T = 'th-1';
const key = ['thread', T, 'timeline'];
const sent: { url: string; body: Record<string, unknown> }[] = [];
const reads: string[] = [];
type Reply = { status?: number; body: unknown };
let reply: () => Reply = () => ({ body: { id: 'm9' } });

const asked = (id: string, extra: Partial<ChatMessage> = {}, question: Record<string, unknown> = {}): ChatMessage => ({
  id,
  seq: 1,
  thread_id: T,
  conversation_id: T,
  sender_id: 'bot:gm',
  body: 'Switch the HeyGen check to the new address?\nIt failed twice today.',
  created_at: '2026-11-23T12:00:00Z',
  refs: { question: { id: `q-${id}`, to: ME, choices: ['Yes', 'Not now'], ...question } },
  ...extra,
});

const seed = (messages: ChatMessage[]) => queryClient.setQueryData(key, { pages: [{ messages, next_cursor: null }], pageParams: [null] });

beforeEach(() => {
  sent.length = 0;
  reads.length = 0;
  reply = () => ({ body: { id: 'm9' } });
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if ((init?.method ?? 'GET') === 'GET') reads.push(String(url));
    else sent.push({ url: String(url), body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown> });
    // a read of the chat again answers what it holds now
    const r: Reply = String(url).includes('/timeline') ? { body: queryClient.getQueryData<{ pages: unknown[] }>(key)?.pages[0] ?? { messages: [] } } : reply();
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

const show = (more: { canPost?: boolean } = {}) =>
  render(
    <QueryClientProvider client={queryClient}>
      <ChatQuestions thread={T} agent="bot:gm" {...more} />
    </QueryClientProvider>,
  );
const card = () => screen.getByRole('region', { name: 'Question' });

describe('ChatQuestions', () => {
  it('pins a question to me with the answers it offers, and a tap answers it in this chat', async () => {
    seed([asked('m1')]);
    show();
    expect(within(card()).getByText('Switch the HeyGen check to the new address?')).toBeTruthy();
    expect(within(card()).getAllByRole('button').map((b) => b.textContent)).toEqual(['Not now', 'Yes']);
    fireEvent.click(within(card()).getByRole('button', { name: 'Yes' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Question' })).toBeNull());
    expect(sent).toEqual([{ url: `/api/v2/threads/${T}/messages`, body: { text: 'Yes', ask: 'qmark:q-m1' } }]);
  });

  it('says so when it was answered already, and reads the chat again so the answered card goes', async () => {
    reply = () => ({ status: 409, body: { error: { code: 'answered', detail: 'Already answered' } } });
    seed([asked('m1')]);
    show();
    fireEvent.click(within(card()).getByRole('button', { name: 'Yes' }));
    expect(await within(card()).findByRole('alert')).toBeTruthy();
    expect(card().textContent).toContain('Already answered');
    await waitFor(() => expect(reads.some((u) => u.startsWith(`/api/v2/threads/${T}/timeline`))).toBe(true));
  });

  it("pins nothing for a question to someone else, another agent's, one in a sub-thread, a deleted one or a plain message", () => {
    seed([
      asked('m1', {}, { to: 'human:ben' }),
      asked('m2', { deleted: true }),
      { id: 'm3', seq: 3, thread_id: T, sender_id: 'bot:gm', body: 'Done.', refs: {} },
      asked('m4', { sender_id: 'bot:qa' }),
      asked('m5', { thread_id: 'th-sub' }),
    ]);
    const { container } = show();
    expect(container.innerHTML).toBe('');
  });

  it('shows a free-text question with the reply box alone, and the chat reads again after the answer', async () => {
    seed([asked('m1', {}, { choices: undefined })]);
    show();
    expect(within(card()).queryAllByRole('button')).toEqual([]);
    fireEvent.change(within(card()).getByRole('textbox', { name: 'Reply' }), { target: { value: 'Yes, switch it.' } });
    fireEvent.click(within(card()).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent).toEqual([{ url: `/api/v2/threads/${T}/messages`, body: { text: 'Yes, switch it.', ask: 'qmark:q-m1' } }]));
    await waitFor(() => expect(reads.some((u) => u.startsWith(`/api/v2/threads/${T}/timeline`))).toBe(true));
  });

  it('shows no card where the viewer may not post', () => {
    seed([asked('m1')]);
    const { container } = show({ canPost: false });
    expect(container.innerHTML).toBe('');
  });

  it("titles it with the question's line as the server keeps it, a Warn rule's line left out", () => {
    const warned = "Warning: Ops's message may break the rule 'Plain words'.\n\n  Ship   it Friday?\nWhy: QA passed.";
    expect(askedLine(warned)).toBe('Ship it Friday?');
    expect(askedLine('\n\nShip it Friday?')).toBe('Ship it Friday?');
    const long = askedLine('word '.repeat(60));
    expect([long.length, long.endsWith('…')]).toEqual([160, true]);
  });

  it('goes when the question clears on its message (answered elsewhere, or taken back)', async () => {
    seed([asked('m1')]);
    show();
    expect(card()).toBeTruthy();
    act(() => seed([{ ...asked('m1'), refs: {} }]));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Question' })).toBeNull());
  });

  it('lists several oldest first', () => {
    const later = asked('m2', { created_at: '2026-11-23T13:00:00Z' });
    expect(questionsIn([later, asked('m1')], ME, 'bot:gm', T).map((x) => x.m.id)).toEqual(['m1', 'm2']);
    expect(questionsIn([asked('m1')], undefined, 'bot:gm', T)).toEqual([]);
  });
});
