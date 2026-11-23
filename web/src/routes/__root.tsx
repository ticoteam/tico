import { useInfiniteQuery, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { Link, Outlet, createRootRouteWithContext, useLocation, useRouter, useRouterState } from '@tanstack/react-router';
import { Fragment, Suspense, lazy, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { ActionsRoot, run } from '@/actions';
import { reportCallToDesktop, useCallWindow } from '@/desktop';
import { ticoRoomQuery } from '@/conversation';
import { configQuery, meQuery, pageOwnersQuery, recordVisit, useActor, useDirectory, useHeldCount, type Actor, type Directory } from '@/data';
import { callQuery, isCallActive, ringingQuery, useCall } from '@/features/calls/routing';
import { useFileOpen } from '@/features/library/routing';
import { AssetRecovery, DesktopMove, PlanLock } from '@/features/notices/exports';
import { StartScreen, useStartGate } from '@/features/start/routing';
import { SUPPORT_CHAT, supportUnreadQuery } from '@/features/support/routing';
import { TaskChat } from '@/features/task/exports';
import { boardOf, CHAT_TAB, useLoadedTask } from '@/features/task/routing';
import { filtersOf, flowCountQuery, flowQuery, pipelinesQuery, pipelineTasksQuery, roadmapPipeline, useFlowCountRefresh, validateTasksSearch, type TaskItem } from '@/features/tasks/routing';
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
  fromPage,
  pageAt,
  useBarFold,
  useSheet,
  type AgentBar,
  type PageAt,
  isCallWindow,
  isTeammate,
  reportToDesktop,
  useCompanies,
  useSheetOpen,
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
import { usePhoneCards } from '@/shell/phone';
import { TicoSheet, useTicoPower } from '@/tico';
import { dot } from '@/tico/dot';
import { pageName, usePageName } from '@/tico/pageName';
import { boardSearch, chatAddress, leaveTask, noteList, notePlace, pressListRow } from '@/lib';

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
  const me = useQuery(meQuery);
  useFlowCountRefresh(!gate.screen && me.isSuccess);
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
  const flow = useQuery({ ...flowCountQuery, enabled: on && me.isSuccess });
  const signedIn = me.isSuccess && config.isFetched ? true : signedOut ? false : null;
  const name = config.data?.company_name || config.data?.app_name || '';
  const email = me.data?.email ?? '';
  const needs = flow.data;
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
    // read from the address as written: a task's own routes drop `tab` from the search they keep
    tab: useLocation({ select: (l) => new URLSearchParams(l.searchStr).get('tab') ?? undefined }),
  };
  const [, top, a, b, c] = at.path.split('/');
  // A teammate's own page is the `agent` arrangement; a team's old address and your own page are plain pages. Only that
  // one answer is read from the directory, so an agent's status line does not redraw the shell.
  const who = top === 'team' && a ? decodeURIComponent(a) : '';
  const known = useDirectory((d) => (who ? isTeammate(who, (key) => d.byId[key], me.data?.id) : false));
  // a team's old address is a plain page on its way home; a teammate's page is your DM with them
  const isActor = (w: string) => !w.startsWith('team:') && (known ?? isTeammate(w, undefined, me.data?.id));
  const arrangement = arrangementFor(at.path, at.chat ? { chat: at.chat } : {}, isActor);
  useNotePlaces();
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
      <SecondChat chat={at.chat} task={arrangement === 'task' && top === 'tasks' ? `/tasks/${a}/${b}/${CHAT_TAB}` : undefined} />
    ) : undefined;
  let widgets: ReactNode;
  let side: ReactNode;
  let list: ReactNode;
  // An open task: its pipeline (`/tasks/<pipeline>/<n>`; none by its number alone) and the task as its route loaded it.
  const task = arrangement === 'task' ? (top === 'tasks' ? b : a) : undefined;
  const pipeline = arrangement === 'task' && top === 'tasks' && a && a !== 'all' ? decodeURIComponent(a) : undefined;
  const open = useLoadedTask(task && decodeURIComponent(task));
  if (arrangement === 'task') {
    /* Beside the open task, its pipeline's tasks as its board showed them, the task chosen. A task with no pipeline of
       its own (`all`), or its list closed: the menu stays in its place. */
    if (task && pipeline && !listShut) list = <PipelineList key={pipeline} pipeline={pipeline} open={decodeURIComponent(task)} onClose={closeList} />;
    // opened from a thread (lib besideThread), that thread stays beside it (plan/layers.html); else the task's own chat
    if (!at.chat) chat = task ? (
      <Sheet kind="chat" label="The task's chat">
        <Suspense fallback={null}>
          <TaskChat task={decodeURIComponent(task)} />
        </Suspense>
      </Sheet>
    ) : undefined;
  } else if (arrangement === 'call' && a) {
    side = (
      <Suspense fallback={null}>
        <CallSideBeside room={decodeURIComponent(a)} />
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
  // On a phone: whose face the Agent icon shows (the page owner's comes with the bar), and whether the chat comes up: a
  // link to a task's chat (`/tasks/<p>/<n>/chat`, `?tab=chat`), or a ❓ in it that waits on the person.
  const chatWho = arrangement === 'agent' ? who : at.chat ? (at.chat === SUPPORT_CHAT ? undefined : bare(at.chat)) : (open.task?.owner_id ?? (callChat ? bare(callChat) : undefined));
  const chatAsked = arrangement === 'task' && (c === CHAT_TAB || at.tab === CHAT_TAB);
  const asked = open.asked.join(' ') || undefined;
  const raiseChat = chatAsked || !!asked;
  // an open task's own chat is the phone's Agent once its read shows it has one: a task that did not load (gone, not
  // theirs) or has no chat has none. A thread the address names (`?chat=`) is its own.
  const chatLater = arrangement === 'task' && !at.chat && !open.task?.conversation_id;
  // the chat card is set again when the open task or the chat its address names changes; a tuck holds until then
  const chatKey = task ? `${task}\n${at.chat ?? ''}\n${chatAsked}` : undefined;
  const client = useQueryClient();
  const leave = () => leaveTask(router, boardOf(client, open.task?.pipeline_id ?? pipeline));
  // Every company page's owner, in the Agent bar beside it (master plan P5): the viewer's own DM with them.
  const place = pageAt(at.path);
  // the phone's card is set again on any change of the full address (path and search); the fold stays per page
  const address = useLocation({ select: (l) => `${l.pathname}${l.searchStr}` });
  const bar = useOwnerBar(place, me.data?.id, address);
  const barShown = !!bar && arrangement === 'page' && !chat;
  if (barShown) chat = <OwnerBar bar={bar} place={place} />;
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
          arrangement={callChat || barShown ? 'chat' : arrangement}
          tico={<TicoSheet />}
          menu={<AppMenu note={status === 'offline' ? <OfflineBar held={held} /> : <DesktopMove />} />}
          page={page}
          chat={chat}
          list={list}
          widgets={widgets}
          side={side}
          panel={panel ? <PanelHost /> : undefined}
          trail={trail}
          marks={{ menu: <CompanyMark />, chat: <ChatFace id={chatWho} /> }}
          raiseChat={raiseChat}
          asked={asked}
          chatLater={chatLater}
          chatKey={chatKey}
          onSearch={() => openSearch()}
          onLeave={leave}
          edgeActs={{ tico: <SearchButton /> }}
          onCloseChat={callChat ? hideCallChat : closeChat}
          onClosePanel={closePanel}
          bar={barShown ? bar : undefined}
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

/** A call's side, told whether Tico's chat shows beside it (Tico's sheet open, and Tico on: off, the sheet says so
    instead): in Tico's own room the side then leaves its Chat tab out, so one conversation never shows twice. */
function CallSideBeside({ room }: { room: string }) {
  const open = useSheetOpen('tico');
  const on = useTicoPower()?.available !== false;
  return <CallSide room={room} ticoBeside={open && on} />;
}

/* ---------------------------------------------------------------- the trail */

/** The trail over every page (shell/Trail.tsx), with the names it needs: the company's, as the menu reads it; a
    pipeline's; the teammate `/team/<who>` names; and the open page's own. */
function useTrailParts(path: string, who: string): TrailPart[] {
  const { data: config } = useQuery(configQuery);
  const { data: pipelines } = useQuery(pipelinesQuery);
  const name = useDirectory((d) => (who ? (d.byId[who] ?? d.byId[`human:${who}`])?.name : undefined));
  const head = usePageName();
  // a call's title comes with its data, after the route has drawn: read from the call itself, not its head
  const room = /^\/call\/([^/]+)/.exec(path)?.[1];
  const call = useQuery({ ...callQuery(room ? decodeURIComponent(room) : ''), enabled: !!room, select: (c) => c.title });
  const page = room ? call.data || head : head;
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
  const { data: flow } = useQuery(flowCountQuery);
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

/* The company's mark, and the Flow mark when something waits on you (from the same read as the menu's count): on the
   menu's tucked edge and the phone's Company icon. */
function CompanyMark() {
  const { data: config } = useQuery(configQuery);
  const { data: flow } = useQuery(flowCountQuery);
  return (
    <>
      <span className="co-mark" aria-hidden="true">
        {(config?.company_name || config?.app_name || '').slice(0, 1).toUpperCase()}
      </span>
      {flow ? (
        <span className="lip-end">
          <span className="dot flow" style={{ '--d': '12px' } as CSSProperties} role="img" aria-label="Waits on you">
            <i />
            <b />
          </span>
        </span>
      ) : null}
    </>
  );
}

/* The open chat's face on the phone's Agent icon: the agent or person it is with, else a chat's mark (a thread). */
function ChatFace({ id }: { id: string | null | undefined }) {
  const who = useActor(id);
  return who ? <Avatar decorative kind={who.kind} name={who.name} initials={short(who.name)} hue={who.hue ?? 0} size={28} live={who.working} /> : <Icon name="message" />;
}

/* Search under Tico's Dot on its tucked edge. It opens with the cursor in its field, inside this same press (iOS's
   keyboard; search/state.ts). */
function SearchButton() {
  return <IconButton icon="search" label="Search" onClick={() => openSearch()} />;
}

/* ---------------------------------------------------------------- the chats */

/** A chat beside the page. Beside a task on a phone (a side thread a card opened it from), it links to the task's own
    chat (`task`, its address), which takes its place in the same card. */
function SecondChat({ chat, task }: { chat: string; task?: string }) {
  const who = useActor(bare(chat));
  const phone = usePhoneCards();
  return (
    <Sheet kind="chat" label={`Chat with ${who?.name ?? 'teammate'}`}>
      {task && phone ? (
        <Link className="bar-ask" data-stay to={task as never}>
          Task chat
        </Link>
      ) : null}
      <Suspense fallback={null}>
        <ChatSheet chat={chat} />
      </Suspense>
    </Sheet>
  );
}

/* ---------------------------------------------------------------- the Agent bar */

type OwnerBarState = AgentBar & { owner: string; askHref: string | null; askKind: 'question' | 'ask' | null };

/** The open page's owner for the Agent bar (shell/pageOwner.ts): a pipeline's own agent while one is shown, else the
    page's (backend/page_owners.py, which falls back to the General Manager); none on a personal page (Tico is its chat)
    or a page that is a chat already. Its fold is the person's for this page; an ask from the owner that waits on them
    opens it until they fold it again. */
function useOwnerBar(place: PageAt, me: string | undefined, address: string): OwnerBarState | null {
  const company = place.owner === 'page';
  const { data: owners } = useQuery({ ...pageOwnersQuery, enabled: company && !!me });
  const { data: pipelines } = useQuery({ ...pipelinesQuery, enabled: company && !!me });
  const pipeline =
    place.item?.kind === 'pipeline' ? pipelines?.find((p) => p.id === place.item!.id) : place.page === 'roadmap' && pipelines ? roadmapPipeline(pipelines) : null;
  // the pipeline's first agent that is set up and on the team (never a planned or archived one), else the page's
  const live = (a: Actor | undefined) => a?.kind === 'agent' && a.lifecycle !== 'planned' && a.lifecycle !== 'archived';
  const mine = useDirectory((d) => (pipeline ? [pipeline.manager, pipeline.owner].find((a) => !!a?.startsWith('bot:') && live(d.byId[a])) : undefined));
  const bot = owners?.pages[place.page]?.bot;
  const owner = !company ? null : (mine ?? (bot ? (bot.includes(':') ? bot : `bot:${bot}`) : null));
  const who = useActor(owner);
  const opened = usePageOpened();
  // a ❓ question or an ask from this owner that waits on you for this page (Needs you's own items, already read):
  // one on a task opens its pipeline's page; one with no task opens the pages the agent owns itself, never those where
  // it only stands in (the General Manager for a page with no owner)
  // the agent owns this page itself: the pipeline's own agent while one is shown, else the page's own owner (not the
  // General Manager standing in)
  const ownPage = !!mine || owners?.pages[place.page]?.own === true;
  const pipe = pipeline?.id ?? null;
  const { data: asks } = useQuery({ ...flowQuery, enabled: opened && !!owner, select: (f) => f.asks });
  // the questions the person folded the bar on, by their Needs you item: a new one opens it again, that one does not
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const waitingHere = (owner ? asks?.filter((a) => (a.by === owner || a.by === who?.id) && (a.pipeline ? a.pipeline === pipe : ownPage)) : undefined) ?? [];
  const fresh = waitingHere.find((a) => !dismissed.has(a.id));
  const waiting = fresh ?? waitingHere[0];
  const [fold, setFold] = useBarFold(me, place.page);
  if (!owner) return null;
  const name = who?.name ?? 'Agent';
  return {
    owner,
    name,
    face: <Avatar decorative kind="agent" name={name} initials={short(name)} hue={who?.hue ?? 0} size={28} live={who?.working} />,
    page: place.page,
    address,
    fold,
    ask: !!fresh,
    askHref: waiting?.href ?? null,
    askKind: waiting?.kind ?? null,
    onFold: (folded) => {
      setFold(folded);
      if (folded && fresh) setDismissed(new Set([...dismissed, ...waitingHere.map((a) => a.id)]));
    },
  };
}

/* The Agent bar's sheet: the owner's DM, never a shared thread. Opening it sends nothing: its latest messages, or words
   to start with while it is empty. Each message carries the page it was written on. */
function OwnerBar({ bar, place }: { bar: OwnerBarState; place: PageAt }) {
  // drawn only while it shows: a folded bar opens no chat, so it makes no DM and marks nothing read
  const sheet = useSheet();
  const phone = usePhoneCards();
  const shown = phone ? phone.card === 'chat' : sheet.state === 'open';
  // a file open over the page (`?file=`) is what is open there; read alone, so the page's other search draws nothing here
  const file = useLocation({ select: (l) => new URLSearchParams(l.searchStr).get('file') ?? '' });
  const here = file ? pageAt(location.pathname, `?file=${encodeURIComponent(file)}`) : place;
  return (
    <Sheet kind="chat" label={`Chat with ${bar.name}`}>
      {/* what waits on you, wherever it lives (this DM, a Home, a task's chat): one line to it */}
      {/* shown while the question waits, whether the bar opened for it or was folded and opened by hand */}
      {shown && bar.askHref && (
        <Link className="bar-ask" to={bar.askHref as never}>
          {bar.askKind === 'ask' ? 'An OK waits on you' : 'A question waits on you'}
        </Link>
      )}
      {shown && (
        <Suspense fallback={null}>
          <ChatSheet
            dm
            chat={chatAddress(bar.owner, 'agent')}
            from={fromPage(here)}
            starters={[`What needs me on ${place.label}?`, `What changed on ${place.label} this week?`]}
          />
        </Suspense>
      )}
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

/** Every address shown is noted (lib/places.ts), so closing a task goes back to where it was opened from. */
function useNotePlaces() {
  const router = useRouter();
  useEffect(() => {
    const note = (l: { pathname: string; searchStr: string; search: unknown; state: unknown }) =>
      notePlace(l.pathname.replace(/^\/next(?=\/|$)/, '') || '/', l.searchStr, l.search as Record<string, unknown>, l.state);
    note(router.state.location);
    return router.subscribe('onResolved', (ev) => note(ev.toLocation));
  }, [router]);
}

/* ---------------------------------------------------------------- an open task's list */

/* The list's head is a crumb: Tasks, then the pipeline with its picker at the end. Tasks goes to every pipeline, with
   the menu back in the list's place. */
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

/* Beside a task opened under a pipeline, in the menu's place: that pipeline's open tasks by stage, with the filters
   its board was last shown with, the open task chosen. The pick goes to another pipeline's board, and Close folds the
   list to the menu's icons. */
function PipelineList({ pipeline, open, onClose }: { pipeline: string; open: string; onClose: () => void }) {
  const me = useQuery(meQuery).data?.id;
  const { data: pipes } = useQuery(pipelinesQuery);
  const p = pipes?.find((x) => x.id === pipeline);
  const { data: page } = useQuery({ ...pipelineTasksQuery(pipeline, filtersOf(validateTasksSearch(boardSearch(pipeline)))), enabled: !!p });
  const [shut, setShut] = useState<ReadonlySet<string>>(new Set());
  const flip = (key: string) => setShut((was) => (was.has(key) ? new Set([...was].filter((k) => k !== key)) : new Set(was).add(key)));
  const stages = useMemo(() => (p?.stages ?? []).map((s) => ({ ...s, items: (page?.tasks ?? []).filter((t) => t.stage_id === s.id) })).filter((s) => s.items.length), [p, page]);
  // the rows in the order drawn, folded stages left out, while the list shows (not folded or tucked away): an answer
  // that leaves the open task with nothing on it for the person goes on to the row below it (TaskPage)
  const sheet = useSheet();
  const shows = sheet.state === 'open' || sheet.peek;
  const drawn = useMemo(() => stages.flatMap((g) => (shut.has(g.id) ? [] : g.items.map((t) => ({ id: t.id, n: String(t.number ?? t.id) })))), [stages, shut]);
  useEffect(() => noteList(shows ? { pipeline, open, rows: drawn } : null), [shows, pipeline, open, drawn]);
  useEffect(() => () => noteList(null), []);
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
    <Link
      to="/tasks/$pipeline/$task"
      params={{ pipeline, task: n }}
      className={n === open || t.id === open ? 'li is-on' : 'li'}
      title={t.title}
      data-target={`task:${n}`}
      onClick={(e) => pressListRow(pipeline, n, e)}
    >
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
