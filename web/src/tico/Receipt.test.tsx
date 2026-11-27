import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { Message, type ChatMessage } from '@/conversation';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { okCard } from './OkCard';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* The receipt of an OK the person's Assistant gave on their word (backend/assistant_oks.py receipt): the Assistant's
   message names the task and stage back, and its card has Undo while that OK still stands, which takes it back as the
   person's own Undo does (POST /api/v2/answers/{id}/undo). Made-up rows; every answer is stubbed. */

const ME = 'human:ana';
const RECEIPT: ChatMessage = {
  id: 'm2',
  seq: 2,
  sender_id: 'bot:assistant',
  sender_name: 'Tico',
  body: "OK'd: Hide suggestion cards when typing, Approval",
  created_at: '2026-11-26T14:02:00Z',
  refs: { assistant_ok: { answer_id: 'a1', task_id: 't1', title: 'Hide suggestion cards when typing', stage: 'Approval' }, task: 't1' },
};

/** The task as GET /api/v2/tasks/t1 reads it: its Approval OK answered by `answer` (null: not answered now). */
const taskWith = (answer: string | null, may: string[]) => ({
  task: {
    id: 't1',
    number: 12,
    title: 'Hide suggestion cards when typing',
    labels: [],
    links: [],
    to_leave: [{ requirement_id: 'r1', kind: 'ok', met: answer !== null, may, answer: answer && { id: answer, by: ME, at: '2026-11-26T14:02:00Z', value: 'ok', note: null } }],
  },
  children: [],
  comments: [],
  events: [],
  messages: [],
  has_more: false,
});

let sent: string[] = [];
let task: unknown;
let undo: { status: number; body: unknown };

beforeEach(() => {
  sent = [];
  task = taskWith('a1', ['undo']);
  undo = { status: 200, body: { task: taskWith(null, ['ok', 'no']).task } };
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push(`${method} ${String(url)}`);
    const r = String(url).startsWith('/api/v2/answers/') ? undo : String(url).startsWith('/api/v2/tasks/t1') ? { status: 200, body: task } : { status: 200, body: {} };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

function show(m: ChatMessage) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Message m={m} variant="tico" me={ME} below={okCard} />
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
}

describe("the receipt of an OK the Assistant gave for the person", () => {
  it('names the task and stage, and Undo takes that answer back', async () => {
    show(RECEIPT);
    const undoButton = await screen.findByRole('button', { name: 'Undo' });
    expect(screen.getAllByText(/Hide suggestion cards when typing/).length).toBeGreaterThan(0);
    fireEvent.click(undoButton);
    await waitFor(() => expect(sent).toEqual(['POST /api/v2/answers/a1/undo']));
    // taken back: the OK no longer stands, so the card offers no Undo
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull());
  });

  it('offers no Undo once that OK no longer stands (the stage moved on, or it was taken back)', async () => {
    task = taskWith(null, ['ok', 'no']);
    show(RECEIPT);
    expect(await screen.findByText('Approval · via assistant')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('says why when the server refuses the Undo', async () => {
    undo = { status: 409, body: { error: { code: 'task_changed', detail: 'The stage moved on' } } };
    show(RECEIPT);
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    expect(await screen.findByText(/The stage moved on/)).toBeTruthy();
  });

  it('a message without the receipt draws no receipt card', async () => {
    show({ ...RECEIPT, refs: { task: 't1' } });
    await screen.findByText(/OK'd: Hide suggestion cards/);
    expect(screen.queryByText('Approval · via assistant')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });
});
