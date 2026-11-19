import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { ContactsPage } from './ContactsPage';
import { DocPage } from './DocPage';
import { DocsPage } from './DocsPage';
import {
  archivedDocsQuery,
  attributesQuery,
  cardQuery,
  contactsQuery,
  countsQuery,
  docQuery,
  docsListQuery,
  docVersionsQuery,
  installTargetsQuery,
  proposalsQuery,
  skillQuery,
  skillsQuery,
  skillUsageQuery,
  sourcesQuery,
  typesQuery,
  type Card,
} from './queries';
import { SkillsPage } from './SkillsPage';
import { skillsSearch } from './search';

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
  it('lists archived docs from their own read, each with Restore @s791', async () => {
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

  it('archives a doc: off the list at once, and sent with its version @s791', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc('d1', 'Live doc')], next_cursor: null });
    at('/library/d1');
    const done = run('docs.archive', { doc: 'd1', version: 3, archived: true }, { via: 'click' });
    await waitFor(() => expect(queryClient.getQueryData<{ docs: unknown[] }>(docsListQuery.queryKey)?.docs).toEqual([]));
    expect((await done).ok).toBe(true);
    expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, archived: true } }]);
  });

  it('turns the archived view on from the head’s ⋯ @s791', async () => {
    serve(() => ({ docs: [], next_cursor: null }));
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc('d1', 'Live doc')], next_cursor: null });
    const router = at('/library/docs', <DocsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archived' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ archived: 1 }));
  });
});

describe('editing a doc', () => {
  it('keeps what the person typed when the save is refused, and sends the version they started from again @s815', async () => {
    const full = { ...doc('d1', 'Notes'), body: 'Old text', archived: false, created_at: '', created_by_id: '', created_by_name: '' };
    const patches: unknown[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        patches.push(JSON.parse(String(init.body)));
        return Response.json({ error: { code: 'doc_version_conflict', detail: 'Changed meanwhile' } }, { status: 409 });
      }
      // the conflict reads the doc again: someone else's newer text
      if (String(url) === '/api/v2/docs/d1') return Response.json({ doc: { ...full, version: 4, body: 'Their text' } });
      return Response.json({ docs: [], next_cursor: null, versions: [] });
    });
    queryClient.setQueryData(docQuery('d1').queryKey, full);
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

describe('docs list', () => {
  it('names a section as a person reads it, and puts the head’s ⋯ after New doc @s773', async () => {
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

  it('files a doc under the folders below its section, never its own file name', async () => {
    serve();
    queryClient.setQueryData(docsListQuery.queryKey, {
      docs: [
        { ...doc('d1', 'Glossary'), path: 'plant-care/glossary.md' },
        { ...doc('d2', 'Repotting'), path: 'plant-care/how-to/repotting.md' },
      ],
      next_cursor: null,
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
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc('d1', 'Fern index')], next_cursor: null });
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

describe('import a file', () => {
  it('sends the file to the import and opens the new doc @s819', async () => {
    const sent = serve((url) => (url.includes('/docs/import') ? { doc: { ...doc('d9', 'Handbook'), body: '', archived: false } } : {}));
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

  it('shows past values in the panel from the field’s ⋯ @s835', async () => {
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
  it('installs for one agent, or for a team’s members at once @s831', async () => {
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

  it('offers Install from the skill’s ⋯ @s831', async () => {
    serve(() => ({ files: [] }));
    const v = { number: 1, approved_at: '2026-11-01T00:00:00Z', approved_by_id: 'human:me' };
    queryClient.setQueryData(skillQuery('triage').queryKey, { skill: { id: 's1', name: 'triage', description: '', latest: v }, versions: [v], installs: [] } as never);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    queryClient.setQueryData(installTargetsQuery.queryKey, { me: { type: 'Human', id: 'me' }, bots: [{ id: 'b1', name: 'Helper' }], people: [], teams: [] });
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Install' }));
    expect(await screen.findByRole('dialog', { name: 'Install triage' })).toBeTruthy();
  });
});

describe('propose a new skill', () => {
  const TEXT = '---\ndescription: Use when a call comes in\n---\n\n# Steps\nAsk who is calling.';
  const proposal = { id: 'skp_1', name: 'triage-calls', status: 'pending', description: 'Use when a call comes in', note: null, base_version: null, version: null, proposed_by: { type: 'Bot', id: 'b1' }, task_id: null, created_at: '2026-11-19T00:00:00Z', updated_at: '2026-11-19T00:00:00Z' };
  const reply = (url: string, method: string) => {
    if (method === 'POST' && url === '/api/v2/skills/proposals') return { proposal };
    if (url === '/api/v2/skills/proposals/skp_1') return { proposal, files: [{ path: 'SKILL.md', content: TEXT }], diff: '', stale: false, screening: null };
    if (url.startsWith('/api/v2/skills/proposals')) return { proposals: [proposal] };
    if (url.startsWith('/api/v2/skills/usage')) return { skills: [] };
    return { skills: [] };
  };
  const skill = (name: string) => ({ id: 's1', name, description: '', latest: null, installs_count: 0, on: [] });

  it('Tico may propose one: it is sent with no base version, waits at the top, and lands on the proposal', async () => {
    const sent = serve(reply);
    queryClient.setQueryData(skillsQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    const router = at('/library/skills');
    const r = await run('skills.propose', { name: 'triage-calls', text: TEXT }, { via: 'tico' });
    expect(r).toMatchObject({ ok: true, did: 'Proposed triage-calls' });
    expect(writes(sent)).toEqual([{ url: '/api/v2/skills/proposals', method: 'POST', body: { name: 'triage-calls', files: [{ path: 'SKILL.md', content: TEXT }] } }]);
    expect(queryClient.getQueryData(proposalsQuery.queryKey)).toEqual([proposal]);
    expect(router.state.location.search).toEqual({ proposal: 'skp_1' });
  });

  it('refuses a name that is already a skill, or not a skill name, and sends nothing', async () => {
    const sent = serve(reply);
    queryClient.setQueryData(skillsQuery.queryKey, [skill('triage-calls')]);
    at('/library/skills');
    expect(await run('skills.propose', { name: 'triage-calls', text: TEXT }, { via: 'click' })).toMatchObject({ ok: false, error: 'exists' });
    expect((await run('skills.propose', { name: 'Triage Calls', text: TEXT }, { via: 'click' })).error).toMatch(/^bad_input/);
    expect(writes(sent)).toEqual([]);
  });

  it('New skill on the Skills page takes a name and its text, and opens the proposal', async () => {
    const sent = serve(reply);
    queryClient.setQueryData(skillsQuery.queryKey, [skill('review-a-pr')]);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    const Skills = () => <SkillsPage search={skillsSearch(useSearch({ strict: false }))} />;
    const router = at('/library/skills', <Skills />);
    const make = await screen.findByRole('button', { name: 'New skill' });
    expect(make.className).toContain('btn-fill');
    fireEvent.click(make);
    const dialog = await screen.findByRole('dialog', { name: 'New skill' });
    const propose = screen.getByRole('button', { name: 'Propose' });
    expect(propose).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Triage calls' } });
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('triage-calls');
    fireEvent.change(screen.getByLabelText('Text'), { target: { value: TEXT } });
    fireEvent.click(propose);
    await waitFor(() => expect(router.state.location.search).toEqual({ proposal: 'skp_1' }));
    expect(dialog.isConnected).toBe(false);
    expect(await screen.findByRole('heading', { name: 'triage-calls' })).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/skills/proposals', method: 'POST', body: { name: 'triage-calls', files: [{ path: 'SKILL.md', content: TEXT }] } }]);
  });
});

describe('a proposal: who may decide it', () => {
  const TEXT = '---\ndescription: Use when a call comes in\n---\n\n# Steps\nAsk who is calling.';
  const proposal = { id: 'skp_1', name: 'triage-calls', status: 'pending', description: 'Use when a call comes in', note: null, base_version: null, version: null, proposed_by: { type: 'Human', id: 'me' }, task_id: null, created_at: '2026-11-19T00:00:00Z', updated_at: '2026-11-19T00:00:00Z' };
  /** The server as a reader with `you` in Access (null: Access refuses, a member). */
  function server(you: string | null) {
    vi.stubGlobal('fetch', async (url: string) => {
      const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
      if (url === '/api/v2/access') return you ? json(200, { you }) : json(403, { error: { code: 'forbidden', detail: 'Only owners and admins see who can sign in' } });
      if (url === '/api/v2/skills/proposals/skp_1') return json(200, { proposal, files: [{ path: 'SKILL.md', content: TEXT }], diff: '', stale: false, screening: null });
      return json(200, { skills: [], proposals: [] });
    });
  }
  const Skills = () => <SkillsPage search={skillsSearch(useSearch({ strict: false }))} />;

  it('a member reads it only: no Approve, no Send back', async () => {
    server(null);
    at('/library/skills?proposal=skp_1', <Skills />);
    expect(await screen.findByRole('heading', { name: 'triage-calls' })).toBeTruthy();
    await screen.findByText('Ask who is calling.');
    await waitFor(() => expect(queryClient.getQueryState(['skills', 'access-role'])?.status).toBe('success'));
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Send back' })).toBeNull();
  });

  it('an admin, or the owner, sees Approve and Send back', async () => {
    server('admin');
    at('/library/skills?proposal=skp_1', <Skills />);
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send back' })).toBeTruthy();
    cleanup();
    queryClient.clear();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    queryClient.setQueryData(['directory'], { actors: [], byId: {} });
    server(null);
    at('/library/skills?proposal=skp_1', <Skills />);
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeTruthy();
  });

  it('the list says it waits on you only to a decider; a member reads who it waits on', async () => {
    const list = (you: string | null) =>
      vi.stubGlobal('fetch', async (url: string) => {
        const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
        if (url === '/api/v2/access') return you ? json(200, { you }) : json(403, { error: { code: 'forbidden', detail: 'Owners and admins only' } });
        return json(200, { skills: [], proposals: [proposal] });
      });
    list(null);
    queryClient.setQueryData(skillsQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, [proposal]);
    at('/library/skills', <Skills />);
    await waitFor(() => expect(queryClient.getQueryState(['skills', 'access-role'])?.status).toBe('success'));
    const row = (await screen.findByText('triage-calls')).closest('a')!;
    expect(within(row).getByText('Waits on an admin')).toBeTruthy();
    expect(within(row).queryByRole('img', { name: 'Waits on you' })).toBeNull();
    cleanup();
    list('admin');
    queryClient.setQueryData(proposalsQuery.queryKey, [proposal]);
    queryClient.removeQueries({ queryKey: ['skills', 'access-role'] });
    at('/library/skills', <Skills />);
    const mine = (await screen.findByText('triage-calls')).closest('a')!;
    expect(await within(mine).findByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(within(mine).queryByText('Waits on an admin')).toBeNull();
  });

  it("a refused bundle says the server's reason", async () => {
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const refused = init?.method === 'POST' && url === '/api/v2/skills/proposals';
      const body = refused ? { error: { code: 'skill_bundle', detail: 'The SKILL.md description is over 1024 characters' } } : { skills: [], proposals: [] };
      return new Response(JSON.stringify(body), { status: refused ? 422 : 200, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData(skillsQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at(
      '/library/skills',
      <>
        <Skills />
        <Toasts />
      </>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'New skill' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'triage-calls' } });
    fireEvent.change(screen.getByLabelText('Text'), { target: { value: TEXT } });
    fireEvent.click(screen.getByRole('button', { name: 'Propose' }));
    expect(await screen.findByText('The SKILL.md description is over 1024 characters')).toBeTruthy();
    expect(screen.getByText(/Not proposed/)).toBeTruthy();
  });
});

describe('connect Claude Code', () => {
  it('opens the setup steps only: a code is never taken into the address', async () => {
    serve();
    const router = at('/library/skills');
    expect((await run('skills.connect', { code: 'WXYZ-2345' }, { via: 'click' })).error).toMatch(/^bad_input/);
    expect(router.state.location.search).toEqual({});
    expect((await run('skills.connect', {}, { via: 'click' })).ok).toBe(true);
    expect(router.state.location.search).toEqual({ connect: 'setup' });
  });
});
