import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data';
import { FirstRun } from './FirstRun';
import { landing, type SetupRecord } from './queries';

/* Tier 2 check of the Start track: where an owner who owes the first run lands, and the three steps by hand.
   Made-up data only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

let calls: { method: string; url: string; body?: unknown }[] = [];
function serve(reply: (method: string, url: string) => unknown) {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify(reply(method, url) ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}
const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split('?')[0] === path);

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const RECORD: SetupRecord = {
  names: { company_name: '', app_name: '', assistant_name: '', owner_name: '', team_domain: '', website: '' },
  answers: {},
  selected: {},
  completed: null,
  needed: true,
  default_chart: [
    { team: 'Engineering', on: true },
    { team: 'Support', on: false },
    { team: 'Sales', on: false },
    { team: 'Founders', on: false, locked: true },
  ],
  first_jobs: {
    choices: [
      { key: 'build', label: 'Build the app', seat: 'eng', team: 'Engineering' },
      { key: 'support', label: 'Answer customers', seat: 'sup', team: 'Support' },
    ],
    picked: 'build',
  },
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  calls = [];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
});
afterEach(() => vi.unstubAllGlobals());

describe('Setup, first run @s1154', () => {
  it('an owner the server says still owes the first run lands with it open; others land plain', async () => {
    serve(() => ({}));
    queryClient.setQueryData(['config'], { onboarding_needed: true });
    expect(await landing(queryClient)).toEqual({ setup: true });
    queryClient.setQueryData(['config'], { onboarding_needed: false });
    expect(await landing(queryClient)).toEqual({ setup: false });
  });

  it('not signed in, it leaves the answer to the sign-in gate', async () => {
    serve(() => ({}));
    queryClient.removeQueries({ queryKey: ['me'] });
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ error: { code: 'signin', detail: 'Sign in' } }), { status: 401, headers: { 'Content-Type': 'application/json' } }));
    expect(await landing(queryClient)).toBeNull();
    expect(queryClient.getQueryState(['me'])?.error).toBeInstanceOf(ApiError);
  });
});

describe('Setup dialog, three steps @s1171', () => {
  it('step 1 saves the company name and moves to step 2', async () => {
    serve((method, url) => {
      if (url.startsWith('/api/v2/model-subscriptions')) return { subscriptions: [] };
      if (method === 'PUT') return { ...RECORD, names: { ...RECORD.names, company_name: 'Fernhill' } };
      return RECORD;
    });
    queryClient.setQueryData(['setup'], RECORD);
    const router = show(<FirstRun />, '/start?setup=name');
    const dialog = await screen.findByRole('dialog', { name: 'Name your company' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Company name' }), { target: { value: ' Fernhill ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect((router.state.location.search as { setup?: string }).setup).toBe('model'));
    expect(sent('PUT', '/api/v2/setup')[0]?.body).toMatchObject({ names: { company_name: 'Fernhill' } });
    const next = await screen.findByRole('dialog', { name: 'Connect a model' });
    expect(within(next).getByRole('img', { name: 'Step 2 of 3' })).toBeTruthy();
  });

  it('step 3 makes the team with the first job picked, then lands on Start with the dialog shut', async () => {
    serve((method) => (method === 'PUT' ? RECORD : {}));
    queryClient.setQueryData(['setup'], RECORD);
    const router = show(<FirstRun />, '/start?setup=team');
    const dialog = await screen.findByRole('dialog', { name: 'Pick a team' });
    expect(within(dialog).getByRole('img', { name: 'Step 3 of 3' })).toBeTruthy();
    expect(within(dialog).getByRole('radio', { name: /Build the app/ }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(within(dialog).getByRole('radio', { name: /Answer customers/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/start'));
    await waitFor(() => expect((router.state.location.search as { setup?: string }).setup).toBeUndefined());
    // the chart as the server laid it out, less the teams off; the picked job's team and a locked one are never off
    expect(sent('PUT', '/api/v2/setup')[0]?.body).toMatchObject({ answers: { first_job: 'support', teams_off: ['Sales'] }, selected: {} });
    expect(sent('POST', '/api/v2/setup/complete')).toHaveLength(1);
  });

  it('a pasted key is a password field held in the step only, never in the cache', async () => {
    serve((_method, url) => (url.startsWith('/api/v2/model-subscriptions') ? { subscriptions: [] } : {}));
    show(<FirstRun />, '/start?setup=paste');
    const dialog = await screen.findByRole('dialog', { name: 'Paste a key' });
    const key = within(dialog).getByLabelText('Key') as HTMLInputElement;
    expect(key.type).toBe('password');
    fireEvent.change(key, { target: { value: 'made-up-secret-123' } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('made-up-secret-123');
  });
});
