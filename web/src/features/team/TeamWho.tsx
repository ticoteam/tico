import { useQuery } from '@tanstack/react-query';
import { directoryQuery } from '@/data';
import { Record as RecordPage } from '@/pagekinds';
import { ADD_STEPS, TABS, type AddStep, type Tab } from './actions';
import { AgentPage } from './AgentPage';
import { resolveWho } from './model';
import { PersonPage } from './PersonPage';
import { groupsQuery } from './queries';
import { TeamPage } from './TeamPage';
import './team.css';

export interface TeamSearch {
  tab?: Tab;
  add?: AddStep;
}

/** `?tab=settings|instructions|computer|screen&add=pick|new|template|person`; anything else is dropped. */
export function validateTeamSearch(raw: Record<string, unknown>): TeamSearch {
  const out: TeamSearch = {};
  if (TABS.includes(raw.tab as Tab)) out.tab = raw.tab as Tab;
  if (ADD_STEPS.includes(raw.add as AddStep)) out.add = raw.add as AddStep;
  return out;
}

/** `/team/<who>`: an agent's page, a person's page, or a team's. */
export function TeamWho({ who, tab, add }: { who: string } & TeamSearch) {
  const dir = useQuery(directoryQuery);
  const groups = useQuery(groupsQuery);
  const found = resolveWho(who, dir.data, groups.data);
  if (found?.kind === 'agent') return <AgentPage actor={found.actor} tab={tab} />;
  if (found?.kind === 'person') return <PersonPage actor={found.actor} settings={tab === 'settings'} />;
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
