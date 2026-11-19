import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Avatar } from '@/kit/Avatar';
import { useAction } from '@/actions';
import { directoryQuery, meQuery, type Actor } from '@/data';
import { useFollow } from '@/live/follow';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { fmtMoney } from '@/lib';
import { FiguresStrip, List, PageMore, Section, type PageState } from '@/pagekinds';
import type { AddStep } from './actions';
import { AddTeammate } from './AddTeammate';
import { AccessDialog } from './dialogs';
import { managerOf, memberIds, membersUnder, startOfWeek, subTeams, teamSlug } from './model';
import { MateRow, OwnerButton, waitsOn } from './parts';
import {
  agentsQuery,
  allTasksQuery,
  groupsQuery,
  monthSpendQuery,
  peopleQuery,
  seatsQuery,
  teammateAccessQuery,
  type AgentRow,
  type Group,
  type OwnTask,
} from './queries';

export interface TeamPageProps {
  group: Group;
  add?: AddStep;
}

/** A team (mates.js teamPage): its manager, its teammates, the teams inside it. Its manager's chat opens beside it. */
export function TeamPage({ group, add }: TeamPageProps) {
  useFollow({ allTasks: true });
  const { data: me } = useQuery(meQuery);
  const dir = useQuery(directoryQuery);
  const groups = useQuery(groupsQuery);
  const roster = useQuery(agentsQuery);
  const people = useQuery(peopleQuery);
  const [since] = useState(() => startOfWeek(new Date()).toISOString());
  const openTasks = useQuery(allTasksQuery());
  const done = useQuery(allTasksQuery(since));
  const addOpen = useAction('team.addTeammate');
  const reach = useQuery(teammateAccessQuery('team', group.id)).data;
  const [access, setAccess] = useState(false);

  const all = groups.data ?? [];
  const actorOf = (key: string) => dir.data?.byId[key];
  const lead = managerOf(group, roster.data ?? [], people.data ?? []);
  const manager = lead ? actorOf(lead) : undefined;
  const under = membersUnder(all, group.id);
  const everyone = new Set(memberIds(under).map((k) => actorOf(k)?.id).filter(Boolean));
  const tally = (tasks: readonly OwnTask[] | undefined) => {
    const by = new Map<string, OwnTask[]>();
    for (const t of tasks ?? []) {
      const who = t.owner_id ? actorOf(t.owner_id)?.id : undefined;
      if (who) by.set(who, [...(by.get(who) ?? []), t]);
    }
    return by;
  };
  const openBy = tally(openTasks.data?.tasks);
  const doneBy = tally(done.data?.tasks);
  const sum = (by: Map<string, OwnTask[]>) => [...everyone].reduce((n, id) => n + (by.get(id!)?.length ?? 0), 0);
  const paused = new Set((roster.data ?? []).filter((r: AgentRow) => r.status === 'paused').map((r) => r.slug));
  const slugs = (roster.data ?? []).filter((r) => everyone.has(`bot:${r.id}`)).map((r) => r.slug);
  const spend = useQuery(monthSpendQuery(slugs)).data;
  const seats = useQuery(seatsQuery).data ?? [];

  const row = (a: Actor) => {
    const mine = openBy.get(a.id) ?? [];
    const late = mine.filter((t) => t.late).length;
    return (
      <MateRow
        key={a.id}
        actor={a}
        needs={mine.some((t) => waitsOn(t, me?.id))}
        pill={a.slug && paused.has(a.slug) ? 'Stopped' : late ? `${late} late` : undefined}
        count={mine.length}
      />
    );
  };
  const members = (g: Group) =>
    memberIds(g)
      .map(actorOf)
      .filter((a): a is Actor => !!a && a.id !== manager?.id)
      .sort((a, b) => a.name.localeCompare(b.name));
  // An open seat: a dashed face and its title; nobody sits there, so nothing opens (mates.js mateRow seat).
  const seatRows = (g: Group) =>
    seats
      .filter((x) => x.team === g.id)
      .map((x) => (
        <div key={x.id} className="row mate is-seat" title={x.title}>
          <span className="row-mark" />
          <Avatar kind="agent" name={x.title} initials="" size={22} className="is-seat" />
          <span className="mate-name">{x.title}</span>
          <span className="mate-now">Open seat</span>
          <span className="row-pills" />
          <span className="count" />
        </div>
      ));
  const direct = members(group);
  const open = seatRows(group);
  const nested = (g: Group, depth = 0): ReactNode[] =>
    subTeams(all, g.id).flatMap((s) => [
      <Section key={s.id} name={s.name} count={members(s).length} target={`team:${s.id}`}>
        {members(s).map(row)}
        {seatRows(s)}
      </Section>,
      ...(depth < 4 ? nested(s, depth + 1) : []),
    ]);

  const state: PageState = groups.isError || dir.isError ? 'fault' : groups.isPending || dir.isPending ? 'loading' : everyone.size ? 'ready' : 'empty';
  return (
    <>
      <List
        title={group.name}
        path={`/team/${teamSlug(group.name)}`}
        className="rec"
        tabs={[
          { label: 'Teammates', to: `/team/${teamSlug(group.name)}` },
          { label: 'Rules', to: '/company/rules', search: { who: `group:${group.id}` } },
        ]}
        actions={
          <>
            <IconButton icon="plus" label="Add a teammate" onClick={() => void addOpen.run({ step: 'pick' })} data-target="team:add" data-phone />
            {/* On a phone the manager's face stays: it is the way to their chat there. */}
            {manager && (
              <span className="tm-face" data-phone>
                <OwnerButton actor={manager.id} />
              </span>
            )}
          </>
        }
        more={
          reach?.you?.manage ? (
            <PageMore>
              <MenuItem label="Access" icon="lock" onSelect={() => setAccess(true)} />
            </PageMore>
          ) : undefined
        }
        state={state}
        onRetry={() => void groups.refetch()}
        empty={{ title: 'No teammates yet' }}
        figures={
          <FiguresStrip
            label={`${group.name} at a glance`}
            figures={[
              { label: 'Teammates', value: everyone.size },
              { label: 'Open tasks', value: openTasks.data ? sum(openBy) : '' },
              { label: 'Done this week', value: done.data ? sum(doneBy) : '' },
              { label: 'Spend this month', value: spend === undefined ? '' : fmtMoney(spend) },
            ]}
          />
        }
        sections={
          <>
            {manager && <Section name="Manager">{row(manager)}</Section>}
            {direct.length + open.length > 0 && (
              <Section name="Teammates" count={direct.length}>
                {direct.map(row)}
                {open}
              </Section>
            )}
            {nested(group)}
          </>
        }
      />
      {add && <AddTeammate step={add} team={group.id} />}
      {reach?.you?.manage && <AccessDialog open={access} onOpenChange={setAccess} kind="team" id={group.id} name={group.name} now={reach} />}
    </>
  );
}
