import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { configQuery, queryClient } from '@/data';
// Keep the lazy Connect plan import's transform out of the timed parts.
import '@/features/settings/exports';
import { useSetupOwed } from './gate';
import type { SetupRecord } from './queries';
import { SetupChat } from './SetupChat';
import { keepTranscript, useSetupTranscript } from './transcript';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Made-up data only. The server is a table of answers by "METHOD /path" (a function answers from what was sent); anything
   else answers 404. Every call is kept to check what was, and was not, sent. */

type Answer = { status?: number; body: unknown };
let answers: Record<string, Answer | ((body: unknown) => Answer)>;
let calls: { method: string; path: string; body?: unknown }[];

const json = (a: Answer) => new Response(JSON.stringify(a.body), { status: a.status ?? 200, headers: { 'Content-Type': 'application/json' } });
const refusal = (status: number, code: string, detail: string): Answer => ({ status, body: { error: { code, detail } } });
const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path);

const NAMES = { company_name: 'Acme', app_name: 'Tico', assistant_name: 'Tico', owner_name: '', team_domain: '', website: '' };
const FRESH: SetupRecord = {
  names: NAMES,
  answers: {},
  selected: {},
  completed: null,
  needed: true,
  company_import: null,
  default_chart: [
    { team: 'Growth', on: true },
    { team: 'Product', on: false },
  ],
  first_jobs: { choices: [{ key: 'grow', label: 'Get more customers', seat: 'growth-manager', team: 'Growth' }], picked: 'grow' },
};
const PEOPLE = [
  { id: 'priya', name: 'Priya Shah', email: 'priya@acme.example' },
  { id: 'leo', name: 'Leo Park', email: 'leo@acme.example' },
  // typed no email on the setup page: never offered
  { id: 'sam', name: 'Sam Lee' },
];
const FROM_FILE: SetupRecord = {
  ...FRESH,
  completed: '2026-11-23T10:00:00Z',
  live_owed: true,
  company_import: { fingerprint: 'f1', drafts_waiting: 183, people: PEOPLE },
};
const READY = { kinds: [], subscriptions: [{ id: 's1', name: 'Claude plan', runtime: 'claude', status: 'working', available: true }] };
const NONE = { kinds: [], subscriptions: [] };
const CREDIT = { state: 'ok', credits: [{ label: 'Welcome', amount_micros: 20_000_000, left_micros: 20_000_000, current: true }] };

function serve(record: SetupRecord, subs: unknown = READY) {
  answers = {
    'GET /api/v2/setup': { body: record },
    'GET /api/v2/model-subscriptions': { body: subs },
    'GET /api/v2/billing': { body: CREDIT },
    'POST /api/v2/setup/complete': { body: { ...record, completed: record.completed ?? 'now', live_owed: false, needed: false } },
    'PUT /api/v2/setup': { body: record },
    'POST /api/v2/flow/ping': { body: { pinged: [] } },
    'POST /api/v2/humans/me': { body: {} },
    'GET /api/v2/assistant': { body: { available: true, bot: 'assistant', can_turn_on: false, state: 'active', room_id: 'room-1' } },
    'POST /api/v2/assistant/messages': { body: { id: 'm1' } },
  };
}

function show() {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <SetupChat />
      </>
    ),
  });
  const tasks = createRoute({ getParentRoute: () => root, path: '/tasks', component: () => null });
  const router = createRouter({ routeTree: root.addChildren([tasks]), history: createMemoryHistory({ initialEntries: ['/'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const log = () => within(screen.getByRole('log', { name: 'Setup with Tico' }));
/** A message's paragraph whose words (across its line breaks) match. */
const said = (re: RegExp) => (_: string, el: Element | null) => el?.tagName === 'P' && re.test(el.textContent ?? '');

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  keepTranscript([]);
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(configQuery.queryKey, { company_name: 'Acme', onboarding_needed: true } as never);
  calls = [];
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
    const path = url.split('?')[0]!;
    calls.push({ method, path, body });
    const a = answers[`${method} ${path}`];
    if (!a) return json({ status: 404, body: { error: { code: 'not_found', detail: 'no such thing' } } });
    return json(typeof a === 'function' ? a(body) : a);
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Setup in Tico’s chat, for a company a setup file made', () => {
  it('welcomes them back with the drafts waiting, offers only the people with emails, and goes live on Tasks', async () => {
    serve(FROM_FILE);
    answers['POST /api/v2/setup/company/people/priya/invite'] = { body: FROM_FILE };
    answers['POST /api/v2/setup/company/people/leo/invite'] = { body: FROM_FILE };
    const router = show();
    await screen.findByRole('log', { name: 'Setup with Tico' });
    expect(await log().findByText('Welcome back. Acme is saved. 183 drafts are waiting for your yes.')).toBeTruthy();
    // the plan is connected already: its line, with the credit the company has
    expect(await log().findByText(said(/Your plan runs the agents\.The \$20 credit covers rule checks\./))).toBeTruthy();
    expect(log().getByText('Invite Priya and Leo?')).toBeTruthy();
    expect(log().getByText('priya@acme.example')).toBeTruthy();
    expect(log().queryByText('Sam Lee')).toBeNull();
    // nothing asked yet about where to reach them, until the invite is answered
    expect(log().queryByText(/Where should I reach you/)).toBeNull();
    fireEvent.click(log().getByRole('button', { name: 'Invite' }));
    expect(await log().findByText('Priya and Leo have invites.')).toBeTruthy();
    expect(sent('POST', '/api/v2/setup/company/people/priya/invite')[0]!.body).toEqual({ email: 'priya@acme.example' });
    expect(sent('POST', '/api/v2/setup/company/people/leo/invite')).toHaveLength(1);
    expect(calls.some((c) => c.path.includes('/people/sam/'))).toBe(false);

    fireEvent.click(await log().findByRole('button', { name: 'Email digest' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(sent('POST', '/api/v2/humans/me')[0]!.body).toEqual({ email_digest: true });
    // the file made the team: going live lays nothing out, and its agents are asked for what they still need
    expect(sent('PUT', '/api/v2/setup')).toHaveLength(0);
    expect(sent('POST', '/api/v2/setup/complete')).toHaveLength(1);
    expect(sent('POST', '/api/v2/flow/ping')[0]!.body).toEqual({ scope: 'setup' });
    expect(sent('POST', '/api/v2/assistant/messages')).toHaveLength(0);
    // what was said stays above Tico's chat, ending live
    const kept = renderHook(() => useSetupTranscript()).result.current;
    expect(kept.at(-1)?.body).toBe("You're live. Open any page and I'll show you around it the first time.");
    expect(kept.map((m) => m.created_at)).toEqual([...kept.map((m) => m.created_at)].sort());
  });

  it('asks nothing about invites when no one in the file gave an email, and shows no credit line without credit', async () => {
    serve({ ...FROM_FILE, company_import: { fingerprint: 'f1', people: [{ id: 'sam', name: 'Sam Lee' }] } });
    answers['GET /api/v2/billing'] = { body: { state: 'no_gateway', credits: [] } };
    show();
    await screen.findByRole('log', { name: 'Setup with Tico' });
    expect(await log().findByText('Welcome back. Acme is saved.')).toBeTruthy();
    expect(await log().findByText(/Where should I reach you/)).toBeTruthy();
    expect(log().queryByText(/Invite/)).toBeNull();
    expect(log().queryByText(/credit/)).toBeNull();
  });
});

describe('Setup in Tico’s chat, for a company started here', () => {
  it('meets Tico, connects nothing until a plan is tested, falls back to a key after a failed sign-in, then hands the setup to Tico', async () => {
    let saved = false;
    serve(FRESH, NONE);
    answers['GET /api/v2/model-subscriptions'] = () => ({ body: saved ? READY : NONE });
    answers['POST /api/v2/model-subscriptions/connect'] = refusal(409, 'offline', 'No computer can sign in');
    answers['POST /api/v2/model-subscriptions'] = (body) => {
      saved = (body as { secret?: string }).secret === 'sk-ant-made-up';
      return saved ? { body: { id: 's1', name: 'Anthropic key' } } : refusal(422, 'secret', 'An Anthropic API key starts with sk-ant-');
    };
    show();
    await screen.findByRole('log', { name: 'Setup with Tico' });
    expect(await log().findByText("Hi, I'm Tico. I help companies win, with a team of AI agents.")).toBeTruthy();
    const card = () => log().getByText('Claude').closest('.setup-card') as HTMLElement;
    await waitFor(() => expect(within(card()).getAllByRole('button', { name: 'Connect' })).toHaveLength(2));
    // Claude's sign-in cannot start: said in the card, and nothing goes on
    fireEvent.click(within(card()).getAllByRole('button', { name: 'Connect' })[0]!);
    expect(await within(card()).findByText('No computer can sign in')).toBeTruthy();
    expect(log().queryByText(/Where should I reach you/)).toBeNull();
    // a wrong key is refused in the server's words; the right one connects
    fireEvent.click(within(card()).getByRole('button', { name: 'API key' }));
    const key = within(card()).getByLabelText('Key') as HTMLInputElement;
    expect(key.type).toBe('password');
    fireEvent.change(key, { target: { value: 'not-a-key' } });
    fireEvent.click(within(card()).getAllByRole('button', { name: 'Connect' }).at(-1)!);
    expect((await within(card()).findByRole('alert')).textContent).toBe('An Anthropic API key starts with sk-ant-');
    fireEvent.change(within(card()).getByLabelText('Key'), { target: { value: 'sk-ant-made-up' } });
    fireEvent.click(within(card()).getAllByRole('button', { name: 'Connect' }).at(-1)!);
    expect(await log().findByText(said(/^Your plan runs the agents\./))).toBeTruthy();
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('sk-ant-made-up');
    // no file, no one to invite
    expect(log().queryByText(/Invite/)).toBeNull();

    fireEvent.click(await log().findByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(sent('POST', '/api/v2/assistant/messages')).toHaveLength(1));
    expect(sent('POST', '/api/v2/assistant/messages')[0]!.body).toMatchObject({ text: 'Set up my company.' });
    // the starting team: the first job setup offers, the rest off; Tico asks the rest, so no agent is pinged
    expect(sent('PUT', '/api/v2/setup')[0]!.body).toMatchObject({ answers: { first_job: 'grow', teams_off: ['Product'] }, selected: {} });
    expect(sent('POST', '/api/v2/setup/complete')).toHaveLength(1);
    expect(sent('POST', '/api/v2/flow/ping')).toHaveLength(0);
    expect(renderHook(() => useSetupTranscript()).result.current.some((m) => /You're live/.test(m.body ?? ''))).toBe(false);
  });

  it('a finish that fails says so, keeps the chat, and goes again on Try again', async () => {
    serve(FRESH);
    answers['POST /api/v2/setup/complete'] = refusal(409, 'busy', 'Setup is busy');
    show();
    await screen.findByRole('log', { name: 'Setup with Tico' });
    fireEvent.click(await log().findByRole('button', { name: 'Phone' }));
    expect(await log().findByText(/on your phone and add it to your home screen\./)).toBeTruthy();
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(renderHook(() => useSetupTranscript()).result.current).toEqual([]);
    answers['POST /api/v2/setup/complete'] = { body: { ...FRESH, completed: 'now', needed: false } };
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(sent('POST', '/api/v2/assistant/messages')).toHaveLength(1));
  });
});

describe('Who sees setup', () => {
  it('only the owner of a company that owes it', () => {
    const owed = () => renderHook(() => useSetupOwed(), { wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider> }).result.current;
    expect(owed()).toBe(true);
    queryClient.setQueryData(['me'], { id: 'human:pat', email: '', role: 'person', slug: null });
    expect(owed()).toBe(false);
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    queryClient.setQueryData(configQuery.queryKey, { company_name: 'Acme', onboarding_needed: false } as never);
    expect(owed()).toBe(false);
  });
});
