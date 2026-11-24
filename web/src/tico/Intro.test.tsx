import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { liveQuery, sendToDm, ticoRoomQuery, timelineQuery } from '@/conversation';
import { configQuery, directoryQuery, meQuery, pageOwnersQuery, queryClient } from '@/data';
import { subscriptionsQuery } from '@/features/settings/routing';
import { IntroPin, PageIntro, resetIntros } from './Intro';
import { askReplay, introKey, INTROS } from './intros';
import { introsSeenQuery, ticoPowerQuery } from './queries';

vi.mock('@/conversation', async (orig) => ({ ...(await orig<object>()), sendToDm: vi.fn(async () => null) }));

const ROOM = 'room-1';
const GM = 'bot:growth-manager';
const wait = (ms = 20) => act(() => new Promise((r) => setTimeout(r, ms)));

/** Tico's sheet as TicoSheet draws an intro (pinned over its box), in the frame's place for Tico's sheet. */
function Chat({ shut }: { shut: boolean }) {
  return (
    <div data-sheet="tico" data-shut={shut || undefined}>
      <aside aria-label="Tico">
        <IntroPin />
      </aside>
    </div>
  );
}

interface Seed {
  role?: string;
  seen?: Record<string, string>;
  plan?: boolean;
  lifecycle?: string;
  owed?: boolean;
  /** The company's config not read yet. */
  noConfig?: boolean;
}

function seed({ role = 'owner', seen = {}, plan = true, lifecycle = 'active', owed = false, noConfig = false }: Seed = {}) {
  queryClient.setQueryData(meQuery.queryKey, { id: role === 'agent' ? 'bot:ops' : 'human:maya', email: '', role, slug: null } as never);
  if (!noConfig) queryClient.setQueryData(configQuery.queryKey, { onboarding_needed: owed } as never);
  queryClient.setQueryData(ticoRoomQuery.queryKey, ROOM);
  queryClient.setQueryData(ticoPowerQuery.queryKey, { available: true, bot: 'assistant', can_turn_on: false, state: 'active' });
  queryClient.setQueryData(introsSeenQuery.queryKey, seen);
  queryClient.setQueryData(timelineQuery(ROOM).queryKey, { pages: [{ messages: [], next_cursor: null }], pageParams: [null] } as never);
  queryClient.setQueryData(subscriptionsQuery.queryKey, { subscriptions: plan ? [{ id: 's1', status: 'ok', in_use_on: [] }] : [] } as never);
  // Strategy's own owner is the Growth Manager here; Goals, its tab, has its own row and no owner of its own
  const pages = { growth: { bot: GM, own: true }, strategy: { bot: GM, own: true }, goals: { bot: 'bot:general-manager', own: false } };
  queryClient.setQueryData(pageOwnersQuery.queryKey, { pages, canChange: false });
  const gm = { id: GM, kind: 'agent', name: 'Growth Manager', slug: 'growth-manager', lifecycle };
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [gm], byId: { [GM]: gm, 'growth-manager': gm } } as never);
}

/** The app at `path`, with `page` in its page sheet and Tico's sheet open or tucked (`shut`); on a phone, the phone's
    cards with `card` up. A fresh one each time. */
function mount(path: string, page: ReactNode = null, shut = false, card?: string) {
  cleanup();
  const root = createRootRoute({
    component: () => (
      <>
        <div className={card ? 'phone' : undefined} data-card={card}>
          {page && <main className="pane page">{page}</main>}
          <Chat shut={shut} />
        </div>
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
const posts = () => vi.mocked(api.post).mock.calls.filter(([path]) => path === '/api/v2/preferences/intros.seen').map(([, body]) => (body as { value: Record<string, string> }).value);
const BOX = { top: 10, left: 10, width: 120, height: 30, right: 130, bottom: 40, x: 10, y: 10, toJSON: () => ({}) } as DOMRect;

beforeEach(() => {
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.spyOn(api, 'get').mockRejectedValue(new Error('not in this test'));
  // the server adds the page sent to those kept, as backend/preferences_routes.py seen_union does
  vi.spyOn(api, 'post').mockImplementation(async (_path: string, body: unknown) => ({ value: { ...(body as { value: object }).value, ...queryClient.getQueryData(introsSeenQuery.queryKey) } }) as never);
});

afterEach(() => {
  act(() => resetIntros());
  queryClient.clear();
  delete document.documentElement.dataset.layout;
  vi.restoreAllMocks();
  vi.mocked(sendToDm).mockClear();
});

describe('page intros', () => {
  it('play once per person: in Tico’s chat with the page’s pill, sending only that page as seen, not again next time', async () => {
    seed();
    const router = mount('/company/rules');
    expect(await screen.findByText('First visit · Rules')).toBeTruthy();
    expect(chat().textContent).toContain('Each shows who made it.');
    expect(posts()).toEqual([{ rules: expect.any(String) }]);

    await act(() => router.navigate({ to: '/library/docs' as never }));
    expect(await screen.findByText('First visit · Docs')).toBeTruthy();
    expect(screen.queryByText('First visit · Rules')).toBeNull();
    expect(posts().at(-1)).toEqual({ docs: expect.any(String) });

    await act(() => router.navigate({ to: '/company/rules' as never }));
    await wait();
    expect(chat().textContent).toBe('');
  });

  it('do not play where this person has had it, nor ever for an agent', async () => {
    seed({ seen: { rules: '2026-11-20T10:00:00.000Z' } });
    mount('/company/rules');
    await wait();
    expect(chat().textContent).toBe('');
    act(() => resetIntros());
    queryClient.clear();

    seed({ role: 'agent' });
    mount('/company/rules');
    await wait();
    expect(chat().textContent).toBe('');
    expect(posts()).toEqual([]);
  });

  it('wait while Tico is answering, then play at the next quiet moment', async () => {
    seed();
    const run = { run: 'r1', bot: 'bot:assistant', parts: [], typing: null, doing: 'think' as const, ended: false };
    queryClient.setQueryData(liveQuery(ROOM).queryKey, [run]);
    mount('/company/routines');
    await wait();
    expect(chat().textContent).toBe('');
    expect(posts()).toEqual([]);

    act(() => queryClient.setQueryData(liveQuery(ROOM).queryKey, [{ ...run, ended: true }]));
    expect(await screen.findByText('First visit · Routines')).toBeTruthy();
  });

  it('wait while the first run is open or setup is still owed', async () => {
    seed();
    const router = mount('/company/usage?setup=name');
    await wait();
    expect(chat().textContent).toBe('');
    await act(() => router.navigate({ to: '/company/usage' as never }));
    expect(await screen.findByText('First visit · Usage')).toBeTruthy();
    act(() => resetIntros());
    queryClient.clear();

    seed({ owed: true });
    mount('/company/usage');
    await wait();
    expect(chat().textContent).toBe('');
    expect(posts()).toEqual([{ usage: expect.any(String) }]);
  });

  it('wait for an owner until the company’s config says setup is done', async () => {
    seed({ noConfig: true });
    mount('/company/usage');
    await wait();
    expect(chat().textContent).toBe('');
    act(() => queryClient.setQueryData(configQuery.queryKey, { onboarding_needed: false } as never));
    expect(await screen.findByText('First visit · Usage')).toBeTruthy();
  });

  it('go with Got it, and come back on "Show me how this works" where the page has one', async () => {
    seed();
    const router = mount('/company/finance');
    await screen.findByText('First visit · Finance');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(chat().textContent).toBe('');

    act(() => void askReplay());
    // a replay is the page's name alone, and its chip runs the app's own action
    expect(await screen.findByText('Finance')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect bank' })).toBeTruthy();

    await act(() => router.navigate({ to: '/tasks/p1/7' as never }));
    await wait();
    expect(askReplay()).toBe(false);
  });

  it('pin the card over Tico’s box and point at the page; with Tico folded on a computer wait, marking nothing', async () => {
    seed();
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(BOX);
    const tool = <div data-target="tool:shopify">Shopify</div>;
    mount('/library/tools', tool, true);
    await wait(50);
    expect(chat().textContent).toBe('');
    expect(document.querySelector('.intro-float, .intro-spot')).toBeNull();
    expect(posts()).toEqual([]);

    act(() => document.querySelector('[data-sheet="tico"]')!.removeAttribute('data-shut'));
    const pin = await waitFor(() => document.querySelector<HTMLElement>('.intro-pin') ?? Promise.reject(new Error('no pin')));
    expect(pin.textContent).toContain('First visit · Tools');
    expect(document.querySelector('.intro-spot')?.textContent).toBe('Tico holds the keys');
    expect(document.querySelector('.intro-float')).toBeNull();
    expect(posts()).toEqual([{ tools: expect.any(String) }]);
  });

  it('on a phone with its page up, float one card over the page with the beats as words and no spotlight', async () => {
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(BOX);
    const tool = <div data-target="tool:shopify">Shopify</div>;

    document.documentElement.dataset.layout = 'cards';
    seed();
    mount('/library/tools', tool, false, 'none');
    const phone = await waitFor(() => document.querySelector<HTMLElement>('.intro-float') ?? Promise.reject(new Error('no card')));
    expect(phone.querySelector('.intro-beats')?.textContent).toBe('Tico holds the keys');
    expect(phone.querySelector('.intro-beats button')).toBeNull();
    expect(document.querySelector('.intro-spot')).toBeNull();
    expect(chat().textContent).toBe('');
  });

  it('on a phone wait while the agent’s chat is up, and show in Tico’s chat when it is up; seen only once drawn', async () => {
    document.documentElement.dataset.layout = 'cards';
    seed();
    mount('/company/routines', null, false, 'chat');
    await wait();
    expect(document.querySelector('.intro-float')).toBeNull();
    expect(chat().textContent).toBe('');
    expect(posts()).toEqual([]);

    act(() => document.querySelector<HTMLElement>('.phone')!.setAttribute('data-card', 'tico'));
    expect(await screen.findByText('First visit · Routines')).toBeTruthy();
    expect(document.querySelector('.intro-float')).toBeNull();
    expect(posts()).toEqual([{ routines: expect.any(String) }]);
  });

  it('point only at what shows: never into a tucked sheet, and at an element that draws a moment later', async () => {
    seed();
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(BOX);
    const tucked = (
      <div data-shut="">
        <button type="button" data-target="org:add">
          Add teammate
        </button>
      </div>
    );
    mount('/company/strategy/org', tucked);
    await screen.findByText('First visit · Team');
    await wait(1100);
    expect(document.querySelector('.intro-spot')).toBeNull();
    act(() => resetIntros());
    queryClient.clear();

    seed();
    mount('/company/rules');
    await screen.findByText('First visit · Rules');
    const late = document.createElement('a');
    late.dataset.target = 'constitution';
    await wait(300);
    act(() => void document.body.append(late));
    await waitFor(() => expect(document.querySelector('.intro-spot')?.textContent).toBe('Above every rule: agents can’t change it'));
    late.remove();
  });

  it('send nothing to the page’s owner by itself: one chip asks it, only with a connected plan and the agent on the team', async () => {
    seed();
    mount('/company/growth');
    await screen.findByText('First visit · Growth');
    await wait(50);
    expect(sendToDm).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'Ask Growth Manager for one idea' }));
    expect(sendToDm).toHaveBeenCalledOnce();
    const [agent, text, from] = vi.mocked(sendToDm).mock.calls[0]!;
    expect([agent, from]).toEqual([GM, { page: 'growth', label: 'Growth' }]);
    expect(text).toContain('one real addition');
    expect(chat().textContent).toBe('');

    for (const missing of [{ plan: false }, { lifecycle: 'planned' }]) {
      act(() => resetIntros());
      queryClient.clear();
      seed(missing);
      mount('/company/growth');
      await screen.findByText('First visit · Growth');
      await wait(50);
      expect(screen.queryByRole('button', { name: /for one idea/ }), JSON.stringify(missing)).toBeNull();
    }
  });

  it('ask the owner of the page as it is named: Goals has its own, though it plays Strategy’s intro', async () => {
    seed();
    const router = mount('/company/strategy');
    await screen.findByText('First visit · Strategy');
    expect(await screen.findByRole('button', { name: 'Ask Growth Manager for one idea' })).toBeTruthy();
    await act(() => router.navigate({ to: '/company/strategy/goals' as never }));
    await wait(50);
    expect(screen.getByText('First visit · Strategy')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /for one idea/ })).toBeNull();
  });
});

/* Every route file has its intro, or none on purpose, each with why. A new route file fails here until it is decided. */
const ROUTES: Record<string, [path: string, intro: string | null]> = {
  // developers' pictures, not the product
  '[_]dev.dot.tsx': ['/_dev/dot', null],
  '[_]dev.kinds.tsx': ['/_dev/kinds', null],
  'calendar.tsx': ['/calendar', 'calendar'],
  // a call is a conversation under way: never interrupted
  'call.$room.tsx': ['/call/r1', null],
  'company.finance.tsx': ['/company/finance', 'finance'],
  'company.growth.tsx': ['/company/growth', 'growth'],
  'company.index.tsx': ['/company', 'company'],
  'company.listening.tsx': ['/company/listening', 'listening'],
  'company.products.tsx': ['/company/products', 'products'],
  'company.risk.tsx': ['/company/risk', 'risk'],
  'company.risk_.disputes.tsx': ['/company/risk/disputes', 'risk'],
  'company.routines.tsx': ['/company/routines', 'routines'],
  'company.rules.tsx': ['/company/rules', 'rules'],
  'company.rules_.links.tsx': ['/company/rules/links', 'rules'],
  'company.strategy.tsx': ['/company/strategy', 'strategy'],
  'company.strategy_.$tab.tsx': ['/company/strategy/goals', 'strategy'],
  'company.usage.tsx': ['/company/usage', 'usage'],
  // old Flow links and the landing address only pass on to a page that has its own
  'flow.$task.tsx': ['/flow/t1', null],
  'flow.index.tsx': ['/flow', null],
  'index.tsx': ['/', null],
  'learned.tsx': ['/learned', 'learned'],
  // one doc, read or written: its words are the page
  'library.$doc.tsx': ['/library/d1', null],
  'library.contacts.tsx': ['/library/contacts', 'contacts'],
  'library.docs.tsx': ['/library/docs', 'docs'],
  'library.files.tsx': ['/library/files', 'files'],
  'library.index.tsx': ['/library', 'docs'],
  'library.skills.tsx': ['/library/skills', 'skills'],
  'library.tools.tsx': ['/library/tools', 'tools'],
  'needs-you.tsx': ['/needs-you', 'needs-you'],
  'roadmap.tsx': ['/roadmap', 'roadmap'],
  'settings.$section.tsx': ['/settings/agents', 'settings'],
  'settings.index.tsx': ['/settings', 'settings'],
  // the design system's reference, not a product page
  'settings.style-guide.tsx': ['/settings/style-guide', null],
  'tags.$tag.tsx': ['/tags/vip', 'tags'],
  'tags.index.tsx': ['/tags', 'tags'],
  // a task is its own chat with its agents: the work, never a tour
  'task.$task.tsx': ['/task/t1', null],
  'tasks.$pipeline.$task.$tab.tsx': ['/tasks/eng/7/files', null],
  'tasks.$pipeline.$task.index.tsx': ['/tasks/eng/7', null],
  'tasks.$pipeline.edit.tsx': ['/tasks/eng/edit', 'pipeline'],
  'tasks.$pipeline.index.tsx': ['/tasks/eng', 'tasks'],
  'tasks.index.tsx': ['/tasks', 'tasks'],
  'team.$who.tsx': ['/team/scout', 'who'],
  // one thread is a conversation: never interrupted
  'threads.$thread.tsx': ['/threads/th1', null],
  'threads.catch-up.tsx': ['/threads/catch-up', 'threads'],
  'threads.index.tsx': ['/threads', 'threads'],
  'threads.mail.tsx': ['/threads/mail', 'threads'],
  'threads.messaging.tsx': ['/threads/messaging', 'threads'],
  'updates.$tab.tsx': ['/updates/daily', 'updates'],
  'updates.index.tsx': ['/updates', 'updates'],
};

it('maps every route file to its intro, under /next too, and the org chart to Team', () => {
  const files = Object.keys(import.meta.glob('../routes/*.tsx'))
    .map((f) => f.replace('../routes/', ''))
    .filter((f) => f !== '__root.tsx')
    .sort();
  expect(files).toEqual(Object.keys(ROUTES).sort());
  for (const [file, [path, intro]] of Object.entries(ROUTES)) {
    expect([file, introKey(path)]).toEqual([file, intro]);
    expect(introKey(`/next${path === '/' ? '' : path}`)).toBe(intro);
    if (intro && intro !== 'who') expect(INTROS[intro]).toBeTruthy();
  }
  expect(introKey('/company/strategy/org')).toBe('team');
});
