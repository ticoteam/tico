import { useInfiniteQuery, useQuery, type QueryClient } from '@tanstack/react-query';
import { Link, Outlet, createRootRouteWithContext, useLocation, useRouter, useRouterState } from '@tanstack/react-router';
import { Fragment, Suspense, lazy, useEffect, useState, type ReactNode } from 'react';
import { ActionsRoot, run } from '@/actions';
import { reportCallToDesktop, useCallWindow } from '@/desktop';
import { ticoRoomQuery } from '@/conversation';
import { configQuery, meQuery, recordVisit, useActor, useDirectory, useHeldCount, type Actor, type Directory } from '@/data';
import { TOGGLE_MUTE, isCallActive, ringingQuery, useCall } from '@/features/calls/routing';
import { useFileOpen } from '@/features/library/routing';
import { AssetRecovery, PlanLock } from '@/features/notices/exports';
import { StartScreen, useStartGate } from '@/features/start/routing';
import { SUPPORT_CHAT, supportUnreadQuery } from '@/features/support/routing';
import { flowGroups, flowQuery, pipelinesQuery, roadmapPipeline } from '@/features/tasks/routing';
import { teamSlug, whoHref } from '@/features/team/routing';
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
  useSheet,
  useTabTitle,
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

/* An open task's chat comes with the task's own code: the first load carries the shell only. So does every part the
   root mounts over a page: each loads the first time it has something to show (Later), never before the first paint. */
const TaskChat = lazy(() => import('@/features/task/exports').then((m) => ({ default: m.TaskChat })));
const CallBar = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.CallBar })));
const CallSide = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.CallSide })));
const RingCard = lazy(() => import('@/features/calls/exports').then((m) => ({ default: m.RingCard })));
const FileViewerHost = lazy(() => import('@/features/library/exports').then((m) => ({ default: m.FileViewerHost })));
const FirstRun = lazy(() => import('@/features/start/exports').then((m) => ({ default: m.FirstRun })));
const SupportChat = lazy(() => import('@/features/support/exports').then((m) => ({ default: m.SupportChat })));
const TeammateChat = lazy(() => import('@/features/team/exports').then((m) => ({ default: m.TeammateChat })));
const ChatSheet = lazy(() => import('@/features/threads/exports').then((m) => ({ default: m.ChatSheet })));
const MenuThreads = lazy(() => import('@/features/threads/exports').then((m) => ({ default: m.MenuThreads })));
const MessagePick = lazy(() => import('@/features/threads/exports').then((m) => ({ default: m.MessagePick })));
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
  // A teammate's own page is the `agent` arrangement; a team's page and your own page are plain pages. Only that one
  // answer is read from the directory, so an agent's status line does not redraw the shell.
  const who = top === 'team' && a ? decodeURIComponent(a) : '';
  const known = useDirectory((d) => (who ? isTeammate(who, (key) => d.byId[key], me.data?.id) : false));
  // a team's page is a plain page (its thread is a row in Threads); a teammate's page is your DM with them
  const isActor = (w: string) => !w.startsWith('team:') && (known ?? isTeammate(w, undefined, me.data?.id));
  const arrangement = arrangementFor(at.path, at.chat ? { chat: at.chat } : {}, isActor);

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
    // Task and Flow are one view: what waits on you stands in the menu's place, the open task chosen
    list = task ? <TaskList open={decodeURIComponent(task)} /> : undefined;
    chat = task ? (
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
          tico={<TicoSheet foot={<CatchUpChip />} />}
          menu={<AppMenu note={status === 'offline' ? <OfflineBar held={held} /> : null} />}
          page={page}
          chat={chat}
          list={list}
          widgets={widgets}
          side={side}
          panel={panel ? <PanelHost /> : undefined}
          lips={{ menu: <PhoneMenuLip /> }}
          lipActs={{ call: <CallMute /> }}
          onCloseChat={callChat ? hideCallChat : closeChat}
          onClosePanel={closePanel}
        />
      </div>
      {signedIn ? (
        <Later on={setupOpen}>
          <FirstRun />
        </Later>
      ) : null}
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

function teamsOf(d: Directory, meId: string | undefined, asks: Asks): MenuTeam[] {
  // a DM names an agent by its id or slug; the directory knows both
  const asking = new Set([...asks.mates].map((x) => d.byId[x]?.id ?? x));
  const by = new Map<string, MenuTeam>();
  for (const a of d.actors) {
    if (!a.team || a.id === meId) continue;
    const t = by.get(a.team) ?? { id: a.team, name: a.team, to: `/team/${encodeURIComponent(teamSlug(a.team))}`, members: [] };
    t.members.push({ ...mate(a), needs: asking.has(a.id) });
    by.set(a.team, t);
  }
  return [...by.values()];
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
  const teams = useDirectory((d) => teamsOf(d, me?.id, asks));
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
        teams={teams ?? []}
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
        onSearch={() => openSearch()}
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
    </>
  );
}

/* Tico's chip over its box (plan/threads-team.html "Catch up"): while something is new, one tap asks Tico to catch
   you up, through Tico's own chat. It goes once pressed, until something else comes in. */
const CATCH_UP = 'Catch me up';
function CatchUpChip() {
  // The menu reads the same feed once the first page has opened; the chip waits with it.
  const { data } = useQuery({ ...catchUpQuery, enabled: usePageOpened() });
  const [asked, setAsked] = useState<number | null>(null);
  if (!data?.count || asked === data.count) return null;
  return (
    <button
      type="button"
      className="qchip"
      onClick={() => {
        setAsked(data.count);
        void run('tico.ask', { text: CATCH_UP }, { via: 'click' });
      }}
    >
      <Icon name="archive" />
      Catch up
    </button>
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
  const name = actor?.name ?? 'Teammate';
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

/* ---------------------------------------------------------------- an open task's list */

function ListFace({ id }: { id: string | null }) {
  const who = useActor(id);
  return id ? <Avatar decorative kind={who?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? 'Teammate'} hue={who?.hue} live={who?.working} size={22} /> : null;
}

/* Beside an open task, in the menu's place (pages/1-tasks/more.js flowList): every task that waits on you, by pipeline
   and stage, the open one chosen. The pick goes to a pipeline's board, Flow (pressed) back to Tasks, and Close folds
   the list to the menu's icons. */
function TaskList({ open }: { open: string }) {
  const sheet = useSheet();
  const { data: flow } = useQuery(flowQuery);
  const { data: pipes = [] } = useQuery(pipelinesQuery);
  const [shut, setShut] = useState<ReadonlySet<string>>(new Set());
  const flip = (key: string) => setShut((was) => (was.has(key) ? new Set([...was].filter((k) => k !== key)) : new Set(was).add(key)));
  const groups = flowGroups(flow?.items ?? [], pipes);
  return (
    <Sheet kind="list" label="Flow">
      <header className="li-head">
        <DropMenu
          align="start"
          trigger={
            <button className="li-pick" type="button">
              All pipelines
              <Icon name="chevdown" />
            </button>
          }
        >
          <MenuItem label="All pipelines" on onSelect={() => undefined} />
          {pipes.map((p) => (
            <MenuItem key={p.id} label={p.name} onSelect={press('shell.goTo', { to: `/tasks/${encodeURIComponent(p.id)}` })} />
          ))}
        </DropMenu>
        <span className="grow" />
        <Link to="/tasks" className="li-play is-on" aria-pressed aria-label="Flow" title="Flow">
          <FlowMark play live />
          {flow?.count ? <b>{flow.count}</b> : null}
        </Link>
        {sheet.canFold ? <IconButton icon="close" label="Close" quiet onClick={sheet.toggleFold} /> : null}
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
