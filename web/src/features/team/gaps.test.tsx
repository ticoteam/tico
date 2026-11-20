// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { instructionsChange } from './model';
import { agentsQuery, groupsQuery, peopleQuery, type AgentRow, type Group } from './queries';
import { loadWho } from './routing';
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
  if (url.includes('/api/v2/pipelines')) return { status: 200, body: { pipelines: [] } };
  return { status: 200, body: {} };
};

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const kim: Actor = { id: 'human:p2', kind: 'person', name: 'Kim Roe' };
const directory = (actors: Actor[]) => ({ actors, byId: Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]])) });
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p1', 'p2'] }];
const roster: AgentRow[] = [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, users: [{ id: 'p1', name: 'Pat Doe' }] } as AgentRow];

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, directory([ana, pat, kim]));
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, {
    people: [
      { id: 'p1', name: 'Pat Doe', slack: 'pat' },
      { id: 'p2', name: 'Kim Roe' },
    ],
    seats: [],
  });
});
afterEach(() => vi.unstubAllGlobals());

/* ---------------------------------------------------------------- s626: a person's Slack */

const thread = (n: number, body: string) => ({
  bot: '0190-ana',
  display_name: 'Ana',
  conversation_id: `c${n}`,
  channel: 'D1',
  last_message_at: `2026-11-1${n}T10:00:00Z`,
  messages: [
    { id: `m${n}a`, body: 'Earlier line', sender_id: 'human:p1' },
    { id: `m${n}b`, body, sender_id: 'bot:0190-ana' },
  ],
});
const slackOf = (threads: unknown[] | null, status = 200) => (url: string) =>
  url.includes('/api/v2/humans/p1/slack') ? { status, body: status === 200 ? { person: 'p1', threads } : { error: { code: 'forbidden' } } } : undefined;

describe("A person's Slack", () => {
  it('shows their newest Slack threads in the column, each opening its thread, and All threads opens the rest @s626', async () => {
    serve(slackOf([thread(4, 'Can the price hold?'), thread(3, 'Who gets which offer'), thread(2, 'Three subject lines'), thread(1, 'Kits for Canada')]));
    show(<TeamWho who="p1" />, '/team/p1');
    const slack = await screen.findByRole('region', { name: /Slack/ });
    expect(within(slack).getByText('Can the price hold?').closest('a')?.getAttribute('href')).toBe('/threads/c4');
    expect(within(slack).getByText('Three subject lines')).toBeTruthy();
    expect(within(slack).queryByText('Kits for Canada')).toBeNull();
    expect(within(slack).getAllByText('Ana')).toHaveLength(3);
    expect(within(slack).getByRole('link', { name: /All threads/ }).getAttribute('href')).toBe('/team/p1?tab=slack');
  });

  it('lists every thread one step in @s626', async () => {
    serve(slackOf([thread(4, 'Can the price hold?'), thread(1, 'Kits for Canada')]));
    show(<TeamWho who="p1" tab="slack" />, '/team/p1?tab=slack');
    const all = await screen.findByRole('region', { name: /Threads/ });
    expect(within(all).getByText('Kits for Canada').closest('a')?.getAttribute('href')).toBe('/threads/c1');
  });

  it('leaves the section out for someone who may not read it @s626', async () => {
    serve(slackOf(null, 403));
    show(<TeamWho who="p1" />, '/team/p1');
    expect(await screen.findByRole('region', { name: /Tasks/ })).toBeTruthy();
    await waitFor(() => expect(queryClient.getQueryState(['team', 'slack', 'p1'])?.status).toBe('error'));
    expect(screen.queryByRole('region', { name: /Slack/ })).toBeNull();
  });

  // The router's fault page draws a 403 as no access, without Try again.
  it('hands a refused read one step in to the router as a 403 @s626', async () => {
    serve(slackOf(null, 403));
    await expect(loadWho(queryClient, 'p1', 'slack')).rejects.toMatchObject({ status: 403 });
  });

  it('says not connected when they have no Slack and no threads @s626', async () => {
    serve((url) => (url.includes('/humans/p2/slack') ? { status: 200, body: { person: 'p2', threads: [] } } : undefined));
    show(<TeamWho who="p2" />, '/team/p2');
    const slack = await screen.findByRole('region', { name: /Slack/ });
    expect(within(slack).getByText('Not connected')).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- s616: an agent's rules */

const rules = (url: string) =>
  url.endsWith('/bots/ana/rules')
    ? {
        status: 200,
        body: {
          settings: [{ text: 'Answer within a day' }],
          internal: { rules: [{ id: 'r1', kind: 'internal', text: 'Ask before spending', status: 'active' }] },
          external: { rules: [{ id: 'r2', kind: 'external', text: 'Keep records a year', status: 'active' }] },
        },
      }
    : undefined;

describe("An agent's rules", () => {
  it('starts a new rule for this agent on the Rules page @s616', async () => {
    serve(rules);
    const router = show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: /Rules/ });
    fireEvent.click(within(section).getByRole('button', { name: 'New rule' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/company/rules'));
    expect(router.state.location.search).toEqual({ who: 'bot:0190-ana', panel: 'org:new' });
  });

  it('opens a rule from its More, on the tab it lives on; its settings have none @s616', async () => {
    serve(rules);
    const router = show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    const section = await screen.findByRole('region', { name: /Rules/ });
    const outside = (await within(section).findByText('Keep records a year')).closest('.rule') as HTMLElement;
    const setting = within(section).getByText('Answer within a day').closest('.rule') as HTMLElement;
    expect(within(setting).queryByRole('button', { name: 'More' })).toBeNull();
    fireEvent.click(within(outside).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/company/rules'));
    expect(router.state.location.search).toEqual({ tab: 'outside', panel: 'org:r2' });
  });
});

/* ---------------------------------------------------------------- s617: an agent's instructions */

const INS = '# Role\n\nYou write the weekly note.\n\nKeep it under 200 words.';
const instructions = (url: string, method: string) =>
  method === 'GET' && url.endsWith('/bots/ana/instructions') ? { status: 200, body: { content: INS, updated_at: '2026-11-17T10:00:00Z' } } : undefined;
const toTico = () => sent.find((s) => s.url.endsWith('/api/v2/assistant/messages'))?.body as { text: string } | undefined;

describe("An agent's instructions", () => {
  it('lets whoever manages it edit the whole text, and sends only the change to Tico @s617', async () => {
    serve(instructions);
    show(<TeamWho who="ana" tab="instructions" />, '/team/ana?tab=instructions');
    expect(await screen.findByText('You write the weekly note.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const box = await screen.findByRole('textbox', { name: 'Instructions' });
    fireEvent.change(box, { target: { value: INS.replace('200 words', '150 words') } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toTico()).toBeTruthy());
    expect(toTico()!.text).toContain('Ana [ana]');
    expect(toTico()!.text).toContain('Replace this:\n\nKeep it under 200 words.\n\nwith this:\n\nKeep it under 150 words.');
    expect(toTico()!.text).not.toContain('You write the weekly note.');
  });

  it('gives anyone who may not manage it a request instead, and neither is open to Tico @s617', async () => {
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...roster[0]!, can_manage: false }]);
    serve(instructions);
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    fireEvent.click(await screen.findByRole('button', { name: 'Request changes' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'What should change' }), { target: { value: 'Sign every note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(toTico()?.text).toContain('Ask whoever manages Ana [ana]'));
    expect(toTico()!.text).toContain('Sign every note');
    expect(await run('agents.editInstructions', { slug: 'ana', text: 'New' }, { via: 'click' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('agents.requestInstructions', { slug: 'ana', changes: 'x' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('words a change as the one stretch that differs @s617', () => {
    expect(instructionsChange('A\n\nB', 'A\n\nB')).toBeNull();
    expect(instructionsChange('A\n\nB', 'A\n\nB\n\nC')).toBe('Add this after the paragraph that starts "B":\n\nC');
    expect(instructionsChange('A\n\nB\n\nC', 'A\n\nC')).toBe('Remove this:\n\nB');
  });
});

/* ---------------------------------------------------------------- the checker's notes */

const openTasks = (url: string, method: string) =>
  method === 'GET' && url.includes('/api/v2/tasks') && url.includes('status=open')
    ? { status: 200, body: { tasks: [{ id: 't1', number: null, title: 'Pick a font', status: 'open', owner_id: ana.id, updated_at: '2026-11-01T00:00:00Z' }] } }
    : undefined;

describe("A teammate's page", () => {
  it('opens All tasks filtered to them, on the pipeline Tasks opens @s608', async () => {
    serve((url, method) =>
      url.includes('/api/v2/pipelines')
        ? { status: 200, body: { pipelines: [{ id: 'eng', name: 'Engineering', stages: [], revision: 1 }] } }
        : openTasks(url, method),
    );
    show(<TeamWho who="ana" />, '/team/ana');
    const tasks = await screen.findByRole('region', { name: /Tasks/ });
    await waitFor(() => expect(within(tasks).getByRole('link', { name: /All tasks/ }).getAttribute('href')).toBe('/tasks/eng?owner=bot%3A0190-ana'));
  });

  it('offers Add teammate only on a team page, where its dialog is', async () => {
    serve(openTasks);
    const router = show(<TeamWho who="ana" />, '/team/ana');
    await screen.findByRole('region', { name: /Tasks/ });
    expect(await run('team.addTeammate', {}, { via: 'click' })).toMatchObject({ ok: false, error: 'not_allowed' });
    router.history.push('/team/design');
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/design'));
    expect(await run('team.addTeammate', {}, { via: 'click' })).toMatchObject({ ok: true });
  });
});
