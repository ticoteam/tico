import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import type { TaskQuestion } from '@/api/schema';
import type { TaskData } from './queries';
import { TaskChat, TaskPage } from './TaskPage';

/* A question is a task for a person (backend/questions.py): its page opens on the card, who asks on what, and an answer
   given to the round shown. Made-up rows only; every answer is stubbed. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
// The conversation is tested in its own folder: here a stand-in with its foot and, while it may post, its message box.
vi.mock('@/conversation', async (orig) => ({
  ...(await orig<object>()),
  Conversation: ({ foot, canPost = true }: { foot?: ReactNode; canPost?: boolean }) => (
    <section aria-label="Chat">
      {foot}
      {canPost && <textarea aria-label="Message" />}
    </section>
  ),
}));

type Reply = { status?: number; body: unknown };
const sent: { url: string; body: Record<string, unknown> }[] = [];
const reads: string[] = [];
let reply: (url: string, method: string) => Reply | null = () => null;

const question = (more: Partial<TaskQuestion & { detail: string | null }> = {}): TaskQuestion & { detail: string | null } => ({
  parent: { id: 'p9', number: 35, title: 'Credentials for agents' },
  asker: 'bot:gm',
  person: 'human:me',
  round: 2,
  link_url: 'https://acme.example/settings/keys',
  state: 'open',
  ended_reason: null,
  answered_at: null,
  answered_by: null,
  answer_message_id: null,
  answer_text: null,
  detail: 'One shared key is simplest; separate keys let us cut one off.',
  ...more,
});

const task = (q: TaskQuestion, more: Partial<TaskData> = {}): TaskData =>
  ({
    id: 't1',
    number: 40,
    version: 1,
    title: 'Share an AWS key with 35 agents?',
    status: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'General' },
    stage_id: 's1',
    stage: { id: 's1', name: 'To do' },
    owner_id: 'human:me',
    requester_id: 'bot:gm',
    conversation_id: null,
    labels: [],
    created_at: '2026-11-19T10:00:00Z',
    documents: [],
    to_leave: [],
    links: [],
    question: q,
    ...more,
  }) as unknown as TaskData;

let shown = task(question());

function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2340') || url.endsWith('/api/v2/tasks/t1')) return { body: { task: shown, runs: [], children: [], can_comment: true, mover: true } };
  if (url.endsWith('/files?all=1')) return { body: { files: [] } };
  if (url.includes('/needs-you'))
    return {
      body: {
        actor: 'human:me',
        items: [
          { kind: 'question_task', id: 't1', task_id: 't1', number: 40, title: 'Share an AWS key with 35 agents?', asker: 'bot:gm', since: '2026-11-19T10:00:00Z', link_url: null, round: 2, owner_id: 'human:me', line: '' },
          { kind: 'ask', id: 'a9', task: { id: 't9', number: 13, title: 'Release notes', pipeline_id: 'p1', stage_id: 's1', owner_id: 'bot:gm' }, since: '2026-11-19T09:00:00Z', line: '' },
        ],
      },
    };
  if (url.includes('/api/v2/pipelines')) return { body: { pipelines: [] } };
  return { body: {} };
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  sent.length = 0;
  reads.length = 0;
  shown = task(question());
  reply = () => null;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [
    { id: 'bot:gm', kind: 'agent', name: 'General Manager' },
    { id: 'human:dana', kind: 'person', name: 'Dana Ruiz' },
  ];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') reads.push(String(url));
    else sent.push({ url: String(url), body: typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {} });
    const r = reply(String(url), method) ?? base(String(url));
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        <Toasts />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const card = () => screen.findByRole('region', { name: 'Question' });
const answerBox = () => screen.findByRole('region', { name: 'Answer the question' });
const answerWith = (box: HTMLElement, text: string) => {
  fireEvent.change(within(box).getByRole('textbox', { name: 'Your answer' }), { target: { value: text } });
  fireEvent.click(within(box).getByRole('button', { name: 'Answer' }));
};

describe('the question card', () => {
  it('shows who asks on what, the detail and the link, and answers to the round shown', async () => {
    reply = (url, method) => (method === 'POST' && url.endsWith('/api/v2/tasks/t1/question/answer') ? { body: {} } : null);
    show(<TaskPage task="40" pipeline="p1" />, '/tasks/p1/40');
    const box = await card();
    expect(box.textContent).toContain('From General Manager · on Credentials for agents (#35)');
    expect(within(box).getByRole('link', { name: /Credentials for agents/ }).getAttribute('href')).toBe('/task/35');
    expect(box.textContent).toContain('One shared key is simplest');
    const input = await answerBox();
    expect(within(input).getByRole('link', { name: 'Open ↗' }).getAttribute('href')).toBe('https://acme.example/settings/keys');
    expect(within(input).getByRole('button', { name: 'Answer' })).toHaveProperty('disabled', true);
    answerWith(input, '  Keep apart  ');
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/tasks/t1/question/answer', body: { text: 'Keep apart', round: 2 } }]));
  });

  it('once answered, says who answered and what, with no box', async () => {
    shown = task(question({ state: 'answered', ended_reason: 'answered', answered_by: 'human:dana', answered_at: '2026-11-19T11:00:00Z', answer_message_id: 'm5', answer_text: 'Keep apart.' }));
    show(<TaskPage task="40" pipeline="p1" />, '/tasks/p1/40');
    const box = await card();
    expect(await within(box).findByText('Keep apart.')).toBeTruthy();
    expect(box.textContent).toContain('Answered by Dana Ruiz');
    expect(screen.queryByRole('region', { name: 'Answer the question' })).toBeNull();
  });

  it('once withdrawn, says why, with no box', async () => {
    shown = task(question({ state: 'withdrawn', ended_reason: 'parent_ended' }));
    show(<TaskPage task="40" pipeline="p1" />, '/tasks/p1/40');
    const box = await card();
    expect(box.textContent).toContain('Withdrawn: its task ended');
    expect(screen.queryByRole('region', { name: 'Answer the question' })).toBeNull();
  });

  it('a question asked again since it was shown is read again and says Look again; the next press answers the new round', async () => {
    let refused = false;
    reply = (url, method) => {
      if (method !== 'POST' || !url.endsWith('/api/v2/tasks/t1/question/answer')) return null;
      if (refused) return { body: {} };
      refused = true;
      shown = task(question({ round: 3 }));
      return { status: 409, body: { error: { code: 'conflict', detail: 'Look again' } } };
    };
    show(<TaskPage task="40" pipeline="p1" />, '/tasks/p1/40');
    const box = await answerBox();
    const before = reads.filter((u) => u.endsWith('/api/v2/tasks/t1') || u.includes('%2340')).length;
    answerWith(box, 'Keep apart');
    expect((await within(box).findByRole('alert')).textContent).toBe('Look again');
    expect(reads.filter((u) => u.endsWith('/api/v2/tasks/t1') || u.includes('%2340')).length).toBeGreaterThan(before);
    expect(sent.at(-1)?.body).toEqual({ text: 'Keep apart', round: 2 });
    fireEvent.click(within(box).getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(sent.at(-1)?.body).toEqual({ text: 'Keep apart', round: 3 }));
  });

  it('in Flow, opens the next item once answered', async () => {
    reply = (url, method) => (method === 'POST' && url.endsWith('/api/v2/tasks/t1/question/answer') ? { body: {} } : null);
    const router = show(<TaskPage task="40" flow />, '/flow/40');
    const box = await answerBox();
    // Flow's list is in before the press (Next links to it), so the next item is known before this one leaves it
    expect(await screen.findByRole('link', { name: 'Next' })).toBeTruthy();
    answerWith(box, 'Keep apart');
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });

  it('names no task when the one it was asked on is gone or not readable', async () => {
    shown = task(question({ parent: null }));
    show(<TaskPage task="40" pipeline="p1" />, '/tasks/p1/40');
    const box = await card();
    expect(box.textContent).toContain('From General Manager');
    expect(box.textContent).not.toContain('Credentials');
    expect(within(box).queryByRole('link')).toBeNull();
    expect(await answerBox()).toBeTruthy();
  });

  it("in the task's chat, the person it asks answers in its box, not the message box; anyone else posts as before", async () => {
    shown = task(question(), { conversation_id: 'c1' });
    show(<TaskChat task="40" />, '/tasks/p1/40');
    const chat = await screen.findByRole('region', { name: 'Chat' });
    expect(await within(chat).findByRole('region', { name: 'Answer the question' })).toBeTruthy();
    expect(within(chat).queryByRole('textbox', { name: 'Message' })).toBeNull();
  });

  it('once answered, the chat has its message box back', async () => {
    shown = task(question({ state: 'answered', ended_reason: 'answered', answered_by: 'human:me', answered_at: '2026-11-19T11:00:00Z', answer_text: 'Keep apart.' }), { conversation_id: 'c1' });
    show(<TaskChat task="40" />, '/tasks/p1/40');
    const chat = await screen.findByRole('region', { name: 'Chat' });
    expect(within(chat).getByRole('textbox', { name: 'Message' })).toBeTruthy();
    expect(within(chat).queryByRole('region', { name: 'Answer the question' })).toBeNull();
  });

  it('a question for someone else leaves the message box as it is', async () => {
    shown = task(question({ person: 'human:dana' }), { conversation_id: 'c1', owner_id: 'human:dana' });
    show(<TaskChat task="40" />, '/tasks/p1/40');
    const chat = await screen.findByRole('region', { name: 'Chat' });
    expect(within(chat).getByRole('textbox', { name: 'Message' })).toBeTruthy();
    expect(within(chat).queryByRole('region', { name: 'Answer the question' })).toBeNull();
  });
});
