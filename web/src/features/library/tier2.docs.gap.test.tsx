import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import './actions';
import { DocPage, wholeLinks } from './DocPage';
import {
  externalDocAtQuery,
  externalDocQuery,
  proposalQuery,
  proposalsQuery,
  skillDetailUsageQuery,
  skillQuery,
  skillsQuery,
  skillTextQuery,
  skillUsageQuery,
} from './queries';
import { SkillsPage } from './SkillsPage';

/* The tier-2 gap rows for Skills and an outside doc: removing an install, a proposal's Withdraw, Screen again and decided state, the Waiting filter, and an outside doc's links and commit.
   Made-up skills, agents and sources only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({ BrowserDialog: () => null }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['skills', 'access-role'], null);
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

const skill = (name: string, fn: string | null) => ({ id: `s-${name}`, name, description: `Does ${name}`, function: fn, latest: null, installs_count: 0, on: [] });
const v = (n: number) => ({ number: n, sha256: 'abc', approved_at: '2026-11-02T00:00:00Z', approved_by_id: 'human:me' });
function list(rows: unknown[]) {
  queryClient.setQueryData(skillsQuery.queryKey, rows as never);
  queryClient.setQueryData(proposalsQuery.queryKey, []);
  queryClient.setQueryData(skillUsageQuery.queryKey, []);
}

describe('a skill', () => {
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
});

describe('the Skills filter', () => {
  it('narrows the one list to the changes that wait, in the address @s804', async () => {
    serve();
    list([skill('triage', null)]);
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
});

describe('a proposed change', () => {
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

  it('its relative links lead to where it lives, and it shows the commit it was read at @s778', async () => {
    serve();
    queryClient.setQueryData(externalDocQuery('x1').queryKey, doc as never);
    at('/library/ext:x1', <DocPage doc="ext:x1" search={{}} />);
    const guide = (await screen.findByText('the guide')).closest('a');
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
