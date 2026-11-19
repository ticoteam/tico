import { useSuspenseQuery } from '@tanstack/react-query';
import { directoryQuery } from '@/data';
import { Record as RecordPage } from '@/pagekinds';
import { AgentPage } from './AgentPage';
import { resolveWho } from './model';
import { PersonPage } from './PersonPage';
import { groupsQuery } from './queries';
import type { TeamSearch } from './routing';
import { TeamPage } from './TeamPage';
import './team.css';

/** `/team/<who>`: an agent's page, a person's page, or a team's. */
export function TeamWho({ who, tab, add }: { who: string } & TeamSearch) {
  const { data: groups } = useSuspenseQuery(groupsQuery);
  // Only who this is: another agent's status line does not draw the page again.
  const { data: found } = useSuspenseQuery({ ...directoryQuery, select: (d) => resolveWho(who, d, groups) });
  if (found?.kind === 'agent') return <AgentPage actor={found.actor} tab={tab} />;
  if (found?.kind === 'person') return <PersonPage actor={found.actor} settings={tab === 'settings'} slack={tab === 'slack'} />;
  const path = `/team/${encodeURIComponent(who)}`;
  if (found?.kind === 'team') return <TeamPage group={found.group} path={path} add={add} />;
  return <RecordPage title="Team" path={path} state="empty" empty={{ title: 'No such teammate' }} />;
}
