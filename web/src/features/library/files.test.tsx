import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { FilePreview, FileViewerHost } from './FileViewer';
import { FilesPage } from './FilesPage';
import { fileQuery, filesQuery, fileVersionsQuery } from './queries';
import { viewing } from './viewing';

/* Files: the list, one file and its versions, and a file opened from a task or a chat. What a person sees and presses,
   and what is sent. Made-up files only. */

// jsdom lays nothing out, so the virtual list would draw no rows: here it draws them all (its own tests cover the rest).
vi.mock('@/kit/VirtualList', () => ({
  VirtualList: <T,>({ items, getKey, renderItem, label }: { items: readonly T[]; getKey: (t: T) => string; renderItem: (t: T, i: number) => ReactNode; label?: string }) => (
    <div role="list" aria-label={label}>
      {items.map((t, i) => (
        <div key={getKey(t)}>{renderItem(t, i)}</div>
      ))}
    </div>
  ),
}));
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
    const r = reply(String(url), method);
    if (r instanceof Response) return r;
    return new Response(JSON.stringify(r), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}

function holdResponse() {
  let send: (response: Response) => void = () => {};
  const response = new Promise<Response>((resolve) => {
    send = resolve;
  });
  return { response, send };
}

function at(path: string, node: ReactNode = null) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: path.split('?')[0] ?? '/', component: () => node });
  const any = createRoute({ getParentRoute: () => root, path: '$', component: () => null });
  const router = createRouter({ routeTree: root.addChildren([page, any]), history: createMemoryHistory({ initialEntries: [path] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const linkOf = (name: string) => screen.getByRole('link', { name }).getAttribute('href');

const file = (o: Record<string, unknown> = {}) => ({
  id: 'f1',
  title: 'Sample notes',
  name: 'notes.txt',
  version: 2,
  kind: 'file',
  library_kind: 'document',
  mime: 'text/plain',
  bot: 'helper',
  last_activity_at: '2026-11-01T00:00:00Z',
  locator: 'blob',
  open: null,
  ...o,
});
const version = (n: number, current = false) => ({ version: n, current, name: 'notes.txt', size: 12, mime: 'text/plain', actor: null, created_at: '2026-11-01T00:00:00Z', url: '' });

describe('a file', () => {
  it('shows its text, Download and its versions, each a link @s52', async () => {
    serve(() => 'plain words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/library/files?file=f1', <FilesPage search={{ file: 'f1' }} />);
    expect(await screen.findByText('plain words')).toBeTruthy();
    expect(linkOf('Download')).toBe('/api/v2/files/f1/versions/2');
    expect(screen.getByRole('link', { name: /v1/ }).getAttribute('href')).toBe('/library/files?file=f1&v=1');
  });

  it('opens a linked file where it lives, with no Download @s52', async () => {
    const url = 'https://docs.google.com/document/d/abcdefghijk123/edit';
    serve();
    queryClient.setQueryData(fileQuery('f1').queryKey, file({ locator: 'remote_link', open: { type: 'external', url } }) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, []);
    at('/library/files?file=f1', <FilesPage search={{ file: 'f1' }} />);
    expect((await screen.findByRole('link', { name: 'Open in Google' })).getAttribute('href')).toBe(url);
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
    expect(document.querySelector('iframe')?.getAttribute('src')).toBe('https://docs.google.com/document/d/abcdefghijk123/preview');
  });

  it('draws the version asked for, as a task shows it @s76', async () => {
    const sent = serve((url) => (url.endsWith('/versions/1') ? 'first words' : 'later words'));
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/tasks/7', <FilePreview file="f1" version={1} />);
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(sent.map((x) => x.url)).toEqual(['/api/v2/files/f1/versions/1']);
  });

  it('uses the historical version’s file type instead of the latest one', async () => {
    serve(() => 'first words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file({ mime: 'image/png', library_kind: 'image', name: 'notes.png' }) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [
      { ...version(2, true), mime: 'image/png', name: 'notes.png' },
      version(1),
    ]);
    at('/library/files?file=f1&v=1', <FilesPage search={{ file: 'f1', v: 1 }} />);
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Sample notes' })).toBeNull();
  });

  it('keeps quoted CSV data in its original cells', async () => {
    const csv = '\uFEFFName,Notes\r\n"Acme, Inc.","First line\r\nSecond ""quoted"" line"';
    serve(() => new Response(csv, { headers: { 'Content-Type': 'text/csv' } }));
    queryClient.setQueryData(fileQuery('f1').queryKey, file({ mime: 'text/csv', name: 'notes.csv' }) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [{ ...version(2, true), mime: 'text/csv', name: 'notes.csv' }]);
    at('/library/files?file=f1', <FilesPage search={{ file: 'f1' }} />);
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    expect(within(table).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['Acme, Inc.', 'First line\r\nSecond "quoted" line']);
  });

  it('keeps the desktop Files filters when opening an older version', async () => {
    serve(() => 'plain words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/library/files?file=f1&kind=document&q=notes', <FilesPage search={{ file: 'f1', kind: 'document', q: 'notes' }} />);
    const link = await screen.findByRole('link', { name: /v1/ });
    expect(link.getAttribute('href')).toBe('/library/files?kind=document&q=notes&file=f1&v=1');
  });

  it('waits for the selected file version before previewing or offering a download', async () => {
    const ready = holdResponse();
    const requests: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      requests.push(url);
      if (url.endsWith('/versions')) return ready.response;
      return Response.json(url.endsWith('/versions/1') ? 'first words' : 'later words');
    });
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    at('/library/files?file=f1&v=1', <FilesPage search={{ file: 'f1', v: 1 }} />);
    await screen.findByRole('status', { name: 'Loading' });
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
    expect(requests).not.toContain('/api/v2/files/f1/versions/2');

    ready.send(Response.json({ versions: [version(2, true), version(1)] }));
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(linkOf('Download')).toBe('/api/v2/files/f1/versions/1');
    expect(requests).not.toContain('/api/v2/files/f1/versions/2');
  });

  it('offers download and lets a phone open an older file version without clearing the filters', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => 'plain words');
      queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
      queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
      const router = at('/library/files?file=f1&kind=document', <FilesPage search={{ file: 'f1', kind: 'document' }} />);
      expect((await screen.findByRole('link', { name: 'Download' })).hasAttribute('data-phone')).toBe(true);
      fireEvent.click(screen.getByRole('button', { name: 'Versions' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: /^v1/ }));
      await waitFor(() => expect(router.state.location.search).toMatchObject({ file: 'f1', kind: 'document', v: 1 }));
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });
});

describe('Files', () => {
  it('searches a filtered list without fetching its unfiltered rows and lets a failed search retry', async () => {
    let searches = 0;
    const sent = serve((url) => {
      if (url === '/api/v2/files/search?q=notes&limit=50&kind=document') {
        if (++searches === 1) return new Response(null, { status: 500 });
        return { results: [{ id: 'f1', title: 'Sample notes' }] };
      }
      return {};
    });
    at('/library/files?kind=document&q=notes', <FilesPage search={{ kind: 'document', q: 'notes' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: 'Sample notes' })).toBeTruthy();
    expect(searches).toBe(2);
    expect(sent.some(({ url }) => url.startsWith('/api/v2/files?'))).toBe(false);
  });

  it('lists files newest first by day, each opening in the page, and filters by kind in the address @s779', async () => {
    serve();
    const today = new Date().toISOString();
    queryClient.setQueryData(filesQuery('').queryKey, {
      pages: [
        {
          files: [
            { ...file({ id: 'f1', title: 'Fresh plan' }), last_activity_at: today },
            { ...file({ id: 'f2', title: 'Old price list' }), last_activity_at: '2026-01-02T00:00:00Z' },
          ],
          has_more: false,
          next_cursor: null,
        },
      ],
      pageParams: [''],
    } as never);
    const router = at('/library/files', <FilesPage search={{}} />);
    expect(await screen.findByText('Today')).toBeTruthy();
    expect(screen.getByText('Earlier')).toBeTruthy();
    expect(screen.getByText('Fresh plan').closest('a')?.getAttribute('href')).toBe('/library/files?file=f1');
    fireEvent.click(screen.getByRole('button', { name: 'Filter by kind' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Images' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ kind: 'image' }));
  });

  it('takes files dropped on the page sheet itself, with no wrapper around the sheet', async () => {
    serve();
    queryClient.setQueryData(filesQuery('').queryKey, { pages: [{ files: [file()], has_more: false, next_cursor: null }], pageParams: [''] } as never);
    at('/library/files', <FilesPage search={{}} />);
    const page = await screen.findByRole('main', { name: 'Files' });
    expect(page.parentElement?.classList.contains('lib-drop')).toBe(false);
    fireEvent.drop(page, { dataTransfer: { files: [new File(['x'], 'a.txt')], types: ['Files'] } });
    expect(await screen.findByRole('dialog', { name: 'Add a.txt' })).toBeTruthy();
  });

  it('searches within the selected file kind', async () => {
    const sent = serve(() => ({ results: [{ id: 'f1', title: 'Seed photo' }] }));
    queryClient.setQueryData(filesQuery('image').queryKey, {
      pages: [{ files: [], has_more: false, next_cursor: null }],
      pageParams: [''],
    });
    at('/library/files?kind=image&q=seed', <FilesPage search={{ kind: 'image', q: 'seed' }} />);
    expect(await screen.findByText('Seed photo')).toBeTruthy();
    expect(sent).toContainEqual(expect.objectContaining({ url: '/api/v2/files/search?q=seed&limit=50&kind=image' }));
  });
});

describe('a file from a task or a chat', () => {
  it('opens as its page in Files, never a dialog over the page @s75 @s76', async () => {
    serve();
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    const router = at('/tasks', <FileViewerHost />);
    await act(async () => {
      expect((await run('files.view', { file: 'f1', version: 2 }, { via: 'click' })).ok).toBe(true);
    });
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/files'));
    expect(router.state.location.search).toEqual({ file: 'f1', v: 2 });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('from a chat, any file opens full screen over the page and stays on the page', async () => {
    serve();
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    const router = at('/tasks', <FileViewerHost />);
    await act(async () => void (await run('files.preview', { file: 'f1' }, { via: 'click' })));
    const dialog = await screen.findByRole('dialog', { name: file().title });
    expect(within(dialog).getByRole('link', { name: 'Open in Files' }).getAttribute('href')).toBe('/library/files?file=f1');
    expect(router.state.location.pathname).toBe('/tasks');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it("from a chat, a message's own file opens full screen from its address", async () => {
    serve();
    at('/tasks', <FileViewerHost />);
    const held = { url: '/api/v2/files/a1', mime: 'application/zip', name: 'beds.zip' };
    await act(async () => void (await run('files.preview', { file: 'a1', held }, { via: 'click' })));
    const dialog = await screen.findByRole('dialog', { name: 'beds.zip' });
    expect(within(dialog).getByText('ZIP')).toBeTruthy();
    expect(within(dialog).getAllByRole('link', { name: 'Download' })[0]?.getAttribute('href')).toBe('/api/v2/files/a1');
    expect(within(dialog).queryByRole('link', { name: 'Open in Files' })).toBeNull();
    await act(async () => void (await run('files.preview', { file: null }, { via: 'click' })));
  });

  it('a picture shows full size over the page, and closes @s77', async () => {
    serve();
    queryClient.setQueryData(fileQuery('f2').queryKey, file({ id: 'f2', title: 'Shelf photo', name: 'shelf.png', mime: 'image/png', library_kind: 'image' }) as never);
    const router = at('/tasks', <FileViewerHost />);
    await act(async () => void (await run('files.view', { file: 'f2' }, { via: 'click' })));
    expect(((await screen.findByAltText('Shelf photo')) as HTMLImageElement).getAttribute('src')).toBe('/api/v2/files/f2');
    expect(router.state.location.pathname).toBe('/tasks');
    await act(async () => void (await run('files.view', { file: null }, { via: 'click' })));
    await waitFor(() => expect(screen.queryByAltText('Shelf photo')).toBeNull());
  });
});
