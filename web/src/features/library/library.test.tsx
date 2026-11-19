import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { ContactsPage, shown } from './ContactsPage';
import { cardQuery, contactsQuery, countsQuery, proposalsQuery, removeKey, saveFact, skillsQuery, skillUsageQuery, typesQuery, apiToolQuery, type Card } from './queries';
import { diffLines, mainText, SkillsPage } from './SkillsPage';
import { nameFromUrl, ReplaceLoginDialog } from './AddTool';
import { replaceLogin } from './queries';
import { skillsSearch } from './search';
import { newAddressFor } from '@/oldAddress/map';
import { forgetHelperCode, heldHelperCode, takeHelperCode } from './helperCode';
import { loadDocs, loadTools } from './routing';

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
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Reply = { status: number; body: unknown };
function serve(reply: (url: string, method: string) => Reply) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const r = reply(String(url), init?.method ?? 'GET');
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });
const refuse = (): Reply => ({ status: 422, body: { detail: 'invalid' } });

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: () => node });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');

describe('helpers', () => {
  it('reads a unified diff as headings, added and removed lines', () => {
    const diff = ['--- a/SKILL.md', '+++ b/SKILL.md', '@@ -1,3 +1,3 @@', ' # Steps', '-Ask twice', '+Ask once', ' Done'].join('\n');
    expect(diffLines(diff, false)).toEqual([
      { kind: 'h', text: 'Steps' },
      { kind: '-', text: 'Ask twice' },
      { kind: '+', text: 'Ask once' },
      { kind: '', text: 'Done' },
    ]);
    expect(diffLines(diff, true)[0]).toEqual({ kind: 'h', text: 'SKILL.md' });
  });

  it("takes a skill's text without its front matter", () => {
    const files = [
      { path: 'notes.txt', content: 'x' },
      { path: 'SKILL.md', content: '---\nname: sample\n---\nDo the thing.' },
    ];
    expect(mainText(files)).toBe('Do the thing.');
  });

  it('shows a fact as words', () => {
    const v = (value: unknown) => ({ value, source_type: 'hub', source_id: null, updated_at: '' });
    expect(shown(undefined)).toBe('');
    expect(shown(v('a@example.com'))).toBe('a@example.com');
    expect(shown([v('one'), v(null), v(2)])).toBe('one, 2');
  });
});

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

describe('changes taken back when refused', () => {
  it('shows a fact at once and puts it back on a refusal', async () => {
    serve(refuse);
    queryClient.setQueryData(cardQuery('c1').queryKey, card);
    const sent = saveFact('c1', 'person', 'role', 'Seller');
    expect(queryClient.getQueryData<Card>(cardQuery('c1').queryKey)?.values.role).toMatchObject({ value: 'Seller' });
    await expect(sent).rejects.toBeTruthy();
    expect(queryClient.getQueryData<Card>(cardQuery('c1').queryKey)?.values.role).toMatchObject({ value: 'Buyer' });
  });

  it('puts a removed key back on a refusal', async () => {
    serve(refuse);
    const key = { id: 'k1', name: 'Main', preview: '••••abcd' };
    queryClient.setQueryData(apiToolQuery('t1').queryKey, { id: 't1', keys: [key] } as never);
    const sent = removeKey('t1', 'k1');
    expect((queryClient.getQueryData(apiToolQuery('t1').queryKey) as { keys: unknown[] }).keys).toHaveLength(0);
    await expect(sent).rejects.toBeTruthy();
    expect((queryClient.getQueryData(apiToolQuery('t1').queryKey) as { keys: unknown[] }).keys).toEqual([key]);
  });
});

describe('Contacts', () => {
  const row = { id: 'c1', type: 'person', name: 'Sam Sample', status: 'active', primary_email: null, primary_domain: null, last_activity_at: null };

  it('opens a contact beside the list', async () => {
    serve(nothing);
    queryClient.setQueryData(typesQuery.queryKey, [{ key: 'person', name: 'Person', plural_name: 'People' }]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type: 'person', count: 1 }]);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [row], next: null }], pageParams: [''] });
    show(<ContactsPage search={{}} />, '/library/contacts');
    expect(href(await screen.findByText('Sam Sample'))).toBe('/library/contacts?panel=c1');
  });

  it('says so when there are none', async () => {
    serve(nothing);
    queryClient.setQueryData(typesQuery.queryKey, []);
    queryClient.setQueryData(countsQuery.queryKey, []);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    show(<ContactsPage search={{}} />, '/library/contacts');
    expect(await screen.findByText('No contacts yet')).toBeTruthy();
  });

  it('shows the plain empty state for an empty tab, and Nothing matches only for a search', async () => {
    serve(nothing);
    queryClient.setQueryData(typesQuery.queryKey, [
      { key: 'person', name: 'Person', plural_name: 'People' },
      { key: 'organization', name: 'Organization', plural_name: 'Organizations' },
    ]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type: 'organization', count: 2 }]);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    show(<ContactsPage search={{}} />, '/library/contacts');
    expect(await screen.findByText('No people yet')).toBeTruthy();
    expect(screen.queryByText('Nothing matches')).toBeNull();
    cleanup();
    queryClient.setQueryData(contactsQuery('person', 'zz').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    show(<ContactsPage search={{ q: 'zz' }} />, '/library/contacts?q=zz');
    expect(await screen.findByText('Nothing matches')).toBeTruthy();
  });
});

describe('Skills', () => {
  it('puts a pending proposal in the same list as the skills, first', async () => {
    serve(nothing);
    queryClient.setQueryData(skillsQuery.queryKey, [{ id: 's1', name: 'triage', description: 'Sort new mail', latest: null, installs_count: 0, on: [] }]);
    queryClient.setQueryData(proposalsQuery.queryKey, [
      {
        id: 'p1',
        name: 'triage',
        status: 'pending',
        description: 'Sort faster',
        note: null,
        base_version: 1,
        version: 2,
        proposed_by: { type: 'Bot', id: 'b1' },
        task_id: null,
        created_at: '2026-11-01T00:00:00Z',
        updated_at: '2026-11-01T00:00:00Z',
      },
    ]);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    show(<SkillsPage search={{}} />, '/library/skills');
    const links = (await screen.findAllByText('triage')).map(href);
    expect(links).toEqual(['/library/skills?proposal=p1', '/library/skills?skill=triage']);
    expect(screen.queryByRole('tab')).toBeNull();
  });

  it('draws skills Tico ships (no id) once each, with no duplicate keys', async () => {
    serve(nothing);
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    const shipped = (name: string) => ({ id: null, name, description: '', source: 'tico' as const, latest: null, installs_count: 0, on_count: 2, on: [] });
    queryClient.setQueryData(skillsQuery.queryKey, [
      shipped('plan-a-trip'),
      shipped('water-the-ferns'),
      { id: 's1', name: 'triage', description: '', latest: null, installs_count: 1, on: [] },
    ]);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    show(<SkillsPage search={{}} />, '/library/skills');
    expect(await screen.findByText('water-the-ferns')).toBeTruthy();
    expect(screen.getAllByText('plan-a-trip')).toHaveLength(1);
    expect(errors.mock.calls.some((c) => String(c[0]).includes('same key'))).toBe(false);
    errors.mockRestore();
  });
});

describe('Add tool', () => {
  it('names an MCP server from its address', () => {
    expect(nameFromUrl('https://mcp.acme.com/sse')).toBe('Acme');
    expect(nameFromUrl('not an address')).toBe('');
  });

  it('sends only the login fields that changed, and null to take the authenticator off @s925', async () => {
    const sent: unknown[] = [];
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      sent.push(JSON.parse(String(init?.body)));
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    await replaceLogin('w1', 'a1', { totp: null });
    expect(sent).toEqual([{ totp: null }]);
    render(
      <QueryClientProvider client={queryClient}>
        <ReplaceLoginDialog tool="w1" account={{ id: 'a1', username: 'ops@example.com', has_password: true, has_totp: true } as never} onClose={() => {}} />
      </QueryClientProvider>,
    );
    // A stored password is never read back: the field starts empty.
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
  });
});

describe("Connect Claude Code from the helper's old link", () => {
  const CODE = 'WXYZ-2345';

  it('takes the code out of the address at once, and keeps it out of the cache', async () => {
    const sent: string[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if ((init?.method ?? 'GET') !== 'GET') sent.push(String(url));
      // The page draws once its list is in: an empty one.
      const body = String(url).endsWith(`/skills/devices/codes/${CODE}`) ? { user_code: CODE, label: 'Studio laptop', expires_at: '2026-11-19T08:00:00Z' } : { skills: [], proposals: [] };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const at = newAddressFor(`/#/skills/connect?code=${CODE}`) ?? '';
    const Skills = () => <SkillsPage search={skillsSearch(useSearch({ strict: false }))} />;
    const root = createRootRoute({ component: Outlet });
    const page = createRoute({ getParentRoute: () => root, path: '/library/skills', component: Skills });
    const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const dialog = await screen.findByRole('dialog', { name: 'Connect Claude Code' });
    expect(await within(dialog).findByText(CODE)).toBeTruthy();
    await waitFor(() => expect(router.state.location.href).toBe('/library/skills'));
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
    expect(skillsSearch({ connect: 'https://evil.example' })).toEqual({});
  });

  /** The Skills page alone, as the app opens it after sign-in: the code is not in the address. */
  function openSkills() {
    vi.stubGlobal('fetch', async (url: string) => {
      // The page draws once its list is in: an empty one.
      const body = String(url).endsWith(`/skills/devices/codes/${CODE}`) ? { user_code: CODE, label: 'Studio laptop', expires_at: '2026-11-19T08:00:00Z' } : { skills: [], proposals: [] };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const Skills = () => <SkillsPage search={skillsSearch(useSearch({ strict: false }))} />;
    const root = createRootRoute({ component: Outlet });
    const page = createRoute({ getParentRoute: () => root, path: '/library/skills', component: Skills });
    const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ['/library/skills'] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  it('takes only the code out of the address, and keeps the rest', () => {
    expect(takeHelperCode(`/library/skills?connect=${CODE}&q=calls`)).toBe('/library/skills?q=calls');
    expect(heldHelperCode()).toBe(CODE);
    expect(takeHelperCode('/library/skills?connect=setup')).toBe('/library/skills?connect=setup');
    expect(takeHelperCode(`/tasks?connect=${CODE}`)).toBe(`/tasks?connect=${CODE}`);
    forgetHelperCode();
    expect(heldHelperCode()).toBeNull();
  });

  it('comes back after sign-in, from this tab only, and the first read takes it out', async () => {
    sessionStorage.setItem('tico.skillsHelper', JSON.stringify({ code: CODE, at: Date.now() }));
    const router = openSkills();
    const dialog = await screen.findByRole('dialog', { name: 'Connect Claude Code' });
    expect(await within(dialog).findByText(CODE)).toBeTruthy();
    expect(sessionStorage.getItem('tico.skillsHelper')).toBeNull();
    expect(heldHelperCode()).toBeNull();
    expect(router.state.location.href).toBe('/library/skills');
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
  });

  it('a code left from over ten minutes ago does not come back', async () => {
    sessionStorage.setItem('tico.skillsHelper', JSON.stringify({ code: CODE, at: Date.now() - 11 * 60_000 }));
    openSkills();
    expect(await screen.findByRole('heading', { name: 'Skills' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Connect Claude Code' })).toBeNull();
    expect(sessionStorage.getItem('tico.skillsHelper')).toBeNull();
  });
});

describe('Loading a Library page', () => {
  it('lets a failed main read through to the router, and leaves an open tool’s failure in its place', async () => {
    queryClient.clear();
    serve((url) => (url.startsWith('/api/v2/tool-index') ? { status: 200, body: { rows: [] } } : { status: 403, body: { detail: 'no' } }));
    await expect(loadTools(queryClient, { tool: 'api:t1' })).resolves.toBeTruthy();
    await expect(loadDocs(queryClient, {})).rejects.toMatchObject({ status: 403 });
  });
});
