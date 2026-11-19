import { useInfiniteQuery, useQuery, type QueryClient } from '@tanstack/react-query';
import { Outlet, createRootRouteWithContext, useLocation, useRouter } from '@tanstack/react-router';
import { useEffect, type ReactNode } from 'react';
import { ActionsRoot, run } from '@/actions';
import { ticoRoomQuery } from '@/conversation';
import { configQuery, directoryQuery, meQuery, recordVisit, useHeldCount, type Actor, type Directory } from '@/data';
import { FirstRun, StartScreen, useStartGate } from '@/features/start/exports';
import { TaskChat } from '@/features/task/exports';
import { flowQuery, pipelinesQuery } from '@/features/tasks/exports';
import { TeammateChat, teamSlug, whoHref } from '@/features/team/exports';
import { ChatSheet, catchUpQuery, updateListQuery } from '@/features/threads/exports';
import { Avatar } from '@/kit/Avatar';
import { OfflineBar } from '@/kit/OfflineBar';
import { Placeholder } from '@/kit/placeholder/Placeholder';
import { Toasts } from '@/kit/Toast';
import { startLive, useLiveStatus } from '@/live';
import { useMotionRoot } from '@/motion';
import { PanelHost, usePagePanelOpen } from '@/pagekinds';
import { useTheme } from '@/prefs';
import { openSearch } from '@/search';
import { Frame, Menu, Sheet, SheetHead, arrangementFor, foldSheet, type MenuMate, type MenuTeam } from '@/shell';
import { TicoSheet } from '@/tico';
import { dot } from '@/tico/dot';
import { pageName } from '@/tico/pageName';

export interface RouterContext {
  queryClient: QueryClient;
}

/** What the shell keeps in every page's address: the second chat and an open side panel (the page reads `panel`). */
export interface RootSearch {
  chat?: string;
  panel?: string;
}
const word = (v: unknown) => (typeof v === 'string' && v ? v : undefined);

export const Route = createRootRouteWithContext<RouterContext>()({
  component: Root,
  // Each page's own keys pass through untouched: a child route's search is the address's, plus what it validates.
  validateSearch: (s: Record<string, unknown>): RootSearch => {
    const out: RootSearch = {};
    const chat = word(s.chat);
    const panel = word(s.panel);
    if (chat) out.chat = chat;
    if (panel) out.panel = panel;
    return out;
  },
  notFoundComponent: () => <Placeholder title="Not found" />,
});

const dotEl = () => dot.el();
const closeChat = () => void run('shell.closeChat', {}, { via: 'click' });
const bare = (id: string) => id.slice(id.indexOf(':') + 1);

/* Before the app: sign in, a refusal, a boot failure. The start feature decides; the app opens once it says so. */
function Root() {
  useMotionRoot();
  useTheme();
  const gate = useStartGate();
  if (gate.screen) return <StartScreen screen={gate.screen} error={gate.error} retry={gate.retry} busy={gate.busy} />;
  return <App />;
}

function App() {
  const router = useRouter();
  const me = useQuery(meQuery);
  const signedIn = me.isSuccess;
  const { data: dir } = useQuery(directoryQuery);
  const status = useLiveStatus();
  const held = useHeldCount();
  const panel = usePagePanelOpen();

  // A teammate's own page is the `agent` arrangement; a team's page (not a teammate) is a plain page.
  const isActor = (who: string) => !!(dir?.byId[who] ?? dir?.byId[`human:${who}`]);
  const at = {
    path: useLocation({ select: (l) => l.pathname.replace(/^\/next(?=\/|$)/, '') || '/' }),
    chat: useLocation({ select: (l) => word((l.search as Record<string, unknown>).chat) }),
  };
  const arrangement = arrangementFor(at.path, at.chat ? { chat: at.chat } : {}, isActor);
  const [, top, a, b] = at.path.split('/');

  // One stream, once the person is known and Tico's room is drawn (its follow then rides the first connection).
  const room = useQuery(ticoRoomQuery);
  const ready = signedIn && !room.isPending;
  useEffect(() => {
    if (ready) startLive();
  }, [ready]);

  // Each page the person opens goes to their page history (dev pages do not), named as its head names it.
  useEffect(() => {
    if (!signedIn || at.path.startsWith('/_dev')) return;
    const t = setTimeout(() => recordVisit(at.path, pageName() || undefined), 400);
    return () => clearTimeout(t);
  }, [signedIn, at.path]);

  const closePanel = () =>
    void router.navigate({ to: '.', search: ({ panel: _, ...rest }: Record<string, unknown>) => rest } as never);

  /* What each place holds in this arrangement (layout.md 6): an open task's chat beside it; a teammate's chat
     stretching, with their page as the column beside it; otherwise the page, and a second chat when one is open. */
  let page: ReactNode = <Outlet />;
  let chat: ReactNode = at.chat ? <SecondChat chat={at.chat} /> : undefined;
  let widgets: ReactNode;
  if (arrangement === 'task') {
    const task = top === 'tasks' ? b : a;
    chat = task ? (
      <Sheet kind="chat" label="The task's chat">
        <TaskChat task={decodeURIComponent(task)} flow={top === 'flow'} />
      </Sheet>
    ) : undefined;
  } else if (arrangement === 'agent' && a) {
    widgets = page;
    page = <TeammateSheet who={decodeURIComponent(a)} />;
    chat = undefined;
  }

  return (
    <>
      <Frame
        arrangement={arrangement}
        tico={<TicoSheet />}
        menu={<AppMenu note={status === 'offline' ? <OfflineBar held={held} /> : null} />}
        page={page}
        chat={chat}
        widgets={widgets}
        panel={panel ? <PanelHost /> : undefined}
        onCloseChat={closeChat}
        onClosePanel={closePanel}
      />
      {signedIn ? <FirstRun /> : null}
      <ActionsRoot dot={dotEl} fold={foldSheet} />
      <Toasts />
    </>
  );
}

/* ---------------------------------------------------------------- the menu, from the directory */

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
  to: whoHref(a),
  live: a.working,
});

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

/* Counts are what the server already answers (needs-you, catch-up, updates); the stream keeps them current. */
const some = (n: number | undefined) => (n ? n : undefined);

function AppMenu({ note }: { note: ReactNode }) {
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);
  const { data: dir } = useQuery(directoryQuery);
  const { data: pipelines } = useQuery(pipelinesQuery);
  const { data: flow } = useQuery(flowQuery);
  const { data: catchUp } = useQuery(catchUpQuery);
  const { data: updates } = useInfiniteQuery(updateListQuery('all'));
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
      pipelines={pipelines?.map((p) => ({ id: p.id, name: p.name, to: `/tasks/${encodeURIComponent(p.id)}` }))}
      counts={{ flow: some(flow?.count), threads: some(catchUp?.count), updates: some(updates?.pages[0]?.unread) }}
      note={note}
      onSearch={() => openSearch()}
    />
  );
}

/* ---------------------------------------------------------------- the chats */

function SecondChat({ chat }: { chat: string }) {
  const { data: dir } = useQuery(directoryQuery);
  const who = dir?.byId[bare(chat)];
  return (
    <Sheet kind="chat" label={`Chat with ${who?.name ?? 'teammate'}`}>
      <ChatSheet chat={chat} />
    </Sheet>
  );
}

/* A teammate's own page: their chat (or, on your own page, Tico's room) stretching beside the page's column. */
function TeammateSheet({ who }: { who: string }) {
  const { data: dir } = useQuery(directoryQuery);
  const actor = dir?.byId[who] ?? dir?.byId[`human:${who}`];
  const name = actor?.name ?? 'Teammate';
  return (
    <Sheet kind="chat" label={`Chat with ${name}`}>
      <SheetHead
        kind="side"
        title={name}
        lead={actor ? <Avatar decorative kind={actor.kind} name={actor.name} initials={short(actor.name)} hue={actor.hue ?? 0} size={28} live={actor.working} /> : null}
      />
      <TeammateChat who={who} />
    </Sheet>
  );
}
