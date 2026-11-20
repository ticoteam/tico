import { useSuspenseQuery } from '@tanstack/react-query';
import { directoryQuery } from '@/data';
import { Record as RecordPage } from '@/pagekinds';
import { AgentPage } from './AgentPage';
import { resolveWho, SEAT_PREFIX } from './model';
import { PersonPage } from './PersonPage';
import { SeatPage } from './SeatPage';
import { groupsQuery } from './queries';
import type { TeamSearch } from './routing';
import { TeamPage } from './TeamPage';
import './team.css';

/** `/team/<who>`: an agent's page, a person's page, a team's, or a requested seat's (`seat:<id>`). */
export function TeamWho({ who, tab, add }: { who: string } & TeamSearch) {
  if (who.startsWith(SEAT_PREFIX)) return <SeatPage seat={who.slice(SEAT_PREFIX.length)} />;
  return <Teammate who={who} tab={tab} add={add} />;
}

function Teammate({ who, tab, add }: { who: string } & TeamSearch) {
  const { data: groups } = useSuspenseQuery(groupsQuery);
  // Only who this is: another agent's status line does not draw the page again.
  const { data: found } = useSuspenseQuery({ ...directoryQuery, select: (d) => resolveWho(who, d, groups) });
  if (found?.kind === 'agent') return <AgentPage actor={found.actor} tab={tab} />;
  if (found?.kind === 'person') return <PersonPage actor={found.actor} settings={tab === 'settings'} slack={tab === 'slack'} />;
  const path = `/team/${encodeURIComponent(who)}`;
  if (found?.kind === 'team') return <TeamPage group={found.group} path={path} add={add} />;
  return <RecordPage title="Team" path={path} state="empty" empty={{ title: 'No such teammate' }} />;
}
