// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { decisionsOf, dreamCards, dreamReply } from './model';
import { agentsQuery, groupsQuery, peopleQuery, type AgentRow, type Dream } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | undefined) {
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = reply(u, method) ?? fallback(u);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const fallback = (url: string): Reply => {
  if (url.includes('/api/v2/groups')) return { status: 200, body: [] };
  if (url.includes('/api/employees')) return { status: 200, body: roster };
  if (url.includes('/api/runs')) return { status: 200, body: [] };
  if (url.includes('/api/v2/tasks')) return { status: 200, body: { tasks: [] } };
  return { status: 200, body: {} };
};

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const roster: AgentRow[] = [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3 } as AgentRow];

const DECISIONS = [
  '# Decisions',
  'What I chose and why.',
  '- 2026-10-02 Keep the weekly digest short',
  '- 2026-11-14 Ask before moving a launch date',
  '  because the last move surprised people',
  '- Use plain words in release notes',
  '- **2026-10-20**: Draft posts in the shared folder',
].join('\n');

const dreaming = (over: Partial<Dream>): Dream => ({
  auto: false,
  closed_at: null,
  doing: null,
  questions: null,
  reason: null,
  started_at: '2026-11-19T09:00:00Z',
  state: 'dreaming',
  status: 'open',
  summary: null,
  task: 't9',
  ...over,
});

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [ana, pat], byId: { [ana.id]: ana, ana, [pat.id]: pat } });
  queryClient.setQueryData(groupsQuery.queryKey, []);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, { people: [{ id: 'p1', name: 'Pat Doe' }], seats: [] });
});
afterEach(() => vi.unstubAllGlobals());

describe('Decisions @s622', () => {
  it('reads decisions.md newest first, an undated entry kept with the one above it', () => {
    const rows = decisionsOf(DECISIONS);
    expect(rows.map((r) => r.title)).toEqual([
      'Ask before moving a launch date',
      'Use plain words in release notes',
      'Draft posts in the shared folder',
      'Keep the weekly digest short',
    ]);
    expect(rows[0]?.on).toBe('2026-11-14');
    expect(rows[0]?.text).toContain('the last move surprised people');
  });

  it("lists them on the agent's settings", async () => {
    serve((url) => (url.includes('/bots/ana/memory') ? { status: 200, body: { documents: { 'memory/decisions.md': DECISIONS } } } : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: 'Decisions' });
    const rows = section.querySelectorAll('.row');
    expect(rows).toHaveLength(4);
    expect(rows[0]?.textContent).toContain('Ask before moving a launch date');
    expect(within(section).getByText('Keep the weekly digest short')).toBeTruthy();
  });

  it('past 20, "N more" opens the rest', async () => {
    const lines = Array.from({ length: 22 }, (_, i) => `- 2026-10-${String(i + 1).padStart(2, '0')} Decision ${i + 1}`);
    serve((url) => (url.includes('/bots/ana/memory') ? { status: 200, body: { documents: { 'memory/decisions.md': ['# Decisions', ...lines].join('\n') } } } : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: 'Decisions' });
    expect(section.querySelectorAll('.row')).toHaveLength(20);
    fireEvent.click(within(section).getByRole('button', { name: '2 more' }));
    expect(section.querySelectorAll('.row')).toHaveLength(22);
  });

  it('shows no Decisions section when the file is empty', async () => {
    serve(() => undefined);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    await screen.findByRole('region', { name: 'Rules' });
    expect(screen.queryByRole('region', { name: 'Decisions' })).toBeNull();
  });
});

describe('Dream @s623', () => {
  it('splits numbered questions and numbers the reply to match', () => {
    const cards = dreamCards('Two things:\n1. Keep the old notes?\n2) Merge the duplicates?');
    expect(cards.map((c) => c.n)).toEqual(['1', '2']);
    expect(dreamReply(cards, ['Yes', ''])).toBe('1. Yes\n2. No answer');
    expect(dreamCards('Anything else?')).toEqual([{ n: '', text: 'Anything else?' }]);
  });

  it('starts a dream from the More menu and shows it at once', async () => {
    let started = false;
    serve((url, method) => {
      if (!url.includes('/bots/ana/dream')) return undefined;
      if (method === 'POST') started = true;
      return { status: 200, body: { dream: started ? dreaming({ state: 'starting', task: 't5' }) : null } };
    });
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Dream' }));
    const section = await screen.findByRole('region', { name: 'Dream' });
    expect(within(section).getByText('Falling asleep')).toBeTruthy();
    await waitFor(() => expect(sent.some((s) => s.method === 'POST' && s.url.endsWith('/api/v2/bots/ana/dream'))).toBe(true));
  });

  it('asks one question with one answer box', async () => {
    serve((url) => (url.includes('/bots/ana/dream') ? { status: 200, body: { dream: dreaming({ state: 'asking', questions: { mine: true, requirement_id: 'r1', task_id: 't9', text: '1. Keep the old notes?' } }) } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Dream' });
    expect(await within(section).findByText('A few questions')).toBeTruthy();
    expect(within(section).getByLabelText('Your answer')).toBeTruthy();
  });

  it('answers its questions as one numbered reply on the dream task', async () => {
    let state = dreaming({ state: 'asking', questions: { mine: true, requirement_id: 'r1', task_id: 't9', text: '1. Keep the old notes?\n2. Merge the duplicates?' } });
    serve((url, method) => {
      if (url.includes('/bots/ana/dream')) return { status: 200, body: { dream: state } };
      if (method === 'POST' && url.includes('/tasks/t9/comments')) {
        state = dreaming({});
        return { status: 200, body: {} };
      }
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.change(await screen.findByLabelText('Answer 1'), { target: { value: 'Yes' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/tasks/t9/comments'))?.body).toEqual({ text: '1. Yes\n2. No answer' }));
    expect(screen.queryByLabelText('Answer 1')).toBeNull();
  });

  it('stops a dream by closing its task, then Wake up puts it away', async () => {
    let state: Dream = dreaming({ doing: 'Merging notes' });
    serve((url, method) => {
      if (url.includes('/bots/ana/dream')) return { status: 200, body: { dream: state } };
      if (method === 'POST' && url.includes('/tasks/t9/move')) {
        state = dreaming({ state: 'stopped', summary: 'Tidied three notes.' });
        return { status: 200, body: { task: { id: 't9' } } };
      }
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Dream' });
    expect(within(section).getByText(/Merging notes/)).toBeTruthy();
    fireEvent.click(within(section).getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/api/v2/tasks/t9/move'))?.body).toEqual({ to: 'closed' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Wake up' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Dream' })).toBeNull());
  });

  it('takes the stop back when the server refuses it', async () => {
    serve((url, method) => {
      if (url.includes('/bots/ana/dream')) return { status: 200, body: { dream: dreaming({ task: 't7' }) } };
      if (method === 'POST' && url.includes('/tasks/t7/move')) return { status: 403, body: { error: { code: 'forbidden', detail: 'Not yours' } } };
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    const section = await screen.findByRole('region', { name: 'Dream' });
    fireEvent.click(within(section).getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(sent.some((s) => s.url.endsWith('/tasks/t7/move'))).toBe(true));
    expect(await within(section).findByRole('button', { name: 'Stop' })).toBeTruthy();
    expect(within(section).queryByRole('button', { name: 'Wake up' })).toBeNull();
  });
});
