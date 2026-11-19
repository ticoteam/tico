import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { accessQuery, activityQuery, changelogQuery, directorySyncQuery, type AccessView } from './queries';
import { SettingsPage } from './SettingsPage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const LIST = { can_add: false, can_review: false, current_version: '0.0.1', drafts: [], next_offset: null, unread_count: 0 };
const ENTRY = { area: 'tasks', bullets: [], kind: 'activity' as const, shipped_at: '2026-01-02T00:00:00Z' };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings › Changelog', () => {
  it("an agent's line opens its task's outcome over the page", async () => {
    serve((url) =>
      url.startsWith('/api/v2/tasks/t9')
        ? { status: 200, body: { task: { id: 't9', title: 'Sample task', note: 'It **shipped**.' }, runs: [], children: [] } }
        : { status: 200, body: {} },
    );
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, entries: [] });
    queryClient.setQueryData(activityQuery.queryKey, {
      ...LIST,
      entries: [
        { ...ENTRY, id: 'e1', title: 'Shipped a sample', task_id: 't9' },
        { ...ENTRY, id: 'e2', title: 'A line with no task' },
      ],
    });
    const router = show(<SettingsPage section="changelog" />, '/settings/changelog');
    fireEvent.click(await screen.findByRole('button', { name: /Agent activity/ }));
    expect(await screen.findAllByRole('button', { name: 'View outcome' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'View outcome' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ open: 't9' }));
  });

  it('the outcome shows the task and its note', async () => {
    serve((url) =>
      url.startsWith('/api/v2/tasks/t9')
        ? { status: 200, body: { task: { id: 't9', title: 'Sample task', note: 'It **shipped**.' }, runs: [], children: [] } }
        : { status: 200, body: {} },
    );
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, entries: [] });
    queryClient.setQueryData(activityQuery.queryKey, { ...LIST, entries: [] });
    show(<SettingsPage section="changelog" open="t9" />, '/settings/changelog?open=t9');
    expect(await screen.findByText('Reported task outcome')).toBeTruthy();
    expect(await screen.findByText('Sample task')).toBeTruthy();
    expect((await screen.findByText('shipped')).tagName).toBe('B');
  });
});

const ACCESS: AccessView = {
  owner: { email: 'owner@example.test', person: 'p1' },
  people: [{ id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true }],
  allowed: [],
  allowed_domains: [],
  revision: 1,
  rules: {},
  you: 'owner',
  invites: {},
  invite_email: true,
};

const SYNC = {
  source: 'scim' as const,
  filter: { groups: [], org_units: [], domains: [] },
  interval_minutes: 360,
  mass_leave_limit: 10,
  revision: 4,
  last: {},
  confirmed: true,
  credentials: { google: { configured: false, hint: '' }, entra: { configured: false, hint: '' } },
  scim: { enabled: true, created_at: '2026-01-01T00:00:00Z', url: 'https://tico.example.test/scim/v2' },
};

describe('Settings › People › Directory sync', () => {
  it('a new SCIM token shows once, in its dialog, and is kept nowhere else', async () => {
    serve((url, method) =>
      method === 'POST' && url === '/api/v2/directory/scim-token' ? { status: 200, body: { token: 'scim-sample-0000' } } : { status: 200, body: SYNC },
    );
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(directorySyncQuery.queryKey, SYNC);
    show(<SettingsPage section="people" />, '/settings/people');
    fireEvent.click(await screen.findByRole('button', { name: 'Replace token' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Replace' }));
    expect(await screen.findByText('scim-sample-0000')).toBeTruthy();
    // Not in the query cache, not in a toast, not in the address.
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('scim-sample-0000');
    expect(document.querySelector('.toast')?.textContent ?? '').not.toContain('scim-sample-0000');
    expect(window.location.href).not.toContain('scim-sample-0000');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByText('scim-sample-0000')).toBeNull());
  });

  it('Sync now previews first, and a first sync applies only once the list is checked', async () => {
    const plan = { hash: 'h1', adds: [{ email: 'new@example.test' }], updates: [], restores: [], leaves: [], protected: [], skipped: [] };
    serve((url) => {
      if (url === '/api/v2/directory/preview') return { status: 200, body: { plan, needs_confirmation: { first: true }, mass_leave_limit: 10 } };
      if (url === '/api/v2/directory/sync') return { status: 200, body: { plan, needs_confirmation: {}, mass_leave_limit: 10, applied: true } };
      return { status: 200, body: SYNC };
    });
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(directorySyncQuery.queryKey, SYNC);
    show(<SettingsPage section="people" />, '/settings/people');
    fireEvent.click(await screen.findByRole('button', { name: 'Sync now' }));
    const apply = await screen.findByRole('button', { name: 'Apply' });
    expect(apply.hasAttribute('disabled') || apply.getAttribute('aria-disabled') === 'true').toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'I checked this list' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/directory/sync', body: { confirm: true, plan_hash: 'h1' } }));
  });
});
