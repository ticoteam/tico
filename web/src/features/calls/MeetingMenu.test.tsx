import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GranolaStatus } from '@/api/schema';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { MeetingMenu } from './MeetingMenu';
import { granolaQuery, meetingSettingsQuery } from './queries';

vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => <span className="lvl" />,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const granola = (p: Partial<GranolaStatus> = {}): GranolaStatus => ({
  connected: false,
  email: null,
  imported_count: 0,
  last_error: null,
  last_error_detail: null,
  last_synced_at: null,
  mode: 'off',
  needs_signin: false,
  plan_hint: null,
  skip_reasons: {},
  skipped: 0,
  syncing: false,
  ...p,
});

const CODE = 'WXYZ-4821';
const sent: { method: string; url: string; body: unknown }[] = [];
let answer: (method: string, url: string) => unknown = () => ({});

beforeEach(() => {
  queryClient.clear();
  sent.length = 0;
  answer = () => ({});
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: { 'human:ana': { id: 'human:ana', kind: 'person', name: 'Ana Reyes' } } });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ method, url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(answer(method, String(url))), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show() {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const page = createRoute({ getParentRoute: () => root, path: '/calendar', component: () => <MeetingMenu /> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ['/calendar?view=list'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

async function pick(label: string | RegExp) {
  fireEvent.click(await screen.findByRole('button', { name: 'More' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: label }));
}

describe('List’s ⋯', () => {
  it('logs a meeting with notes, shared, then opens it', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, granola());
    answer = (m) => (m === 'POST' ? { id: 'rec1' } : {});
    const router = show();
    await pick('Log a meeting');
    const go = await screen.findByRole('button', { name: 'Log meeting' });
    expect(go.hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Pricing review' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'Raise the floor' } });
    fireEvent.click(go);
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]?.url).toBe('/api/v2/meetings/import');
    expect(sent[0]?.body).toMatchObject({ title: 'Pricing review', notes: 'Raise the floor', source: 'manual', review: 'live' });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ view: 'list', panel: 'meeting:rec1' }));
  });

  it('logs a transcript from a file, titled from its name', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, granola());
    answer = (m) => (m === 'POST' ? { id: 'rec2' } : {});
    show();
    await pick('Log a meeting');
    const file = new File(['Ana: we ship Friday'], 'weekly_sync.vtt', { type: 'text/vtt' });
    fireEvent.change(await screen.findByLabelText('Transcript file'), { target: { files: [file] } });
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Title' }) as HTMLInputElement).value).toBe('weekly sync'));
    fireEvent.click(screen.getByRole('button', { name: 'Log meeting' }));
    await waitFor(() => expect(sent[0]?.body).toMatchObject({ title: 'weekly sync', transcript: 'Ana: we ship Friday', source: 'upload' }));
  });

  it('connects Granola with a code that stays on the dialog', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, granola());
    let polled = 0;
    answer = (m, url) => {
      if (m === 'POST' && url.endsWith('/granola/connect')) return { user_code: CODE, interval: 0.1, expires_in: 600, verification_uri: 'https://app.granola.ai/device' };
      if (url.endsWith('/connect/status')) return (polled += 1) < 2 ? { state: 'pending' } : { ...granola({ connected: true }), state: 'connected' };
      return granola({ connected: polled >= 2 });
    };
    show();
    await pick('Connect Granola');
    expect(await screen.findByText(CODE)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Granola' }).getAttribute('href')).toBe('https://app.granola.ai/device');
    await waitFor(() => expect(screen.queryByText(CODE)).toBeNull(), { timeout: 2000 });
    // the code never reached the cache, the address or a toast
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(CODE);
    expect(window.location.href).not.toContain(CODE);
    expect(sent.map((x) => x.url)).toEqual(['/api/v2/meetings/granola/connect']);
  });

  it('shows a sign-in link on another site as its host only', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, granola());
    answer = (m, url) =>
      m === 'POST' && url.endsWith('/granola/connect')
        ? { user_code: CODE, interval: 60, expires_in: 600, verification_uri: 'https://elsewhere.example/device' }
        : { state: 'pending' };
    show();
    await pick('Connect Granola');
    expect(await screen.findByText('elsewhere.example')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Open Granola' })).toBeNull();
  });

  it('lists Granola in Meeting sources: sync now, remove', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, granola({ connected: true, mode: 'account', email: 'ana@example.invalid', imported_count: 3 }));
    let removed = false;
    answer = (m) => {
      if (m === 'POST') return { state: 'syncing', last_synced_at: null };
      if (m === 'DELETE') {
        removed = true;
        return { ok: true };
      }
      return granola({ connected: !removed, imported_count: 3 });
    };
    show();
    await pick(/^Meeting sources/);
    expect(await screen.findByText('Connected')).toBeTruthy();
    expect(screen.getByText('3 notes')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
    await waitFor(() => expect(sent.map((x) => `${x.method} ${x.url}`)).toEqual(['POST /api/v2/meetings/granola/sync?now=true']));
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: 'DELETE', url: '/api/v2/meetings/granola/connect' }));
    expect(await screen.findByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('saves who sees imported meetings, and the company default for an owner', async () => {
    queryClient.setQueryData(granolaQuery.queryKey, granola({ connected: true }));
    queryClient.setQueryData(meetingSettingsQuery.queryKey, { auto_share: true, effective_auto_share: true, review_default: 'review' });
    answer = () => ({ auto_share: true, effective_auto_share: true, review_default: 'review' });
    show();
    expect(screen.queryByRole('menuitem', { name: 'Connect Granola' })).toBeNull();
    await pick('Calendar settings');
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/meetings/settings', body: { auto_share: true, review_default: 'review' } }));
  });
});
