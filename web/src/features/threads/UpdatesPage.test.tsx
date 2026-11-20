import { InfiniteQueryObserver, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { updateListQuery, updateQuery } from './queries';
import { UpdatesPage } from './UpdatesPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

// jsdom lays nothing out: give every element a box so the virtual lists have a window to fill.
const saved = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'clientHeight', { configurable: true, get: () => 600 });
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
afterAll(() => {
  if (saved) Object.defineProperty(Element.prototype, 'clientHeight', saved);
});

type Reply = { status: number; body: unknown };
const calls: { url: string; method: string; body: unknown }[] = [];
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
/** `reply` answers what the test is about; anything else gets an empty list. */
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const r = reply(String(url), method) ?? ok({ items: [], counts: [] });
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const all = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split('?')[0]!.endsWith(path));
const feed = (updates: unknown[], more: Record<string, unknown> = {}) => ok({ updates, unread: 0, next_before: null, ...more });

function show(props: Parameters<typeof UpdatesPage>[0], at: string) {
  const root = createRootRoute({ component: () => <UpdatesPage {...props} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const update = (id: string, kind: string, day: string, body: string, extra: Record<string, unknown> = {}) => ({
  id,
  bot: 'ops',
  kind,
  day,
  body,
  read: true,
  created_at: `${day}T15:00:00.000Z`,
  ...extra,
});

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
});
afterEach(() => vi.unstubAllGlobals());

describe('Updates, one feed @s972 @s986 @s987', () => {
  it('says so when there are none', async () => {
    serve(() => feed([]));
    show({}, '/updates');
    expect(await screen.findByRole('heading', { name: 'No updates yet' })).toBeTruthy();
  });

  it('finishes loading the page when the menu stops watching the same feed', async () => {
    const page = { updates: [], unread: 0, next_before: null };
    let answer!: (response: Response) => void;
    vi.stubGlobal('fetch', () => new Promise<Response>((resolve) => (answer = resolve)));
    const options = updateListQuery('all');
    const menu = new InfiniteQueryObserver(queryClient, options);
    const stop = menu.subscribe(() => {});
    const loading = queryClient.ensureInfiniteQueryData(options);
    stop();
    answer(new Response(JSON.stringify(page), { headers: { 'Content-Type': 'application/json' } }));
    await expect(loading).resolves.toMatchObject({ pages: [page] });
  });

  it('puts every kind in one feed, a section a day, newest day first', async () => {
    serve((url) =>
      url.includes('/api/v2/updates')
        ? feed([
            update('u1', 'daily', '2026-01-01', 'Fixed the login bug'),
            update('u2', 'weekly', '2026-01-02', 'A good week', { created_at: '2026-01-02T18:00:00.000Z' }),
            update('u3', 'daily', '2026-01-02', 'Shipped the page'),
          ])
        : undefined,
    );
    show({}, '/updates');
    const list = await screen.findByRole('feed', { name: 'Updates' });
    await within(list).findByTitle('A good week');
    const order = [...list.querySelectorAll('[title]')].map((el) => el.getAttribute('title'));
    expect(order.filter((t) => t !== null && ['A good week', 'Shipped the page', 'Fixed the login bug'].includes(t))).toEqual(['A good week', 'Shipped the page', 'Fixed the login bug']);
    expect(within(list).getByText('Fri, Jan 2')).toBeTruthy();
    expect(within(list).getByText('Thu, Jan 1')).toBeTruthy();
  });
});

describe('Updates tabs @s984 @s985', () => {
  it('Daily and Weekly read only their kind', async () => {
    serve((url) => (url.includes('/api/v2/updates') ? feed([update('u1', 'daily', '2026-01-02', 'Shipped the page')]) : undefined));
    show({ tab: 'daily' }, '/updates/daily');
    expect(await screen.findByTitle('Shipped the page')).toBeTruthy();
    expect(calls.some((c) => c.url.includes('/api/v2/updates?kind=daily'))).toBe(true);
    expect(screen.getByRole('link', { name: 'Daily' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: 'Weekly' }).getAttribute('href')).toBe('/updates/weekly');
  });
});

describe('Updates: search, weeks and an opened update @s972 @s985 @s992', () => {
  it('looks past pages without a match and reports no matches when the feed is exhausted', async () => {
    const height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(35);
    const recent = Array.from({ length: 50 }, (_, i) => update(`recent-${i}`, 'daily', '2026-01-07', `Recent note ${i}`));
    serve((url) => {
      if (!url.startsWith('/api/v2/updates?')) return;
      const cursor = new URL(url, location.origin).searchParams.get('before');
      if (cursor === 'second') return feed([update('older', 'daily', '2026-01-05', 'Winter inventory')]);
      if (cursor === 'first') return feed([update('middle', 'daily', '2026-01-06', 'A middle note')], { next_before: 'second' });
      return feed(recent, { next_before: 'first' });
    });
    try {
      show({}, '/updates');
      expect(await screen.findByRole('feed', { name: 'Updates' })).toBeTruthy();
      expect(all('GET', '/api/v2/updates')).toHaveLength(1);
      const search = screen.getByRole('textbox', { name: 'Find an update' });
      fireEvent.change(search, { target: { value: 'winter' } });
      expect(screen.queryByRole('heading', { name: 'No matches' })).toBeNull();
      expect(await screen.findByTitle('Winter inventory')).toBeTruthy();
      fireEvent.change(search, { target: { value: 'spring' } });
      expect(await screen.findByRole('heading', { name: 'No matches' })).toBeTruthy();
    } finally {
      height.mockRestore();
    }
  });

  it('Weekly asks for weekly ones, opens them in Weekly, finds them by their words, and reads a section a week', async () => {
    serve((url) => (url.includes('/api/v2/updates') ? feed([update('u1', 'weekly', '2026-01-07', 'Cash is up'), update('u2', 'weekly', '2026-01-05', 'Ads cost less')]) : undefined));
    show({ tab: 'weekly' }, '/updates/weekly');
    const list = await screen.findByRole('feed', { name: 'Updates' });
    expect((await within(list).findByTitle('Cash is up')).getAttribute('href')).toBe('/updates/weekly?open=u1');
    expect(calls.some((c) => c.url.includes('/api/v2/updates?kind=weekly'))).toBe(true);
    expect(within(list).getByText('Mon, Jan 5')).toBeTruthy();
    expect(within(list).queryByText('Wed, Jan 7')).toBeNull();
    fireEvent.change(screen.getByRole('textbox', { name: 'Find an update' }), { target: { value: 'ads' } });
    await waitFor(() => expect(within(list).queryByTitle('Cash is up')).toBeNull());
    expect(within(list).getByTitle('Ads cost less')).toBeTruthy();
  });

  it('reads its body as written, headings kept, and dates it in full', async () => {
    const u = update('u1', 'daily', '2026-01-02', 'Cash summary\nThe report is ready.\n\n## What changed\n\nRefunds doubled.', { created_at: '2026-01-02T15:00:00.000Z' });
    serve((url) => (url.includes('/api/v2/updates/u1') ? ok({ update: u }) : url.includes('/api/v2/updates?') ? feed([u]) : undefined));
    show({ open: 'u1' }, '/updates?open=u1');
    expect(await screen.findByRole('heading', { name: 'What changed' })).toBeTruthy();
    expect(screen.getByText('The report is ready.')).toBeTruthy();
    expect(screen.getByText(/^Fri, Jan 2, \d+:00 [AP]M$/)).toBeTruthy();
  });
});

describe('An update, open @s992', () => {
  it('reads in full, is marked read on opening, and its writer is a press away', async () => {
    const u = update('u1', 'daily', '2026-01-02', 'Shipped the page\n\nThe launch page is live.', { read: false });
    serve((url) => (url.includes('/api/v2/updates/u1') ? ok({ update: u }) : url.includes('/api/v2/updates?') ? feed([u], { unread: 1 }) : undefined));
    show({ open: 'u1' }, '/updates?open=u1');
    expect(await screen.findByText('The launch page is live.')).toBeTruthy();
    await waitFor(() => expect(all('POST', '/api/v2/updates/read')[0]?.body).toMatchObject({ ids: ['u1'] }));
    expect(screen.getByRole('link', { name: 'Chat with Ops' }).getAttribute('href')).toContain('chat=agent%3Aops');
  });

  it('keeps an already-read update unread after choosing Mark unread', async () => {
    const u = update('u1', 'daily', '2026-01-02', 'Shipped the page');
    serve((url, method) => {
      if (url.endsWith('/updates/read') && method === 'POST') {
        u.read = (calls.at(-1)?.body as { read: boolean }).read;
        return ok({});
      }
      if (url.endsWith('/updates/u1')) return ok({ update: u });
      if (url.includes('/updates?')) return ok({ updates: [u], unread: u.read ? 0 : 1 });
    });
    show({ open: 'u1' }, '/updates?open=u1');
    await screen.findByRole('heading', { name: 'Shipped the page' });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Mark unread' }));
    await waitFor(() => expect(queryClient.getQueryData(updateQuery('u1').queryKey)?.read).toBe(false));
    await waitFor(() => expect(queryClient.getQueryState(updateQuery('u1').queryKey)?.fetchStatus).toBe('idle'));
    expect(all('POST', '/updates/read').map((c) => c.body)).toEqual([{ ids: ['u1'], read: false }]);
  });
});

describe('Updates: Archive @s991', () => {
  const up = (id: string, body: string, archived: boolean) => ({ ...update(id, 'daily', '2026-01-02', body), bot: 'qa', archived });

  it('is its own page under Updates, listing only archived updates', async () => {
    serve((url) => (url.includes('archive=true') ? feed([up('u1', 'Old plan', true), up('u2', 'Moved back', false)]) : ok()));
    show({ tab: 'archive' }, '/updates/archive');
    expect(await screen.findByRole('heading', { name: 'Archive' })).toBeTruthy();
    expect((await screen.findByTitle('Old plan')).getAttribute('href')).toBe('/updates/archive?open=u1');
    expect(screen.queryByTitle('Moved back')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark read' })).toBeNull();
  });

  it('opens from ⋯ on Updates', async () => {
    const old = up('u1', 'Old plan', true);
    serve((url) => {
      if (url.includes('/api/v2/updates/u1')) return ok({ update: old });
      if (url.includes('/api/v2/updates')) return feed(url.includes('archive=true') ? [old] : []);
    });
    const router = show({}, '/updates');
    expect((await run('updates.openArchive', {}, { via: 'click' })).ok).toBe(true);
    expect(router.state.location.pathname).toBe('/updates/archive');
  });

  it('moves an open archived update back to Updates', async () => {
    const old = up('u1', 'Old plan', true);
    serve((url) => {
      if (url.includes('/api/v2/updates/u1')) return ok({ update: old });
      if (url.includes('/api/v2/updates/archive')) return ok({});
      return feed([old]);
    });
    show({ tab: 'archive', open: 'u1' }, '/updates/archive?open=u1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move back to Updates' }));
    await waitFor(() => expect(all('POST', '/api/v2/updates/archive').at(-1)?.body).toEqual({ ids: ['u1'], archived: false }));
  });
});
