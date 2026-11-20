import { useSuspenseQuery } from '@tanstack/react-query';
import { Navigate } from '@tanstack/react-router';
import { directoryQuery } from '@/data';
import { Record as RecordPage } from '@/pagekinds';
import { AgentPage } from './AgentPage';
import { resolveWho, SEAT_PREFIX } from './model';
import { PersonPage } from './PersonPage';
import { SeatPage } from './SeatPage';
import { groupsQuery } from './queries';
import { teamAddress, type TeamSearch } from './routing';
import './team.css';

/** `/team/<who>`: an agent's page, a person's page, or a requested seat's (`seat:<id>`). A team has no page: its old
    address goes home, the team shown in the menu (the loader sends it there first). */
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
  if (found?.kind === 'team') return <Navigate to="/" search={teamAddress(found.group.id, add).search as never} replace />;
  return <RecordPage title="Team" path={`/team/${encodeURIComponent(who)}`} state="empty" empty={{ title: 'No such teammate' }} />;
}
