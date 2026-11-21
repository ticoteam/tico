import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Link as RouterLink, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Message, type ChatMessage } from '@/conversation';
import { queryClient } from '@/data';
import { setLinkComponent, type LinkProps } from '@/kit/Link';
import type { FlowItem } from '@/features/tasks/routing';
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

beforeEach(() => {
  gets = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    gets.push(`${init?.method ?? 'GET'} ${String(url)}`);
    const body = String(url).startsWith('/api/v2/needs-you') ? { actor: ME, items: [OK], next: null } : {};
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

function show(m: ChatMessage) {
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
  it('shows under a message that links one pending OK, opens its task, and never reads or answers the OK', async () => {
    const router = show(msg('Spending needs your own tap: [Add Builder 3](/tasks/ops/12).'));
    const card = await screen.findByRole('link', { name: /Review and approve/ });
    expect(card.textContent).toContain("Start Builder 3's cloud machine");
    expect(card.getAttribute('href')).toBe('/tasks/ops/12');
    fireEvent.click(card);
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/ops/12'));
    expect(await screen.findByText('The preview')).toBeTruthy();
    // Needs you was read; no OK preview (it records ok_shown) and no write.
    expect(gets.some((g) => g.includes('/api/v2/needs-you'))).toBe(true);
    expect(gets.filter((g) => g.includes('/api/v2/oks') || g.startsWith('POST'))).toEqual([]);
  });

  it('draws nothing for a message without a pending OK link', async () => {
    show(msg('Done: [Add Builder 3](/tasks/ops/13).'));
    await waitFor(() => expect(gets.some((g) => g.includes('/api/v2/needs-you'))).toBe(true));
    expect(screen.queryByRole('link', { name: /Review and approve/ })).toBeNull();
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
  const items = [item('t1', 12, 'ok'), item('t2', 13, 'ok'), item('t3', 14)];
  const O = 'http://app.test';

  it('finds the one linked task an OK waits on, by any of its addresses', () => {
    expect(linkedOk('[a](/tasks/ops/12)', items, O)?.id).toBe('t1');
    expect(linkedOk('[a](/task/t2)', items, O)?.id).toBe('t2');
    expect(linkedOk('see http://app.test/flow/12', items, O)?.id).toBe('t1');
  });

  it('leaves lists, other pipelines and tasks without an OK as linked text', () => {
    expect(linkedOk('[a](/tasks/ops/12) and [b](/tasks/ops/13)', items, O)).toBeNull();
    expect(linkedOk('[a](/tasks/eng/12)', items, O)).toBeNull();
    expect(linkedOk('[a](/tasks/ops/14)', items, O)).toBeNull();
    expect(linkedOk('no link', items, O)).toBeNull();
  });
});
