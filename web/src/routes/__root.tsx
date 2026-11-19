import { useInfiniteQuery, useQuery, type QueryClient } from '@tanstack/react-query';
import { Outlet, createRootRouteWithContext, useLocation, useRouter } from '@tanstack/react-router';
import { Suspense, lazy, useEffect, type ReactNode } from 'react';
import { ActionsRoot, run } from '@/actions';
import { reportCallToDesktop } from '@/desktop';
import { ticoRoomQuery } from '@/conversation';
import { configQuery, directoryQuery, meQuery, recordVisit, useHeldCount, type Actor, type Directory } from '@/data';
import { CallBar, CallSide, RingCard, TOGGLE_MUTE, isCallActive, useCall } from '@/features/calls/exports';
import { FileViewerHost } from '@/features/library/exports';
import { AssetRecovery, PlanLock } from '@/features/notices/exports';
import { FirstRun, StartScreen, useStartGate } from '@/features/start/exports';
import { SUPPORT_CHAT, SupportChat, supportUnreadQuery } from '@/features/support/exports';
import { flowQuery, pipelinesQuery, roadmapPipeline } from '@/features/tasks/exports';
import { TeammateChat, teamSlug, whoHref } from '@/features/team/exports';
import { ChatSheet, catchUpQuery, updateListQuery } from '@/features/threads/exports';
import { Avatar } from '@/kit/Avatar';
import { IconButton } from '@/kit/IconButton';
import { MenuItem, MenuRule } from '@/kit/Menu';
import { OfflineBar } from '@/kit/OfflineBar';
import { Placeholder } from '@/kit/placeholder/Placeholder';
import { Segmented } from '@/kit/Segmented';
import { Toasts } from '@/kit/Toast';
import { startLive, useLiveStatus } from '@/live';
import { useMotionRoot } from '@/motion';
import { PanelHost, usePagePanelOpen } from '@/pagekinds';
import { THEME_KEY, usePref, useTheme, type Theme } from '@/prefs';
import { openSearch } from '@/search';
import {
  Frame,
  Menu,
  Sheet,
  SheetHead,
  arrangementFor,
  desktop,
  foldSheet,
  isCallWindow,
  isTeammate,
  reportToDesktop,
  useCompanies,
  watchCallWindow,
  type Company,
  type MenuMate,
  type MenuTeam,
} from '@/shell';
import { MenuLip } from '@/shell/phone';
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

/* An open task's chat comes with the task's own code: the first load carries the shell only. */
const TaskChat = lazy(() => import('@/features/task/exports').then((m) => ({ default: m.TaskChat })));

const dotEl = () => dot.el();
const closeChat = () => void run('shell.closeChat', {}, { via: 'click' });
const bare = (id: string) => id.slice(id.indexOf(':') + 1);

/* Before the app: sign in, a refusal, a boot failure. The start feature decides; the app opens once it says so. */
function Root() {
  useMotionRoot();
  useTheme();
  const gate = useStartGate();
  useDesktopReports(gate.screen === 'signin' || gate.screen === 'signed-out');
  const callWindow = useLocation({ select: (l) => isCallWindow(l.pathname, l.searchStr) });
  if (gate.screen) return <StartScreen screen={gate.screen} error={gate.error} retry={gate.retry} busy={gate.busy} />;
  return callWindow ? <CallWindow /> : <App />;
}

/* The desktop app's small call window (/call/<id>?window=1): the call page alone, with no Tico, menu or call bar. */
function CallWindow() {
  return (
    <>
      <div className="shell-callwin">
        <Outlet />
      </div>
      <ActionsRoot dot={dotEl} fold={foldSheet} />
      <Toasts />
      <AssetRecovery />
    </>
  );
}

/* In the desktop app: who is signed in here, this company's Needs you count for the Dock badge, its files origin.
   Nothing is read or sent in a browser. */
function useDesktopReports(signedOut: boolean) {
  const on = !!desktop();
  const callLive = isCallActive(useCall());
  useEffect(() => {
    if (on) reportCallToDesktop(callLive);
  }, [on, callLive]);
  const me = useQuery({ ...meQuery, enabled: on });
  const config = useQuery({ ...configQuery, enabled: on });
  const flow = useQuery({ ...flowQuery, enabled: on && me.isSuccess });
  const signedIn = me.isSuccess && config.isFetched ? true : signedOut ? false : null;
  const name = config.data?.company_name || config.data?.app_name || '';
  const email = me.data?.email ?? '';
  const needs = flow.data?.count;
  const files = config.data?.visual_origin;
  useEffect(() => {
    if (on) reportToDesktop({ signedIn, name, email, needs, filesOrigin: files });
  }, [on, signedIn, name, email, needs, files]);
}

function App() {
  const router = useRouter();
  const me = useQuery(meQuery);
  const signedIn = me.isSuccess;
  const { data: dir } = useQuery(directoryQuery);
  const status = useLiveStatus();
  const held = useHeldCount();
  const panel = usePagePanelOpen();

  // A teammate's own page is the `agent` arrangement; a team's page and your own page are plain pages.
  const find = dir ? (key: string) => dir.byId[key] : undefined;
  const isActor = (who: string) => isTeammate(who, find, me.data?.id);
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

  // the desktop's small call window: 'return' brings the call back here (the room moves, so this window joins it)
  useEffect(
    () =>
      watchCallWindow((id) => {
        void router.navigate({ to: `/call/${encodeURIComponent(id)}` }).then(() => run('calls.join', { id }, { via: 'click' }));
      }),
    [router],
  );

  const closePanel = () =>
    void router.navigate({ to: '.', search: ({ panel: _, ...rest }: Record<string, unknown>) => rest } as never);

  /* What each place holds in this arrangement (layout.md 6): an open task's chat beside it; a teammate's chat
     stretching, with their page as the column beside it; otherwise the page, and a second chat when one is open. */
  let page: ReactNode = <Outlet />;
  let chat: ReactNode =
    at.chat === SUPPORT_CHAT ? (
      <Sheet kind="chat" label="Tico support">
        <SupportChat />
      </Sheet>
    ) : at.chat ? (
      <SecondChat chat={at.chat} />
    ) : undefined;
  let widgets: ReactNode;
  let side: ReactNode;
  if (arrangement === 'task') {
    const task = top === 'tasks' ? b : a;
    chat = task ? (
      <Sheet kind="chat" label="The task's chat">
        <Suspense fallback={null}>
          <TaskChat task={decodeURIComponent(task)} flow={top === 'flow'} />
        </Suspense>
      </Sheet>
    ) : undefined;
  } else if (arrangement === 'call' && a) {
    side = <CallSide room={decodeURIComponent(a)} />;
  } else if (arrangement === 'agent' && a) {
    widgets = page;
    page = <TeammateSheet who={decodeURIComponent(a)} />;
    chat = undefined;
  }
  // A call with agents: their chat beside the page while the call bar shows, until it is closed.
  const callNow = useCall();
  const callChat = arrangement === 'page' && !chat && isCallActive(callNow) ? (callNow.chat ?? null) : null;
  if (callChat) chat = <SecondChat chat={callChat} />;
  const hideCallChat = () => void run('calls.hideChat', {}, { via: 'click' });

  return (
    <>
      {/* a call on another page shows its bar over the frame; the frame takes the rest of the window */}
      <div className="shell-col">
        <CallBar />
        <Frame
          arrangement={callChat ? 'chat' : arrangement}
          tico={<TicoSheet />}
          menu={<AppMenu note={status === 'offline' ? <OfflineBar held={held} /> : null} />}
          page={page}
          chat={chat}
          widgets={widgets}
          side={side}
          panel={panel ? <PanelHost /> : undefined}
          lips={{ menu: <PhoneMenuLip /> }}
          lipActs={{ call: <CallMute /> }}
          onCloseChat={callChat ? hideCallChat : closeChat}
          onClosePanel={closePanel}
        />
      </div>
      {signedIn ? <FirstRun /> : null}
      <ActionsRoot dot={dotEl} fold={foldSheet} />
      <Toasts />
      {/* the file `files.view` opens, over any page */}
      <FileViewerHost />
      <RingCard />
      {/* over every page: a locked plan; the app's own files failing to load after a release */}
      <PlanLock />
      <AssetRecovery />
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
/** A menu row's press: the same action Tico would run. */
const press = (id: string, input: unknown = {}) => () => void run(id, input, { via: 'click' });

function AppMenu({ note }: { note: ReactNode }) {
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);
  const { data: dir } = useQuery(directoryQuery);
  const { data: pipelines } = useQuery(pipelinesQuery);
  const { data: flow } = useQuery(flowQuery);
  const { data: catchUp } = useQuery(catchUpQuery);
  const { data: updates } = useInfiniteQuery(updateListQuery('all'));
  const { data: support } = useQuery(supportUnreadQuery);
  const companies = useCompanies();
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
      pipelines={pipelines && [
        ...pipelines.map((p) => ({ id: p.id, name: p.name, to: `/tasks/${encodeURIComponent(p.id)}` })),
        // the Features pipeline read as horizons: only a company that has one gets the line
        ...(roadmapPipeline(pipelines) ? [{ id: 'roadmap', name: 'Roadmap', to: '/roadmap' }] : []),
      ]}
      counts={{ flow: some(flow?.count), threads: some(catchUp?.count), updates: some(updates?.pages[0]?.unread), support: some(support) }}
      note={note}
      onSearch={() => openSearch()}
      onAddTeammate={press('shell.addTeammate')}
      onHelp={press('support.open')}
      companyMenu={<CompanyRows name={name} all={companies} />}
      accountMenu={<AccountRows me={meMate} email={me?.email ?? ''} />}
    />
  );
}

/* The company pick: every company the desktop app holds, this one checked, then Add company. A browser holds one
   company per address, so it lists this one only. */
function CompanyRows({ name, all }: { name: string; all: Company[] | null }) {
  const list = all ?? [{ id: '', name, active: true, self: true, needs: 0 }];
  return (
    <>
      {list.map((c) => (
        <MenuItem
          key={c.id || 'here'}
          label={c.name}
          lead={
            <span className="co-mark" aria-hidden="true">
              {c.name.slice(0, 1).toUpperCase()}
            </span>
          }
          meta={c.needs && !c.self ? String(c.needs) : undefined}
          on={c.self}
          onSelect={c.self ? () => undefined : press('shell.switchCompany', { id: c.id })}
        />
      ))}
      {all ? (
        <>
          <MenuRule />
          <MenuItem label="Add company" icon="plus" onSelect={press('shell.addCompany')} />
        </>
      ) : null}
    </>
  );
}

const THEMES = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'Auto' },
] as const;

/* The account menu over your name (pages/8-settings/more.js account): you, your page and Settings, the theme, Sign out
   last. Every row is an action; Sign out is the person's own (settings.signOut, tico: false). */
function AccountRows({ me, email }: { me: MenuMate; email: string }) {
  const [theme] = usePref<Theme>(THEME_KEY, 'dark');
  return (
    <>
      <div className="acct-me">
        <Avatar decorative kind={me.kind} name={me.name} initials={me.short} hue={me.hue} size={36} />
        <span>
          <b>{me.name}</b>
          {email && email !== me.name ? <em>{email}</em> : null}
        </span>
      </div>
      <MenuRule />
      <MenuItem label="My page" onSelect={press('team.open', { who: bare(me.id) })} />
      <MenuItem label="Settings" onSelect={press('shell.goTo', { to: '/settings' })} />
      <MenuRule />
      <div className="acct-line">
        <span>Theme</span>
        <Segmented label="Theme" options={THEMES} value={theme} onValueChange={(v) => void run('settings.setTheme', { theme: v }, { via: 'click' })} />
      </div>
      <MenuRule />
      <MenuItem label="Sign out" onSelect={press('settings.signOut')} />
    </>
  );
}

/* The phone's menu lip: the company and the Flow count, from the same reads as the menu itself. */
function PhoneMenuLip() {
  const { data: config } = useQuery(configQuery);
  const { data: flow } = useQuery(flowQuery);
  return <MenuLip company={config?.company_name || config?.app_name || ''} flow={some(flow?.count)} />;
}

/* The phone's call lip: mute without opening the call. */
function CallMute() {
  const { muted } = useCall();
  return <IconButton icon={muted ? 'micoff' : 'mic'} label={muted ? 'Unmute' : 'Mute'} on={muted} onClick={() => void run(TOGGLE_MUTE, {}, { via: 'click' })} />;
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
