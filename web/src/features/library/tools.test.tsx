import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import { FileViewerHost } from './FileViewer';
import { apiToolQuery, editApiTool, editSource, fileQuery, fileVersionsQuery, saveSetting, setKeyExpiry, sourcesQuery } from './queries';
import { RevealDialog } from './ToolDialogs';
import { narrow } from './ToolsPage';
import { viewing } from './viewing';

/* The file viewer, the Tools filter, and the tool changes: what is sent, and that a shown secret is never kept. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  viewing.set(null);
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

interface Sent {
  url: string;
  method: string;
  body: unknown;
}
function serve(reply: (url: string, method: string) => unknown = () => ({})) {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    return new Response(JSON.stringify(reply(String(url), method)), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}

function at(path: string, node: ReactNode = null) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: path.split('?')[0] ?? '/', component: () => node });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

// Made-up file and tools only.
const file = (o: Record<string, unknown>) => ({ id: 'f1', title: 'Sample notes', name: 'notes.txt', version: 1, library_kind: 'doc', mime: 'text/plain', bot: 'helper', ...o });

describe('the file viewer', () => {
  it('opens a file over the page in a dialog from files.view, and closes it @s75 @s198', async () => {
    serve(() => 'plain words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file({}) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, []);
    at('/tasks', <FileViewerHost />);
    expect(screen.queryByRole('dialog')).toBeNull();
    await act(async () => {
      expect((await run('files.view', { file: 'f1' }, { via: 'click' })).ok).toBe(true);
    });
    expect(await screen.findByRole('dialog', { name: 'Sample notes' })).toBeTruthy();
    expect(await screen.findByText('plain words')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open' })).toBeTruthy();
    await act(async () => void (await run('files.view', { file: null }, { via: 'click' })));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Sample notes' })).toBeNull());
  });

  it('shows a picture full size, not in the dialog @s77', async () => {
    serve();
    queryClient.setQueryData(fileQuery('f2').queryKey, file({ id: 'f2', title: 'Shelf photo', name: 'shelf.png', mime: 'image/png', library_kind: 'image' }) as never);
    queryClient.setQueryData(fileVersionsQuery('f2').queryKey, []);
    at('/tasks', <FileViewerHost />);
    act(() => viewing.set({ file: 'f2' }));
    expect(((await screen.findByAltText('Shelf photo')) as HTMLImageElement).getAttribute('src')).toBe('/api/v2/files/f2/versions/1');
    expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
  });
});

describe('the Tools filter', () => {
  const row = (id: string, kind: string, needs = false) =>
    ({ id, kind, name: id, needs_you: needs, status: { tone: needs ? 'fail' : 'ok', text: needs ? 'Key expired' : 'Working' } }) as unknown as ToolIndexRow;
  const rows = [row('api:1', 'api'), row('api:2', 'api', true), row('website:1', 'website')];

  it('keeps one kind, or only the tools that need someone', () => {
    expect(narrow(rows, {}).map((r) => r.id)).toEqual(['api:1', 'api:2', 'website:1']);
    expect(narrow(rows, { kind: 'website' }).map((r) => r.id)).toEqual(['website:1']);
    expect(narrow(rows, { needs: 1 }).map((r) => r.id)).toEqual(['api:2']);
    expect(narrow(rows, { kind: 'website', needs: 1 })).toEqual([]);
  });

  it('puts the filter in the address @s908', async () => {
    serve();
    const router = at('/library/tools');
    await act(async () => void (await run('tools.filter', { kind: 'api', needs: true }, { via: 'click' })));
    expect(router.state.location.search).toMatchObject({ kind: 'api', needs: 1 });
  });
});

describe('tool changes', () => {
  it('sends a run setting, a key end date and an edit to the tool', async () => {
    const sent = serve();
    queryClient.setQueryData(apiToolQuery('t1').queryKey, { tool: { id: 't1', name: 'Sample API' }, keys: [{ id: 'k1', expires_on: null }], settings: [] } as never);
    const settled = [
      saveSetting('t1', { name: 'ACCOUNT_ID', value: '1234' }),
      saveSetting('t1', { id: 's1', name: 'REGION', value: 'us' }),
      setKeyExpiry('t1', 'k1', '2027-01-31'),
      editApiTool('t1', { name: 'Sample API 2' }),
    ];
    expect((queryClient.getQueryData(apiToolQuery('t1').queryKey) as { keys: Array<{ expires_on: string }> }).keys[0]?.expires_on).toBe('2027-01-31');
    await Promise.all(settled);
    const writes = sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => [method, url, body]);
    expect(writes).toEqual([
      ['POST', '/api/v2/api-tools/t1/settings', { name: 'ACCOUNT_ID', value: '1234' }],
      ['PATCH', '/api/v2/api-tools/t1/settings/s1', { name: 'REGION', value: 'us' }],
      ['PATCH', '/api/v2/api-tools/t1/keys/k1', { expires_on: '2027-01-31' }],
      ['PATCH', '/api/v2/api-tools/t1', { name: 'Sample API 2' }],
    ]);
  });

  it('takes a removed source off the list at once', async () => {
    const sent = serve();
    queryClient.setQueryData(sourcesQuery.queryKey, { next_cursor: null, sources: [{ id: 'src1', title: 'Handbook' }] } as never);
    const done = editSource('src1', { archived: true });
    expect(queryClient.getQueryData<{ sources: unknown[] }>(sourcesQuery.queryKey)?.sources).toEqual([]);
    await done;
    expect(sent.find((x) => x.method === 'PATCH')).toMatchObject({ url: '/api/v2/doc-sources/src1', body: { archived: true } });
  });
});

describe('a shown secret', () => {
  const FAKE = 'sk-test-0000-fake';

  it('lands in the dialog only: not the query cache @s840', async () => {
    const sent = serve((url) => (url.endsWith('/reveal') ? { value: FAKE } : {}));
    at('/library/tools', <RevealDialog credential="cred1" title="Main key" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show' }));
    expect(await screen.findByText(FAKE)).toBeTruthy();
    expect(sent.map((x) => [x.method, x.url])).toContainEqual(['POST', '/api/v2/credentials/cred1/reveal']);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(FAKE);
    expect(JSON.stringify(queryClient.getMutationCache().getAll().map((m) => m.state.data))).not.toContain(FAKE);
  });

  it('is never Tico’s to ask for', async () => {
    const sent = serve(() => ({ value: FAKE }));
    at('/library/tools');
    expect(await run('tools.reveal', { credential: 'cred1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.signInCode', { tool: 'w1', account: 'a1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});
