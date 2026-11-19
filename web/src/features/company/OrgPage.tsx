import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { meQuery, useActor, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { FoldButton } from '@/kit/SectionHead';
import { Select } from '@/kit/Select';
import { List, SidePanel, type PageTab } from '@/pagekinds';
import { Face, Go, NameOf, PanelLink, teamPath, useDo } from './parts';
import { groupsQuery, type Team } from './queries';

/* Org (pages/3-company/company.js orgPage): who reports to whom, as a light chart. A teammate opens its sheet beside
   the chart (`?panel=mate:<human:id | bot:slug>`, the old ui/app/redesign-org.ts rdoSheet): its group and what it
   leads, Move to another group for the owner (any group, or none) or a lead (within what it leads), its page, and a
   call for an agent. The built-in agents stay in their group. */

type Head = { title: string; path: string; tabs: readonly PageTab[]; className: string };

/** The group the built-in agents sit in; nobody moves them out (ui/app/state.ts BUILT_IN_GROUP). */
const BUILT_IN = 'built-in';

/** A group's lead as an actor id: the server names a person `person:<id>`, the app `human:<id>`. */
const leadOf = (g: Team) => (g.lead ? g.lead.replace(/^person:/, 'human:') : null);
const members = (g: Team) => [...g.people.map((p) => (p.includes(':') ? p : `human:${p}`)), ...g.bot_slugs.map((b) => `bot:${b}`)];

export function OrgTab({ head, search }: { head: Head; search: { panel?: string } }) {
  const q = useQuery(groupsQuery);
  const me = useQuery(meQuery).data?.id;
  const actors = useActors();
  const close = useDo('company.closePanel');
  const all = q.data ?? [];
  const teams = all.filter((g) => !g.parent).sort((a, b) => a.order - b.order);
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : teams.length ? 'ready' : 'empty';
  const mate = search.panel?.startsWith('mate:') ? search.panel.slice(5) : null;
  return (
    <List
      {...head}
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No teams yet' }}
      sections={
        <div className="oc" role="tree" aria-label="Org">
          <header className="oc-root">
            {me && (
              <Link to={teamPath(me) as never} className="oc-me">
                <Face id={me} size={36} />
                <span>
                  <NameOf id={me} />
                </span>
              </Link>
            )}
            <span className="grow" />
            <span className="stage-n">{actors.length} teammates</span>
          </header>
          {teams.map((t) => (
            <OrgTeam key={t.id} team={t} subs={all.filter((g) => g.parent === t.id).sort((a, b) => a.order - b.order)} open={mate} />
          ))}
        </div>
      }
      panel={mate && q.data && <MateSheet who={mate} groups={q.data} onClose={() => void close.run({})} />}
    />
  );
}

function OrgTeam({ team, subs, open: picked }: { team: Team; subs: Team[]; open: string | null }) {
  const [open, setOpen] = useState(true);
  const lead = leadOf(team);
  const mates = members(team).filter((m) => m !== lead);
  const n = mates.length + subs.reduce((a, s) => a + members(s).length, 0);
  return (
    <section className="oc-team" role="treeitem" aria-expanded={open} aria-label={team.name}>
      <header className="oc-node">
        {lead ? (
          <PanelLink panel={`mate:${lead}`} className="oc-face" aria-label={`${team.name} lead`}>
            <Face id={lead} size={36} />
          </PanelLink>
        ) : (
          <span className="oc-face" />
        )}
        <div className="oc-id">
          <h2>
            <FoldButton name={team.name} count={n} open={open} onToggle={() => setOpen(!open)} />
          </h2>
          {lead && (
            <PanelLink panel={`mate:${lead}`} className="oc-who">
              <NameOf id={lead} />
            </PanelLink>
          )}
        </div>
      </header>
      {open && (
        <div className={subs.length ? 'oc-kids has-subs' : 'oc-kids'} role="group">
          {mates.length > 0 && <Mates ids={mates} open={picked} />}
          {subs.map((s) => (
            <OrgSub key={s.id} team={s} open={picked} />
          ))}
        </div>
      )}
    </section>
  );
}

function OrgSub({ team, open: picked }: { team: Team; open: string | null }) {
  const [open, setOpen] = useState(true);
  const ids = members(team);
  return (
    <>
      <header className="oc-sub">
        <h3>
          <FoldButton name={team.name} count={ids.length} open={open} onToggle={() => setOpen(!open)} />
        </h3>
      </header>
      {open && ids.length > 0 && <Mates ids={ids} open={picked} />}
    </>
  );
}

function Mates({ ids, open }: { ids: string[]; open: string | null }) {
  return (
    <div className="oc-row">
      {ids.map((id) => (
        <PanelLink key={id} panel={`mate:${id}`} className={id === open ? 'oc-mate is-open' : 'oc-mate'} data-target={`mate:${id}`}>
          <span className="oc-in">
            <Face id={id} size={28} />
            <span className="oc-name">
              <NameOf id={id} />
            </span>
          </span>
        </PanelLink>
      ))}
    </div>
  );
}

/** The groups above `gid` and itself, nearest first. */
function chain(gid: string, groups: readonly Team[]): Team[] {
  const out: Team[] = [];
  for (let g = groups.find((x) => x.id === gid); g && out.length < 20; g = g.parent ? groups.find((x) => x.id === g?.parent) : undefined) out.push(g);
  return out;
}

/** Where `me` may move a teammate from `home`: every group and none for the owner; for a lead, the groups under what
    it leads, when the teammate is in one of them (backend/groups.py _led_add). Nothing for a built-in agent. */
export function moveTargets(me: { id: string; role: string } | undefined, who: string, home: string, groups: readonly Team[]): { value: string; label: string }[] {
  if (!me || groups.some((g) => g.id === BUILT_IN && members(g).includes(who))) return [];
  const label = (g: Team) => ' '.repeat(Math.max(0, chain(g.id, groups).length - 1)) + g.name;
  const ordered = treeOrder(groups);
  if (me.role === 'owner') return [{ value: '', label: 'No group' }, ...ordered.map((g) => ({ value: g.id, label: label(g) }))];
  const mine = (gid: string) => chain(gid, groups).some((g) => leadOf(g) === me.id);
  if (!home || !mine(home)) return [];
  return ordered.filter((g) => mine(g.id)).map((g) => ({ value: g.id, label: label(g) }));
}

function treeOrder(groups: readonly Team[]): Team[] {
  const out: Team[] = [];
  const walk = (parent: string) =>
    groups
      .filter((g) => (g.parent || '') === parent && !out.includes(g))
      .sort((a, b) => a.order - b.order)
      .forEach((g) => {
        out.push(g);
        walk(g.id);
      });
  walk('');
  return [...out, ...groups.filter((g) => !out.includes(g))];
}

/** A teammate's sheet: its group and what it leads, Move to, its page, and a call for an agent. */
function MateSheet({ who, groups, onClose }: { who: string; groups: readonly Team[]; onClose: () => void }) {
  const me = useQuery(meQuery).data;
  const a = useActor(who);
  const move = useDo('org.move');
  const call = useDo('calls.start');
  const home = groups.find((g) => members(g).includes(who))?.id ?? '';
  const led = groups.filter((g) => leadOf(g) === who);
  const targets = moveTargets(me, who, home, groups);
  const [to, setTo] = useState(home);
  const name = a?.name ?? who.slice(who.indexOf(':') + 1);
  const agent = who.startsWith('bot:');
  return (
    <SidePanel
      title={name}
      target={`mate:${who}`}
      lead={<Face id={who} />}
      actions={agent && <IconButton icon="call" label={`Call ${name}`} quiet onClick={() => void call.run({ with: [who] })} />}
      onClose={onClose}
      foot={
        <>
          {targets.length > 0 && (
            <Button variant="primary" busy={move.pending} disabled={to === home} onClick={() => void move.run({ who, from: home, to })}>
              Move
            </Button>
          )}
          <Go to={teamPath(who)} className="btn">
            Open
          </Go>
        </>
      }
    >
      <section className="rail-sec">
        <div className="fact">
          <span>Group</span>
          <span className="fact-v">{groups.find((g) => g.id === home)?.name ?? 'No group'}</span>
        </div>
        {led.length > 0 && (
          <div className="fact">
            <span>Leads</span>
            <span className="fact-v fact-clip">{led.map((g) => g.name).join(', ')}</span>
          </div>
        )}
      </section>
      {targets.length > 0 && (
        <section className="rail-sec">
          <div className="kf">
            <span className="kf-l">Move to</span>
            <div className="kf-c">
              <Select label="Move to" labelHidden options={targets} value={to} onValueChange={setTo} />
            </div>
          </div>
        </section>
      )}
    </SidePanel>
  );
}
