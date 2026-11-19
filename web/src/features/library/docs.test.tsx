import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { ContactsPage } from './ContactsPage';
import { DocsPage } from './DocsPage';
import {
  archivedDocsQuery,
  attributesQuery,
  cardQuery,
  contactsQuery,
  countsQuery,
  docsListQuery,
  installTargetsQuery,
  skillQuery,
  skillUsageQuery,
  sourcesQuery,
  typesQuery,
  type Card,
} from './queries';
import { SkillsPage } from './SkillsPage';

/* Archived docs, importing a file, a field's history and installing a skill: what is sent, and what shows. */

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

const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET');

// Made-up docs, people and skills only.
const doc = (id: string, title: string) => ({ id, title, locked: false, path: 'company/notes.md', updated_at: '2026-11-01T00:00:00Z', updated_by_id: '', updated_by_name: 'Sam', version: 3 });

describe('archived docs', () => {
  it('lists archived docs from their own read, each with Restore', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc('d1', 'Live doc')], next_cursor: null });
    queryClient.setQueryData(archivedDocsQuery.queryKey, { docs: [doc('d2', 'Old plan')], next_cursor: null });
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

  it('archives a doc: off the list at once, and sent with its version', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc('d1', 'Live doc')], next_cursor: null });
    at('/library/d1');
    const done = run('docs.archive', { doc: 'd1', version: 3, archived: true }, { via: 'click' });
    await waitFor(() => expect(queryClient.getQueryData<{ docs: unknown[] }>(docsListQuery.queryKey)?.docs).toEqual([]));
    expect((await done).ok).toBe(true);
    expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, archived: true } }]);
  });

  it('turns the archived view on from the head’s ⋯', async () => {
    serve(() => ({ docs: [], next_cursor: null }));
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc('d1', 'Live doc')], next_cursor: null });
    const router = at('/library/docs', <DocsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archived' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ archived: 1 }));
  });
});

describe('docs list', () => {
  it('names a section as a person reads it, and puts the head’s ⋯ after New doc', async () => {
    serve();
    queryClient.setQueryData(docsListQuery.queryKey, {
      docs: [
        { ...doc('d1', 'Fern index'), path: '_greenhouse/index.md' },
        { ...doc('d2', 'Watering how-to'), path: 'plant-care/watering.md' },
        { ...doc('d3', 'Readme'), path: 'README.md' },
      ],
      next_cursor: null,
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
});

describe('import a file', () => {
  it('sends the file to the import and opens the new doc', async () => {
    const sent = serve((url) => (url.includes('/docs/import') ? { doc: { ...doc('d9', 'Handbook'), body: '', archived: false } } : {}));
    const router = at('/library/docs');
    const file = new File(['# Handbook'], 'handbook.md', { type: 'text/markdown' });
    await act(async () => expect((await run('docs.import', { file }, { via: 'click' })).ok).toBe(true));
    const post = writes(sent)[0];
    expect(post?.url).toBe('/api/v2/docs/import');
    expect((post?.body as FormData).get('file')).toBeInstanceOf(File);
    expect(router.state.location.pathname).toBe('/library/d9');
  });

  it('is never Tico’s: there is no file to hand over', async () => {
    serve();
    at('/library/docs');
    expect(await run('docs.import', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('a field’s history', () => {
  const card: Card = {
    id: 'c1',
    type: 'person',
    name: 'Sam Sample',
    status: 'active',
    primary_email: null,
    primary_domain: null,
    last_activity_at: null,
    summary: null,
    values: { role: { value: 'Buyer', source_type: 'hub', source_id: null, updated_at: '' } },
    links: [],
    notes: [],
  };

  it('shows past values in the panel from the field’s ⋯', async () => {
    const sent = serve((url) =>
      url.includes('/entities/history')
        ? { rows: [{ id: 'h1', value: 'Intern', source_type: 'email', source_id: null, confidence: null, active_from: null, active_until: '2026-10-01T00:00:00Z' }], next: null }
        : { detail: 'not_found' },
    );
    queryClient.setQueryData(typesQuery.queryKey, [{ key: 'person', name: 'Person', plural_name: 'People' }]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type: 'person', count: 1 }]);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    queryClient.setQueryData(cardQuery('c1').queryKey, card);
    queryClient.setQueryData(attributesQuery('person').queryKey, [{ key: 'role', name: 'Role', value_type: 'text', multiple: false, sensitive: false }]);
    at('/library/contacts?panel=c1', <ContactsPage search={{ panel: 'c1' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More on Role' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'History' }));
    expect(await screen.findByText('Intern')).toBeTruthy();
    expect(sent.some((x) => x.url === '/api/v2/entities/history?ref=c1&attribute=role')).toBe(true);
    expect(writes(sent)).toEqual([]);
  });
});

describe('install a skill', () => {
  it('installs for one agent, or for a team’s members at once', async () => {
    const sent = serve();
    at('/library/skills');
    await run('skills.install', { skill: 'triage', targets: [{ target_type: 'Bot', target_id: 'b1' }] }, { via: 'click' });
    const team = [
      { target_type: 'Bot', target_id: 'b1' },
      { target_type: 'Human', target_id: 'h1' },
    ];
    await run('skills.install', { skill: 'triage', targets: team, version: 2 }, { via: 'click' });
    expect(writes(sent)).toEqual([
      { url: '/api/v2/skills/triage/installs', method: 'POST', body: { target_type: 'Bot', target_id: 'b1', version_policy: 'latest' } },
      { url: '/api/v2/skills/triage/installs/bulk', method: 'POST', body: { targets: team, version_policy: 'pinned', pinned_version: 2 } },
    ]);
  });

  it('offers Install first on a skill nobody has', async () => {
    serve(() => ({ files: [] }));
    const v = { number: 1, approved_at: '2026-11-01T00:00:00Z', approved_by_id: 'human:me' };
    queryClient.setQueryData(skillQuery('triage').queryKey, { skill: { id: 's1', name: 'triage', description: '', latest: v }, versions: [v], installs: [] } as never);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    queryClient.setQueryData(installTargetsQuery.queryKey, { me: { type: 'Human', id: 'me' }, bots: [{ id: 'b1', name: 'Helper' }], people: [], teams: [] });
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    const install = await screen.findByRole('button', { name: 'Install' });
    expect(install.className).toContain('btn-fill');
    fireEvent.click(install);
    expect(await screen.findByRole('dialog', { name: 'Install triage' })).toBeTruthy();
  });
});
