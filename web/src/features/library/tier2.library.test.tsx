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
import './actions';
import { DocPage } from './DocPage';
import { DocsPage } from './DocsPage';
import {
  docsListQuery,
  externalDocQuery,
  proposalQuery,
  proposalsQuery,
  skillQuery,
  skillsQuery,
  skillTextQuery,
  skillUsageQuery,
  sourceDocsQuery,
  sourcesQuery,
} from './queries';
import { SkillsPage } from './SkillsPage';

/* The tier-2 Library rows: External sources, an outside doc, Skills, a skill, a proposal, Connect Claude Code, a
   mailing address and letters out, the tools' settings, and the dialogs an open tool raises. What a person sees and
   presses, what is sent, and what Tico may not run. Made-up sources, skills, tools and addresses only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({ BrowserDialog: () => null }));

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
    return new Response(JSON.stringify(reply(String(url), method) ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => ({ url, method, body }));
const tico = (id: string, input: unknown) => run(id, input, { via: 'tico' });

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
    queryClient.setQueryData(docsListQuery.queryKey, { pages: [{ docs: [], next_cursor: null }], pageParams: [''] });
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

  it('loads later documents when the reader reaches the end of the source panel', async () => {
    const sent = serve((url) =>
      url === '/api/v2/doc-sources/src1/documents?limit=200&cursor=next'
        ? { source, documents: [{ id: 'x2', title: 'Pruning', path: 'care/pruning.md', commit: '' }], next_cursor: null }
        : {},
    );
    let reachEnd = () => {};
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(callback: (entries: { isIntersecting: boolean }[]) => void, options: { root: Element }) {
          expect(options.root?.classList.contains('rail-body')).toBe(true);
          reachEnd = () => callback([{ isIntersecting: true }]);
        }
        observe() {}
        disconnect() {}
      },
    );
    queryClient.setQueryData(sourceDocsQuery('src1').queryKey, {
      pages: [{ source, documents: [{ id: 'x1', title: 'Watering', path: 'care/watering.md', commit: '' }], next_cursor: 'next' }],
      pageParams: [''],
    });
    at('/library/docs?tab=sources&panel=src1', <DocsPage search={{ tab: 'sources', panel: 'src1' }} />);
    expect((await screen.findByText('Watering')).closest('a')?.getAttribute('href')).toMatch(/^\/library\/ext(:|%3A)x1$/);
    act(() => reachEnd());
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

  it('an outside doc reads as it was read, with where it lives, and nothing to edit @s778', async () => {
    serve();
    queryClient.setQueryData(externalDocQuery('x1').queryKey, {
      id: 'x1',
      title: 'Watering',
      source_title: 'Fern handbook',
      path: 'care/watering.md',
      content: '# Water\n\nWeekly in summer.',
      citation_url: 'https://github.com/example/handbook/blob/main/care/watering.md',
      reference_notice: null,
    } as never);
    at('/library/ext:x1', <DocPage doc="ext:x1" search={{}} />);
    expect(await screen.findByText('Weekly in summer.')).toBeTruthy();
    expect(screen.getByText('Fern handbook')).toBeTruthy();
    expect(screen.getByText('care/watering.md')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open where it lives' }).getAttribute('href')).toBe('https://github.com/example/handbook/blob/main/care/watering.md');
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });
});

/* ---------------------------------------------------------------- skills */

const skill = (name: string, description: string) => ({ id: `s-${name}`, name, description, latest: null, installs_count: 0, on: [] });
const v = (n: number) => ({ number: n, approved_at: '2026-11-0' + n + 'T00:00:00Z', approved_by_id: 'human:me' });
const pending = {
  id: 'skp_1',
  name: 'triage',
  status: 'pending',
  description: 'Sort new mail',
  note: 'Two letters went to the wrong team',
  base_version: 2,
  version: null,
  proposed_by: { type: 'Bot', id: 'b1' },
  task_id: null,
  created_at: '2026-11-18T00:00:00Z',
  updated_at: '2026-11-18T00:00:00Z',
};

describe('Skills', () => {
  it('lists the skills with what each does, each opening its page, narrowed by the words searched @s781', async () => {
    serve();
    queryClient.setQueryData(skillsQuery.queryKey, [skill('triage', 'Sort new mail'), skill('pack-a-kit', 'Packs a kit')]);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    queryClient.setQueryData(skillUsageQuery.queryKey, [{ name: 'triage', uses: 4, last_used_at: '2026-11-18T00:00:00Z' }]);
    at('/library/skills?q=mail', <SkillsPage search={{ q: 'mail' }} />);
    const row = (await screen.findByText('triage')).closest('a') as HTMLElement;
    expect(row.getAttribute('href')).toBe('/library/skills?skill=triage');
    expect(within(row).getByText('Sort new mail')).toBeTruthy();
    expect(screen.queryByText('pack-a-kit')).toBeNull();
    expect(screen.getByRole('button', { name: 'New skill' })).toBeTruthy();
  });

  it('a skill reads as its text without front matter, with who has it, its versions and Install in its ⋯ @s782', async () => {
    serve();
    queryClient.setQueryData(skillQuery('triage').queryKey, {
      skill: { id: 's1', name: 'triage', description: 'Sort new mail', latest: v(2) },
      versions: [v(2), v(1)],
      installs: [{ id: 'i1', target: { type: 'Bot', id: 'b1', name: 'Mail helper' } }],
    } as never);
    queryClient.setQueryData(skillTextQuery('triage', 2).queryKey, { ...v(2), files: [{ path: 'SKILL.md', content: '---\ndescription: Sort new mail\n---\n\n# Steps\nRead each new letter first.' }] } as never);
    queryClient.setQueryData(skillUsageQuery.queryKey, [{ name: 'triage', uses: 4, last_used_at: '2026-11-18T00:00:00Z' }]);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    expect(await screen.findByText('Read each new letter first.')).toBeTruthy();
    expect(screen.queryByText(/description: Sort/)).toBeNull();
    expect(screen.getByText('Mail helper')).toBeTruthy();
    expect(screen.getByText('4 runs')).toBeTruthy();
    expect(screen.getByText('v1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Install' })).toBeTruthy();
  });

  it('a proposed change shows its lines added and removed, and an owner approves it @s783', async () => {
    const sent = serve();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    queryClient.setQueryData(proposalQuery('skp_1').queryKey, {
      proposal: pending,
      files: [],
      diff: '--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1,3 +1,3 @@\n # Steps\n-Read the mail.\n+Read each new letter first.\n Ask who sent it.',
      stale: false,
      screening: { verdict: 'safe', risk: 'low', summary: '' },
    } as never);
    queryClient.setQueryData(skillsQuery.queryKey, [skill('triage', 'Sort new mail')]);
    const router = at('/library/skills?proposal=skp_1', <SkillsPage search={{ proposal: 'skp_1' }} />);
    expect((await screen.findByText('Read each new letter first.')).getAttribute('title')).toBe('Added');
    expect(screen.getByText('Read the mail.').getAttribute('title')).toBe('Removed');
    expect(screen.getByText('1 added, 1 removed')).toBeTruthy();
    expect(screen.getByText('Nothing risky')).toBeTruthy();
    expect(screen.getByText('Two letters went to the wrong team')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/skills'));
    expect(router.state.location.search).toEqual({});
    expect(writes(sent)).toEqual([{ url: '/api/v2/skills/proposals/skp_1/approve', method: 'POST', body: {} }]);
    expect(await tico('skills.approve', { proposal: 'skp_1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Send back takes what to change and sends it as the reason @s832', async () => {
    const sent = serve();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    queryClient.setQueryData(proposalQuery('skp_1').queryKey, { proposal: pending, files: [], diff: '', stale: false, screening: null } as never);
    queryClient.setQueryData(skillsQuery.queryKey, []);
    at('/library/skills?proposal=skp_1', <SkillsPage search={{ proposal: 'skp_1' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Send back' }));
    const dialog = await screen.findByRole('dialog', { name: 'Send back' });
    const send = within(dialog).getByRole('button', { name: 'Send back' });
    expect(send).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText('What to change'), { target: { value: 'Keep the old first step' } });
    fireEvent.click(send);
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/skills/proposals/skp_1/decline', method: 'POST', body: { reason: 'Keep the old first step' } }]));
  });

  it('Connect Claude Code shows the one line to run, and copies it @s830', async () => {
    serve();
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    queryClient.setQueryData(skillsQuery.queryKey, [skill('triage', 'Sort new mail')]);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    const router = at('/library/skills', <SkillsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Connect Claude Code' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ connect: 'setup' }));
    cleanup();
    at('/library/skills?connect=setup', <SkillsPage search={{ connect: 'setup' }} />);
    const dialog = await screen.findByRole('dialog', { name: 'Connect Claude Code' });
    const line = within(dialog).getByText(/skills\/helper\.mjs/).textContent ?? '';
    expect(line).toMatch(/^curl -fsSL --create-dirs .*\/api\/v2\/skills\/helper\.mjs -o ~\/\.tico\/skills-helper\.mjs && node ~\/\.tico\/skills-helper\.mjs setup$/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(line));
  });

  it('approves Claude Code on a computer, sharing which skills are used only when ticked; never Tico’s @s784', async () => {
    const CODE = 'WXYZ-2345';
    const sent = serve((url) => (url === `/api/v2/skills/devices/codes/${CODE}` ? { user_code: CODE, label: 'Studio laptop', expires_at: '2026-11-19T08:00:00Z' } : undefined));
    queryClient.setQueryData(skillsQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills', <SkillsPage search={{ connect: CODE }} />);
    const dialog = await screen.findByRole('dialog', { name: 'Connect Claude Code' });
    expect(await within(dialog).findByText('Studio laptop')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Share which skills I use' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await within(dialog).findByText('Connected. Go back to your terminal.')).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: `/api/v2/skills/devices/codes/${CODE}/approve`, method: 'POST', body: { reporting_enabled: true } }]);
    expect(await tico('skills.decideDevice', { code: CODE, approve: true })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});
