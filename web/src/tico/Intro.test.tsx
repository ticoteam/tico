import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { liveQuery, sendToDm, ticoRoomQuery, timelineQuery } from '@/conversation';
import { directoryQuery, meQuery, pageOwnersQuery, queryClient } from '@/data';
import { subscriptionsQuery } from '@/features/settings/routing';
import { introCard, PageIntro, resetIntros, useIntroMessage } from './Intro';
import { askReplay, isShowMe } from './intros';
import { introsSeenQuery, ticoPowerQuery } from './queries';

vi.mock('@/conversation', async (orig) => ({ ...(await orig<object>()), sendToDm: vi.fn(async () => null) }));

const ROOM = 'room-1';
const GM = 'bot:growth-manager';

/** Tico's chat as TicoSheet draws an intro: its message, drawn by `introCard`. */
function Chat() {
  const messages = useIntroMessage('assistant');
  return (
    <aside aria-label="Tico">
      {messages?.map((m) => (
        <div key={m.id} className="msg">
          {introCard(m)}
        </div>
      ))}
    </aside>
  );
}

interface Seed {
  role?: string;
  seen?: Record<string, string>;
  plan?: boolean;
  lifecycle?: string;
}

function seed({ role = 'owner', seen = {}, plan = true, lifecycle = 'active' }: Seed = {}) {
  queryClient.setQueryData(meQuery.queryKey, { id: role === 'agent' ? 'bot:ops' : 'human:maya', email: '', role, slug: null } as never);
  queryClient.setQueryData(ticoRoomQuery.queryKey, ROOM);
  queryClient.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
  queryClient.setQueryData(introsSeenQuery.queryKey, seen);
  queryClient.setQueryData(timelineQuery(ROOM).queryKey, { pages: [{ messages: [], next_cursor: null }], pageParams: [null] } as never);
  queryClient.setQueryData(subscriptionsQuery.queryKey, { subscriptions: plan ? [{ id: 's1', status: 'ok', in_use_on: [] }] : [] } as never);
  queryClient.setQueryData(pageOwnersQuery.queryKey, { pages: { growth: { bot: GM, own: true } }, canChange: false });
  const gm = { id: GM, kind: 'agent', name: 'Growth Manager', slug: 'growth-manager', lifecycle };
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [gm], byId: { [GM]: gm, 'growth-manager': gm } } as never);
}

/** The app at `path`, with `page` as its page sheet (none: a beat at the page is skipped at once). A fresh one each
    time: the last one is taken down. */
function mount(path: string, page: ReactNode = null) {
  cleanup();
  const root = createRootRoute({
    component: () => (
      <>
        {page && <main className="pane page">{page}</main>}
        <Chat />
        <PageIntro />
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [path] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const chat = () => screen.getByRole('complementary', { name: 'Tico' });
const saved = () => vi.mocked(api.post).mock.calls.filter(([path]) => path === '/api/v2/preferences/intros.seen').map(([, body]) => (body as { value: Record<string, string> }).value);

beforeEach(() => {
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.spyOn(api, 'get').mockRejectedValue(new Error('not in this test'));
  vi.spyOn(api, 'post').mockResolvedValue({});
});

afterEach(() => {
  act(() => resetIntros());
  queryClient.clear();
  delete document.documentElement.dataset.layout;
  vi.restoreAllMocks();
  vi.mocked(sendToDm).mockClear();
});

describe('page intros', () => {
  it('play once per person: in Tico’s chat with the page’s pill, marked seen, and not again on the next visit', async () => {
    seed();
    const router = mount('/company/rules');
    expect(await screen.findByText('First visit · Rules')).toBeTruthy();
    expect(chat().textContent).toContain('Each shows who made it.');
    expect(saved()).toEqual([{ rules: expect.any(String) }]);

    await act(() => router.navigate({ to: '/library/docs' as never }));
    expect(await screen.findByText('First visit · Docs')).toBeTruthy();
    expect(screen.queryByText('First visit · Rules')).toBeNull();

    await act(() => router.navigate({ to: '/company/rules' as never }));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(chat().textContent).toBe('');
    expect(saved().at(-1)).toEqual({ rules: expect.any(String), docs: expect.any(String) });
  });

  it('do not play where this person has had it, nor ever for an agent', async () => {
    seed({ seen: { rules: '2026-11-20T10:00:00.000Z' } });
    mount('/company/rules');
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(chat().textContent).toBe('');
    act(() => resetIntros());
    queryClient.clear();

    seed({ role: 'agent' });
    mount('/company/rules');
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(chat().textContent).toBe('');
    expect(saved()).toEqual([]);
  });

  it('wait while Tico is answering, then play at the next quiet moment', async () => {
    seed();
    const run = { run: 'r1', bot: 'bot:assistant', parts: [], typing: null, doing: 'think' as const, ended: false };
    queryClient.setQueryData(liveQuery(ROOM).queryKey, [run]);
    mount('/company/routines');
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(chat().textContent).toBe('');
    expect(saved()).toEqual([]);

    act(() => queryClient.setQueryData(liveQuery(ROOM).queryKey, [{ ...run, ended: true }]));
    expect(await screen.findByText('First visit · Routines')).toBeTruthy();
  });

  it('wait while the first run is open over the app', async () => {
    seed();
    const router = mount('/company/usage?setup=name');
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(chat().textContent).toBe('');
    await act(() => router.navigate({ to: '/company/usage' as never }));
    expect(await screen.findByText('First visit · Usage')).toBeTruthy();
  });

  it('go with Got it, leaving the page as it was, and come back on "Show me how this works"', async () => {
    seed();
    mount('/company/finance');
    await screen.findByText('First visit · Finance');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(chat().textContent).toBe('');
    expect(saved()).toEqual([{ finance: expect.any(String) }]);

    // typed to Tico: answered here, with nothing sent
    expect(isShowMe('Show me how this works?')).toBe(true);
    expect(isShowMe('show me how this works for Leo')).toBe(false);
    act(() => void askReplay('/company/finance'));
    // a replay is the page's name alone, and its chip runs the app's own action
    expect(await screen.findByText('Finance')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect bank' })).toBeTruthy();
    expect(askReplay('/tasks/p1/7')).toBe(false);
  });

  it('point at the page’s own elements on a computer, and on a phone are one card with the beats as words', async () => {
    seed();
    const tool = <div data-target="tool:shopify">Shopify</div>;
    const box = { top: 10, left: 10, width: 120, height: 30, right: 130, bottom: 40, x: 10, y: 10, toJSON: () => ({}) };
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(box as DOMRect);
    mount('/library/tools', tool);
    await screen.findByText('First visit · Tools');
    expect(document.querySelector('.intro-spot')?.textContent).toBe('Tico holds the keys');
    expect(document.querySelector('.intro-float')).toBeNull();
    act(() => resetIntros());
    queryClient.clear();

    document.documentElement.dataset.layout = 'cards';
    seed();
    mount('/library/tools', tool);
    const card = await waitFor(() => {
      const c = document.querySelector<HTMLElement>('.intro-float');
      expect(c).toBeTruthy();
      return c!;
    });
    expect(card.textContent).toContain('First visit · Tools');
    expect(card.querySelector('.intro-beats')?.textContent).toBe('Tico holds the keys');
    expect(card.querySelector('.intro-beats button')).toBeNull();
    expect(document.querySelector('.intro-spot')).toBeNull();
    expect(chat().textContent).toBe('');
  });

  it('ask the page’s owner for one real addition, once, only with a connected plan and the agent on the team', async () => {
    seed();
    mount('/company/growth');
    await screen.findByText('First visit · Growth');
    await waitFor(() => expect(sendToDm).toHaveBeenCalledOnce());
    const [agent, text, from] = vi.mocked(sendToDm).mock.calls[0]!;
    expect(agent).toBe(GM);
    expect(text).toContain('Propose one real addition');
    expect(from).toEqual({ page: 'growth', label: 'Growth' });
    // a replay asks nothing
    act(() => void askReplay('/company/growth'));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(sendToDm).toHaveBeenCalledOnce();

    for (const missing of [{ plan: false }, { lifecycle: 'planned' }]) {
      act(() => resetIntros());
      queryClient.clear();
      vi.mocked(sendToDm).mockClear();
      seed(missing);
      mount('/company/growth');
      await screen.findByText('First visit · Growth');
      // the beats are over at once here (neither element is on the page), as in the first case, which sent by now
      await act(() => new Promise((r) => setTimeout(r, 50)));
      expect(sendToDm, JSON.stringify(missing)).not.toHaveBeenCalled();
    }
  });
});
