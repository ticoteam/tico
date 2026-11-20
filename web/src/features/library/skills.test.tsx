import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { newAddressFor } from '@/oldAddress/map';
import './actions';
import { forgetHelperCode, heldHelperCode, takeHelperCode } from './helperCode';
import {
  installTargetsQuery,
  proposalQuery,
  proposalsQuery,
  skillDetailUsageQuery,
  skillQuery,
  skillsQuery,
  skillTextQuery,
  skillUsageQuery,
} from './queries';
import { skillsSearch } from './search';
import { diffLines, mainText, SkillsPage } from './SkillsPage';

/* Skills: the one list, a skill and its installs, a proposed change and who may decide it, proposing a new one, and
   connecting Claude Code. What a person sees and presses, what is sent, and what Tico may not run. Made-up skills,
   agents and codes only. */

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
const owner = () => queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });

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
/** The Skills page reading its search from the address, as its route does. */
const Skills = () => <SkillsPage search={skillsSearch(useSearch({ strict: false }))} />;

const skill = (name: string, description = `Does ${name}`) => ({ id: `s-${name}`, name, description, latest: null, installs_count: 0, on: [] });
const v = (n: number) => ({ number: n, sha256: 'abc', approved_at: '2026-11-0' + n + 'T00:00:00Z', approved_by_id: 'human:me' });
/** The list's reads: these skills, no proposals, no runs. */
function list(rows: unknown[]) {
  queryClient.setQueryData(skillsQuery.queryKey, rows as never);
  queryClient.setQueryData(proposalsQuery.queryKey, []);
  queryClient.setQueryData(skillUsageQuery.queryKey, []);
}
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
});

describe('the list', () => {
  it('puts proposals first in one virtual list with the skills, no tabs, however large the catalog', async () => {
    serve();
    queryClient.setQueryData(
      skillsQuery.queryKey,
      Array.from({ length: 60 }, (_, i) => ({ id: `s${i}`, name: `Skill ${i}`, description: '', latest: null, installs_count: 0, on: [] })),
    );
    queryClient.setQueryData(proposalsQuery.queryKey, [
      {
        id: 'p1',
        name: 'Proposed skill',
        status: 'pending',
        description: '',
        note: null,
        base_version: null,
        version: 1,
        proposed_by: { type: 'Bot', id: 'b1' },
        task_id: null,
        created_at: '2026-11-01T00:00:00Z',
        updated_at: '2026-11-01T00:00:00Z',
      },
    ]);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    at('/library/skills', <SkillsPage search={{}} />);
    const list = await screen.findByRole('list', { name: 'Skills' });
    const links = within(list).getAllByRole('link');
    expect(links).toHaveLength(61);
    expect(links[0]?.getAttribute('href')).toBe('/library/skills?proposal=p1');
    expect(links.at(-1)?.getAttribute('href')).toBe('/library/skills?skill=Skill%2059');
    expect(screen.queryByRole('tab')).toBeNull();
  });

  it('draws skills Tico ships (no id) once each, with no duplicate keys', async () => {
    serve();
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    const shipped = (name: string) => ({ id: null, name, description: '', source: 'tico' as const, latest: null, installs_count: 0, on_count: 2, on: [] });
    queryClient.setQueryData(skillsQuery.queryKey, [
      shipped('plan-a-trip'),
      shipped('water-the-ferns'),
      { id: 's1', name: 'triage', description: '', latest: null, installs_count: 1, on: [] },
    ]);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    at('/library/skills', <SkillsPage search={{}} />);
    expect(await screen.findByText('water-the-ferns')).toBeTruthy();
    expect(screen.getAllByText('plan-a-trip')).toHaveLength(1);
    expect(errors.mock.calls.some((c) => String(c[0]).includes('same key'))).toBe(false);
    errors.mockRestore();
  });
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
  it('narrows the one list to the changes that wait, in the address @s804', async () => {
    serve();
    list([skill('triage')]);
    const waiting = { id: 'skp_1', name: 'new-one', status: 'pending', description: 'Does new-one', note: null, base_version: null, version: null, proposed_by: { type: 'Bot', id: 'b1' }, task_id: null, created_at: '2026-11-19T00:00:00Z', updated_at: '2026-11-19T00:00:00Z' };
    queryClient.setQueryData(proposalsQuery.queryKey, [waiting] as never);
    const router = at('/library/skills', <SkillsPage search={{}} />);
    expect(await screen.findByText('triage')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Waiting' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ waiting: 1 }));
    cleanup();
    at('/library/skills?waiting=1', <SkillsPage search={{ waiting: 1 }} />);
    expect(await screen.findByText('new-one')).toBeTruthy();
    expect(screen.queryByText('triage')).toBeNull();
  });
  describe('by function', () => {
    const tagged = (name: string, fn: string | null, description = '') => ({ ...skill(name, description), id: name, function: fn });
    const names = () => screen.queryAllByText(/^(nda|ads|pitch|plain|odd)$/).map((el) => el.textContent);
    const page = (search: Parameters<typeof skillsSearch>[0]) => at('/library/skills', <SkillsPage search={skillsSearch(search)} />);
    beforeEach(() => {
      serve();
      list([tagged('nda', 'legal', 'Review a contract'), tagged('ads', 'Marketing'), tagged('pitch', 'marketing', 'Contract pricing'), tagged('plain', null), tagged('odd', 'adtech')]);
    });

    it('keeps the function in the address, lower case', () => {
      expect(skillsSearch({ function: ' Legal ', q: 'nda' })).toEqual({ function: 'legal', q: 'nda' });
      expect(skillsSearch({ function: '' })).toEqual({});
    });

    it('shows one function; other is unknown or untagged; an unknown value only matches itself', async () => {
      page({ function: 'marketing' });
      await screen.findByText('ads');
      expect(names()).toEqual(['ads', 'pitch']);
      cleanup();
      page({ function: 'other' });
      await screen.findByText('plain');
      expect(names()).toEqual(['plain', 'odd']);
      cleanup();
      page({ function: 'adtech' });
      await screen.findByText('odd');
      expect(names()).toEqual(['odd']);
    });

    it('searches the function too, every word', async () => {
      page({ q: 'legal contract' });
      await screen.findByText('nda');
      expect(names()).toEqual(['nda']);
    });

    it('lists each function in the filter with its count under the search', async () => {
      page({ q: 'contract' });
      fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
      const items = (await screen.findAllByRole('menuitem')).map((el) => el.textContent);
      expect(items).toEqual(['All skills', 'Waiting', 'Legal1', 'Marketing1']);
    });
  });
});

describe('a skill', () => {
  it('reads as its text without front matter, with who has it, its versions and Install in its ⋯ @s782 @s831', async () => {
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
    queryClient.setQueryData(installTargetsQuery('triage').queryKey, { me: { type: 'Human', id: 'me' }, bots: [{ id: 'b1', name: 'Helper' }], people: [], teams: [] });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Install' }));
    expect(await screen.findByRole('dialog', { name: 'Install triage' })).toBeTruthy();
  });
  it('reads a shipped skill and shows which teammates have it', async () => {
    const sent = serve((url) =>
      url === '/api/v2/skills/triage'
        ? {
            skill: { id: null, name: 'triage', description: 'Sort new mail', latest: null },
            versions: [],
            installs: [],
            shipped: {
              files: [{ path: 'SKILL.md', content: 'Read each letter.' }],
              bots: [{ type: 'Bot', id: 'b1', name: 'Mail helper' }],
            },
          }
        : undefined,
    );
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    expect(await screen.findByText('Read each letter.')).toBeTruthy();
    expect(screen.getByText('Mail helper')).toBeTruthy();
    expect(sent.some(({ url }) => url.includes('/versions/'))).toBe(false);
  });
  it("Tico's library skill: Tico approved it, it is on its installs and its roles' agents once each, and installs", async () => {
    const sent = serve();
    const byTico = { ...v(1), approved_by_id: null };
    queryClient.setQueryData(skillQuery('triage').queryKey, {
      skill: { id: 's1', name: 'triage', description: 'Sort new mail', latest: byTico, from_tico: true },
      versions: [byTico],
      installs: [{ id: 'i1', target: { type: 'Bot', id: 'b1', name: 'Mail helper' } }],
      shipped: {
        bots: [
          { type: 'Bot', id: 'b1', name: 'Mail helper' },
          { type: 'Bot', id: 'b2', name: 'Desk agent' },
        ],
      },
    } as never);
    queryClient.setQueryData(skillTextQuery('triage', 1).queryKey, { ...byTico, files: [{ path: 'SKILL.md', content: 'Read each letter.' }] } as never);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    expect(await screen.findByText('Read each letter.')).toBeTruthy();
    expect(screen.getAllByText('Mail helper')).toHaveLength(1);
    expect(screen.getByText('Desk agent')).toBeTruthy();
    expect(screen.getByLabelText('Tico')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Install' }));
    expect(await screen.findByRole('dialog', { name: 'Install triage' })).toBeTruthy();
    // The picker asks for this skill's targets: the agents whose role ships it are left out by the server.
    await waitFor(() => expect(sent.some(({ url }) => url === '/api/v2/skills/install-targets?skill=triage')).toBe(true));
  });
  it("the company's own skill of a name Tico ships is on its installs only", async () => {
    serve();
    queryClient.setQueryData(skillQuery('triage').queryKey, {
      skill: { id: 's1', name: 'triage', description: 'Sort new mail', latest: v(1), from_tico: false },
      versions: [v(1)],
      installs: [{ id: 'i1', target: { type: 'Bot', id: 'b1', name: 'Mail helper' } }],
      shipped: { bots: [{ type: 'Bot', id: 'b2', name: 'Desk agent' }] },
    } as never);
    queryClient.setQueryData(skillTextQuery('triage', 1).queryKey, { ...v(1), files: [{ path: 'SKILL.md', content: 'Read each letter.' }] } as never);
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    expect(await screen.findByText('Mail helper')).toBeTruthy();
    expect(screen.queryByText('Desk agent')).toBeNull();
  });

  it('takes a skill off a teammate from the page’s ⋯, with each one’s runs beside it @s782', async () => {
    const detail = {
      skill: { id: 's1', name: 'triage', description: 'Sort new mail', latest: v(1) },
      versions: [v(1)],
      installs: [
        { id: 'i1', target: { type: 'Bot', id: 'b1', name: 'Mail helper' } },
        { id: 'i2', target: { type: 'Bot', id: 'b2', name: 'Desk helper' } },
      ],
    };
    // read again after the change: the server no longer has the first install
    const sent = serve((url, method) =>
      method !== 'GET' ? {} : url === '/api/v2/skills/triage' ? { ...detail, installs: detail.installs.slice(1) } : url.includes('/versions/') ? { version: { ...v(1), files: [] } } : undefined,
    );
    queryClient.setQueryData(skillQuery('triage').queryKey, detail as never);
    queryClient.setQueryData(skillTextQuery('triage', 1).queryKey, { ...v(1), files: [{ path: 'SKILL.md', content: 'Read each letter.' }] } as never);
    queryClient.setQueryData(skillDetailUsageQuery('triage').queryKey, {
      recent: [],
      installs: [
        { id: 'i1', target: { type: 'Bot', id: 'b1', name: 'Mail helper' }, uses_30d: 7, coverage: 'used', pinned_version: null, removable: true },
        { id: 'i2', target: { type: 'Bot', id: 'b2', name: 'Desk helper' }, uses_30d: 0, coverage: 'unknown', pinned_version: null, removable: false },
      ],
    });
    queryClient.setQueryData(skillUsageQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, []);
    at('/library/skills?skill=triage', <SkillsPage search={{ skill: 'triage' }} />);
    expect(await screen.findByText('7')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove from' }));
    // only the install the reader may take off is offered
    expect(screen.queryByRole('menuitem', { name: 'Desk helper' })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Mail helper' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove from Mail helper' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/skills/installs/i1', method: 'DELETE', body: null }]));
    await waitFor(() => expect(screen.queryByText('Mail helper')).toBeNull());
    expect(await tico('skills.removeInstall', { skill: 'triage', install: 'i2' })).toMatchObject({ ok: true });
  });
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
});

describe('a proposed change', () => {
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
  const mine = {
    id: 'skp_1',
    name: 'triage',
    status: 'pending',
    screen_status: 'not_screened',
    screen_action: 'Raise the spend cap, then screen it again.',
    description: 'Sort new mail',
    note: null,
    base_version: null,
    version: null,
    proposed_by: { type: 'Human', id: 'me' },
    task_id: null,
    created_at: '2026-11-18T00:00:00Z',
    updated_at: '2026-11-18T00:00:00Z',
  };

  it('its proposer withdraws it or sends it to screening again from the ⋯ @s783', async () => {
    const detail = { proposal: mine, files: [{ path: 'SKILL.md', content: 'Read it.' }], diff: '', stale: false, screening: null };
    const sent = serve((url, method) => (method === 'GET' && url === '/api/v2/skills/proposals/skp_1' ? detail : undefined));
    queryClient.setQueryData(proposalQuery('skp_1').queryKey, detail as never);
    queryClient.setQueryData(skillsQuery.queryKey, []);
    const router = at('/library/skills?proposal=skp_1', <SkillsPage search={{ proposal: 'skp_1' }} />);
    expect(await screen.findByText('Not screened')).toBeTruthy();
    expect(screen.getByText('Raise the spend cap, then screen it again.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Screen again' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/skills/proposals/skp_1/rescreen', method: 'POST', body: {} }]));
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Withdraw' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/skills'));
    expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/skills/proposals/skp_1/withdraw', method: 'POST', body: {} });
  });

  it('a decided one says how it ended and what to change, with nothing to press @s783', async () => {
    serve();
    owner();
    queryClient.setQueryData(proposalQuery('skp_1').queryKey, {
      proposal: { ...mine, status: 'declined', screen_status: 'screened', decline_reason: 'Keep the old first step', proposed_by: { type: 'Bot', id: 'b1' } },
      files: [{ path: 'SKILL.md', content: 'Read it.' }],
      diff: '',
      stale: false,
      screening: { verdict: 'safe', risk: 'low', summary: '' },
    } as never);
    queryClient.setQueryData(skillsQuery.queryKey, []);
    at('/library/skills?proposal=skp_1', <SkillsPage search={{ proposal: 'skp_1' }} />);
    expect(await screen.findByText('Sent back')).toBeTruthy();
    expect(screen.getByText('Keep the old first step')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('Tico may send a change back: it only keeps the skill as it is @s832', async () => {
    const sent = serve();
    at('/library/skills');
    expect(await tico('skills.decline', { proposal: 'skp_1', reason: 'Keep the old first step' })).toMatchObject({ ok: true });
    expect(writes(sent)).toEqual([{ url: '/api/v2/skills/proposals/skp_1/decline', method: 'POST', body: { reason: 'Keep the old first step' } }]);
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

  it('a member reads it only: no Approve, no Send back', async () => {
    server(null);
    at('/library/skills?proposal=skp_1', <Skills />);
    expect(await screen.findByRole('heading', { name: 'triage-calls' })).toBeTruthy();
    await screen.findByText('Ask who is calling.');
    await waitFor(() => expect(queryClient.getQueryState(['skills', 'access-role'])?.status).toBe('success'));
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Send back' })).toBeNull();
  });

  it('an admin sees Approve and Send back (the owner, in "a proposed change")', async () => {
    server('admin');
    at('/library/skills?proposal=skp_1', <Skills />);
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send back' })).toBeTruthy();
  });

  it('the list says it waits on you only to a decider; a member reads who it waits on', async () => {
    const access = (you: string | null) =>
      vi.stubGlobal('fetch', async (url: string) => {
        const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
        if (url === '/api/v2/access') return you ? json(200, { you }) : json(403, { error: { code: 'forbidden', detail: 'Owners and admins only' } });
        return json(200, { skills: [], proposals: [proposal] });
      });
    access(null);
    queryClient.setQueryData(skillsQuery.queryKey, []);
    queryClient.setQueryData(proposalsQuery.queryKey, [proposal]);
    at('/library/skills', <Skills />);
    await waitFor(() => expect(queryClient.getQueryState(['skills', 'access-role'])?.status).toBe('success'));
    const row = (await screen.findByText('triage-calls')).closest('a')!;
    expect(within(row).getByText('Waits on an admin')).toBeTruthy();
    expect(within(row).queryByRole('img', { name: 'Waits on you' })).toBeNull();
    cleanup();
    access('admin');
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

describe('connect Claude Code', () => {
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
  it('opens the setup steps only: a code is never taken into the address', async () => {
    serve();
    const router = at('/library/skills');
    expect((await run('skills.connect', { code: 'WXYZ-2345' }, { via: 'click' })).error).toMatch(/^bad_input/);
    expect(router.state.location.search).toEqual({});
    expect((await run('skills.connect', {}, { via: 'click' })).ok).toBe(true);
    expect(router.state.location.search).toEqual({ connect: 'setup' });
  });
});

describe("Connect Claude Code from the helper's old link", () => {
  const CODE = 'WXYZ-2345';

  /** The Skills page at `path`, as the app opens it: the server knows the code, and the list is empty. */
  function openSkills(path = '/library/skills') {
    // The page draws once its list is in: an empty one.
    serve((url) => (url.endsWith(`/skills/devices/codes/${CODE}`) ? { user_code: CODE, label: 'Studio laptop', expires_at: '2026-11-19T08:00:00Z' } : { skills: [], proposals: [] }));
    return at(path, <Skills />);
  }

  it('takes the code out of the address at once, and keeps it out of the cache', async () => {
    const router = openSkills(newAddressFor(`/#/skills/connect?code=${CODE}`) ?? '');
    const dialog = await screen.findByRole('dialog', { name: 'Connect Claude Code' });
    expect(await within(dialog).findByText(CODE)).toBeTruthy();
    await waitFor(() => expect(router.state.location.href).toBe('/library/skills'));
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
    expect(skillsSearch({ connect: 'https://evil.example' })).toEqual({});
  });

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
