import { useQuery, type QueryClient } from '@tanstack/react-query';
import { Outlet, createRootRouteWithContext, useLocation, useRouter } from '@tanstack/react-router';
import { useEffect, type ComponentType, type ReactNode } from 'react';
import { ActionsRoot, run } from '@/actions';
import { ApiError } from '@/api/client';
import { Conversation, ticoRoomQuery } from '@/conversation';
import { configQuery, directoryQuery, meQuery, recordVisit, useHeldCount, type Actor, type Directory } from '@/data';
import { OfflineBar } from '@/kit/OfflineBar';
import { Placeholder } from '@/kit/placeholder/Placeholder';
import { Toasts } from '@/kit/Toast';
import { startLive, useLiveStatus } from '@/live';
import { useMotionRoot } from '@/motion';
import { PanelHost, usePagePanelOpen } from '@/pagekinds';
import { useTheme } from '@/prefs';
import { openSearch } from '@/search';
import { Frame, Menu, Sheet, SheetHead, foldSheet, type MenuMate, type MenuTeam } from '@/shell';
import { TicoSheet } from '@/tico';
import { dot } from '@/tico/dot';

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: Root,
  // the second chat rides every page's address; each page's own keys pass through untouched
  validateSearch: (s: Record<string, unknown>): { chat?: string } => (typeof s.chat === 'string' && s.chat ? { chat: s.chat } : {}),
  notFoundComponent: () => <Placeholder title="Not found" />,
});

/* The threads feature's second chat, once it exists: `ChatSheet` in its exports, given the `?chat=` value. Until then
   a thread opens in a plain side conversation. */
const threads = import.meta.glob<{ ChatSheet?: ComponentType<{ chat: string }> }>('/src/features/threads/exports.ts', { eager: true });
const ChatSheet = Object.values(threads)[0]?.ChatSheet;

const dotEl = () => dot.el();
const closeChat = () => void run('shell.closeChat', {}, { via: 'click' });

function Root() {
  useMotionRoot();
  useTheme();
  const router = useRouter();
  const me = useQuery(meQuery);
  const signedIn = me.isSuccess;
  const status = useLiveStatus();
  const held = useHeldCount();
  const panel = usePagePanelOpen();
  const chat = useLocation({ select: (l) => (l.search as { chat?: unknown }).chat });

  // Signed out: the server's sign-in page, then back here.
  useEffect(() => {
    const e = me.error;
    if (!(e instanceof ApiError) || e.status !== 401 || !e.signIn) return;
    const back = location.pathname + location.search + location.hash;
    location.assign(`${e.signIn}${e.signIn.includes('?') ? '&' : '?'}next=${encodeURIComponent(back)}`);
  }, [me.error]);

  // One stream, once the person is known and Tico's room is drawn (its follow then rides the first connection).
  const room = useQuery(ticoRoomQuery);
  const ready = signedIn && !room.isPending;
  useEffect(() => {
    if (ready) startLive();
  }, [ready]);

  // Each page the person opens goes to their page history (dev pages do not).
  const path = useLocation({ select: (l) => l.pathname.replace(/^\/next(?=\/|$)/, '') || '/' });
  useEffect(() => {
    if (!signedIn || path.startsWith('/_dev')) return;
    const t = setTimeout(() => recordVisit(path, document.title || undefined), 400);
    return () => clearTimeout(t);
  }, [signedIn, path]);

  const closePanel = () =>
    void router.navigate({ to: '.', search: ({ panel: _, ...rest }: Record<string, unknown>) => rest } as never);

  return (
    <>
      <Frame
        tico={<TicoSheet />}
        menu={<AppMenu note={status === 'offline' ? <OfflineBar held={held} /> : null} />}
        page={<Outlet />}
        chat={typeof chat === 'string' && chat ? <SecondChat chat={chat} /> : undefined}
        panel={panel ? <PanelHost /> : undefined}
        onCloseChat={closeChat}
        onClosePanel={closePanel}
      />
      <ActionsRoot dot={dotEl} fold={foldSheet} />
      <Toasts />
    </>
  );
}

/* ---------------------------------------------------------------- the menu, from the directory */

const bare = (id: string) => id.slice(id.indexOf(':') + 1);
const short = (name: string) => {
  const w = name.trim().split(/\s+/).filter(Boolean);
  return (w.length > 1 ? `${w[0]![0]}${w[1]![0]}` : (w[0] ?? '').slice(0, 2)).toUpperCase();
};
const mate = (a: Actor): MenuMate => ({
  id: a.id,
  name: a.name,
  short: short(a.name),
  hue: a.hue ?? 0,
  kind: a.kind,
  to: `/team/${encodeURIComponent(a.slug ?? bare(a.id))}`,
  live: a.working,
});
const teamSlug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

function teamsOf(d: Directory | undefined, meId: string | undefined): MenuTeam[] {
  const by = new Map<string, MenuTeam>();
  for (const a of d?.actors ?? []) {
    if (!a.team || a.id === meId) continue;
    const t = by.get(a.team) ?? { id: a.team, name: a.team, to: `/team/${encodeURIComponent(teamSlug(a.team))}`, members: [] };
    t.members.push(mate(a));
    by.set(a.team, t);
  }
  return [...by.values()];
}

function AppMenu({ note }: { note: ReactNode }) {
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);
  const { data: dir } = useQuery(directoryQuery);
  const self = me ? dir?.byId[me.id] : undefined;
  const name = config?.company_name || config?.app_name || '';
  const meMate: MenuMate = self
    ? mate(self)
    : { id: me?.id ?? 'me', name: me?.email ?? '', short: short(me?.email ?? ''), hue: 0, kind: 'person', to: `/team/${encodeURIComponent(bare(me?.id ?? ''))}` };
  return (
    <Menu
      company={{ name, mark: name.slice(0, 1).toUpperCase() }}
      me={meMate}
      teams={teamsOf(dir, me?.id)}
      note={note}
      onSearch={() => openSearch()}
    />
  );
}

/* ---------------------------------------------------------------- the second chat */

function SecondChat({ chat }: { chat: string }) {
  const kind = chat.slice(0, chat.indexOf(':'));
  const id = bare(chat);
  const { data: dir } = useQuery(directoryQuery);
  const who = dir?.byId[id];
  return (
    <Sheet kind="chat" label={`Chat with ${who?.name ?? 'teammate'}`}>
      {ChatSheet ? (
        <ChatSheet chat={chat} />
      ) : kind === 'thread' ? (
        <Conversation id={id} variant="side" title={who?.name} />
      ) : (
        <SheetHead kind="side" title={who?.name ?? id} />
      )}
    </Sheet>
  );
}
