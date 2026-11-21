import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Link as RouterLink, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Message, type ChatMessage } from '@/conversation';
import { queryClient } from '@/data';
import { setLinkComponent, type LinkProps } from '@/kit/Link';
import { flowQuery, type Flow, type FlowItem } from '@/features/tasks/routing';
import { linkedOk, okCard } from './OkCard';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Tico's approval card: a Tico message that links one task whose OK waits on the person shows one card that opens the
   task, and the chat never reads or answers the OK itself. Made-up rows; every answer is stubbed. */

const ME = 'human:ana';
const OK = {
  kind: 'ask',
  id: 't1:r1',
  task_id: 't1',
  line: "Start Builder 3's cloud machine",
  since: '2026-11-20T09:00:00Z',
  task: { id: 't1', number: 12, title: 'Add Builder 3', pipeline_id: 'ops', stage_id: 'todo', owner_id: 'bot:ops' },
  ask: { requirement_id: 'r1', kind: 'ok', line: "Start Builder 3's cloud machine", met: false, asked_by: 'bot:botops', shows: [], may: ['ok', 'no'] },
};
let gets: string[] = [];
let rows: unknown[] = [OK];

// The Needs-you list as Flow holds it once read: one row per task, and every approval.
const theCards = () => gets.filter((g) => g.includes('/api/v2/needs-you') || g.includes('/api/v2/oks') || !g.startsWith('GET'));
const flowOf = (): Promise<Flow> => queryClient.fetchQuery({ ...flowQuery, staleTime: 0 });

beforeEach(() => {
  gets = [];
  rows = [OK];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    gets.push(`${init?.method ?? 'GET'} ${String(url)}`);
    const body = String(url).startsWith('/api/v2/needs-you') ? { actor: ME, items: rows, next: null } : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  setLinkComponent(({ href, ...rest }: LinkProps) => <RouterLink {...rest} to={href as never} />);
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
  setLinkComponent((p: LinkProps) => <a {...p} />);
});

const msg = (body: string): ChatMessage => ({ id: 'm1', seq: 1, sender_id: 'bot:assistant', sender_name: 'Tico', body, created_at: '2026-11-20T09:01:00Z' });

async function show(m: ChatMessage, read = true) {
  // The tracker over the chat reads Needs you; the card only observes it.
  if (read) queryClient.setQueryData(flowQuery.queryKey, await flowOf());
  gets = [];
  const root = createRootRoute({ component: Outlet });
  const chat = createRoute({ getParentRoute: () => root, path: '/', component: () => <Message m={m} variant="tico" me={ME} below={okCard} /> });
  const task = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline/$task', component: () => <p>The preview</p> });
  const router = createRouter({ routeTree: root.addChildren([chat, task]), history: createMemoryHistory({ initialEntries: ['/'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("Tico's approval card", () => {
  it('shows under a message that links one pending OK, opens its task, and sends no request of its own', async () => {
    const router = await show(msg('Spending needs your own tap: [Add Builder 3](/tasks/ops/12).'));
    const card = await screen.findByRole('link', { name: /Review and approve/ });
    expect(card.textContent).toContain("Start Builder 3's cloud machine");
    expect(card.getAttribute('href')).toBe('/tasks/ops/12');
    // The card read nothing: no Needs you again, no OK preview (it records ok_shown), no write. (The message's own
    // reads, its sender's name, are not the card's.)
    expect(theCards()).toEqual([]);
    fireEvent.click(card);
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/ops/12'));
    expect(await screen.findByText('The preview')).toBeTruthy();
    expect(theCards()).toEqual([]);
  });

  it('starts no read of its own: it draws once the Needs-you list it observes is read', async () => {
    await show(msg('[Add Builder 3](/tasks/ops/12)'), false);
    expect(await screen.findByText(/Add Builder 3|Task/)).toBeTruthy();
    expect(theCards()).toEqual([]);
    expect(screen.queryByRole('link', { name: /Review and approve/ })).toBeNull();
    const flow = await flowOf();
    act(() => queryClient.setQueryData(flowQuery.queryKey, flow));
    expect(await screen.findByRole('link', { name: /Review and approve/ })).toBeTruthy();
  });

  it('shows for an OK behind an older ask on the same task', async () => {
    rows = [{ ...OK, id: 't1:q', ask: { ...OK.ask, requirement_id: 'q', kind: 'question', line: 'Which region?' } }, OK];
    const flow = await flowOf();
    expect(flow.items.map((i) => i.ask?.kind)).toEqual(['question']);
    await show(msg('[Add Builder 3](/tasks/ops/12)'));
    expect((await screen.findByRole('link', { name: /Review and approve/ })).textContent).toContain("Start Builder 3's cloud machine");
  });

  it('draws nothing for a message without a pending OK link', async () => {
    await show(msg('Done: [Add Builder 3](/tasks/ops/13).'));
    expect(await screen.findByText(/Done/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Review and approve/ })).toBeNull();
    expect(theCards()).toEqual([]);
  });
});

describe('linkedOk', () => {
  const item = (id: string, number: number, kind?: string): FlowItem => ({
    id,
    number,
    title: id,
    pipeline_id: 'ops',
    stage_id: 's',
    owner_id: 'bot:ops',
    late: false,
    stuck: false,
    since: '',
    ...(kind && { ask: { kind, by: 'bot:ops', shows: [] } }),
  });
  const items = [item('t1', 12, 'ok'), item('t2', 13, 'page')];
  const O = 'http://app.test';

  it('finds the one linked task an OK waits on, by any of its addresses', () => {
    expect(linkedOk('[a](/tasks/ops/12)', items, O)?.id).toBe('t1');
    expect(linkedOk('[a](/task/t2)', items, O)?.id).toBe('t2');
    expect(linkedOk('see http://app.test/flow/12.', items, O)?.id).toBe('t1');
    expect(linkedOk('[a](/tasks/ops/12) or [b](/task/t1) or [c](/#/task/t1)', items, O)?.id).toBe('t1');
    // a link written before the task moved pipelines still names it
    expect(linkedOk('[a](/tasks/eng/12)', items, O)?.id).toBe('t1');
    // the pipeline's editor is no task: the one task linked beside it still counts
    expect(linkedOk('[a](/tasks/ops/12), see [the stages](/tasks/ops/edit)', items, O)?.id).toBe('t1');
    // a link with a malformed escape names no task: it neither throws nor hides the card
    expect(linkedOk('[a](/tasks/ops/12) and [b](/task/%E0%A4%A)', items, O)?.id).toBe('t1');
  });

  it('leaves lists and tasks without an OK as linked text', () => {
    expect(linkedOk('[a](/tasks/ops/12) and [b](/tasks/ops/13)', items, O)).toBeNull();
    expect(linkedOk('[the stages](/tasks/ops/edit)', items, O)).toBeNull();
    expect(linkedOk('[a](/tasks/ops/14)', items, O)).toBeNull();
    expect(linkedOk('no link', items, O)).toBeNull();
    expect(linkedOk('`http://app.test/flow/12`', items, O)).toBeNull();
    expect(linkedOk('[a](/tasks/ops/12) and [c](/tasks/ops/99)', items, O)).toBeNull();
  });
});
