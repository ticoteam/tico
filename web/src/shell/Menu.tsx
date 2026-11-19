import { Link, useLocation, type LinkProps } from '@tanstack/react-router';
import { useState, type CSSProperties, type ReactElement, type ReactNode } from 'react';
import { Avatar } from '@/kit/Avatar';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu as Drop } from '@/kit/Menu';
import { usePhoneCards } from './phone';
import { useSheet } from './Sheet';
import './Menu.css';

/* The menu: a port of PK.menu and its rail in the design's system/shell.js, with the mock's classes.
   Calendar holds Meetings, so there is no Meetings row. Pages a person cannot open are not drawn. */

export type MenuRowId = 'tasks' | 'calendar' | 'threads' | 'updates' | 'company' | 'library';

export interface MenuCounts {
  /* what waits on you, shown in the Dot's shape on Tasks */
  flow?: number;
  threads?: number;
  updates?: number;
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
  needs?: boolean;
}
export interface MenuTeam {
  id: string;
  name: string;
  to: string;
  needs?: boolean;
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
  /* the pipelines, drawn under Tasks while a Tasks page is open (as Company's pages are under Company) */
  pipelines?: { id: string; name: string; to: string }[];
  /* whether the person may open a page: 'tasks', 'company/finance', 'library/tools', 'settings' … (default: yes) */
  can?: (page: string) => boolean;
  /* the team shown open (by default, the team of the teammate whose page is open) */
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
  onAddTeammate?: () => void;
  onHelp?: () => void;
}

const ROWS: { id: MenuRowId; label: string; icon: IconName }[] = [
  { id: 'tasks', label: 'Tasks', icon: 'tasks' },
  { id: 'calendar', label: 'Calendar', icon: 'calendar' },
  { id: 'threads', label: 'Threads', icon: 'threads' },
  { id: 'updates', label: 'Updates', icon: 'updates' },
  { id: 'company', label: 'Company', icon: 'company' },
  { id: 'library', label: 'Library', icon: 'library' },
];
const KIDS: Partial<Record<MenuRowId, string[]>> = {
  company: ['Strategy', 'Finance', 'Growth', 'Products', 'Risk', 'Routines', 'Listening', 'Rules', 'Usage'],
  library: ['Files', 'Docs', 'Tools', 'Skills', 'Contacts'],
};
const slug = (s: string) => s.toLowerCase();
/* the menu's paths are plain strings from the table above; the router resolves them under its base path */
const to = (path: string) => path as LinkProps['to'];
const ACTIVE = { className: 'is-active', 'aria-current': 'page' as const };
/* a shut team shows this many faces, so its name keeps its room */
const FACES = 5;

/* the row or link already says the name */
function Face({ m, size }: { m: MenuMate; size: 16 | 22 | 28 }) {
  return <Avatar decorative kind={m.kind} name={m.name} initials={m.short} hue={m.hue} size={size} live={m.live} />;
}
function FlowMark() {
  return (
    <span className="dot flow" style={{ '--d': '12px' } as CSSProperties} role="img" aria-label="Waits on you">
      <i />
      <b />
    </span>
  );
}

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
  const top = path.split('/')[1] ?? '';
  const onMate = [p.me, ...p.teams.flatMap(t => t.members)].find(m => path.startsWith(m.to));
  const openTeam = p.openTeam ?? p.teams.find(t => path.startsWith(t.to) || t.members.some(m => m === onMate))?.id;
  const [shut, setShut] = useState<Record<string, boolean>>({});
  const isOpen = (t: MenuTeam) => (t.id in shut ? !shut[t.id] : t.id === openTeam);
  const rows = ROWS.filter(r => can(r.id));
  const c = p.counts ?? {};
  const kidsOf = (id: MenuRowId) =>
    id === 'tasks'
      ? (p.pipelines ?? []).map(x => ({ key: x.id, label: x.name, to: x.to }))
      : (KIDS[id] ?? []).filter(k => can(`${id}/${slug(k)}`)).map(k => ({ key: k, label: k, to: `/${id}/${slug(k)}` }));

  if (folded) {
    const faces = p.teams.find(t => t.id === openTeam)?.members ?? [];
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
            {rows.map(r => {
              const n = r.id === 'threads' ? c.threads : r.id === 'updates' ? c.updates : undefined;
              return (
                <Link key={r.id} className="nav-item" to={to(`/${r.id}`)} activeProps={ACTIVE} aria-label={r.label} title={r.label}>
                  <span className="slot-ico">
                    <Icon name={r.icon} />
                    {n ? <i className="slot-n">{n}</i> : null}
                  </span>
                </Link>
              );
            })}
          </div>
          {faces.length ? (
            <div className="tree">
              {faces.map(m => (
                <Link key={m.id} className="tree-row" to={to(m.to)} activeProps={ACTIVE} aria-label={m.name} title={m.name}>
                  <Face m={m} size={22} />
                  {m.needs ? <FlowMark /> : null}
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

  const right = (id: MenuRowId) => {
    if (id === 'tasks' && c.flow)
      return (
        <span className="nav-flow">
          <FlowMark />
          <b>{c.flow}</b>
        </span>
      );
    if (id === 'calendar' && c.next) return <span className="nav-next">{c.next}</span>;
    if (id === 'threads' && c.threads) return <span className="count">{c.threads}</span>;
    if (id === 'updates' && c.updates) return <span className="count">{c.updates}</span>;
    if (kidsOf(id).length)
      return (
        <span className={`nav-chev${top === id ? ' is-open' : ''}`}>
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
        return [
          <Link key={r.id} className="nav-item" to={to(`/${r.id}`)} activeProps={ACTIVE} activeOptions={{ exact: kids.length > 0 }}>
            <Icon name={r.icon} />
            <span className="nav-label">{r.label}</span>
            {right(r.id)}
          </Link>,
          top === r.id && kids.length ? (
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
  const tree = (
    <div className="tree">
      <div className="tree-head">
        <span>Team</span>
        <IconButton icon="plus" label="Add a teammate" size="sm" onClick={p.onAddTeammate} />
      </div>
      <Link className="tree-row" to={to(p.me.to)} activeProps={ACTIVE}>
        <Face m={p.me} size={22} />
        <span className="tree-name">{p.me.name}</span>
      </Link>
      {p.teams.map(t =>
        isOpen(t) ? (
          [
            <Link
              key={t.id}
              className="tree-team is-open"
              to={to(t.to)}
              activeProps={ACTIVE}
              activeOptions={{ exact: true }}
              aria-expanded="true"
              onClick={e => {
                if (path === t.to) {
                  e.preventDefault();
                  setShut(s => ({ ...s, [t.id]: true }));
                }
              }}
            >
              <span className="tree-chev">
                <Icon name="chevron" />
              </span>
              <span className="tree-name">{t.name}</span>
            </Link>,
            ...t.members.map(m => (
              <Link key={m.id} className="tree-row" to={to(m.to)} activeProps={ACTIVE}>
                <Face m={m} size={22} />
                <span className="tree-name">{m.name}</span>
                {m.needs ? <FlowMark /> : null}
              </Link>
            )),
          ]
        ) : (
          <Link
            key={t.id}
            className="tree-team"
            to={to(t.to)}
            activeProps={ACTIVE}
            activeOptions={{ exact: true }}
            aria-expanded="false"
            onClick={() => setShut(s => ({ ...s, [t.id]: false }))}
          >
            <span className="tree-chev">
              <Icon name="chevron" />
            </span>
            <span className="tree-name">{t.name}</span>
            {t.needs ? <FlowMark /> : null}
            <span className="faces">
              {t.members.slice(0, FACES).map(m => (
                <Face key={m.id} m={m} size={16} />
              ))}
            </span>
          </Link>
        ),
      )}
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
