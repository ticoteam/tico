// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { agentsQuery, computersQuery, groupsQuery, modelsQuery, moveAgent, peopleQuery, setTeammateAccess, type AgentRow, type Computer, type Group } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
let sent: Sent[] = [];
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
  if (url.includes('/api/v2/groups')) return { status: 200, body: groups };
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
const kim: Actor = { id: 'human:p2', kind: 'person', name: 'Kim Roe' };
const actors = [ana, pat, kim];
const byId = Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]]));
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p1', 'p2'] }];
const roster: AgentRow[] = [
  { id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, machine: { runner_id: 'r1', label: 'Studio', generation: 2 }, users: [{ id: 'p1', name: 'Pat Doe' }] } as AgentRow,
];
const studio = {
  id: 'r1',
  label: 'Studio',
  online: true,
  bots: ['0190-ana'],
  readiness: { runtimes: { claude: { installed: true, authenticated: 'ready', account: { email: 'ops@example.com' }, weekly: { used_percent: 40 } } } },
} as unknown as Computer;

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, {
    people: [
      { id: 'p1', name: 'Pat Doe' },
      { id: 'p2', name: 'Kim Roe' },
    ],
    seats: [],
  });
  queryClient.setQueryData(computersQuery.queryKey, [studio, { ...studio, id: 'r2', label: 'Annex', bots: [] }]);
  queryClient.setQueryData(modelsQuery.queryKey, { models: [], harnesses: [{ id: 'claude', name: 'Claude Code', capabilities: { browser_sign_in: true, sign_out: true } }] });
});
afterEach(() => vi.unstubAllGlobals());

describe('Team parity', () => {
  it('lists what an agent waits on from someone else, naming who @s609', async () => {
    const task = { id: 't1', number: null, title: 'Approve the banner', status: 'open', owner_id: ana.id, turn: { state: 'person', who: kim.id }, updated_at: '2026-11-01T00:00:00Z' };
    serve((url, method) => (method === 'GET' && url.includes('/api/v2/tasks') && url.includes('status=open') ? { status: 200, body: { tasks: [task] } } : undefined));
    show(<TeamWho who="ana" />, '/team/ana');
    expect(await screen.findByRole('heading', { name: /Waiting/ })).toBeTruthy();
    expect(screen.getByText('Approve the banner')).toBeTruthy();
    expect(screen.getByText('On Kim Roe')).toBeTruthy();
  });

  it('gives a person a task: shown at once, sent with them as owner @s654', async () => {
    serve(() => undefined);
    show(<TeamWho who="p2" />, '/team/p2');
    fireEvent.click(await screen.findByRole('button', { name: 'Give Kim Roe a task' }));
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Draft the brief' } });
    fireEvent.click(screen.getByRole('button', { name: 'Give it' }));
    expect(await screen.findByText('Draft the brief')).toBeTruthy();
    await waitFor(() => expect(sent.find((s) => s.url.includes('/api/v2/tasks'))?.body).toEqual({ title: 'Draft the brief', owner: 'human:p2' }));
  });

  it('changes who an agent works for @s717', async () => {
    serve(() => undefined);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: /Works for/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Kim Roe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.find((s) => s.url.includes('/bots/ana/owners'))?.body).toEqual({ owners: ['p1', 'p2'], expected_revision: 3 }));
  });

  it('moves an agent to another computer against its generation and revision', async () => {
    serve(() => undefined);
    await act(() => moveAgent('ana', 'r2'));
    expect(sent[0]).toMatchObject({ url: expect.stringContaining('/api/v2/bots/ana/transitions'), body: { kind: 'machine', runner_id: 'r2', expected_generation: 2, expected_revision: 3 } });
    expect(queryClient.getQueryData(agentsQuery.queryKey)?.[0]?.machine?.runner_id).toBe('r2');
  });

  it('shows Access to whoever manages a person and saves it with its revision', async () => {
    const everyone = { everyone: true, people: [], teams: [], bots: [] };
    const view = { see: everyone, read: everyone, write: everyone, revision: 7, target: 'human:p2', you: { manage: true, see: true, read: true, write: true } };
    serve((url, method) => (url.includes('/humans/p2/access') && method === 'GET' ? { status: 200, body: view } : undefined));
    show(<TeamWho who="p2" tab="settings" />, '/team/p2?tab=settings');
    expect(await screen.findByRole('button', { name: /Access/ })).toBeTruthy();
    const narrow = { ...everyone, everyone: false, people: ['p1'] };
    await act(() => setTeammateAccess('person', 'p2', { see: everyone, read: narrow, write: narrow }));
    const put = sent.find((s) => s.method === 'PUT');
    expect(put?.url).toContain('/api/v2/humans/p2/access');
    expect(put?.body).toMatchObject({ read: narrow, revision: 7 });
  });

  it('names the agents a sign-out stops before it signs out @s732', async () => {
    serve((url) => (url.includes('/logouts/bots') ? { status: 200, body: { bots: [{ bot: '0190-ana', name: 'Ana' }] } } : undefined));
    show(<TeamWho who="ana" tab="computer" />, '/team/ana?tab=computer');
    expect(await screen.findByText('40% this week')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out of Claude Code' }));
    expect(await screen.findByText('Ana will stop until someone signs in again.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(sent.find((s) => s.url.includes('/computers/r1/logouts'))?.body).toEqual({ runtime: 'claude' }));
  });
});

describe('Copies of an agent', () => {
  const copy: Actor = { id: 'bot:0191-anb', kind: 'agent', name: 'Ana', slug: 'ana-pat' };
  const family = (branches: unknown[]) => ({ status: 200, body: { original: '0190-ana', shared: true, branches } });
  const mine = { id: '0191-anb', slug: 'ana-pat', owner_id: 'p1', status: 'active', revision: 4, profile: { name: 'custom', label: 'Custom' } };
  const pick = async (name: string, option: string) => {
    fireEvent.click(await screen.findByRole('combobox', { name }));
    const o = await screen.findByRole('option', { name: option });
    fireEvent.pointerDown(o, { pointerType: 'mouse' });
    fireEvent.click(o);
  };
  beforeEach(() => {
    const all = [...actors, copy];
    queryClient.setQueryData(directoryQuery.queryKey, { actors: all, byId: { ...byId, [copy.id]: copy, 'ana-pat': copy } });
    queryClient.setQueryData(agentsQuery.queryKey, [...roster, { id: '0191-anb', slug: 'ana-pat', display_name: 'Ana', revision: 4, owner_id: 'p1' } as AgentRow]);
    queryClient.setQueryData(computersQuery.queryKey, [
      { ...studio, owner_id: 'p1' },
      { ...studio, id: 'r2', label: 'Annex', bots: [], owner_id: 'p1' },
    ]);
  });

  it('makes my copy of a shared agent on my computer that does not run it', async () => {
    serve((url, method) => {
      if (url.includes('/bots/ana/branches')) return family([]);
      if (method === 'POST' && url.includes('/bots/ana/copies')) return { status: 201, body: { ...mine, status: 'active' } };
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    fireEvent.click(await screen.findByRole('button', { name: 'Copy: Original' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Make my copy' }));
    expect(await screen.findByRole('combobox', { name: 'Computer' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Make copy' }));
    await waitFor(() => expect(sent.find((s) => s.url.includes('/bots/ana/copies'))?.body).toEqual({ runner_id: 'r2' }));
  });

  it('pauses my copy against its revision', async () => {
    serve((url) => (url.includes('/bots/ana-pat/branches') ? family([mine]) : undefined));
    show(<TeamWho who="ana-pat" tab="settings" />, '/team/ana-pat?tab=settings');
    expect(await screen.findByRole('link', { name: /Ana/ })).toBeTruthy();
    await pick('Status', 'Paused');
    await waitFor(() => expect(sent.find((s) => s.url.includes('/bots/ana-pat/definition'))?.body).toEqual({ status: 'paused', expected_revision: 4 }));
  });

  it('asks before leaving Custom drops its own wake rules, then sends the change again', async () => {
    let tries = 0;
    serve((url, method) => {
      if (url.includes('/bots/ana-pat/branches')) return family([mine]);
      if (url.includes('/api/v2/profiles')) return { status: 200, body: { profiles: [{ name: 'custom', label: 'Custom' }, { name: 'doer', label: 'Doer' }] } };
      if (method === 'POST' && url.includes('/bots/ana-pat/profile'))
        return tries++ ? { status: 200, body: {} } : { status: 409, body: { error: { code: 'conflict', detail: 'Wake rules would be deleted' } } };
      return undefined;
    });
    show(<TeamWho who="ana-pat" tab="settings" />, '/team/ana-pat?tab=settings');
    await pick('Profile', 'Doer');
    fireEvent.click(await screen.findByRole('button', { name: 'Leave Custom' }));
    await waitFor(() => expect(sent.filter((s) => s.url.includes('/profile')).map((s) => s.body)).toEqual([
      { profile: 'doer', expected_revision: 4 },
      { profile: 'doer', expected_revision: 4, confirm_drop_rules: true },
    ]));
  });
});
