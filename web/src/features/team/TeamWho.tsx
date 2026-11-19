import { useQuery } from '@tanstack/react-query';
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
  const dir = useQuery(directoryQuery);
  const groups = useQuery(groupsQuery);
  const found = resolveWho(who, dir.data, groups.data);
  if (found?.kind === 'agent') return <AgentPage actor={found.actor} tab={tab} />;
  if (found?.kind === 'person') return <PersonPage actor={found.actor} settings={tab === 'settings'} slack={tab === 'slack'} />;
  if (found?.kind === 'team') return <TeamPage group={found.group} add={add} />;
  const pending = dir.isPending || groups.isPending;
  return (
    <RecordPage
      title="Team"
      path={`/team/${encodeURIComponent(who)}`}
      state={dir.isError ? 'fault' : pending ? 'loading' : 'empty'}
      onRetry={() => void Promise.all([dir.refetch(), groups.refetch()])}
      empty={{ title: 'No such teammate' }}
    />
  );
}
