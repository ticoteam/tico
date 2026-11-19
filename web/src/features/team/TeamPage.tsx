import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { directoryQuery, meQuery, type Actor } from '@/data';
import { useFollow } from '@/live/follow';
import { IconButton } from '@/kit/IconButton';
import { FiguresStrip, List, Section, type PageState } from '@/pagekinds';
import type { AddStep } from './actions';
import { AddTeammate } from './AddTeammate';
import { managerOf, memberIds, membersUnder, startOfWeek, subTeams, teamSlug } from './model';
import { MateRow, OwnerButton, waitsOn } from './parts';
import { agentsQuery, allTasksQuery, groupsQuery, peopleQuery, type AgentRow, type Group, type OwnTask } from './queries';

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
  const open = useQuery(allTasksQuery());
  const done = useQuery(allTasksQuery(since));
  const addOpen = useAction('team.addTeammate');

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
  const openBy = tally(open.data?.tasks);
  const doneBy = tally(done.data?.tasks);
  const sum = (by: Map<string, OwnTask[]>) => [...everyone].reduce((n, id) => n + (by.get(id!)?.length ?? 0), 0);
  const paused = new Set((roster.data ?? []).filter((r: AgentRow) => r.status === 'paused').map((r) => r.slug));

  const row = (a: Actor) => {
    const mine = openBy.get(a.id) ?? [];
    return (
      <MateRow
        key={a.id}
        actor={a}
        needs={mine.some((t) => waitsOn(t, me?.id))}
        pill={a.slug && paused.has(a.slug) ? 'Paused' : mine.some((t) => t.late) ? 'Late' : undefined}
        count={mine.length}
      />
    );
  };
  const members = (g: Group) =>
    memberIds(g)
      .map(actorOf)
      .filter((a): a is Actor => !!a && a.id !== manager?.id)
      .sort((a, b) => a.name.localeCompare(b.name));
  const direct = members(group);
  const nested = (g: Group, depth = 0): ReactNode[] =>
    subTeams(all, g.id).flatMap((s) => [
      <Section key={s.id} name={s.name} count={members(s).length} target={`team:${s.id}`}>
        {members(s).map(row)}
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
        actions={
          <>
            <IconButton icon="plus" label="Add a teammate" onClick={() => void addOpen.run({ step: 'pick' })} data-target="team:add" />
            {manager && <OwnerButton actor={manager.id} />}
          </>
        }
        state={state}
        onRetry={() => void groups.refetch()}
        empty={{ title: 'No teammates yet' }}
        figures={
          <FiguresStrip
            label={`${group.name} at a glance`}
            figures={[
              { label: 'Teammates', value: everyone.size },
              { label: 'Open tasks', value: open.data ? sum(openBy) : '' },
              { label: 'Done this week', value: done.data ? sum(doneBy) : '' },
            ]}
          />
        }
        sections={
          <>
            {manager && <Section name="Manager">{row(manager)}</Section>}
            {direct.length > 0 && (
              <Section name="Teammates" count={direct.length}>
                {direct.map(row)}
              </Section>
            )}
            {nested(group)}
          </>
        }
      />
      {add && <AddTeammate step={add} team={group.id} />}
    </>
  );
}
