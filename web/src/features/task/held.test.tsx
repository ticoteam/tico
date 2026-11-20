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
import { holdName } from './Held';
import type { Hold } from './holds';
import type { TaskData } from './queries';
import { TaskChat, TaskPage } from './TaskPage';

/* A rule's review task ("Held by: <rule>"): the decision box shows what the rule held and is answered with Send anyway
   or Keep blocked (POST /api/v2/rule-holds/<id>/send and /keep). Made-up rows only; every answer is stubbed. */

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
    title: 'Held by: Never name a customer to another customer',
    status: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Tasks' },
    stage_id: 's1',
    stage: { id: 's1', name: 'Open' },
    owner_id: 'human:me',
    conversation_id: 'c7',
    labels: ['rule-review'],
    created_at: '2026-11-01T10:00:00Z',
    documents: [],
    to_leave: [],
    links: [],
    ...more,
  }) as unknown as TaskData;

const RULE = { id: 'o1', kind: 'internal', text: 'Never name a customer to another customer', if_broken: 'escalate', version: 1, status: 'active' };
const summary = (more: Partial<Hold> = {}): Hold => ({
  id: 'h1',
  operation: 'threads',
  author: 'bot:sales',
  author_name: 'Sales Owner',
  state: 'held',
  outcome: 'escalated',
  rules: [RULE],
  to: ['Ana Ruiz'],
  task_id: 't1',
  created_at: '2026-11-19T10:00:00Z',
  can_send: true,
  can_withdraw: false,
  ...more,
});
const opened = (more: Partial<Hold> = {}): Hold =>
  summary({
    text: 'Fernhill signed last week, you could ask them.',
    rules: [{ ...RULE, description: 'Customers trust us with their names', part: 'Fernhill signed last week' }],
    nonce: 'n-1',
    ...more,
  });

let holds: Hold[] = [summary()];
let detail: Record<string, Hold | Reply> = { h1: opened() };

function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { body: { task: task(), runs: [], children: [], can_comment: true, mover: true } };
  if (url.endsWith('/files?all=1')) return { body: { files: [] } };
  if (url.endsWith('/api/v2/rule-holds?task=t1')) return { body: { holds, next_cursor: null } };
  const one = /\/api\/v2\/rule-holds\/(\w+)$/.exec(url)?.[1];
  if (one) {
    const d = detail[one];
    return d && 'body' in d ? (d as Reply) : { status: d ? 200 : 404, body: d ?? { error: { code: 'not_found', detail: `There is no hold ${one}` } } };
  }
  if (url.includes('/needs-you')) return { body: { items: [{ task: { id: 't1', number: 12, pipeline_id: 'p1' } }, { task: { id: 't9', number: 13, pipeline_id: 'p1' } }] } };
  if (url.includes('/api/v2/pipelines')) return { body: { pipelines: [] } };
  return { body: {} };
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  sent.length = 0;
  reply = () => null;
  holds = [summary()];
  detail = { h1: opened() };
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
/** The box once the hold is opened: its text is on screen. */
const loaded = async () => {
  const box = await decision();
  await within(box).findByLabelText('What was held');
  return box;
};

describe('a held message on its review task', () => {
  it('shows who it was for, the text with the part that breaks the rule, and the rule with its Why', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    expect(within(box).getByText('Message to Ana Ruiz')).toBeTruthy();
    expect(within(box).getByRole('img', { name: 'Sales Owner' })).toBeTruthy();
    const text = within(box).getByLabelText('What was held');
    expect(text.textContent).toBe('Fernhill signed last week, you could ask them.');
    expect(text.querySelector('mark')?.textContent).toBe('Fernhill signed last week');
    expect(within(box).getByText('Never name a customer to another customer')).toBeTruthy();
    expect(within(box).getByText('Customers trust us with their names')).toBeTruthy();
    expect(within(box).getByRole('link', { name: 'Edit rule' }).getAttribute('href')).toBe('/company/rules?panel=org%3Ao1');
    const buttons = within(box).getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toEqual(['Keep blocked', 'Send anyway']);
    expect(within(box).getByRole('button', { name: 'Send anyway' }).className).toContain('btn-fill');
  });

  it('Send anyway posts the hold’s nonce once, then the review leaves', async () => {
    reply = (url, method) => (method === 'POST' && url.endsWith('/rule-holds/h1/send') ? { body: { hold: opened({ state: 'sent', nonce: undefined }), result: {} } } : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    holds = [summary({ state: 'sent' })];
    fireEvent.click(within(box).getByRole('button', { name: 'Send anyway' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/rule-holds/h1/send', body: { nonce: 'n-1' } }]));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull());
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Sent anyway'));
  });

  it('Keep blocked posts nothing but the press; a refusal from the server is said in the box', async () => {
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/rule-holds/h1/keep') ? { status: 404, body: { error: { code: 'not_found', detail: 'There is no hold h1' } } } : null;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    fireEvent.click(within(box).getByRole('button', { name: 'Keep blocked' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/rule-holds/h1/keep', body: {} }]));
    expect((await within(box).findByRole('alert')).textContent).toBe('There is no hold h1');
    expect(within(box).getByRole('button', { name: 'Keep blocked' })).toHaveProperty('disabled', false);
  });

  it('the next hold of the rule takes the box once one is decided', async () => {
    holds = [summary(), summary({ id: 'h2', operation: 'mail', to: ['dana@example.test'] })];
    detail = { h1: opened(), h2: opened({ id: 'h2', operation: 'mail', to: ['dana@example.test'], text: 'Subject: Hello', rules: [RULE], nonce: 'n-2' }) };
    reply = (url, method) => (method === 'POST' && url.endsWith('/rule-holds/h1/keep') ? { body: opened({ state: 'kept' }) } : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    holds = [summary({ state: 'kept' }), summary({ id: 'h2', operation: 'mail', to: ['dana@example.test'] })];
    fireEvent.click(within(box).getByRole('button', { name: 'Keep blocked' }));
    expect(await screen.findByText('Email to dana@example.test')).toBeTruthy();
    const next = await loaded();
    fireEvent.click(within(next).getByRole('button', { name: 'Send anyway' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/rule-holds/h2/send', body: { nonce: 'n-2' } }));
  });

  it('in Flow, opens the next item once the last hold is decided', async () => {
    reply = (url, method) => (method === 'POST' && url.endsWith('/rule-holds/h1/keep') ? { body: opened({ state: 'kept' }) } : null);
    const router = show(<TaskPage task="12" flow />, '/flow/12');
    const box = await loaded();
    holds = [summary({ state: 'kept' })];
    fireEvent.click(within(box).getByRole('button', { name: 'Keep blocked' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });
});

describe('Send anyway is only drawn over what the person can read', () => {
  it('not while the text is still loading', async () => {
    let open!: (r: Reply) => void;
    reply = (url, method) => (method === 'GET' && url.endsWith('/rule-holds/h1') ? new Promise((resolve) => (open = resolve)) : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect(within(box).getByRole('button', { name: 'Keep blocked' })).toBeTruthy();
    expect(within(box).queryByRole('button', { name: 'Send anyway' })).toBeNull();
    open({ body: opened() });
    expect(await within(box).findByRole('button', { name: 'Send anyway' })).toBeTruthy();
  });

  it('not when the text is cut, nor when the server says this person may not send it', async () => {
    detail = { h1: opened({ text_cut: 25000 }) };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    expect(within(box).getByText('Too long to show in full')).toBeTruthy();
    expect(within(box).queryByRole('button', { name: 'Send anyway' })).toBeNull();
  });

  it('not over a copy that leaves out words the person may not read: someone who can must send it', async () => {
    detail = { h1: opened({ partial: true, can_send: false, nonce: undefined }) };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    expect(within(box).getByText('Someone who can read all of it must send it.')).toBeTruthy();
    expect(within(box).queryByRole('button', { name: 'Send anyway' })).toBeNull();
    expect(within(box).getByRole('button', { name: 'Keep blocked' })).toBeTruthy();
  });

  it('not over a partial copy even with a nonce; the server’s own refusal of one is said in the box', async () => {
    detail = { h1: opened({ partial: true }) };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    expect(within(box).queryByRole('button', { name: 'Send anyway' })).toBeNull();
  });

  it('a send the server refuses as partial says so and stays held', async () => {
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/rule-holds/h1/send')
        ? { status: 409, body: { error: { code: 'partial', detail: 'Someone who can read all of it must send it.' } } }
        : null;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    fireEvent.click(within(box).getByRole('button', { name: 'Send anyway' }));
    expect((await within(box).findByRole('alert')).textContent).toBe('Someone who can read all of it must send it.');
    expect(within(box).getByText('Message to Ana Ruiz')).toBeTruthy();
  });

  it('not for a reader the server gives no nonce', async () => {
    detail = { h1: opened({ can_send: false, nonce: undefined }) };
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await loaded();
    expect(within(box).queryByRole('button', { name: 'Send anyway' })).toBeNull();
    expect(within(box).getByRole('button', { name: 'Keep blocked' })).toBeTruthy();
  });

  it('not when the hold did not open: why is said, with Try again', async () => {
    detail = {};
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect((await within(box).findByRole('alert')).textContent).toContain('There is no hold h1');
    expect(within(box).getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(within(box).queryByRole('button', { name: 'Send anyway' })).toBeNull();
  });
});

describe('what is not a held message', () => {
  it('a task that is no rule review reads no holds', async () => {
    reply = (url) => (url.includes('/api/v2/tasks/') && !url.includes('/files') ? { body: { task: task({ labels: [] }), runs: [], children: [], can_comment: true, mover: true } } : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    await screen.findByRole('region', { name: 'Chat with Me' });
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
  });

  it('names an email, a task change or a message by who it was for', () => {
    expect(holdName({ operation: 'mail', to: ['Dana Ruiz'] })).toBe('Email to Dana Ruiz');
    expect(holdName({ operation: 'task_update', to: [] })).toBe('Task change');
    expect(holdName({ operation: 'message', to: ['Ana', 'Ben', 'Cy', 'Di'] })).toBe('Message to Ana, Ben +2');
  });
});
