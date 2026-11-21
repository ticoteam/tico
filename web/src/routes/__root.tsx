import { useInfiniteQuery, useQuery, type QueryClient } from '@tanstack/react-query';
import { Link, Outlet, createRootRouteWithContext, useLocation, useRouter, useRouterState } from '@tanstack/react-router';
import { Fragment, Suspense, lazy, useEffect, useState, type ReactNode } from 'react';
import { ActionsRoot, run } from '@/actions';
import { reportCallToDesktop, useCallWindow } from '@/desktop';
import { ticoRoomQuery } from '@/conversation';
import { configQuery, meQuery, recordVisit, useActor, useDirectory, useHeldCount, type Actor, type Directory } from '@/data';
import { TOGGLE_MUTE, isCallActive, ringingQuery, useCall } from '@/features/calls/routing';
import { useFileOpen } from '@/features/library/routing';
import { AssetRecovery, DesktopMove, PlanLock } from '@/features/notices/exports';
import { StartScreen, useStartGate } from '@/features/start/routing';
import { SUPPORT_CHAT, supportUnreadQuery } from '@/features/support/routing';
import { TaskChat } from '@/features/task/exports';
import { filtersOf, flowGroups, flowQuery, pipelinesQuery, pipelineTasksQuery, roadmapPipeline, validateTasksSearch, type TaskItem } from '@/features/tasks/routing';
import { teammateAccessQuery, validateTeamSearch, whoHref } from '@/features/team/routing';
import { ChatSheet, MenuThreads, MessagePick } from '@/features/threads/exports';
import { asksOf, catchUpQuery, threadListQuery, type Asks } from '@/features/threads/routing';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Empty } from '@/kit/Empty';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu as DropMenu, MenuItem, MenuRule } from '@/kit/Menu';
import { OfflineBar } from '@/kit/OfflineBar';
import { LatePill } from '@/kit/Pill';
import { Placeholder } from '@/kit/placeholder/Placeholder';
import { FoldButton } from '@/kit/SectionHead';
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
  useTabTitle,
  watchCallWindow,
  type Company,
  type MenuMate,
  type MenuNode,
  type MenuTeam,
  orgTree,
  trailOf,
  type OrgNode,
  type TrailPart,
} from '@/shell';
import { MenuLip } from '@/shell/phone';
import { TicoSheet } from '@/tico';
import { dot } from '@/tico/dot';
import { pageName, usePageName } from '@/tico/pageName';
import { boardSearch, isFlowPath, leaveFlow, notePlace } from '@/lib';

export interface RouterContext {
  queryClient: QueryClient;
}

/** What the shell keeps in every page's address: the second chat, an open side panel (the page reads `panel`),
    `via=thread` when the page was opened from that chat (its head draws ✕, lib besideThread), a team the menu shows
    (`team`, where old team links land) and Add teammate's step (`add`, onto that team). */
export interface RootSearch {
  chat?: string;
  panel?: string;
  via?: 'thread';
  team?: string;
  add?: AddStep;
}
type AddStep = NonNullable<ReturnType<typeof validateTeamSearch>['add']>;
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
    if (s.via === 'thread') out.via = 'thread';
    const team = word(s.team);
    const { add } = validateTeamSearch(s);
    if (team) out.team = team;
    if (add) out.add = add;
    return out;
  },
  notFoundComponent: () => <Placeholder title="Not found" />,
});

/* An open task's chat comes with the task's own code: the first load carries the shell only. So does every part the
   root mounts over a page: each loads the first time it has something to show (Later), never before the first paint. */
const CallBar = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.CallBar })));
const CallSide = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.CallSide })));
const RingCard = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.RingCard })));
const FileViewerHost = lazy(() => import('@/features/library/exports').then((m) => ({ default: m.FileViewerHost })));
const FirstRun = lazy(() => import('@/features/start/exports').then((m) => ({ default: m.FirstRun })));
const SupportChat = lazy(() => import('@/features/support/exports').then((m) => ({ default: m.SupportChat })));
const TeammateChat = lazy(() => import('@/features/team/exports').then((m) => ({ default: m.TeammateChat })));
const AddTeammate = lazy(() => import('@/features/team/exports').then((m) => ({ default: m.AddTeammate })));
const TeamAccess = lazy(() => import('@/features/team/exports').then((m) => ({ default: m.TeamAccess })));
const StartCall = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.StartCall })));

/** Mounted from the first time `on` holds, and kept after: its code loads only once it is needed, and what it shows
    can still close in its own way (a dialog's last step stays drawn while it closes). */
function Later({ on, children }: { on: boolean; children: ReactNode }) {
  const [seen, setSeen] = useState(on);
  if (on && !seen) setSeen(true);
  return seen ? <Suspense fallback={null}>{children}</Suspense> : null;
}

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
  useTabTitle();
  const router = useRouter();
  const me = useQuery(meQuery);
  const signedIn = me.isSuccess;
  const status = useLiveStatus();
  const held = useHeldCount();
  const panel = usePagePanelOpen();

  const at = {
    path: useLocation({ select: (l) => l.pathname.replace(/^\/next(?=\/|$)/, '') || '/' }),
    chat: useLocation({ select: (l) => word((l.search as Record<string, unknown>).chat) }),
  };
  const [, top, a, b] = at.path.split('/');
  // A teammate's own page is the `agent` arrangement; a team's old address and your own page are plain pages. Only that
  // one answer is read from the directory, so an agent's status line does not redraw the shell.
  const who = top === 'team' && a ? decodeURIComponent(a) : '';
  const known = useDirectory((d) => (who ? isTeammate(who, (key) => d.byId[key], me.data?.id) : false));
  // a team's old address is a plain page on its way home; a teammate's page is your DM with them
  const isActor = (w: string) => !w.startsWith('team:') && (known ?? isTeammate(w, undefined, me.data?.id));
  const arrangement = arrangementFor(at.path, at.chat ? { chat: at.chat } : {}, isActor);
  useNotePlaces();
  useLeaveFlowKey(isFlowPath(at.path));
  const trail = useTrailParts(at.path, who);
  /* A task's list closed with its ×: the menu takes its place until you leave the tasks (a board, a page), so the
     company is always one press away; the next task opened from a board has its list again. */
  const [listShut, setListShut] = useState(false);
  if (listShut && arrangement !== 'task') setListShut(false);
  const closeList = () => {
    setListShut(true);
    foldSheet('list', false);
  };

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
        <Suspense fallback={null}>
          <SupportChat />
        </Suspense>
      </Sheet>
    ) : at.chat ? (
      <SecondChat chat={at.chat} />
    ) : undefined;
  let widgets: ReactNode;
  let side: ReactNode;
  let list: ReactNode;
  if (arrangement === 'task') {
    const task = top === 'tasks' ? b : a;
    /* Beside the open task, the list it was opened from, the task chosen: inside Flow, what waits on you; under a
       pipeline, that pipeline's tasks as its board showed them. Flow is entered only on purpose. */
    if (task && top === 'flow') list = <TaskList open={decodeURIComponent(task)} />;
    // a task with no pipeline of its own (`all`), or its list closed: the menu stays in its place
    else if (task && top === 'tasks' && a && a !== 'all' && !listShut)
      list = <PipelineList pipeline={decodeURIComponent(a)} open={decodeURIComponent(task)} onClose={closeList} />;
    // opened from a thread (lib besideThread), that thread stays beside it (plan/layers.html); else the task's own chat
    if (!at.chat) chat = task ? (
      <Sheet kind="chat" label="The task's chat">
        <Suspense fallback={null}>
          <TaskChat task={decodeURIComponent(task)} flow={top === 'flow'} />
        </Suspense>
      </Sheet>
    ) : undefined;
  } else if (arrangement === 'call' && a) {
    side = (
      <Suspense fallback={null}>
        <CallSide room={decodeURIComponent(a)} />
      </Suspense>
    );
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
  const callOut = useCallWindow().open;
  const ringing = (useQuery(ringingQuery).data?.calls ?? []).some((c) => c.ring && c.state === 'live');
  const setupOpen = useLocation({ select: (l) => !!(l.search as { setup?: unknown }).setup });
  const adding = useLocation({ select: (l) => validateTeamSearch(l.search as Record<string, unknown>).add });
  const addTo = useLocation({ select: (l) => word((l.search as Record<string, unknown>).team) });
  const fileOpen = useFileOpen();

  return (
    <>
      {/* a call on another page shows its bar over the frame; the frame takes the rest of the window */}
      <div className="shell-col">
        <Later on={isCallActive(callNow) || callOut}>
          <CallBar />
        </Later>
        <Frame
          arrangement={callChat ? 'chat' : arrangement}
          tico={<TicoSheet />}
          menu={<AppMenu note={status === 'offline' ? <OfflineBar held={held} /> : <DesktopMove />} />}
          page={page}
          chat={chat}
          list={list}
          widgets={widgets}
          side={side}
          panel={panel ? <PanelHost /> : undefined}
          trail={trail}
          lips={{ menu: <PhoneMenuLip /> }}
          lipActs={{ call: <CallMute />, tico: <SearchButton /> }}
          edgeActs={{ tico: <SearchButton /> }}
          onCloseChat={callChat ? hideCallChat : closeChat}
          onClosePanel={closePanel}
        />
      </div>
      {signedIn ? (
        <Later on={setupOpen}>
          <FirstRun />
        </Later>
      ) : null}
      {/* Add teammate over any page (`?add=`), onto the team the address names */}
      <Later on={!!adding}>{adding ? <AddTeammate step={adding} team={addTo ?? ''} /> : null}</Later>
      <ActionsRoot dot={dotEl} fold={foldSheet} />
      <Toasts />
      {/* the file `files.view` opens, over any page */}
      <Later on={fileOpen}>
        <FileViewerHost />
      </Later>
      <Later on={ringing}>
        <RingCard />
      </Later>
      {/* over every page: a locked plan; the app's own files failing to load after a release */}
      <PlanLock />
      <AssetRecovery />
    </>
  );
}

/* ---------------------------------------------------------------- the trail */

/** The trail over every page (shell/Trail.tsx), with the names it needs: the company's, as the menu reads it; a
    pipeline's; the teammate `/team/<who>` names; and the open page's own. */
function useTrailParts(path: string, who: string): TrailPart[] {
  const { data: config } = useQuery(configQuery);
  const { data: pipelines } = useQuery(pipelinesQuery);
  const name = useDirectory((d) => (who ? (d.byId[who] ?? d.byId[`human:${who}`])?.name : undefined));
  const page = usePageName();
  return trailOf(path, { company: config?.company_name || config?.app_name || '', pipelines, who: name, page });
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

/* The Team tab's tree (shell/orgTree.ts), its rows as the menu draws them: each person's or agent's DM, and the Dot
   on whoever asks you something. */
function menuOrg(d: Directory, meId: string | undefined, asks: Asks): MenuNode[] {
  // a DM names an agent by its id or slug; the directory knows both
  const asking = new Set([...asks.mates].map((x) => d.byId[x]?.id ?? x));
  const draw = (n: OrgNode): MenuNode =>
    n.kind === 'team'
      ? { ...n, kids: n.kids.map(draw) }
      : { kind: 'mate', key: n.key, mate: { ...mate(n.actor), needs: asking.has(n.actor.id) }, team: n.team, label: n.label, kids: n.kids.map(draw), size: n.size };
  return orgTree(d, meId).map(draw);
}

/* A team's ⋯ in the menu: Add teammate onto it, its Rules, and its Access for whoever manages it (read when the menu
   opens). */
function TeamRows({ team, onAccess }: { team: MenuTeam; onAccess: () => void }) {
  const router = useRouter();
  const reach = useQuery(teammateAccessQuery('team', team.id)).data;
  return (
    <>
      <MenuItem label="Add teammate" icon="plus" onSelect={press('team.addTeammate', { team: team.id })} />
      <MenuItem label="Rules" icon="rules" onSelect={() => void router.navigate({ to: '/company/rules', search: { who: `group:${team.id}` } as never })} />
      {reach?.you?.manage ? <MenuItem label="Access" icon="lock" onSelect={onAccess} /> : null}
    </>
  );
}

/** True once the router has resolved its first page. */
const usePageOpened = () => useRouterState({ select: (state) => state.resolvedLocation !== undefined });

/* Counts are what the server already answers (needs-you, catch-up, updates); the stream keeps them current. */
const some = (n: number | undefined) => (n ? n : undefined);
/** A menu row's press: the same action Tico would run. */
const press = (id: string, input: unknown = {}) => () => void run(id, input, { via: 'click' });

function AppMenu({ note }: { note: ReactNode }) {
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);
  const self = useActor(me?.id);
  // Catch up and Support read whole feeds. Give the first page's loader the connection before warming those caches.
  const badgesEnabled = usePageOpened() && !!me;
  const { data: catchUp } = useQuery({ ...catchUpQuery, enabled: badgesEnabled });
  const { data: list } = useInfiniteQuery(threadListQuery);
  const asks = asksOf(catchUp, list?.pages.flatMap((p) => p.rows) ?? [], me?.id);
  const org = useDirectory((d) => menuOrg(d, me?.id, asks));
  const reveal = useLocation({ select: (l) => word((l.search as Record<string, unknown>).team) });
  const [access, setAccess] = useState<MenuTeam | null>(null);
  const { data: pipelines } = useQuery(pipelinesQuery);
  const { data: flow } = useQuery({ ...flowQuery, select: (data) => data.count });
  const { data: support } = useQuery({ ...supportUnreadQuery, enabled: badgesEnabled });
  const companies = useCompanies();
  const [calling, setCalling] = useState(false);
  const [messaging, setMessaging] = useState(false);
  const name = config?.company_name || config?.app_name || '';
  const meMate: MenuMate = self
    ? mate(self)
    : { id: me?.id ?? 'me', name: me?.email ?? '', short: short(me?.email ?? ''), hue: 0, kind: 'person', to: `/team/${encodeURIComponent(bare(me?.id ?? ''))}` };
  return (
    <>
      <Menu
        company={{ name, mark: name.slice(0, 1).toUpperCase() }}
        me={meMate}
        org={org ?? []}
        reveal={reveal}
        teamMenu={(t) => <TeamRows team={t} onAccess={() => setAccess(t)} />}
        pipelines={pipelines && [
          ...pipelines.map((p) => ({ id: p.id, name: p.name, to: `/tasks/${encodeURIComponent(p.id)}` })),
          // the Features pipeline read as horizons: only a company that has one gets the line
          ...(roadmapPipeline(pipelines) ? [{ id: 'roadmap', name: 'Roadmap', to: '/roadmap' }] : []),
        ]}
        counts={{ flow: some(flow), support: some(support) }}
        threads={
          <Suspense fallback={null}>
            <MenuThreads asks={asks.threads} />
          </Suspense>
        }
        note={note}
        onHelp={press('support.open')}
        onMessage={() => setMessaging(true)}
        onCall={() => setCalling(true)}
        onAutomations={press('shell.goTo', { to: '/company/routines' })}
        companyMenu={<CompanyRows name={name} all={companies} />}
        accountMenu={<AccountRows me={meMate} email={me?.email ?? ''} />}
      />
      <Later on={calling}>
        <StartCall open={calling} onOpenChange={setCalling} sheet />
      </Later>
      <Later on={messaging}>
        <MessagePick open={messaging} onOpenChange={setMessaging} />
      </Later>
      <Later on={!!access}>{access ? <TeamAccess key={access.id} team={access.id} name={access.name} onClose={() => setAccess(null)} /> : null}</Later>
    </>
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
  return <IconButton icon={muted ? 'micoff' : 'mic'} label={muted ? 'Unmute' : 'Mute'} on={muted} onClick={() => run(TOGGLE_MUTE, {}, { via: 'click' })} />;
}

/* Search beside Tico: on the phone's bar and on Tico's edge. It opens with the cursor in its field, inside this same
   press (iOS's keyboard; search/state.ts). */
function SearchButton() {
  return <IconButton icon="search" label="Search" onClick={() => openSearch()} />;
}

/* ---------------------------------------------------------------- the chats */

function SecondChat({ chat }: { chat: string }) {
  const who = useActor(bare(chat));
  return (
    <Sheet kind="chat" label={`Chat with ${who?.name ?? 'teammate'}`}>
      <Suspense fallback={null}>
        <ChatSheet chat={chat} />
      </Suspense>
    </Sheet>
  );
}

/* A teammate's own page: their chat (or, on your own page, Tico's room) stretching beside the page's column. */
function TeammateSheet({ who }: { who: string }) {
  const actor = useDirectory((d) => d.byId[who] ?? d.byId[`human:${who}`]);
  const name = actor?.name ?? (who.startsWith('bot:') ? 'Agent' : 'Person');
  return (
    <Sheet kind="chat" label={`Chat with ${name}`}>
      <SheetHead
        kind="side"
        title={name}
        lead={actor ? <Avatar decorative kind={actor.kind} name={actor.name} initials={short(actor.name)} hue={actor.hue ?? 0} size={28} live={actor.working} /> : null}
      />
      <Suspense fallback={null}>
        <TeammateChat who={who} />
      </Suspense>
    </Sheet>
  );
}

/* ---------------------------------------------------------------- where the person came from */

/** Every address shown is noted (lib/places.ts), so leaving Flow and closing a task go back to where they started. */
function useNotePlaces() {
  const router = useRouter();
  useEffect(() => {
    const note = (l: { pathname: string; searchStr: string; search: unknown; state: unknown }) =>
      notePlace(l.pathname.replace(/^\/next(?=\/|$)/, '') || '/', l.searchStr, l.search as Record<string, unknown>, l.state);
    note(router.state.location);
    return router.subscribe('onResolved', (ev) => note(ev.toLocation));
  }, [router]);
}

/** A field, or an open dialog or menu: their own Escape comes first. */
const OVERLAY = '[role="dialog"], [role="alertdialog"], [role="menu"], [aria-modal="true"], dialog[open]';
const typingIn = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');

/** In Flow, Escape leaves it: back to the page it was entered from. Not while typing, nor over a dialog or a menu,
    nor when a sheet took the key (its own Escape). */
function useLeaveFlowKey(on: boolean) {
  const router = useRouter();
  useEffect(() => {
    if (!on) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      if (typingIn(e.target) || document.querySelector(OVERLAY)) return;
      e.preventDefault();
      leaveFlow(router);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [on, router]);
}

/* ---------------------------------------------------------------- an open task's list */

/* The list's head is a crumb: Tasks, then the pipeline (or Flow) with its picker at the end. Tasks goes to every
   pipeline, with the menu back in the list's place. */
function ListUp() {
  return (
    <>
      <Link to="/tasks" className="li-up">
        Tasks
      </Link>
      <Icon name="chevron" className="li-sep" />
    </>
  );
}

function ListFace({ id }: { id: string | null }) {
  const who = useActor(id);
  return id ? <Avatar decorative kind={who?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? (id.startsWith('bot:') ? 'Agent' : 'Person')} hue={who?.hue} live={who?.working} size={22} /> : null;
}

/* Beside an open task, in the menu's place (pages/1-tasks/more.js flowList): every task that waits on you, by pipeline
   and stage, the open one chosen. The pick goes to a pipeline's board, Flow (pressed) back to Tasks, and Close folds
   the list to the menu's icons. */
function TaskList({ open }: { open: string }) {
  const { data: flow } = useQuery(flowQuery);
  const { data: pipes = [] } = useQuery(pipelinesQuery);
  const [shut, setShut] = useState<ReadonlySet<string>>(new Set());
  const flip = (key: string) => setShut((was) => (was.has(key) ? new Set([...was].filter((k) => k !== key)) : new Set(was).add(key)));
  const router = useRouter();
  const groups = flowGroups(flow?.items ?? [], pipes);
  return (
    <Sheet kind="list" label="Flow">
      <header className="li-head">
        <ListUp />
        <DropMenu
          align="start"
          trigger={
            <button className="li-pick" type="button">
              Flow
              <Icon name="chevdown" />
            </button>
          }
        >
          <MenuItem label="Flow" on onSelect={() => undefined} />
          {pipes.map((p) => (
            <MenuItem key={p.id} label={p.name} onSelect={press('shell.goTo', { to: `/tasks/${encodeURIComponent(p.id)}` })} />
          ))}
        </DropMenu>
        <span className="grow" />
        <span className="li-play is-on">
          <FlowMark play live />
          <b>{flow?.count ? `Flow · ${flow.count}` : 'Flow'}</b>
        </span>
        <IconButton icon="close" label="Exit Flow" quiet onClick={() => leaveFlow(router)} />
      </header>
      <div className="nav-scroll li-body">
        {flow && !groups.length ? <Empty title="Nothing waits on you" /> : null}
        {groups.map((g) => (
          <Fragment key={g.key}>
            <div className="li-sec">
              <h2 className="sec-name">
                <FoldButton
                  name={
                    <>
                      {g.pipe}
                      <Icon name="chevron" className="sec-sep" />
                      {g.stage}
                    </>
                  }
                  count={g.items.length}
                  open={!shut.has(g.key)}
                  onToggle={() => flip(g.key)}
                />
              </h2>
            </div>
            {shut.has(g.key)
              ? null
              : g.items.map((it) => {
                  const n = String(it.number ?? it.id);
                  return (
                    <Link key={it.id} to="/flow/$task" params={{ task: n }} className={n === open || it.id === open ? 'li is-on' : 'li'} title={it.title} data-target={`task:${n}`}>
                      <span className="li-mark">
                        <FlowMark size={10} />
                      </span>
                      <span className="li-main">
                        <span className="li-title">{it.title}</span>
                        {it.late ? <LatePill /> : null}
                      </span>
                      <span className="li-who">
                        <ListFace id={it.owner_id} />
                        <Ago at={it.since} className="row-age" />
                      </span>
                    </Link>
                  );
                })}
          </Fragment>
        ))}
      </div>
    </Sheet>
  );
}

/* Beside a task opened under a pipeline, in the menu's place: that pipeline's open tasks by stage, with the filters
   its board was last shown with, the open task chosen. The pick goes to another pipeline's board, the play mark
   enters Flow, and Close folds the list to the menu's icons. */
function PipelineList({ pipeline, open, onClose }: { pipeline: string; open: string; onClose: () => void }) {
  const me = useQuery(meQuery).data?.id;
  const { data: flow } = useQuery(flowQuery);
  const { data: pipes } = useQuery(pipelinesQuery);
  const p = pipes?.find((x) => x.id === pipeline);
  const { data: page } = useQuery({ ...pipelineTasksQuery(pipeline, filtersOf(validateTasksSearch(boardSearch(pipeline)))), enabled: !!p });
  const [shut, setShut] = useState<ReadonlySet<string>>(new Set());
  const flip = (key: string) => setShut((was) => (was.has(key) ? new Set([...was].filter((k) => k !== key)) : new Set(was).add(key)));
  const rows = page?.tasks ?? [];
  const stages = (p?.stages ?? []).map((s) => ({ ...s, items: rows.filter((t) => t.stage_id === s.id) })).filter((s) => s.items.length);
  const name = p?.name ?? 'Tasks';
  return (
    <Sheet kind="list" label={name}>
      <header className="li-head">
        <ListUp />
        <DropMenu
          align="start"
          trigger={
            <button className="li-pick" type="button">
              {name}
              <Icon name="chevdown" />
            </button>
          }
        >
          {(pipes ?? []).map((x) => (
            <MenuItem key={x.id} label={x.name} on={x.id === pipeline} onSelect={press('shell.goTo', { to: `/tasks/${encodeURIComponent(x.id)}` })} />
          ))}
        </DropMenu>
        <span className="grow" />
        <Link to="/flow" className="li-play" aria-pressed={false} aria-label="Flow" title="Flow">
          <FlowMark play />
          {flow?.count ? <b>{flow.count}</b> : null}
        </Link>
        <IconButton icon="close" label="Close the list" quiet onClick={onClose} />
      </header>
      <div className="nav-scroll li-body">
        {page && !stages.length ? <Empty title="No open tasks" /> : null}
        {stages.map((g) => (
          <Fragment key={g.id}>
            <div className="li-sec">
              <h2 className="sec-name">
                <FoldButton name={g.name} count={g.items.length} open={!shut.has(g.id)} onToggle={() => flip(g.id)} />
              </h2>
            </div>
            {shut.has(g.id) ? null : g.items.map((t) => <PipelineRow key={t.id} t={t} pipeline={pipeline} open={open} me={me} />)}
          </Fragment>
        ))}
      </div>
    </Sheet>
  );
}

function PipelineRow({ t, pipeline, open, me }: { t: TaskItem; pipeline: string; open: string; me: string | undefined }) {
  const n = String(t.number ?? t.id);
  const waits = !!me && t.turn?.state === 'person' && t.turn.who === me;
  return (
    <Link to="/tasks/$pipeline/$task" params={{ pipeline, task: n }} className={n === open || t.id === open ? 'li is-on' : 'li'} title={t.title} data-target={`task:${n}`}>
      <span className="li-mark">{waits ? <FlowMark size={10} /> : null}</span>
      <span className="li-main">
        <span className="li-title">{t.title}</span>
        {t.late ? <LatePill /> : null}
      </span>
      <span className="li-who">
        <ListFace id={t.owner_id} />
        <Ago at={t.stage_since ?? t.updated_at} className="row-age" />
      </span>
    </Link>
  );
}
