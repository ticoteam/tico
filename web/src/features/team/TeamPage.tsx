import { useQueries, useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { Avatar, Faces } from '@/kit/Avatar';
import { useAction } from '@/actions';
import { directoryQuery, type Actor, type Directory } from '@/data';
import { useFollow } from '@/live/follow';
import { Icon } from '@/kit/Icon';
import { MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Row, TaskRow } from '@/kit/Row';
import { VirtualList } from '@/kit/VirtualList';
import { fmtMoney } from '@/lib';
import { FiguresStrip, List, PageMore, Section } from '@/pagekinds';
import type { AddStep } from './actions';
import { AddTeammate } from './AddTeammate';
import { AccessDialog } from './dialogs';
import { managerOf, memberIds, membersUnder, seatHref, startOfMonth, subTeams, teamSlug, whoHref } from './model';
import { LONG_LIST, taskHref } from './parts';
import {
  agentsQuery,
  groupsQuery,
  isOpenRequest,
  monthSpendQuery,
  peopleRosterQuery,
  seatsQuery,
  teamDoneCountQueries,
  teamOpenTasksQueries,
  teammateAccessQuery,
  type AgentRow,
  type Group,
  type OwnTask,
} from './queries';

export interface TeamPageProps {
  group: Group;
  path: string;
  add?: AddStep;
}

const face = (a: Actor) => ({ kind: a.kind, name: a.name, hue: a.hue, live: a.working });
const MONTH = new Intl.DateTimeFormat('en-US', { month: 'short' });

/** Team rows show a working badge, but have no use for the actor's focus or for other actors' updates. */
function teamActors(dir: Directory, members: readonly string[]): Record<string, Actor> {
  const shown: Record<string, Actor> = {};
  for (const member of members) {
    const a = dir.byId[member];
    if (!a) continue;
    const { id, kind, name, slug, hue, working } = a;
    const actor = { id, kind, name, slug, hue, working };
    for (const key of [member, id, id.slice(id.indexOf(':') + 1), slug, slug && `bot:${slug}`]) {
      if (key && dir.byId[key] === a) shown[key] = actor;
    }
  }
  return shown;
}

/** A team (threads-team pass 7, teamPage): its icon, name and faces; its figures; what its teammates are running; its
    people, the lead marked; the teams inside it. Rows carry no mark before the title, as the sketch draws them. The
    team's thread beside it is the shell's. */
export function TeamPage({ group, path, add }: TeamPageProps) {
  useFollow({ allTasks: true });
  const { data: all } = useSuspenseQuery(groupsQuery);
  const roster = useQuery(agentsQuery);
  const { data: peopleRoster } = useQuery(peopleRosterQuery);
  const [since] = useState(() => startOfMonth(new Date()).toISOString());
  const [month] = useState(() => MONTH.format(new Date()));
  const addOpen = useAction('team.addTeammate');
  const navigate = useNavigate();
  const reach = useQuery(teammateAccessQuery('team', group.id)).data;
  const [access, setAccess] = useState(false);

  const lead = managerOf(group, roster.data ?? [], peopleRoster?.people ?? []);
  const members = memberIds(membersUnder(all, group.id));
  const { data: actors } = useSuspenseQuery({ ...directoryQuery, select: (d) => teamActors(d, lead ? [...members, lead] : members) });
  const actorOf = (key: string) => actors[key];
  const manager = lead ? actorOf(lead) : undefined;
  const everyone = new Set(members.map((k) => actorOf(k)?.id).filter((id): id is string => !!id));
  const openReads = useQueries({ queries: teamOpenTasksQueries(everyone) });
  const openRead = openReads.every((r) => r.data);
  const doneCounts = useQueries({ queries: teamDoneCountQueries(everyone, since) });
  const done = doneCounts.reduce<number | undefined>((sum, { data }) => sum === undefined || !data?.counts ? undefined : sum + data.counts.total, 0);
  const ownerOf = (t: OwnTask) => (t.owner_id ? actorOf(t.owner_id) : undefined);
  // What the team is on: every open task a teammate here (or in a team inside) holds, the latest first.
  const running = openReads.flatMap((r) => r.data?.tasks ?? [])
    .filter((t) => everyone.has(ownerOf(t)?.id ?? ''))
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
  const paused = new Set((roster.data ?? []).filter((r: AgentRow) => r.status === 'paused').map((r) => r.slug));
  const slugs = [...everyone].map((id) => actorOf(id)?.slug).filter((slug): slug is string => !!slug);
  const spend = useQuery(monthSpendQuery(slugs)).data;
  const seats = (peopleRoster?.seats ?? []).filter((x) => x.team === group.id);
  // The roster's seats carry no request state: the seats read does, read only when this team has a seat.
  const { data: requests } = useQuery({ ...seatsQuery, enabled: seats.length > 0 });

  const people = [
    ...(manager ? [manager] : []),
    ...memberIds(group)
      .map(actorOf)
      .filter((a): a is Actor => !!a && a.id !== manager?.id)
      .sort((a, b) => a.name.localeCompare(b.name)),
  ];
  const inner = subTeams(all, group.id);
  const taskRow = (t: OwnTask) => {
    const who = ownerOf(t);
    return (
      <TaskRow
        key={t.id}
        title={t.title ?? ''}
        href={taskHref(t)}
        target={`task:${t.number ?? t.id}`}
        late={!!t.late}
        avatar={who && <Avatar {...face(who)} size={22} />}
        at={t.updated_at ?? new Date(0).toISOString()}
      />
    );
  };

  return (
    <>
      <List
        title={group.name}
        path={path}
        className="rec is-col tm-page"
        lead={
          <span className="tm-ic">
            <Icon name="people" />
          </span>
        }
        actions={
          people.length > 0 && (
            <span className="tm-faces" data-phone>
              <Faces people={people.map(face)} size={16} max={5} />
            </span>
          )
        }
        more={
          // Tico's flight to Add teammate lands on ⋯, which holds it.
          <PageMore target="team:add">
            <MenuItem label="Add teammate" icon="plus" onSelect={() => void addOpen.run({ step: 'pick' })} />
            <MenuItem label="Rules" icon="rules" onSelect={() => void navigate({ to: '/company/rules', search: { who: `group:${group.id}` } })} />
            {reach?.you?.manage && <MenuItem label="Access" icon="lock" onSelect={() => setAccess(true)} />}
          </PageMore>
        }
        state={people.length || seats.length || inner.length ? 'ready' : 'empty'}
        empty={{ title: 'No teammates yet' }}
        figures={
          <FiguresStrip
            label={`${group.name} at a glance`}
            figures={[
              { label: 'Open tasks', value: openRead ? running.length : '' },
              { label: `Done, ${month}`, value: done ?? '' },
              { label: `Spend, ${month}`, value: spend === undefined ? '' : fmtMoney(spend) },
            ]}
          />
        }
        sections={
          <>
            {running.length > 0 && (
              <Section name="Running">
                {running.length <= LONG_LIST ? (
                  running.map(taskRow)
                ) : (
                  <VirtualList className="tm-long" label="Running" items={running} getKey={(t) => t.id} estimateSize={34} measure renderItem={taskRow} />
                )}
              </Section>
            )}
            {people.length + seats.length > 0 && (
              <Section name="People">
                {people.map((a) => (
                  <Row key={a.id} className="tm-mate" title={a.name} href={whoHref(a)} target={`teammate:${a.slug ?? a.id}`}>
                    <Avatar {...face(a)} size={22} />
                    <span className="row-title">{a.name}</span>
                    <span className="row-pills">{a.slug && paused.has(a.slug) && <Pill flag>Stopped</Pill>}</span>
                    <span className="tm-role">{a.id === manager?.id ? 'Lead' : ''}</span>
                  </Row>
                ))}
                {/* An open seat: a dashed face and its title (mates.js mateRow seat). Nobody sits there, so it opens only
                    when a cloud agent is requested for it: its Connecting page. */}
                {seats.map((x) => {
                  const asked = requests?.find((r) => r.id === x.id && isOpenRequest(r));
                  return (
                    <Row key={x.id} className="tm-mate is-seat" title={x.title} href={asked && seatHref(x.id)} target={`seat:${x.id}`}>
                      <Avatar kind="agent" name={x.title} initials="" size={22} className="is-seat" />
                      <span className="row-title">{x.title}</span>
                      <span className="row-pills" />
                      <span className="tm-role">{asked ? 'Requested' : 'Open seat'}</span>
                    </Row>
                  );
                })}
              </Section>
            )}
            {inner.length > 0 && (
              <Section name="Teams">
                {inner.map((s) => (
                  <Row key={s.id} className="tm-mate" title={s.name} href={`/team/${encodeURIComponent(teamSlug(s.name))}`} target={`team:${s.id}`}>
                    <span className="tm-ic">
                      <Icon name="people" />
                    </span>
                    <span className="row-title">{s.name}</span>
                    <span className="row-pills" />
                    <Faces people={memberIds(s).map(actorOf).filter((a): a is Actor => !!a).map(face)} size={16} max={4} />
                  </Row>
                ))}
              </Section>
            )}
          </>
        }
      />
      {add && <AddTeammate step={add} team={group.id} />}
      {reach?.you?.manage && access && <AccessDialog open onOpenChange={setAccess} kind="team" id={group.id} name={group.name} now={reach} />}
    </>
  );
}
