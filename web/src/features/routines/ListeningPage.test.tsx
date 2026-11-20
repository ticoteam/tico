import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import { ListeningPage } from './ListeningPage';
import { validateListeningSearch } from './load';
import { watchesQuery, watchQuery } from './queries';
import type { Routine } from './types';

/* Listening: the watches, one watch's sources and matches. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const nothing = (): Reply => ({ status: 404, body: { error: { code: 'not_found', detail: 'Not found' } } });
/** Answers every call with `reply`, keeping each write the page sends. */
function record(reply: (url: string, method: string) => Reply = nothing): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const pick = async (name: string | RegExp, option: string | RegExp, scope: HTMLElement) => {
  fireEvent.click(within(scope).getByRole('combobox', { name }));
  const o = await screen.findByRole('option', { name: option });
  fireEvent.pointerDown(o, { pointerType: 'mouse' });
  fireEvent.click(o);
};
const href = (el: HTMLElement) => decodeURIComponent((el.tagName === 'A' ? el : el.closest('a'))?.getAttribute('href') ?? '');

/** The page at its own address, reading its search from the router so an action's move lands, with the action layer bound. */
function show(at: string) {
  const Live = () => <ListeningPage search={useSearch({ strict: false })} />;
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root,
    path: '/company/listening',
    validateSearch: validateListeningSearch,
    component: () => (
      <>
        <Live />
        <ActionsRoot dot={() => null} fold={() => {}} />
      </>
    ),
  });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const watch = (over: Partial<Routine> = {}) =>
  ({
    id: 'w1',
    name: 'Mentions of us',
    enabled: true,
    can_edit: true,
    can_pause: true,
    owner_id: 'human:me',
    trigger: { type: 'listening', sources: [{ id: 'a', kind: 'search', site: 'reddit.com', target: 'raised bed' }], rules: [], limits: [{ count: 10, per: 'day' }], at: '08:00', tz: 'UTC' },
    steps: [],
    ...over,
  }) as unknown as Routine;

describe('The watches', () => {
  it('lists each watch, opening it', async () => {
    record();
    queryClient.setQueryData(watchesQuery.queryKey, [watch()]);
    show('/company/listening');
    const row = await screen.findByTitle('Mentions of us');
    expect(href(row)).toBe('/company/listening?watch=w1');
    expect(within(row).getByText('1 source')).toBeTruthy();
    expect(within(row).getByText('10 a day')).toBeTruthy();
  });

  it('with none says so, and its owner is a face in the head that opens their chat @s516', async () => {
    const auto = { id: 'bot:auto', kind: 'agent', name: 'Automations Owner' };
    queryClient.setQueryData(['directory'], { actors: [auto], byId: { [auto.id]: auto } });
    record((u) => (u.includes('/api/v2/page-owners') ? ok({ pages: { listening: { bot: 'auto' } } }) : nothing()));
    queryClient.setQueryData(watchesQuery.queryKey, []);
    show('/company/listening');
    expect(await screen.findByText('No watches yet')).toBeTruthy();
    const face = await screen.findByRole('link', { name: 'Chat with Automations Owner' });
    expect(href(face)).toContain('chat=agent:auto');
  });

  it('the watches are searched in the address, and one not reading says so @s516', async () => {
    record();
    queryClient.setQueryData(watchesQuery.queryKey, [watch({ listening: { dot: 'hollow' } }), watch({ id: 'w2', name: 'Seed talk', trigger: { type: 'listening', sources: [], limits: [] } as Routine['trigger'] })]);
    show('/company/listening?q=raised');
    const row = await screen.findByTitle('Mentions of us');
    expect(within(row).getByText('Not reading')).toBeTruthy();
    expect(screen.queryByTitle('Seed talk')).toBeNull();
    expect(validateListeningSearch({ q: 'raised' })).toEqual({ q: 'raised' });
  });

  it('adds a watch, off and with no sources, and opens its sources; a paused one is marked @s516', async () => {
    const sent = record((url, method) => (method === 'POST' && url.endsWith('/api/v2/automations') ? ok(watch({ id: 'w9', name: 'Seed talk', enabled: false })) : nothing()));
    queryClient.setQueryData(watchesQuery.queryKey, [watch({ enabled: false })]);
    const router = show('/company/listening');
    expect((await screen.findByTitle('Mentions of us')).className).toContain('is-off');
    fireEvent.click(screen.getByRole('button', { name: 'Add watch' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add watch' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Seed talk' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({ name: 'Seed talk', enabled: false, trigger: { type: 'listening', sources: [] } });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ watch: 'w9', tab: 'sources' }));
  });
});

describe('One watch', () => {
  it('shows the health of a source when its storage id differs from its trigger key', async () => {
    const w = watch({ trigger: { type: 'listening', sources: [{ id: 'release-feed', kind: 'github', target: 'sample/widgets' }] } as Routine['trigger'] });
    queryClient.setQueryData(watchesQuery.queryKey, [w]);
    record((url) => {
      if (url.endsWith('/automations/w1')) return ok(w);
      if (url.startsWith('/api/v2/listening/sources?')) return ok({ sources: [{ id: 'db-source-1', key: 'release-feed', kind: 'github', status: 'blocked' }] });
      return nothing();
    });
    show('/company/listening?watch=w1&tab=sources');
    const source = within(await screen.findByTitle('sample/widgets'));
    expect(await source.findByText('Blocked')).toBeTruthy();
  });

  it('its sources, and Add source puts one in its trigger @s540', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/w1') ? ok(watch()) : nothing()));
    queryClient.setQueryData(watchQuery('w1').queryKey, watch());
    show('/company/listening?watch=w1&tab=sources');
    expect(within(await screen.findByRole('region', { name: 'Sources' })).getByTitle('raised bed')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Matches' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add source' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Words' }), { target: { value: 'seed kit' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Site' }), { target: { value: 'reddit.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const sources = (sent[0]!.body as { trigger: { sources: { kind: string; target: string; site?: string }[] } }).trigger.sources;
    expect(sources).toHaveLength(2);
    expect(sources[1]).toMatchObject({ kind: 'search', target: 'seed kit', site: 'reddit.com' });
  });

  it('renamed, its rules changed, and a GitHub source added; a push source is not Tico’s @s540', async () => {
    const sent = record((url, method) => (method === 'PATCH' && url.endsWith('/automations/w1') ? ok(watch()) : nothing()));
    queryClient.setQueryData(watchQuery('w1').queryKey, watch());
    show('/company/listening?watch=w1&tab=sources');
    await screen.findByRole('region', { name: 'Sources' });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit watch' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Brand mentions' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add rule' }));
    const rule = within(dialog).getByRole('group', { name: 'Rule 1' });
    fireEvent.change(within(rule).getByRole('textbox', { name: 'Tag' }), { target: { value: 'asks' } });
    fireEvent.change(within(rule).getByRole('textbox', { name: 'Sentence' }), { target: { value: 'Someone asks for a kit' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({
      name: 'Brand mentions',
      trigger: { type: 'listening', rules: [{ do: 'keep', tag: 'asks', text: 'Someone asks for a kit' }], limits: [{ count: 10, per: 'day' }] },
    });

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Edit watch' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Add source' }));
    const add = await screen.findByRole('dialog', { name: 'Add source' });
    fireEvent.click(within(within(add).getByRole('group', { name: 'Kind' })).getByRole('button', { name: 'GitHub' }));
    fireEvent.change(within(add).getByRole('textbox', { name: 'Repository' }), { target: { value: 'https://github.com/acme/widgets' } });
    await pick('Follow', 'Releases', add);
    fireEvent.click(within(add).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toHaveLength(2));
    const sources = (sent[1]!.body as { trigger: { sources: unknown[] } }).trigger.sources;
    expect(sources[1]).toMatchObject({ kind: 'github', site: 'github', target: 'acme/widgets', follow: 'releases' });
    expect((await run('listening.addSource', { watch: 'w1', kind: 'push', site: 'Support form' }, { via: 'tico' })).ok).toBe(false);
  });

  it('with no matches says so in the page, and one that failed has Try again @s540', async () => {
    let n = 0;
    record((u) => (u.includes('/listening/items') ? (n++ ? ok({ items: [] }) : { status: 500, body: { error: { code: 'boom', detail: 'boom' } } }) : nothing()));
    queryClient.setQueryData(watchQuery('w1').queryKey, watch({ trigger: { type: 'listening', sources: [] } as unknown as Routine['trigger'] }));
    show('/company/listening?watch=w1');
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No matches yet')).toBeTruthy();
  });
});
