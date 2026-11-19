import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import './actions';
import { AddDialog } from './AddTool';
import { DocPage } from './DocPage';
import { DocsPage } from './DocsPage';
import {
  apiToolQuery,
  docsListQuery,
  externalDocQuery,
  inboxesQuery,
  inboxMessagesQuery,
  intakeQuery,
  legalMailQuery,
  lettersQuery,
  lettersSettingsQuery,
  mailQuery,
  postalAddressesQuery,
  proposalQuery,
  proposalsQuery,
  skillQuery,
  skillRecentQuery,
  skillsQuery,
  skillTextQuery,
  skillUsageQuery,
  sourceDocsQuery,
  sourcesQuery,
  toolsQuery,
  websiteToolQuery,
} from './queries';
import { SkillsPage } from './SkillsPage';
import { ToolsPage } from './ToolsPage';

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
const cached = () => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data));
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

const source = {
  id: 'src1',
  title: 'Fern handbook',
  kind: 'github',
  status: 'ready',
  doc_count: 12,
  added_by: 'human:me',
  last_checked_at: '2026-11-18T00:00:00Z',
  url: 'https://github.com/example/handbook',
  description: '',
  reference_override: null,
};

describe('External sources', () => {
  beforeEach(() => {
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [], next_cursor: null });
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
    queryClient.setQueryData(sourceDocsQuery('src1').queryKey, { source, documents: [{ id: 'x1', title: 'Watering', path: 'care/watering.md' }] } as never);
    at('/library/docs?tab=sources&panel=src1', <DocsPage search={{ tab: 'sources', panel: 'src1' }} />);
    const doc = (await screen.findByText('Watering')).closest('a') as HTMLElement;
    expect(doc.getAttribute('href')).toMatch(/^\/library\/ext(:|%3A)x1$/);
    expect(screen.getByRole('link', { name: 'Open the source' }).getAttribute('href')).toBe(source.url);
    fireEvent.click(screen.getByRole('button', { name: 'Read again' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/doc-sources/src1/refresh', method: 'POST', body: {} }]));
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
    queryClient.setQueryData(sourceDocsQuery('src1').queryKey, { source, documents: [] } as never);
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

  it('a skill reads as its text without front matter, with who has it, its versions and Install @s782', async () => {
    serve();
    queryClient.setQueryData(skillQuery('triage').queryKey, {
      skill: { id: 's1', name: 'triage', description: 'Sort new mail', latest: v(2) },
      versions: [v(2), v(1)],
      installs: [{ id: 'i1', target: { type: 'Bot', id: 'b1', name: 'Mail helper' } }],
    } as never);
    queryClient.setQueryData(skillTextQuery('triage', 2).queryKey, { ...v(2), files: [{ path: 'SKILL.md', content: '---\ndescription: Sort new mail\n---\n\n# Steps\nRead each new letter first.' }] } as never);
    queryClient.setQueryData(skillUsageQuery.queryKey, [{ name: 'triage', uses: 4, last_used_at: '2026-11-18T00:00:00Z' }]);
    queryClient.setQueryData(skillRecentQuery('triage').queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    expect(await screen.findByText('Read each new letter first.')).toBeTruthy();
    expect(screen.queryByText(/description: Sort/)).toBeNull();
    expect(screen.getByText('Mail helper')).toBeTruthy();
    expect(screen.getByText('4 runs')).toBeTruthy();
    expect(screen.getByText('v1')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Install' })).toBeTruthy();
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

/* ---------------------------------------------------------------- tools */

const indexRow = (o: Record<string, unknown>) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who: [], who_total: 0, needs_you: false, detail: '', way: '', status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;
const KEY = { id: 'k1', name: 'Main key', preview: 'sk-…abcd', access_level: 'read_write', can_remove: true, expires_on: '2026-12-31', expires_source: null };
const apiTool = (o: Record<string, unknown> = {}) => ({
  tool: { id: 't1', name: 'Sample API', service: 'sample', base_url: 'https://api.example.com', auth_scheme: 'bearer', delivery: 'proxy', check_path: '/v1/me' },
  keys: [KEY, { ...KEY, id: 'k2', name: 'Vendor key', expires_source: 'vendor' }],
  access: [],
  checks: [],
  recent: [],
  settings: [{ id: 'set1', name: 'REGION', value: 'us', updated_at: '2026-11-01T00:00:00Z' }],
  status: { tone: 'ok', text: 'Working' },
  can_manage: true,
  can_set_audience: false,
  mcp: null,
  ...o,
});
const openApi = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
  queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
  at('/library/tools?tool=api%3At1', <ToolsPage search={{ tool: 'api:t1' }} />);
};
/** The open tool's own ⋯ (the list's head has one too). */
const toolMore = async (name: string) => {
  const tool = await screen.findByRole('region', { name });
  fireEvent.click(within(tool).getByRole('button', { name: 'More' }));
};

describe('an API tool’s dialogs', () => {
  it('Edit sends only what changed; Tico may not move a tool that holds a key @s912', async () => {
    const sent = serve();
    openApi();
    await toolMore('Sample API');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit API' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Sample API v2' } });
    fireEvent.change(within(dialog).getByLabelText('Base URL'), { target: { value: 'https://api2.example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1', method: 'PATCH', body: { name: 'Sample API v2', base_url: 'https://api2.example.com' } }]),
    );
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
    expect(await tico('tools.editApi', { tool: 't1', base_url: 'https://elsewhere.example' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.editApi', { tool: 't1', name: 'Renamed' })).ok).toBe(true);
  });

  it('adds a run setting by its name in capitals, and removes one @s913', async () => {
    const sent = serve((url, method) => (method === 'GET' && url === '/api/v2/api-tools/t1' ? apiTool() : undefined));
    openApi();
    await toolMore('Sample API');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add run setting' }));
    const add = await screen.findByRole('dialog', { name: 'Add run setting' });
    fireEvent.change(within(add).getByLabelText('Name'), { target: { value: 'account_id' } });
    fireEvent.change(within(add).getByLabelText('Value'), { target: { value: '42' } });
    fireEvent.click(within(add).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/settings', method: 'POST', body: { name: 'ACCOUNT_ID', value: '42' } }]));
    fireEvent.click(await screen.findByRole('button', { name: /^REGION/ }));
    const edit = await screen.findByRole('dialog', { name: 'REGION' });
    fireEvent.click(within(edit).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(writes(sent).at(-1)).toMatchObject({ url: '/api/v2/api-tools/t1/settings/set1', method: 'DELETE' }));
  });

  it('a key’s end date is set from its ⋯ and cleared with Never ends; a vendor’s date is not ours to set @s915', async () => {
    const sent = serve();
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Vendor key options' }));
    expect(await screen.findByRole('menuitem', { name: 'Show key' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Set end date' })).toBeNull();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    cleanup();
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Main key options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Set end date' }));
    const dialog = await screen.findByRole('dialog', { name: 'When Main key ends' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Never ends' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/keys/k1', method: 'PATCH', body: { expires_on: '' } }]));
  });

  it('Show key reads the value once into the dialog only, and is never Tico’s @s916', async () => {
    const FAKE = 'sk-shown-fake-0000';
    const sent = serve((url) => (url === '/api/v2/credentials/k1/reveal' ? { id: 'k1', value: FAKE } : undefined));
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Main key options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show key' }));
    const dialog = await screen.findByRole('dialog', { name: 'Main key' });
    expect(within(dialog).getByText('Seeing it is logged.')).toBeTruthy();
    expect(writes(sent)).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show' }));
    expect(await within(dialog).findByText(FAKE)).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/credentials/k1/reveal', method: 'POST', body: {} }]);
    expect(cached()).not.toContain(FAKE);
    expect(await tico('tools.reveal', { credential: 'k1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Add many keys shows what a paste holds by name first, then stores it in one step @s917', async () => {
    const PASTE = 'ACME_KEY=sk-fake-1234\nREGION=us';
    const sent = serve((url) =>
      url === '/api/v2/tool-templates/classify'
        ? { template: null, items: [{ role: 'secret', name: 'ACME_KEY', preview: 'sk-…1234' }, { role: 'setting', name: 'REGION', preview: 'us' }] }
        : url.endsWith('/paste')
          ? { keys: 1, settings: 1 }
          : undefined,
    );
    openApi();
    await toolMore('Sample API');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add many keys' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add many keys' });
    fireEvent.change(within(dialog).getByLabelText('Paste'), { target: { value: PASTE } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    expect(await within(dialog).findByText('ACME_KEY')).toBeTruthy();
    expect(within(dialog).queryByText('sk-fake-1234')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([
        { url: '/api/v2/tool-templates/classify', method: 'POST', body: { text: PASTE } },
        { url: '/api/v2/api-tools/t1/paste', method: 'POST', body: { text: PASTE } },
      ]),
    );
    expect(cached()).not.toContain('sk-fake-1234');
    expect(await tico('tools.pasteKeys', { tool: 't1', text: PASTE })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

const website = (o: Record<string, unknown> = {}) => ({
  tool: { id: 'w1', name: 'Sample site', sites: ['app.example.com'], central_only: false },
  accounts: [
    { id: 'a1', username: 'ops@example.com', kind: 'shared', has_password: true, has_totp: true, can_open: false, can_use: true, can_remove: false, holder: null, access: [], all_bots: false },
  ],
  recent: [],
  status: { tone: 'ok', label: 'Working' },
  can_manage: true,
  host: null,
  ...o,
});
const openSite = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'website:w1', kind: 'website', name: 'Sample site' })] } as never);
  queryClient.setQueryData(websiteToolQuery('w1').queryKey, website() as never);
  at('/library/tools?tool=website%3Aw1', <ToolsPage search={{ tool: 'website:w1' }} />);
};

describe('a website’s dialogs', () => {
  it('renames the website from its name in the head @s920', async () => {
    const sent = serve();
    openSite();
    const tool = await screen.findByRole('region', { name: 'Sample site' });
    fireEvent.click(within(tool).getByRole('heading', { name: 'Sample site' }));
    const name = within(tool).getByRole('textbox', { name: 'Name' });
    fireEvent.change(name, { target: { value: 'Shop admin' } });
    fireEvent.keyDown(name, { key: 'Enter' });
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/website-tools/w1', method: 'PATCH', body: { name: 'Shop admin' } }]));
  });

  it('changes the sites one login covers, one to a line; Tico may not add a site to one with logins @s921', async () => {
    const sent = serve();
    openSite();
    fireEvent.click(await screen.findByRole('button', { name: /^Site/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Sites' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Sites' }), { target: { value: 'app.example.com\nshop.example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/website-tools/w1', method: 'PATCH', body: { sites: ['app.example.com', 'shop.example.com'] } }]),
    );
    queryClient.setQueryData(websiteToolQuery('w1').queryKey, website() as never);
    expect(await tico('tools.editWebsite', { tool: 'w1', sites: ['app.example.com', 'other.example.com'] })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.editWebsite', { tool: 'w1', sites: [] })).ok).toBe(true);
  });

  it('shows the sign-in code the authenticator has now, spaced, in the dialog only @s922', async () => {
    const sent = serve((url) => (url === '/api/v2/website-tools/w1/accounts/a1/code' ? { code: '482913', expires_in: 30 } : undefined));
    openSite();
    fireEvent.click(await screen.findByRole('button', { name: 'ops@example.com options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show sign-in code' }));
    const dialog = await screen.findByRole('dialog', { name: 'Sign-in code' });
    expect(await within(dialog).findByText('482 913')).toBeTruthy();
    expect(sent.some((x) => x.url === '/api/v2/website-tools/w1/accounts/a1/code')).toBe(true);
    expect(cached()).not.toContain('482913');
    expect(await tico('tools.signInCode', { tool: 'w1', account: 'a1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('shows the stored password once, on Show, in the dialog only @s923', async () => {
    const FAKE = 'fake-pass-0000';
    const sent = serve((url) => (url === '/api/v2/credentials/a1/reveal' ? { password: FAKE } : undefined));
    openSite();
    fireEvent.click(await screen.findByRole('button', { name: 'ops@example.com options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show password' }));
    const dialog = await screen.findByRole('dialog', { name: 'Password' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show' }));
    expect(await within(dialog).findByText(FAKE)).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/credentials/a1/reveal', method: 'POST', body: {} }]);
    expect(cached()).not.toContain(FAKE);
  });
});

const box = { inbox_id: 'i1', kind: 'email', name: 'Logins', address: 'logins@in.example.test', state: 'active', audience: 'company', used_for: 'Bank sign-in codes' };
const openInbox = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'tico:i1', kind: 'tico', name: 'Logins', detail: box.address })] } as never);
  queryClient.setQueryData(inboxesQuery.queryKey, [box]);
  queryClient.setQueryData(inboxMessagesQuery('i1').queryKey, [{ message_id: 'm1', seq: 1, from: 'bank@example.test', subject: 'Your code', received_at: 1763500000 }] as never);
  return at('/library/tools?tool=tico%3Ai1', <ToolsPage search={{ tool: 'tico:i1' }} />);
};

describe('a Tico address’s dialogs', () => {
  it('a message that came in opens as plain text, a page at a time @s932', async () => {
    const sent = serve((url) =>
      url === '/api/v2/inbound/messages/m1'
        ? { text: 'Your code is <b>482913</b>.', next_offset: 30 }
        : url === '/api/v2/inbound/messages/m1?offset=30'
          ? { text: ' It ends in ten minutes.', next_offset: null }
          : undefined,
    );
    openInbox();
    fireEvent.click(await screen.findByRole('button', { name: /Your code/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Your code' });
    expect(await within(dialog).findByText('Your code is <b>482913</b>.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Read more' }));
    expect(await within(dialog).findByText('Your code is <b>482913</b>. It ends in ten minutes.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Read more' })).toBeNull();
    expect(sent.filter((x) => x.url.startsWith('/api/v2/inbound/messages/m1')).map((x) => x.url)).toEqual(['/api/v2/inbound/messages/m1', '/api/v2/inbound/messages/m1?offset=30']);
    expect(cached()).not.toContain('482913');
  });

  it('Edit renames it and says what depends on it @s935', async () => {
    const sent = serve();
    openInbox();
    await toolMore('Logins');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Bank logins' } });
    fireEvent.change(within(dialog).getByLabelText('Used for'), { target: { value: 'Bank and payroll codes' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes/i1', method: 'PATCH', body: { name: 'Bank logins', used_for: 'Bank and payroll codes' } }]),
    );
  });

  it('Stop using asks for the address typed back when something depends on it, and is never Tico’s @s936', async () => {
    const sent = serve();
    // Stopping is an owner's or admin's (as the old page); see tier2.tools.gap.test.tsx.
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    const router = openInbox();
    await toolMore('Logins');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Stop using' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Stop using Logins?' });
    const stop = within(dialog).getByRole('button', { name: 'Stop using' });
    expect(stop).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText(`Type ${box.address}`), { target: { value: box.address } });
    fireEvent.click(stop);
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes/i1/release', method: 'POST', body: { confirm: box.address } }]));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(await tico('tools.releaseInbox', { inbox: 'i1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Add tool’s mail dialogs', () => {
  const add = (adding: 'tico' | 'number' | 'mailbox' | 'address') => at('/library/tools', <AddDialog adding={adding} onClose={() => {}} />);

  it('a new Tico address takes a name, and opens @s933', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { inbox_id: 'i9' } : undefined));
    const router = add('tico');
    const dialog = await screen.findByRole('dialog', { name: 'New Tico address' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Account logins' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Make' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'tico:i9' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes', method: 'POST', body: { kind: 'email', name: 'Account logins' } }]);
  });

  it('asks for a US mobile number, which buys nothing, and opens the requests; Tico may ask too @s934', async () => {
    const sent = serve();
    const router = add('number');
    const dialog = await screen.findByRole('dialog', { name: 'Ask for a US mobile number' });
    expect(within(dialog).getByText(/nothing is bought now/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ask' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'number' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/phone-requests', method: 'POST', body: { name: 'Mobile number' } }]);
    // Asking buys nothing; accepting the quote stays the person's own.
    expect((await tico('tools.requestNumber', { name: 'Mobile number' })).ok).toBe(true);
    expect(await tico('tools.acceptQuote', { request: 'r1', price_micros: 1 })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('connects a mailbox by its address, shared or not, and opens it; never Tico’s @s938', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { mailbox: { id: 'mb1' } } : undefined));
    const router = add('mailbox');
    const dialog = await screen.findByRole('dialog', { name: 'Connect mailbox' });
    const go = within(dialog).getByRole('button', { name: 'Connect' });
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'sales' } });
    expect(go).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'Sales@Example.test' } });
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Shared inbox' }));
    fireEvent.click(go);
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'mailbox:mb1' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/mailboxes', method: 'POST', body: { address: 'sales@example.test', shared: true } }]);
    expect(await tico('tools.connectMailbox', { address: 'ops@example.test', shared: false })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('orders a mailing address by kind, state and company name; paid, so never Tico’s @s940', async () => {
    const sent = serve();
    add('address');
    const dialog = await screen.findByRole('dialog', { name: 'Get a mailing address' });
    expect(within(dialog).getByText('$10 a month.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Private' }));
    expect(await within(dialog).findByText('From $25 a month.')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Company name'), { target: { value: 'Sample Co' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Get address' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/postal/orders', method: 'POST', body: { kind: 'private', state: 'DE', company_name: 'Sample Co' } }]));
    expect(await tico('tools.orderAddress', { kind: 'shared', state: 'DE', company_name: 'Sample Co' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('the mailing address and letters', () => {
  const openAddress = (letters: unknown[]) => {
    queryClient.setQueryData(toolsQuery.queryKey, {
      rows: [indexRow({ id: 'address', kind: 'address', name: 'Mailing address', detail: '1 Fern Way, Dover DE', way: 'Tico', status: { tone: 'ok', text: 'Connected' } })],
    } as never);
    queryClient.setQueryData(postalAddressesQuery.queryKey, {
      addresses: [{ id: 'pa1', address: '1 Fern Way, Dover DE', status: 'active', state: 'DE', kind: 'shared' }],
      orders: [{ order_id: 'o1', state: 'TX', kind: 'private', status: 'open' }],
    } as never);
    queryClient.setQueryData(mailQuery.queryKey, [{ id: 'm1', kind: 'letter', summary: 'Tax notice', sender: 'County office', status: 'filed', received_at: '2026-11-17T00:00:00Z' }]);
    queryClient.setQueryData(lettersQuery.queryKey, letters as never);
    queryClient.setQueryData(lettersSettingsQuery.queryKey, { mode: 'ask_first', monthly_cap_micros: 50_000_000, return_address: null, availability: 'ready' } as never);
    queryClient.setQueryData(intakeQuery.queryKey, {});
    queryClient.setQueryData(legalMailQuery.queryKey, null as never);
    at('/library/tools?tool=address', <ToolsPage search={{ tool: 'address' }} />);
  };

  it('shows its addresses and orders, and the letters that came in @s868', async () => {
    serve();
    openAddress([]);
    const tool = await screen.findByRole('region', { name: 'Mailing address' });
    expect(within(tool).getAllByText('1 Fern Way, Dover DE').length).toBeGreaterThan(0);
    expect(within(tool).getAllByText('Connected').length).toBeGreaterThan(0);
    expect(within(tool).getByText('TX, Private')).toBeTruthy();
    expect(within(tool).getByText('Ordered')).toBeTruthy();
    expect(within(tool).getByText('County office')).toBeTruthy();
    expect(within(tool).getByText('Filed')).toBeTruthy();
  });

  it('lists the letters sent, a private one by its pages, and stops one before it is mailed @s869', async () => {
    const sent = serve();
    openAddress([
      { id: 'L1', status: 'mailed', can_cancel: false, created_at: '2026-11-16T00:00:00Z', redacted: false, recipient: { name: 'Sam Sample', city: 'Dover' }, page_count: 2 },
      { id: 'L2', status: 'waiting_approval', can_cancel: true, created_at: '2026-11-18T00:00:00Z', redacted: true, recipient: {}, page_count: 1 },
    ]);
    expect(await screen.findByText('Sam Sample, Dover')).toBeTruthy();
    expect(screen.getByText('Mailed')).toBeTruthy();
    const waiting = screen.getByText('A letter, 1 page').closest('.row') as HTMLElement;
    expect(within(waiting).getByText('Waits on approval')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Stop this letter' })).toHaveLength(1);
    fireEvent.click(within(waiting).getByRole('button', { name: 'Stop this letter' }));
    expect(await within(waiting).findByText('Stopped')).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/letters/L2/cancel', method: 'POST', body: {} }]);
  });
});

describe('the tools’ settings', () => {
  it('Email, number and letter settings are in the Tools head’s ⋯, each opening its tool @s871', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    const router = at('/library/tools', <ToolsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Email settings' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Number requests' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Letter settings' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'address' }));
    await act(async () => void (await run('tools.open', { tool: 'mailbox' }, { via: 'click' })));
    expect(router.state.location.search).toEqual({ tool: 'mailbox' });
  });
});

/* ---------------------------------------------------------------- outgoing webhooks, in Tools */

describe('an outgoing webhook in Tools', () => {
  it('shows where it sends and how its sends went, sends a test and pauses @s515', async () => {
    const sent = serve((url) =>
      url === '/api/v2/webhooks/wh1'
        ? {
            webhook: {
              id: 'wh1',
              name: 'Order feed',
              url: 'https://hooks.example.test/orders',
              host: 'hooks.example.test',
              events: ['task.created'],
              enabled: true,
              paused_reason: '',
              disabled_reason: null,
              can_change: true,
              last_7_days: { delivered: 9, failed: 1, pending: 0 },
            },
          }
        : url.startsWith('/api/v2/webhooks/wh1/sends')
          ? { items: [{ id: 'x1', type: 'task.created', subject: 'New task', state: 'failed', last_status: 500, last_error: 'Server error', created_at: '2026-11-18T00:00:00Z', delivered_at: null }] }
          : undefined,
    );
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'webhook:wh1', kind: 'webhook', name: 'Order feed' })] } as never);
    at('/library/tools?tool=webhook%3Awh1', <ToolsPage search={{ tool: 'webhook:wh1' }} />);
    expect(await screen.findByText('https://hooks.example.test/orders')).toBeTruthy();
    expect(screen.getByText('9 delivered, 1 failed, 0 waiting')).toBeTruthy();
    expect(await screen.findByText('Failed (500)')).toBeTruthy();
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Send test' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks/wh1/send', method: 'POST', body: { type: 'custom.test', subject: 'Test' } }]));
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/pause', method: 'POST', body: {} }));
  });
});
