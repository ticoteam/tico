import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import type * as conversations from '@/conversation';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { forgetPlaces, notePlace } from '@/lib';
import type { TaskData } from './queries';
import { TaskChat } from './TaskPage';

/* A stopped agent's notice (backend/bot_stops.py): the task read names the agent when the person may release it, and
   the decision box offers Release in place (POST /api/v2/bots/<id>/release). Made-up rows only; every answer is stubbed. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/conversation', async (orig) => ({
  ...(await orig<typeof conversations>()),
  Conversation: ({ foot, title }: { foot?: ReactNode; title?: string }) => <section aria-label={`Chat with ${title}`}>{foot}</section>,
}));

type Reply = { status?: number; body: unknown };
const sent: { url: string; body: Record<string, unknown> }[] = [];
let reply: (url: string, method: string) => Reply | Promise<Reply> | null = () => null;

const task = (more: Partial<TaskData> = {}): TaskData =>
  ({
    id: 't1',
    number: 12,
    version: 3,
    title: 'Sales Owner stopped after 5 failed runs in a row',
    status: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Tasks' },
    stage_id: 's1',
    stage: { id: 's1', name: 'Open' },
    owner_id: 'human:me',
    conversation_id: 'c7',
    labels: [],
    stopped_agent: { id: 'b-sales', slug: 'sales', name: 'Sales Owner' },
    created_at: '2026-11-01T10:00:00Z',
    documents: [],
    to_leave: [],
    links: [],
    ...more,
  }) as unknown as TaskData;

let released = false;

function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1'))
    return { body: { task: task(released ? { stopped_agent: null, status: 'closed' } : {}), runs: [], children: [], can_comment: true, mover: true, stopped_agent: released ? null : task().stopped_agent } };
  if (url.endsWith('/files?all=1')) return { body: { files: [] } };
  if (url.includes('/needs-you')) return { body: { items: [{ task: { id: 't1', number: 12, pipeline_id: 'p1' } }] } };
  if (url.includes('/api/v2/pipelines')) return { body: { pipelines: [] } };
  return { body: {} };
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  sent.length = 0;
  reply = () => null;
  released = false;
  queryClient.clear();
  forgetPlaces();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [
    { id: 'bot:sales', kind: 'agent', name: 'Sales Owner' },
    { id: 'human:me', kind: 'person', name: 'Me' },
  ];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), body: typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {} });
    const r = await (reply(String(url), method) ?? base(String(url)));
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
  const note = (l: typeof router.state.location) => notePlace(l.pathname, l.searchStr, l.search as Record<string, unknown>, l.state);
  note(router.state.location);
  router.subscribe('onResolved', (e) => note(e.toLocation));
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const decision = () => screen.findByRole('region', { name: 'The decision' });

describe("a stopped agent's notice", () => {
  it('names the agent and offers Release in place', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect(within(box).getByText('Sales Owner is stopped')).toBeTruthy();
    expect(within(box).getAllByRole('button').map((b) => b.textContent)).toEqual(['Release']);
  });

  it('Release posts the agent’s release once, then the box leaves', async () => {
    reply = (url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/bots/b-sales/release')) {
        released = true;
        return { body: { bot: 'b-sales', released: { run_hold: true, budget: false, claim: 0 } } };
      }
      return null;
    };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    fireEvent.click(within(await decision()).getByRole('button', { name: 'Release' }));
    await waitFor(() => expect(sent.map((s) => s.url)).toEqual(['/api/v2/bots/b-sales/release']));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull());
  });

  it('a refusal is said in the box, and Release stays', async () => {
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/release') ? { status: 403, body: { error: { code: 'forbidden', detail: 'Only a person who manages this bot can release it' } } } : null;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    fireEvent.click(within(await decision()).getByRole('button', { name: 'Release' }));
    expect(await within(await decision()).findByRole('alert')).toBeTruthy();
    expect(within(await decision()).getByRole('button', { name: 'Release' })).toBeTruthy();
  });

  it('shows nothing on a task that is no notice', async () => {
    released = true; // the task read names no stopped agent
    show(<TaskChat task="12" />, '/tasks/p1/12');
    await screen.findByLabelText('Chat with Me');
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
  });
});
