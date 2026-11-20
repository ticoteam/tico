import { Link, useLocation, type LinkProps } from '@tanstack/react-router';
import { useEffect, useState, type ReactElement, type ReactNode } from 'react';
import { Avatar } from '@/kit/Avatar';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { FlowMark } from '@/kit/FlowMark';
import { Menu as Drop, MenuItem } from '@/kit/Menu';
import { usePref } from '@/prefs';
import { usePhoneCards } from './phone';
import { useSheet } from './Sheet';
import './Menu.css';

/* The menu: a port of PK.menu and its rail in the design's system/shell.js, with the mock's classes.
   Calendar holds Meetings, so there is no Meetings row. Pages a person cannot open are not drawn. Under the places,
   the conversation area (plan/threads-team.html): two small tabs, Team | Threads, with Message and Call beside them.
   Team is the org chart and your DMs; Threads every other conversation. No counts there: bold is new, and the Dot on
   a row marks a conversation that asks you (never on a tab or a folded team). */

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
  /* the team's page (its thread is a row in Threads) */
  to: string;
  members: MenuMate[];
}
export interface MenuCompany {
  mark: string;
  name: string;
  needs?: boolean;
}
export interface MenuProps {
  company: MenuCompany;
  me: MenuMate;
  teams: MenuTeam[];
  counts?: MenuCounts;
  /* the pipelines, drawn under Tasks while it is open (as Company's pages are under Company) */
  pipelines?: { id: string; name: string; to: string }[];
  /* whether the person may open a page: 'tasks', 'company/finance', 'library/tools', 'settings' … (default: yes) */
  can?: (page: string) => boolean;
  /* the team shown open until the person opens or shuts one (by default, the team of the page the menu first drew on) */
  openTeam?: string;
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
/* the team of the page at `path`: the team's own page, or the team of the teammate whose page it is */
function teamAt(p: MenuProps, path: string) {
  const onMate = [p.me, ...p.teams.flatMap(t => t.members)].find(m => path.startsWith(m.to));
  return p.teams.find(t => path.startsWith(t.to) || t.members.some(m => m === onMate))?.id;
}
/* the menu's paths are plain strings from the table above; the router resolves them under its base path */
const to = (path: string) => path as LinkProps['to'];
const ACTIVE = { className: 'is-active', 'aria-current': 'page' as const };
/* a shut team shows this many faces, so its name keeps its room */
const FACES = 5;

/* the row or link already says the name */
function Face({ m, size }: { m: MenuMate; size: 16 | 22 | 28 }) {
  return <Avatar decorative kind={m.kind} name={m.name} initials={m.short} hue={m.hue} size={size} live={m.live} />;
}
const Asks = () => <FlowMark label="Asks you" />;
const TALK: { id: MenuTalk; label: string }[] = [
  { id: 'team', label: 'Team' },
  { id: 'threads', label: 'Threads' },
];

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
  /* What is open here is the person's: only the menu's own controls open or shut a section or a team. Until they do,
     the section and the team of the page the menu first drew on are open, and that first guess is kept. Going deeper,
     or landing on a page from a link, a card or an address, never opens, shuts or grows anything here: the page's
     section is only marked. */
  const [first] = useState(path);
  const [sections, setSections] = usePref<Shown | null>('menu.sections.v1', null);
  const [teamsShown, setTeamsShown] = usePref<Shown | null>('menu.teams.v1', null);
  const firstTeam = p.openTeam ?? teamAt(p, first);
  const shownSections = sections ?? { [topOf(first)]: true };
  const shownTeams = teamsShown ?? (firstTeam ? { [firstTeam]: true } : {});
  const keepSections = sections == null;
  const keepTeams = teamsShown == null && p.teams.length > 0;
  useEffect(() => {
    if (keepSections) setSections({ [topOf(first)]: true });
  }, [keepSections, first, setSections]);
  useEffect(() => {
    if (keepTeams) setTeamsShown(firstTeam ? { [firstTeam]: true } : {});
  }, [keepTeams, firstTeam, setTeamsShown]);
  // Your last tab stays; the Threads page with no thread open says its list is here, so it shows that tab.
  const [talk, setTalk] = usePref<MenuTalk>('menu.talk.v1', 'team');
  const onThreads = path === '/threads' || path === '/threads/';
  useEffect(() => {
    if (onThreads) setTalk('threads');
  }, [onThreads, setTalk]);
  const isOpen = (t: MenuTeam) => shownTeams[t.id] === true;
  const rows = ROWS.filter(r => can(r.id));
  const c = p.counts ?? {};
  const kidsOf = (id: MenuRowId) =>
    id === 'tasks'
      ? (p.pipelines ?? []).map(x => ({ key: x.id, label: x.name, to: x.to }))
      : (KIDS[id] ?? []).filter(k => can(`${id}/${slug(k)}`)).map(k => ({ key: k, label: k, to: `/${id}/${slug(k)}` }));

  if (folded) {
    const faces = p.teams.find(isOpen)?.members ?? [];
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
            // choosing a section here opens it, and shuts the others
            <Link
              key={r.id}
              className="nav-item"
              to={to(`/${r.id}`)}
              activeProps={ACTIVE}
              activeOptions={{ exact: expanded }}
              onClick={() => setSections({ [r.id]: true })}
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
    <div className="cat" role="tablist" aria-label="Conversations">
      {TALK.map(x => (
        <button key={x.id} type="button" role="tab" aria-selected={talk === x.id} className={talk === x.id ? 'is-on' : undefined} onClick={() => setTalk(x.id)}>
          {x.label}
        </button>
      ))}
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
  /* Team: the org chart, the teams each folding to its faces; open, its teammates. A team opens its page, a teammate
     your DM with them. The chevron always folds or opens it, wherever you are. */
  const team = (
    <div className="ca-list" role="tabpanel" aria-label="Team">
      {p.teams.map(t => {
        const open = isOpen(t);
        const head = (
          <Link
            key={t.id}
            className={`tree-team${open ? ' is-open' : ''}`}
            to={to(t.to)}
            activeProps={ACTIVE}
            activeOptions={{ exact: true }}
            aria-expanded={open}
            onClick={e => {
              const flip = !!(e.target as Element).closest('.tree-chev') || (open && path === t.to);
              if (flip) e.preventDefault();
              setTeamsShown({ ...shownTeams, [t.id]: flip ? !open : true });
            }}
          >
            <span className="tree-chev">
              <Icon name="chevron" />
            </span>
            <span className="tree-name">{t.name}</span>
            {open ? null : (
              <span className="faces">
                {t.members.slice(0, FACES).map(m => (
                  <Face key={m.id} m={m} size={16} />
                ))}
              </span>
            )}
          </Link>
        );
        if (!open) return head;
        return [
          head,
          <div key={`${t.id}-org`} className="org">
            {t.members.map(m => (
              <Link key={m.id} className="tree-row" to={to(m.to)} activeProps={ACTIVE}>
                <Face m={m} size={22} />
                <span className="tree-name">{m.name}</span>
                {m.needs ? <Asks /> : null}
              </Link>
            ))}
          </div>,
        ];
      })}
    </div>
  );
  const tree = (
    <div className="ca">
      {tabs}
      {talk === 'team' ? team : p.threads}
    </div>
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
