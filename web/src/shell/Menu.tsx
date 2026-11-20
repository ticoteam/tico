import { Tabs } from '@base-ui/react/tabs';
import { Link, useLocation, type LinkProps } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react';
import { Avatar } from '@/kit/Avatar';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { FlowMark } from '@/kit/FlowMark';
import { ContextMenu, Menu as Drop, MenuItem } from '@/kit/Menu';
import { usePref } from '@/prefs';
import { revealTeam } from './orgTree';
import { usePhoneCards } from './phone';
import { useSheet } from './Sheet';
import './Menu.css';

/* The menu: a port of PK.menu and its rail in the design's system/shell.js, with the mock's classes.
   Calendar holds Meetings, so there is no Meetings row. Pages a person cannot open are not drawn. Under the places,
   the conversation area (plan/threads-team.html): two small tabs, Team | Threads, with Message and Call beside them.
   Team is the org chart and your DMs; Threads every other conversation. No counts there: bold is new, and the Dot on
   a row marks a conversation that asks you (never on a tab or a folded row). */

export type MenuRowId = 'tasks' | 'calendar' | 'company' | 'library';
export type MenuTalk = 'team' | 'threads';

export interface MenuCounts {
  /* what waits on you, shown in the Dot's shape on Tasks */
  flow?: number;
  /* the next meeting's time, beside Calendar ("2:30 PM") */
  next?: string;
  /* Tico support's replies not read yet, on the "?" */
  support?: number;
}
export interface MenuMate {
  id: string;
  name: string;
  /* initials */
  short: string;
  hue: number;
  kind: 'person' | 'agent';
  to: string;
  live?: boolean;
  /* their chat with you asks you something */
  needs?: boolean;
}
export interface MenuTeam {
  id: string;
  name: string;
}
/* A row of the Team tab's tree (orgTree.ts): a person or an agent, with who reports to them under it; or a team whose
   teammates have no manager, where the team sits. */
export type MenuNode =
  | {
      kind: 'mate';
      /* the actor's id */
      key: string;
      mate: MenuMate;
      /* the team this row heads: its ⋯ holds the team's Add teammate, Rules and Access */
      team?: MenuTeam;
      /* the team's name beside theirs, quiet */
      label?: string;
      kids: MenuNode[];
      /* everyone underneath */
      size: number;
    }
  | { kind: 'team'; key: string; team: MenuTeam; kids: MenuNode[]; size: number };
export interface MenuCompany {
  mark: string;
  name: string;
  needs?: boolean;
}
export interface MenuProps {
  company: MenuCompany;
  me: MenuMate;
  /* the Team tab: your direct reports, each folded, with who reports to them under them */
  org: MenuNode[];
  /* a team's ⋯ (and its right click or long press): Add teammate, Rules, Access */
  teamMenu?: (team: MenuTeam) => ReactNode;
  /* a team to show (`?team=<id>`, where old team links land): the Team tab, open down to it, its row in view */
  reveal?: string;
  counts?: MenuCounts;
  /* the pipelines, drawn under Tasks while it is open (as Company's pages are under Company) */
  pipelines?: { id: string; name: string; to: string }[];
  /* whether the person may open a page: 'tasks', 'company/finance', 'library/tools', 'settings' … (default: yes) */
  can?: (page: string) => boolean;
  /* drawn as icons; inside a Frame this follows the sheet's fold */
  folded?: boolean;
  /* a note above the foot, open menu only: the offline bar */
  note?: ReactNode;
  onSearch?: () => void;
  /* the company pick's menu (the root's rows: this company, the others the desktop app holds, Add company); without
     it the pick is a plain button that calls onPickCompany */
  companyMenu?: ReactNode;
  /* the account menu that opens from your name at the foot (your page, Settings, the theme, Sign out); without it
     your name links to your page */
  accountMenu?: ReactNode;
  onPickCompany?: () => void;
  onHelp?: () => void;
  /* the Threads tab's list: every conversation but your DMs, newest first, Archived folded at its foot */
  threads?: ReactNode;
  onMessage?: () => void;
  onCall?: () => void;
  /* the Threads tab's ⋯: its one item, Automations (filters are automations, in Routines) */
  onAutomations?: () => void;
}

const ROWS: { id: MenuRowId; label: string; icon: IconName }[] = [
  { id: 'tasks', label: 'Tasks', icon: 'tasks' },
  { id: 'calendar', label: 'Calendar', icon: 'calendar' },
  { id: 'company', label: 'Company', icon: 'company' },
  { id: 'library', label: 'Library', icon: 'library' },
];
const KIDS: Partial<Record<MenuRowId, string[]>> = {
  company: ['Strategy', 'Finance', 'Growth', 'Products', 'Risk', 'Routines', 'Listening', 'Rules', 'Usage'],
  library: ['Files', 'Docs', 'Tools', 'Skills', 'Contacts'],
};
const slug = (s: string) => s.toLowerCase();
type Shown = Record<string, boolean>;
const topOf = (path: string) => path.split('/')[1] ?? '';
/* the menu's paths are plain strings from the table above; the router resolves them under its base path */
const to = (path: string) => path as LinkProps['to'];
const ACTIVE = { className: 'is-active', 'aria-current': 'page' as const };

/* the row or link already says the name */
function Face({ m, size }: { m: MenuMate; size: 16 | 22 | 28 }) {
  return <Avatar decorative kind={m.kind} name={m.name} initials={m.short} hue={m.hue} size={size} live={m.live} />;
}
const Asks = () => <FlowMark label="Asks you" />;
/* a folded row's hint: a person mark and how many are underneath */
function Under({ n }: { n: number }) {
  return (
    <span className="tree-n">
      <Icon name="person" />
      {n}
    </span>
  );
}
/* the rows showing, top to bottom: the folded menu's faces */
function shownMates(nodes: readonly MenuNode[], open: (key: string) => boolean): MenuMate[] {
  return nodes.flatMap(n => [...(n.kind === 'mate' ? [n.mate] : []), ...(open(n.key) ? shownMates(n.kids, open) : [])]);
}
const TALK = [
  { id: 'team', label: 'Team' },
  { id: 'threads', label: 'Threads' },
] as const;

/* a control that opens a kit menu of the given rows, or the bare control when there are none */
function dropIf(rows: ReactNode, trigger: ReactElement, side: 'top' | 'bottom' | 'right', align: 'start' | 'end') {
  if (!rows) return trigger;
  return (
    <Drop trigger={trigger} side={side} align={align}>
      {rows}
    </Drop>
  );
}

export function Menu(p: MenuProps) {
  const sheet = useSheet();
  const phone = usePhoneCards();
  const folded = p.folded ?? sheet.folded;
  const path = useLocation({ select: l => l.pathname.replace(/^\/next(?=\/|$)/, '') });
  const can = p.can ?? (() => true);
  const top = topOf(path);
  /* What is open here is the person's: only the menu's own controls open or shut a section or a row. Until they do,
     the section of the page the menu first drew on is open, and that first guess is kept; the Team tab shows your
     direct reports, each folded. Going deeper, or landing on a page from a link, a card or an address, never opens,
     shuts or grows anything here: the page's section is only marked. An old team link (`?team=`) is the one address
     that opens rows: the team it names. */
  const [first] = useState(path);
  const [sections, setSections] = usePref<Shown | null>('menu.sections.v1', null);
  const [rowsShown, setRowsShown] = usePref<Shown>('menu.tree.v1', {});
  const shownSections = sections ?? { [topOf(first)]: true };
  const keepSections = sections == null;
  useEffect(() => {
    if (keepSections) setSections({ [topOf(first)]: true });
  }, [keepSections, first, setSections]);
  // Your last tab stays; the Threads page with no thread open says its list is here, so it shows that tab.
  const [talk, setTalk] = usePref<MenuTalk>('menu.talk.v1', 'team');
  const onThreads = path === '/threads' || path === '/threads/';
  useEffect(() => {
    if (onThreads) setTalk('threads');
  }, [onThreads, setTalk]);
  const isOpen = (key: string) => rowsShown[key] === true;
  const flip = (key: string) => setRowsShown({ ...rowsShown, [key]: !isOpen(key) });
  // `?team=`: once the tree is read, open down to the team, show the Team tab (on a phone, the menu card) and bring
  // its first row into view. A team named is opened once while the address names it; then its rows are the person's.
  const reveal = p.reveal ? revealTeam(p.org, p.reveal) : null;
  const revealing = reveal?.first ? p.reveal : undefined;
  const revealed = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!p.reveal) revealed.current = undefined;
    if (!revealing || !reveal?.first || revealed.current === revealing) return;
    revealed.current = revealing;
    setRowsShown({ ...rowsShown, ...Object.fromEntries(reveal.open.map(k => [k, true])) });
    setTalk('team');
    phone?.raise('menu');
    const row = reveal.first;
    requestAnimationFrame(() => document.querySelector(`[data-row="${CSS.escape(row)}"]`)?.scrollIntoView?.({ block: 'center' }));
  }, [p.reveal, revealing, reveal, rowsShown, setRowsShown, setTalk, phone]);
  const rows = ROWS.filter(r => can(r.id));
  const c = p.counts ?? {};
  const kidsOf = (id: MenuRowId) =>
    id === 'tasks'
      ? (p.pipelines ?? []).map(x => ({ key: x.id, label: x.name, to: x.to }))
      : (KIDS[id] ?? []).filter(k => can(`${id}/${slug(k)}`)).map(k => ({ key: k, label: k, to: `/${id}/${slug(k)}` }));

  if (folded) {
    const faces = shownMates(p.org, isOpen);
    return (
      <nav className="pane nav is-rail" aria-label="Main">
        <header className="nav-head">
          {dropIf(
            p.companyMenu,
            <button className="co-mark" type="button" aria-label={p.company.name} title={p.company.name} onClick={p.companyMenu ? undefined : p.onPickCompany}>
              {p.company.mark}
            </button>,
            'right',
            'start',
          )}
        </header>
        <div className="nav-scroll">
          <div className="nav-items">
            {rows.map(r => (
              <Link key={r.id} className="nav-item" to={to(`/${r.id}`)} activeProps={ACTIVE} aria-label={r.label} title={r.label}>
                <span className="slot-ico">
                  <Icon name={r.icon} />
                </span>
              </Link>
            ))}
          </div>
          {faces.length ? (
            <div className="tree">
              {faces.map(m => (
                <Link key={m.id} className="tree-row" to={to(m.to)} activeProps={ACTIVE} aria-label={m.name} title={m.name}>
                  <Face m={m} size={22} />
                  {m.needs ? <Asks /> : null}
                </Link>
              ))}
            </div>
          ) : null}
        </div>
        <footer className="nav-foot">
          <IconButton icon="search" label="Search" quiet onClick={p.onSearch} />
          <Help n={c.support} quiet onClick={p.onHelp} />
          {can('settings') ? (
            <Link className="ib ib-quiet" to={to('/settings')} aria-label="Settings" title="Settings">
              <Icon name="gear" />
            </Link>
          ) : null}
          <IconButton icon="panel" label="Open the menu" quiet onClick={sheet.toggleFold} />
          {p.accountMenu ? (
            dropIf(
              p.accountMenu,
              <button className="me" type="button" aria-label={p.me.name} title={p.me.name}>
                <Face m={p.me} size={28} />
              </button>,
              'right',
              'end',
            )
          ) : (
            <Link className="me" to={to(p.me.to)} aria-label={p.me.name} title={p.me.name}>
              <Face m={p.me} size={28} />
            </Link>
          )}
        </footer>
      </nav>
    );
  }

  const right = (id: MenuRowId, hasKids: boolean, expanded: boolean) => {
    if (id === 'tasks' && c.flow)
      return (
        <span className="nav-flow">
          <FlowMark />
          <b>{c.flow}</b>
        </span>
      );
    if (id === 'calendar' && c.next) return <span className="nav-next">{c.next}</span>;
    if (hasKids)
      return (
        <span className={`nav-chev${expanded ? ' is-open' : ''}`}>
          <Icon name="chevron" />
        </span>
      );
    return null;
  };

  const pick = dropIf(
    p.companyMenu,
    <button className="co-pick" type="button" aria-haspopup="menu" onClick={p.companyMenu ? undefined : p.onPickCompany}>
      <span className="co-mark" aria-hidden="true">
        {p.company.mark}
      </span>
      <span className="co-name">{p.company.name}</span>
      <Icon name="chevdown" />
      {p.company.needs ? <FlowMark /> : null}
    </button>,
    'bottom',
    'start',
  );
  const items = (
    <div className="nav-items">
      {rows.map(r => {
        const kids = kidsOf(r.id);
        const hasKids = kids.length > 0;
        const canToggle = hasKids && top === r.id && !!KIDS[r.id];
        const expanded = hasKids && shownSections[r.id] === true;
        // shut, the section marks the page inside it, which changes nothing else
        const here = path === `/${r.id}` || (!expanded && top === r.id);
        const content = (
          <>
            <Icon name={r.icon} />
            <span className="nav-label">{r.label}</span>
            {right(r.id, hasKids, expanded)}
          </>
        );
        return [
          canToggle ? (
            <button
              key={r.id}
              type="button"
              className={here ? 'nav-item is-active' : 'nav-item'}
              aria-current={here ? 'page' : undefined}
              aria-expanded={expanded}
              onClick={() => setSections({ ...shownSections, [r.id]: !expanded })}
            >
              {content}
            </button>
          ) : (
            // choosing a section here opens it, and shuts the others (in the capture phase: on a phone the menu card
            // takes the press over to open the page in the menu's place, and the link's own click never runs)
            <Link
              key={r.id}
              className="nav-item"
              to={to(`/${r.id}`)}
              activeProps={ACTIVE}
              activeOptions={{ exact: expanded }}
              onClickCapture={() => setSections({ [r.id]: true })}
            >
              {content}
            </Link>
          ),
          expanded ? (
            <div key={`${r.id}-kids`} className="nav-kids">
              {kids.map(k => (
                <Link key={k.key} className="nav-kid" to={to(k.to)} activeProps={ACTIVE}>
                  {k.label}
                </Link>
              ))}
            </div>
          ) : null,
        ];
      })}
    </div>
  );
  const tabs = (
    <div className="cat">
      <Tabs.List className="cat-tabs" aria-label="Conversations" activateOnFocus>
        {TALK.map(x => (
          <Tabs.Tab key={x.id} value={x.id} className={talk === x.id ? 'is-on' : undefined}>
            {x.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      <span className="acts">
        <IconButton icon="message" label="Message" size="sm" quiet onClick={p.onMessage} />
        <IconButton icon="call" label="Call" size="sm" quiet onClick={p.onCall} />
        {talk === 'threads' && p.onAutomations && can('company/routines') ? (
          <Drop trigger={<IconButton icon="more" label="More" size="sm" quiet />} side="bottom" align="end">
            <MenuItem label="Automations" icon="refresh" onSelect={p.onAutomations} />
          </Drop>
        ) : null}
      </span>
    </div>
  );
  /* Team: one tree by reporting line, from your direct reports down (orgTree.ts). A name opens your DM with them; the
     chevron (with the count of everyone underneath while folded) opens or folds their reports. A team that has no
     row of its own to sit on is a row that opens and folds wherever it is pressed. A row that heads a team carries
     the team's Add teammate, Rules and Access on its ⋯, a right click or a long press. */
  const row = (n: MenuNode): ReactNode => {
    const open = n.kids.length > 0 && isOpen(n.key);
    // the team the address names: the rows that stand for it are lit
    const hl = !!p.reveal && n.team?.id === p.reveal;
    const own = n.team && p.teamMenu ? p.teamMenu(n.team) : null;
    // on a person's or an agent's row, the menu says whose team it acts on
    const items =
      own && n.kind === 'mate' ? (
        <>
          <div className="menu-team">
            <Icon name="people" />
            <span>{n.team!.name}</span>
          </div>
          {own}
        </>
      ) : (
        own
      );
    const withMenu = (el: ReactElement) => (items ? <ContextMenu items={items}>{el}</ContextMenu> : el);
    const more = items ? (
      <Drop trigger={<IconButton icon="more" label={`More for ${n.team!.name}`} size="sm" quiet className="tree-more" />} side="bottom" align="end">
        {items}
      </Drop>
    ) : null;
    const under = n.kids.length && !open ? <Under n={n.size} /> : null;
    const line =
      n.kind === 'team' ? (
        <div className={`tree-line${hl ? ' is-hl' : ''}`} data-row={n.key}>
          {withMenu(
            <button type="button" className={`tree-team${open ? ' is-open' : ''}`} aria-expanded={open} onClick={() => flip(n.key)}>
              <span className="tree-ic">
                <Icon name="people" />
              </span>
              <span className="tree-name">{n.team.name}</span>
              {under}
              <span className="tree-chev">
                <Icon name="chevron" />
              </span>
            </button>,
          )}
          {more}
        </div>
      ) : (
        <div className={`tree-line${hl ? ' is-hl' : ''}`} data-row={n.key}>
          {withMenu(
            <Link className="tree-row" to={to(n.mate.to)} activeProps={ACTIVE} title={n.mate.name}>
              <Face m={n.mate} size={22} />
              <span className="tree-name">{n.mate.name}</span>
              {n.label ? <span className="tree-label">{n.label}</span> : null}
              {n.mate.needs ? <Asks /> : null}
            </Link>,
          )}
          {more}
          {n.kids.length ? (
            <button
              type="button"
              className={`tree-fold${open ? ' is-open' : ''}`}
              aria-expanded={open}
              aria-label={open ? `Fold ${n.mate.name}'s reports` : `${n.size} under ${n.mate.name}`}
              title={open ? undefined : `${n.size} under ${n.mate.name}`}
              onClick={() => flip(n.key)}
            >
              {under}
              <span className="tree-chev">
                <Icon name="chevron" />
              </span>
            </button>
          ) : null}
        </div>
      );
    return (
      <div key={n.key} className="tree-node">
        {line}
        {open ? <div className="org">{n.kids.map(row)}</div> : null}
      </div>
    );
  };
  const team = (
    <Tabs.Panel className="ca-list tree-org" value="team" tabIndex={-1}>
      {p.org.map(row)}
    </Tabs.Panel>
  );
  const tree = (
    <Tabs.Root className="ca" value={talk} onValueChange={value => setTalk(value as MenuTalk)}>
      {tabs}
      {team}
      <Tabs.Panel className="ca-panel" value="threads" tabIndex={-1}>
        {p.threads}
      </Tabs.Panel>
    </Tabs.Root>
  );
  const foot = (
    <>
      {p.accountMenu ? (
        dropIf(
          p.accountMenu,
          <button className="me me-name" type="button">
            <Face m={p.me} size={28} />
            <span>{p.me.name}</span>
          </button>,
          'top',
          'start',
        )
      ) : (
        <Link className="me me-name" to={to(p.me.to)}>
          <Face m={p.me} size={28} />
          <span>{p.me.name}</span>
        </Link>
      )}
      <span className="grow" />
      <Help n={p.counts?.support} onClick={p.onHelp} />
      {can('settings') ? (
        <Link className="ib" to={to('/settings')} aria-label="Settings" title="Settings">
          <Icon name="gear" />
        </Link>
      ) : null}
    </>
  );

  // on the phone the menu is a card (B/phone/phone.js menuFull): its head tucks it away, the list scrolls between
  if (phone)
    return (
      <>
        <header className="m-head">
          {pick}
          <span className="grow" />
          <IconButton icon="search" label="Search" onClick={p.onSearch} />
          <IconButton icon="chevdown" label="Tuck the menu away" quiet onClick={phone.tuck} />
        </header>
        <nav className="m-scroll" aria-label="Main">
          {items}
          {tree}
        </nav>
        {p.note}
        <footer className="m-foot">{foot}</footer>
      </>
    );

  return (
    <nav className="pane nav" aria-label="Main">
      <header className="nav-head">
        {pick}
        <span className="grow" />
        <IconButton icon="search" label="Search" onClick={p.onSearch} />
        {sheet.canFold ? <IconButton icon="panel" label="Fold the menu" quiet onClick={sheet.toggleFold} /> : null}
      </header>
      <div className="nav-scroll">
        {items}
        {tree}
      </div>
      {p.note}
      <footer className="nav-foot">{foot}</footer>
    </nav>
  );
}

/* The foot's "?": Tico support, with a count of its unread replies (the menu's grey count). */
function Help({ n, quiet, onClick }: { n?: number; quiet?: boolean; onClick?: () => void }) {
  const label = n ? `Tico support, ${n} unread` : 'Tico support';
  return (
    <span className="slot-ico help-slot">
      <IconButton icon="help" label={label} quiet={quiet} onClick={onClick} />
      {n ? (
        <i className="slot-n" aria-hidden="true">
          {n}
        </i>
      ) : null}
    </span>
  );
}
