import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import type { DocSource } from '@/api/schema';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import './actions';
import { DocPage, wholeLinks } from './DocPage';
import { DocsPage } from './DocsPage';
import {
  archivedDocsQuery,
  docQuery,
  docRows,
  docsListQuery,
  docVersionQuery,
  docVersionsQuery,
  editSource,
  externalDocAtQuery,
  externalDocQuery,
  skillsQuery,
  sourceDocsQuery,
  sourcesQuery,
} from './queries';
import { SkillsPage } from './SkillsPage';
import { skillsSearch } from './search';

/* Docs: the list, its search and archive, one doc read, typed in and versioned, importing a file, and outside sources
   and their docs. What a person sees and presses, what is sent, and what Tico may not run. Made-up docs and sources only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(sourcesQuery.queryKey, { sources: [], next_cursor: null });
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
/** Each call answers `reply`: a body (`undefined` is `{}`), or a whole Response. */
function serve(reply: (url: string, method: string) => unknown = () => ({})) {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method);
    if (r instanceof Response) return r;
    return new Response(JSON.stringify(r ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => ({ url, method, body }));

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
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
const list = <T,>(docs: T[], next_cursor: string | null = null) => ({ pages: [{ docs, next_cursor }], pageParams: [''] });

const row = (id: string, title: string, path = 'company/notes.md') => ({ id, title, path, locked: false, updated_at: '2026-11-01T00:00:00Z', updated_by_id: '', updated_by_name: 'Sam', version: 3 });
const full = (o: Record<string, unknown> = {}) => ({
  ...row('d1', 'Plan', 'company/plan.md'),
  body: '# Goals\n\nGrow slowly.\n\n## Next\n\nHire.',
  archived: false,
  created_at: '',
  created_by_id: '',
  created_by_name: '',
  ...o,
});

describe('docs list', () => {
  it('windows a long folder in the page scroller', async () => {
    serve();
    queryClient.setQueryData(docsListQuery.queryKey, list(Array.from({ length: 80 }, (_, i) => row(`d${i}`, `Doc ${i}`))));
    const height = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
    const width = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
    Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 340 });
    Object.defineProperty(Element.prototype, 'clientWidth', { configurable: true, get: () => 400 });
    try {
      at('/library/docs', <DocsPage search={{}} />);
      const group = await screen.findByRole('list', { name: 'Company' });
      expect(within(group).getByRole('link', { name: /Doc 0/ })).toBeTruthy();
      expect(within(group).getAllByRole('link').length).toBeLessThan(30);
      const pageBody = group.closest('.pk-body')!;
      pageBody.scrollTop = 2300;
      fireEvent.scroll(pageBody);
      expect(await within(group).findByRole('link', { name: /Doc 79/ })).toBeTruthy();
    } finally {
      if (height) Object.defineProperty(Element.prototype, 'clientHeight', height);
      if (width) Object.defineProperty(Element.prototype, 'clientWidth', width);
    }
  });

  it('loads the next page from Load more at the end of the docs list', async () => {
    const sent = serve(() => ({ docs: [row('d2', 'Another doc')], next_cursor: null }));
    queryClient.setQueryData(docsListQuery.queryKey, {
      pages: [{ docs: [row('d1', 'First doc')], next_cursor: 'next-cursor' }],
      pageParams: [''],
    });
    at('/library/docs', <DocsPage search={{}} />);
    await screen.findByRole('link', { name: /First doc/ });
    expect(sent).not.toContainEqual(expect.objectContaining({ url: '/api/v2/docs?limit=200&cursor=next-cursor' }));
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByRole('link', { name: /Another doc/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: /First doc/ })).toBeTruthy();
    expect(sent).toContainEqual(expect.objectContaining({ url: '/api/v2/docs?limit=200&cursor=next-cursor' }));
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });

  it('names a section as a person reads it, and puts the head’s ⋯ after New doc @s773', async () => {
    serve();
    queryClient.setQueryData(docsListQuery.queryKey, {
      pages: [
        {
          docs: [
            { ...row('d1', 'Fern index'), path: '_greenhouse/index.md' },
            { ...row('d2', 'Watering how-to'), path: 'plant-care/watering.md' },
            { ...row('d3', 'Readme'), path: 'README.md' },
          ],
          next_cursor: null,
        },
      ],
      pageParams: [''],
    });
    at('/library/docs', <DocsPage search={{}} />);
    expect(await screen.findByText('Greenhouse')).toBeTruthy();
    expect(screen.getByText('Plant care')).toBeTruthy();
    expect(screen.queryByText('_greenhouse')).toBeNull();
    expect(screen.queryByText('README.md')).toBeNull();
    const add = screen.getByRole('button', { name: 'New doc' });
    const more = screen.getByRole('button', { name: 'More' });
    expect(add.compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('files a doc under the folders below its section, never its own file name', async () => {
    serve();
    queryClient.setQueryData(docsListQuery.queryKey, {
      pages: [
        {
          docs: [
            { ...row('d1', 'Glossary'), path: 'plant-care/glossary.md' },
            { ...row('d2', 'Repotting'), path: 'plant-care/how-to/repotting.md' },
          ],
          next_cursor: null,
        },
      ],
      pageParams: [''],
    });
    at('/library/docs', <DocsPage search={{}} />);
    const glossary = (await screen.findByText('Glossary')).closest('.drow') as HTMLElement;
    expect(glossary.querySelector('.tie')?.textContent).toBe('');
    expect(screen.queryByText('glossary.md')).toBeNull();
    const repotting = screen.getByText('Repotting').closest('.drow') as HTMLElement;
    expect(repotting.querySelector('.tie')?.textContent).toBe('How to');
  });

  it('puts the owner’s face in the head, opening their chat; a phone’s head keeps only search, the filled button and ⋯', async () => {
    serve();
    queryClient.setQueryData(['settings', 'page-owners'], { docs: { bot: 'fern-librarian' }, skills: { bot: 'fern-ops' } });
    queryClient.setQueryData(docsListQuery.queryKey, list([row('d1', 'Fern index')]));
    at('/library/docs', <DocsPage search={{}} />);
    const face = await screen.findByRole('link', { name: /^Chat with/ });
    expect(face.closest('[data-phone]')).toBeNull();
    expect(face.getAttribute('href')).toContain('chat=');
    cleanup();
    queryClient.setQueryData(skillsQuery.queryKey, []);
    at('/library/skills', <SkillsPage search={skillsSearch({})} />);
    expect((await screen.findByRole('link', { name: /^Chat with/ })).closest('[data-phone]')).toBeNull();
  });
});

describe('Docs search', () => {
  it('searches the docs’ text on the server and opens each hit where it lives @s773', async () => {
    const sent = serve((url) =>
      url.startsWith('/api/v2/docs/search')
        ? {
            results: [
              { type: 'internal', id: 'd9', title: 'Watering schedule', path: 'garden/watering.md', excerpt: 'twice a week', score: 2 },
              { type: 'external', id: 'x1', title: 'Soil guide', path: 'soil.md', source_title: 'Garden wiki', citation_url: 'https://example.com/soil', excerpt: '', score: 1 },
            ],
          }
        : {},
    );
    queryClient.setQueryData(docsListQuery.queryKey, list([row('d1', 'Plan', 'company/plan.md')]));
    at('/library/docs?q=water', <DocsPage search={{ q: 'water' }} />);
    expect((await screen.findByText('Watering schedule')).closest('a')?.getAttribute('href')).toBe('/library/d9');
    expect(screen.getByText('Soil guide').closest('a')?.getAttribute('href')).toBe('/library/ext:x1');
    expect(screen.getByText('Garden wiki')).toBeTruthy();
    expect(sent.some((x) => x.url.startsWith('/api/v2/docs/search?q=water'))).toBe(true);
  });

  it('keeps matching titles in the archive, without the server search @s773', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, list([]));
    queryClient.setQueryData(archivedDocsQuery.queryKey, list([row('d2', 'Old plan', 'company/old.md')]));
    at('/library/docs?q=old&archived=1', <DocsPage search={{ q: 'old', archived: 1 }} />);
    expect(await screen.findByText('Old plan')).toBeTruthy();
    expect(sent.some((x) => x.url.includes('/docs/search'))).toBe(false);
  });

  it('searches document bodies for words written without ASCII letters', async () => {
    const sent = serve((url) =>
      url.startsWith('/api/v2/docs/search')
        ? { results: [{ type: 'internal', id: 'd1', title: 'The garden', path: 'garden.md', excerpt: '庭園', score: 1 }] }
        : {},
    );
    queryClient.setQueryData(docsListQuery.queryKey, list([row('d1', 'The garden', 'garden.md')]));
    at('/library/docs?q=庭園', <DocsPage search={{ q: '庭園' }} />);
    expect(await screen.findByText('The garden')).toBeTruthy();
    expect(sent.some((x) => x.url.startsWith('/api/v2/docs/search?q=' + encodeURIComponent('庭園')))).toBe(true);
  });
});

describe('archived docs', () => {
  it('lists archived docs from their own read, each with Restore @s791', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, list([row('d1', 'Live doc')]));
    queryClient.setQueryData(archivedDocsQuery.queryKey, list([row('d2', 'Old plan')]));
    at('/library/docs?archived=1', <DocsPage search={{ archived: 1 }} />);
    expect(await screen.findByText('Old plan')).toBeTruthy();
    expect(screen.queryByText('Live doc')).toBeNull();
    // the head's ⋯ comes first, the row's after it
    const more = screen.getAllByRole('button', { name: 'More' });
    fireEvent.click(more[more.length - 1] as HTMLElement);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Restore' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d2', method: 'PATCH', body: { version: 3, archived: false } }]));
    await waitFor(() => expect(screen.queryByText('Old plan')).toBeNull());
  });

  it('archives a doc: off the list at once, and sent with its version @s791', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, list([row('d1', 'Live doc')]));
    at('/library/d1');
    const done = run('docs.archive', { doc: 'd1', version: 3, archived: true }, { via: 'click' });
    await waitFor(() => expect(docRows(queryClient.getQueryData(docsListQuery.queryKey))).toEqual([]));
    expect((await done).ok).toBe(true);
    expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, archived: true } }]);
  });

  it('turns the archived view on from the head’s ⋯ @s791', async () => {
    serve(() => ({ docs: [], next_cursor: null }));
    queryClient.setQueryData(docsListQuery.queryKey, list([row('d1', 'Live doc')]));
    const router = at('/library/docs', <DocsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archived' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ archived: 1 }));
  });
});

describe('import a file', () => {
  it('sends the file to the import and opens the new doc @s819', async () => {
    const sent = serve((url) => (url.includes('/docs/import') ? { doc: { ...row('d9', 'Handbook'), body: '', archived: false } } : {}));
    const router = at('/library/docs');
    const file = new File(['# Handbook'], 'handbook.md', { type: 'text/markdown' });
    await act(async () => expect((await run('docs.import', { file }, { via: 'click' })).ok).toBe(true));
    const post = writes(sent)[0];
    expect(post?.url).toBe('/api/v2/docs/import');
    expect((post?.body as FormData).get('file')).toBeInstanceOf(File);
    expect(router.state.location.pathname).toBe('/library/d9');
  });

  it('is never Tico’s: there is no file to hand over @s819', async () => {
    serve();
    at('/library/docs');
    expect(await run('docs.import', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('a doc', () => {
  it('reads with its outline; a locked one is typed in only by an owner, who may unlock it, and Tico may only lock @s774', async () => {
    const sent = serve();
    queryClient.setQueryData(docQuery('d1').queryKey, full({ locked: true }));
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(await screen.findByText('Grow slowly.')).toBeTruthy();
    expect(within(screen.getByRole('navigation', { name: 'Outline' })).getByText('Next')).toBeTruthy();
    expect(screen.queryByLabelText('Text')).toBeNull();
    cleanup();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(await screen.findByLabelText('Text')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Unlock' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, locked: false } }]));
    expect(await run('docs.lock', { doc: 'd1', version: 3, locked: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await run('docs.lock', { doc: 'd1', version: 3, locked: true }, { via: 'tico' })).ok).toBe(true);
  });

  it('shows an old version from the head, and Restore brings it back @s816', async () => {
    const sent = serve(() => ({ doc: full({ version: 4 }) }));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, [
      { version: 3, current: true, actor: null, actor_name: 'Sam', created_at: '2026-11-01T00:00:00Z' },
      { version: 2, current: false, actor: null, actor_name: 'Sam', created_at: '2026-10-01T00:00:00Z' },
    ] as never);
    queryClient.setQueryData(docVersionQuery('d1', 2).queryKey, { version: 2, title: 'Plan', body: 'Older words', actor: null, actor_name: 'Sam', created_at: '2026-10-01T00:00:00Z' } as never);
    at('/library/d1?v=2', <DocPage doc="d1" search={{ v: 2 }} />);
    expect(await screen.findByText('Older words')).toBeTruthy();
    expect(screen.getByRole('button', { name: /v2/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1/restore', method: 'POST', body: { version: 2 } }]));
  });

  it('never shows or edits the current doc when a historical version is loading or fails', async () => {
    const ready = holdResponse();
    vi.stubGlobal('fetch', async () => ready.response);
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1?v=2', <DocPage doc="d1" search={{ v: 2 }} />);
    await screen.findByRole('status', { name: 'Loading' });
    expect(screen.queryByLabelText('Text')).toBeNull();
    expect(screen.queryByText('Grow slowly.')).toBeNull();

    ready.send(Response.json({ error: { code: 'not_found' } }, { status: 404 }));
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.queryByLabelText('Text')).toBeNull();
    expect(screen.queryByText('Grow slowly.')).toBeNull();
  });

  it('a new doc is the same page, empty: naming it in the head makes it with what was typed, and opens it @s775', async () => {
    const sent = serve(() => ({ doc: { ...full(), id: 'd9', version: 1 } }));
    const router = at('/library/new', <DocPage doc="new" search={{}} />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Text' }), { target: { value: 'Two roles.' } });
    fireEvent.click(screen.getByRole('heading', { name: 'Title' }));
    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Hiring plan' } });
    fireEvent.keyDown(title, { key: 'Enter' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/d9'));
    expect(writes(sent)).toEqual([{ url: '/api/v2/docs', method: 'POST', body: { title: 'Hiring plan', body: 'Two roles.', path: null } }]);
  });
});

describe('a doc', () => {
  it('renames from its title in the head, sending only the title and the version it read @s815', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { doc: full({ title: 'Plan B', version: 4 }) } : { docs: [], next_cursor: null }));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    fireEvent.click(await screen.findByRole('heading', { name: 'Plan' }));
    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Plan B' } });
    fireEvent.keyDown(title, { key: 'Enter' });
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]).toMatchObject({ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, title: 'Plan B' } });
  });

  it('is typed in place, with no edit mode, and saves on leaving the text @s815', async () => {
    // the write reads the doc again after: the saved text
    const sent = serve((url) => (url === '/api/v2/docs/d1' ? { doc: full({ body: 'Grow fast.', version: 4 }) } : { docs: [], next_cursor: null, versions: [] }));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    fireEvent.focus(await screen.findByLabelText('Text'));
    const text = await screen.findByRole('textbox', { name: 'Text' });
    fireEvent.change(text, { target: { value: 'Grow fast.' } });
    fireEvent.blur(text);
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]).toMatchObject({ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, body: 'Grow fast.' } });
    expect(await screen.findByText('Grow fast.')).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Text' })).toBeNull();
  });
});

describe('editing a doc', () => {
  it('can open the docs list after saving a doc from a direct link', async () => {
    const saved = { ...row('d1', 'Renamed notes'), body: 'Notes', archived: false, created_at: '', created_by_id: '', created_by_name: '', version: 4 };
    serve((_url, method) => (method === 'PATCH' ? { doc: saved } : { docs: [saved], next_cursor: null }));
    queryClient.setQueryData(docQuery('d1').queryKey, { ...saved, title: 'Notes', version: 3 });
    at('/library/d1');
    expect((await run('docs.save', { doc: 'd1', version: 3, title: saved.title }, { via: 'click' })).ok).toBe(true);
    const loaded = await queryClient.ensureInfiniteQueryData(docsListQuery);
    expect(docRows(loaded).map((d) => d.title)).toEqual([saved.title]);
  });

  it('keeps what the person typed when the save is refused, and sends the version they started from again @s815', async () => {
    const notes = full({ title: 'Notes', path: 'company/notes.md', body: 'Old text' });
    const patches: unknown[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        patches.push(JSON.parse(String(init.body)));
        return Response.json({ error: { code: 'doc_version_conflict', detail: 'Changed meanwhile' } }, { status: 409 });
      }
      // the conflict reads the doc again: someone else's newer text
      if (String(url) === '/api/v2/docs/d1') return Response.json({ doc: { ...notes, version: 4, body: 'Their text' } });
      return Response.json({ docs: [], next_cursor: null, versions: [] });
    });
    queryClient.setQueryData(docQuery('d1').queryKey, notes);
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    fireEvent.focus(await screen.findByLabelText('Text'));
    const text = await screen.findByRole('textbox', { name: 'Text' });
    fireEvent.change(text, { target: { value: 'My text' } });
    fireEvent.blur(text);
    await waitFor(() => expect(queryClient.getQueryData<{ version: number }>(docQuery('d1').queryKey)?.version).toBe(4));
    expect(screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Text' }).value).toBe('My text');
    fireEvent.blur(screen.getByRole('textbox', { name: 'Text' }));
    await waitFor(() => expect(patches).toHaveLength(2));
    expect(patches).toEqual([
      { version: 3, body: 'My text' },
      { version: 3, body: 'My text' },
    ]);
  });
});

/* ---------------------------------------------------------------- docs: outside sources */

const source: DocSource = {
  id: 'src1',
  title: 'Fern handbook',
  kind: 'github',
  provider: 'github',
  status: 'ready',
  doc_count: 12,
  added_by: 'human:me',
  last_checked_at: '2026-11-18T00:00:00Z',
  url: 'https://github.com/example/handbook',
  description: '',
  reference_override: '',
  repo: 'example/handbook',
  reference: 'main',
  path: '',
  skipped: 0,
  error_code: '',
};

describe('External sources', () => {
  beforeEach(() => {
    queryClient.setQueryData(docsListQuery.queryKey, list([]));
    queryClient.setQueryData(sourcesQuery.queryKey, {
      sources: [source, { ...source, id: 'src2', title: 'Old wiki', kind: 'web', status: 'failed', doc_count: 0, url: 'https://wiki.example.com' }],
      next_cursor: null,
    } as never);
  });

  it('lists each outside source with its docs, says which failed, and opens one beside the list @s776', async () => {
    serve();
    at('/library/docs?tab=sources', <DocsPage search={{ tab: 'sources' }} />);
    const row = (await screen.findByText('Fern handbook')).closest('a') as HTMLElement;
    expect(row.getAttribute('href')).toBe('/library/docs?tab=sources&panel=src1');
    expect(within(row).getByText('12 docs')).toBeTruthy();
    expect(within(screen.getByText('Old wiki').closest('a') as HTMLElement).getByText('Failed')).toBeTruthy();
    expect(screen.getByRole('link', { name: /External sources/ })).toBeTruthy();
  });

  it('one source lists its documents, each opening as an outside doc, and reads it again @s777', async () => {
    const sent = serve();
    queryClient.setQueryData(sourceDocsQuery('src1').queryKey, {
      pages: [{ source, documents: [{ id: 'x1', title: 'Watering', path: 'care/watering.md', commit: '' }], next_cursor: null }],
      pageParams: [''],
    });
    at('/library/docs?tab=sources&panel=src1', <DocsPage search={{ tab: 'sources', panel: 'src1' }} />);
    const doc = (await screen.findByText('Watering')).closest('a') as HTMLElement;
    expect(doc.getAttribute('href')).toMatch(/^\/library\/ext(:|%3A)x1$/);
    expect(screen.getByRole('link', { name: 'Open the source' }).getAttribute('href')).toBe(source.url);
    fireEvent.click(screen.getByRole('button', { name: 'Read again' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/doc-sources/src1/refresh', method: 'POST', body: {} }]));
  });

  it('loads later documents from Load more in the source panel', async () => {
    const sent = serve((url) =>
      url === '/api/v2/doc-sources/src1/documents?limit=200&cursor=next'
        ? { source, documents: [{ id: 'x2', title: 'Pruning', path: 'care/pruning.md', commit: '' }], next_cursor: null }
        : {},
    );
    queryClient.setQueryData(sourceDocsQuery('src1').queryKey, {
      pages: [{ source, documents: [{ id: 'x1', title: 'Watering', path: 'care/watering.md', commit: '' }], next_cursor: 'next' }],
      pageParams: [''],
    });
    at('/library/docs?tab=sources&panel=src1', <DocsPage search={{ tab: 'sources', panel: 'src1' }} />);
    expect((await screen.findByText('Watering')).closest('a')?.getAttribute('href')).toMatch(/^\/library\/ext(:|%3A)x1$/);
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect((await screen.findByText('Pruning')).closest('a')?.getAttribute('href')).toMatch(/^\/library\/ext(:|%3A)x2$/);
    expect(screen.getByText('Watering')).toBeTruthy();
    expect(sent.filter(({ url }) => url.includes('/documents'))).toEqual([
      expect.objectContaining({ url: '/api/v2/doc-sources/src1/documents?limit=200&cursor=next' }),
    ]);
  });

  it('links a new source and lands on it; Edit takes one off the list @s817', async () => {
    const sent = serve((url, method) => (method === 'POST' && url === '/api/v2/doc-sources' ? { source: { ...source, id: 'src9', title: 'Seed notes' } } : undefined));
    const router = at('/library/docs?tab=sources', <DocsPage search={{ tab: 'sources' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect a source' });
    const go = within(dialog).getByRole('button', { name: 'Connect' });
    expect(go).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'https://github.com/example/seed-notes' } });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Seed notes' } });
    fireEvent.click(go);
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: 'sources', panel: 'src9' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/doc-sources', method: 'POST', body: { url: 'https://github.com/example/seed-notes', title: 'Seed notes' } }]);
    cleanup();

    const edits = serve((url) => (url.startsWith('/api/v2/doc-sources?') ? { sources: [source], next_cursor: null } : undefined));
    queryClient.setQueryData(sourcesQuery.queryKey, { sources: [source], next_cursor: null } as never);
    queryClient.setQueryData(sourceDocsQuery('src1').queryKey, { pages: [{ source, documents: [], next_cursor: null }], pageParams: [''] });
    at('/library/docs?tab=sources&panel=src1', <DocsPage search={{ tab: 'sources', panel: 'src1' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const edit = await screen.findByRole('dialog', { name: 'Edit source' });
    fireEvent.click(within(edit).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(writes(edits)).toEqual([{ url: '/api/v2/doc-sources/src1', method: 'PATCH', body: { archived: true } }]));
  });
});

describe('an outside doc', () => {
  const doc = {
    id: 'external:x1',
    type: 'external',
    trust: 'untrusted_external',
    title: 'Setup',
    path: 'docs/setup.md',
    source_id: 'src1',
    source_title: 'Fern handbook',
    source_url: 'https://github.com/example/handbook',
    commit: '0123456789abcdef',
    last_checked_at: '2026-11-18T00:00:00Z',
    citation_url: 'https://github.com/example/handbook/blob/0123456789abcdef/docs/setup.md#L1-L4',
    content: 'See [the guide](guide.md), [the top](/README.md), [a part](#steps) and [the site](https://example.com).',
    reference_notice: 'Untrusted external reference.',
  };

  it('reads as it was read, with where it lives and the commit it was read at, its relative links leading there, and nothing to edit @s778', async () => {
    serve();
    queryClient.setQueryData(externalDocQuery('x1').queryKey, doc as never);
    at('/library/ext:x1', <DocPage doc="ext:x1" search={{}} />);
    const guide = (await screen.findByText('the guide')).closest('a');
    expect(screen.getByText('Fern handbook')).toBeTruthy();
    expect(screen.getByText('docs/setup.md')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open where it lives' }).getAttribute('href')).toBe(doc.citation_url);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(guide?.getAttribute('href')).toBe('https://github.com/example/handbook/blob/0123456789abcdef/docs/guide.md');
    expect(screen.getByText('the top').closest('a')?.getAttribute('href')).toBe('https://github.com/README.md');
    expect(screen.getByText('a part').closest('a')?.getAttribute('href')).toBe('#steps');
    expect(screen.getByText('the site').closest('a')?.getAttribute('href')).toBe('https://example.com');
    expect(screen.getByText('0123456')).toBeTruthy();
  });

  it('reads the doc at the commit the address names @s778', async () => {
    const sent = serve((url) => (url.startsWith('/api/v2/docs/external/') ? { doc: { ...doc, commit: 'abcdef0123' } } : undefined));
    at('/library/ext:x1?revision=abcdef0123', <DocPage doc="ext:x1" search={{ revision: 'abcdef0123' }} />);
    expect(await screen.findByText('abcdef0')).toBeTruthy();
    expect(sent.map((x) => x.url)).toContain('/api/v2/docs/external/x1?revision=abcdef0123');
    expect(queryClient.getQueryData(externalDocAtQuery('x1', 'abcdef0123').queryKey)).toBeTruthy();
  });

  it('leaves pictures and addresses alone and resolves ../ against the doc @s778', () => {
    const base = 'https://github.com/o/r/blob/c1/a/b.md?plain=1#L2';
    expect(wholeLinks('[up](../c.md) ![pic](img.png) [mail](mailto:x@example.com)', base)).toBe(
      '[up](https://github.com/o/r/blob/c1/c.md) ![pic](https://github.com/o/r/blob/c1/a/img.png) [mail](mailto:x@example.com)',
    );
  });
});

describe('editing a source', () => {
  it('takes a removed source off the list once the server has', async () => {
    const sent = serve();
    queryClient.setQueryData(sourcesQuery.queryKey, { next_cursor: null, sources: [{ id: 'src1', title: 'Handbook' }] } as never);
    const done = editSource('src1', { archived: true });
    expect(queryClient.getQueryData<{ sources: unknown[] }>(sourcesQuery.queryKey)?.sources).toHaveLength(1);
    await done;
    expect(queryClient.getQueryData<{ sources: unknown[] }>(sourcesQuery.queryKey)?.sources).toEqual([]);
    expect(sent.find((x) => x.method === 'PATCH')).toMatchObject({ url: '/api/v2/doc-sources/src1', body: { archived: true } });
  });
});
