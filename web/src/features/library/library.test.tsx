import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type { ToolIndexRow } from '@/api/schema';
import { ContactsPage, shown } from './ContactsPage';
import { cardQuery, contactsQuery, countsQuery, proposalsQuery, removeKey, saveFact, skillsQuery, skillUsageQuery, typesQuery, apiToolQuery, type Card } from './queries';
import { diffLines, mainText, SkillsPage } from './SkillsPage';
import { entries } from './ToolsPage';

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

// Made-up rows only.
const tool = (o: Partial<ToolIndexRow> & { id: string; name: string }) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who_total: 0, needs_you: false, status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;

describe('helpers', () => {
  it('groups a vendor with several tools into one entry, problems first', () => {
    const out = entries([
      tool({ id: 'api:1', name: 'Zeta', who_total: 1 }),
      tool({ id: 'api:2', name: 'Acme mail', vendor: 'acme', vendor_name: 'Acme' }),
      tool({ id: 'api:3', name: 'Acme files', vendor: 'acme', vendor_name: 'Acme', who_total: 2 }),
      tool({ id: 'api:4', name: 'Beta', needs_you: true, status: { tone: 'fail', text: 'Key expired' } as ToolIndexRow['status'] }),
    ]);
    expect(out.map((e) => e.id)).toEqual(['api:4', 'vendor:acme', 'api:1']);
    expect(out[0]?.problem).toBe('Key expired');
    expect(out[1]).toMatchObject({ name: 'Acme', count: 2, inUse: true });
  });

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

  it('shows the fault with Try again when the list fails', async () => {
    serve((url) => (url.includes('/entities/find') ? { status: 500, body: { detail: 'boom' } } : nothing()));
    show(<ContactsPage search={{}} />, '/library/contacts');
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy();
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
});
