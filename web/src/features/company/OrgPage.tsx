import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { meQuery, useActor, useActors } from '@/data';
import type { FactoryMap } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { FoldButton } from '@/kit/SectionHead';
import { Select } from '@/kit/Select';
import { List, PageMore, Section, SidePanel, type PageTab } from '@/pagekinds';
import { Dot } from '@/tico/dot';
import { Face, Go, NameOf, PageOwner, PanelLink, teamPath, useDo } from './parts';
import { accessQuery, factoriesQuery, groupsQuery, type Team } from './queries';

/* Org (pages/3-company/company.js orgPage): who reports to whom, as a light chart. A teammate opens its sheet beside
   the chart (`?panel=mate:<human:id | bot:slug>`, the old ui/app/redesign-org.ts rdoSheet): its group and what it
   leads, Move to another group for the owner (any group, or none) or a lead (within what it leads), its page, and a
   call for an agent. The built-in agents stay in their group. Under the chart, the factories (ui/app/org-factories.ts):
   each factory's pipelines in the order work moves through them, each opening its tasks; the owner and Admins make a
   factory from the ⋯ (`?panel=factory-new`). */

type Head = { title: string; path: string; tabs: readonly PageTab[]; className: string };

/** The group the built-in agents sit in; nobody moves them out (ui/app/state.ts BUILT_IN_GROUP). */
const BUILT_IN = 'built-in';

/** A group's lead as an actor id: the server names a person `person:<id>`, the app `human:<id>`. */
const leadOf = (g: Team) => (g.lead ? g.lead.replace(/^person:/, 'human:') : null);
const members = (g: Team) => [...g.people.map((p) => (p.includes(':') ? p : `human:${p}`)), ...g.bots.map((b) => `bot:${b}`)];

type Subgroups = ReadonlyMap<string, Team[]>;

function memberCount(team: Team, byParent: Subgroups): number {
  return team.people.length + team.bots.length + (byParent.get(team.id) ?? []).reduce((n, child) => n + memberCount(child, byParent), 0);
}

export function OrgTab({ head, search }: { head: Head; search: { panel?: string } }) {
  const all = useSuspenseQuery(groupsQuery).data;
  const me = useQuery(meQuery).data;
  const access = useQuery(accessQuery).data;
  const actors = useActors();
  const close = useDo('company.closePanel');
  const open = useDo('company.openPanel');
  const add = useDo('shell.addTeammate');
  // The company's owner and Admins make factories (backend/task_factories_routes.py create).
  const maker = me?.role === 'owner' || access?.you === 'owner' || access?.you === 'admin';
  // The root is the company's owner (Access names them to the owner and Admins; a member sees themself there).
  const root = access?.owner ? `human:${access.owner}` : me?.id;
  const byParent = new Map<string, Team[]>();
  for (const group of [...all].sort((a, b) => a.order - b.order)) {
    const parent = group.parent || '';
    const kids = byParent.get(parent);
    if (kids) kids.push(group);
    else byParent.set(parent, [group]);
  }
  const teams = byParent.get('') ?? [];
  const requested = search.panel?.startsWith('mate:') ? search.panel.slice(5) : null;
  const mate = useActor(requested)?.id ?? requested;
  return (
    <List
      {...head}
      actions={
        <>
          <Button variant={search.panel ? 'plain' : 'primary'} onClick={() => void add.run({})} data-target="org:add">
            Add teammate
          </Button>
          <PageOwner page="strategy" />
        </>
      }
      state={teams.length ? 'ready' : 'empty'}
      more={
        maker && (
          <PageMore target="org:more">
            <MenuItem label="New factory" onSelect={() => void open.run({ panel: 'factory-new' })} />
          </PageMore>
        )
      }
      empty={{ title: 'No teams yet' }}
      sections={
        <>
          <div className="oc" aria-label="Org">
            <header className="oc-root">
              {root && (
                <Link to={teamPath(root) as never} className="oc-me">
                  <Face id={root} size={36} />
                  <span>
                    <NameOf id={root} />
                  </span>
                </Link>
              )}
              <span className="oc-tico">
                <Dot size={28} still />
                <span>Tico</span>
              </span>
              <span className="grow" />
              <span className="stage-n">{actors.length} teammates</span>
            </header>
            {teams.map((t) => (
              <OrgTeam key={t.id} team={t} byParent={byParent} open={mate} />
            ))}
          </div>
          <Factories />
        </>
      }
      panel={
        search.panel === 'factory-new' && maker ? (
          <NewFactory onClose={() => void close.run({})} />
        ) : (
          mate && <MateSheet key={mate} who={mate} groups={all} onClose={() => void close.run({})} />
        )
      }
    />
  );
}

function OrgTeam({ team, byParent, open: picked }: { team: Team; byParent: Subgroups; open: string | null }) {
  const lead = leadOf(team);
  const mates = members(team).filter((m) => m !== lead);
  // The built-in agents stay folded until someone opens them (redesign-org.ts), unless one of them is the open sheet.
  const [open, setOpen] = useState(team.id !== BUILT_IN || (!!picked && members(team).includes(picked)));
  const subs = byParent.get(team.id) ?? [];
  const n = mates.length + subs.reduce((a, s) => a + memberCount(s, byParent), 0);
  return (
    <section className="oc-team" aria-label={team.name}>
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
        <div className={subs.length ? 'oc-kids has-subs' : 'oc-kids'}>
          {mates.length > 0 && <Mates ids={mates} open={picked} />}
          {subs.map((s) => (
            <OrgSub key={s.id} team={s} byParent={byParent} open={picked} />
          ))}
        </div>
      )}
    </section>
  );
}

function OrgSub({ team, byParent, open: picked }: { team: Team; byParent: Subgroups; open: string | null }) {
  const [open, setOpen] = useState(true);
  const ids = members(team);
  return (
    <>
      <header className="oc-sub">
        <h3>
          <FoldButton name={team.name} count={memberCount(team, byParent)} open={open} onToggle={() => setOpen(!open)} />
        </h3>
      </header>
      {open && ids.length > 0 && <Mates ids={ids} open={picked} />}
      {open && byParent.get(team.id)?.map((s) => <OrgSub key={s.id} team={s} byParent={byParent} open={picked} />)}
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
          <Go to={teamPath(agent && a?.slug ? a.slug : who)} className="btn">
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

// ---------------------------------------------------------------- the factories

type Box = FactoryMap['pipelines'][number];
export type FactoryGroup = { id: string; name: string; pipelines: Box[] };

/** Each live factory with its pipelines, a pipeline before the ones it files into, then the pipelines in no factory
    (org-factories.ts orgFacGroups). Nothing when there is no factory. */
export function factoryGroups(map: FactoryMap | null | undefined): FactoryGroup[] {
  if (!map?.factories.length) return [];
  const ids = new Set(map.factories.map((f) => f.id));
  const groups = map.factories.map((f) => ({ id: f.id, name: f.name, pipelines: flowOrder(map.pipelines.filter((p) => p.factory_id === f.id)) }));
  const loose = map.pipelines.filter((p) => !p.factory_id || !ids.has(p.factory_id));
  return loose.length ? [...groups, { id: '', name: 'No factory', pipelines: flowOrder(loose) }] : groups;
}

function flowOrder(boxes: Box[]): Box[] {
  const fed = new Set(boxes.flatMap((b) => b.files_into));
  const out: Box[] = [];
  const walk = (b: Box): void => {
    if (out.includes(b)) return;
    out.push(b);
    for (const to of b.files_into) {
      const next = boxes.find((x) => x.id === to);
      if (next) walk(next);
    }
  };
  boxes.filter((b) => !fed.has(b.id)).forEach(walk);
  boxes.forEach(walk); // a loop with no start
  return out;
}

function Factories() {
  const groups = factoryGroups(useQuery(factoriesQuery).data);
  if (!groups.length) return null;
  return (
    <Section name="Factories" count={groups.filter((g) => g.id).length} target="org:factories">
      <ol className="fac-list">
        {groups.map((g) => (
          <li key={g.id || 'none'} aria-label={g.name} data-target={g.id ? `factory:${g.id}` : undefined}>
            <h3 className="fac-name">{g.name}</h3>
            {g.pipelines.map((p) => (
              <Go key={p.id} to={`/tasks/${encodeURIComponent(p.id)}`} className="row fac-pipe" data-target={`pipeline:${p.id}`}>
                <span className="row-title">{p.name}</span>
              </Go>
            ))}
          </li>
        ))}
      </ol>
    </Section>
  );
}

/** A factory's name and goal. A name a live factory has already is refused (409) with the server's reason. */
function NewFactory({ onClose }: { onClose: () => void }) {
  const make = useDo('org.newFactory');
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [tried, setTried] = useState(false);
  const save = async () => {
    setTried(true);
    if (name.trim() && (await make.run({ name: name.trim(), ...(goal.trim() ? { goal: goal.trim() } : {}) }))) onClose();
  };
  return (
    <SidePanel
      title="New factory"
      target="panel:factory-new"
      onClose={onClose}
      foot={
        <>
          <Button variant="primary" busy={make.pending} onClick={() => void save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
        </>
      }
    >
      <form
        className="rail-sec fac-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field label="Name" value={name} onValueChange={setName} autoFocus error={tried && !name.trim() ? 'Name it' : undefined} />
        <Field label="Goal" value={goal} onValueChange={setGoal} />
      </form>
    </SidePanel>
  );
}
