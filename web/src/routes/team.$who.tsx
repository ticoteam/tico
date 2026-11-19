import { createFileRoute } from '@tanstack/react-router';
import { directoryQuery } from '@/data';
import { TeamWho } from '@/features/team/exports';
import { groupsQuery, validateTeamSearch } from '@/features/team/routing';

/* `/team/<who>`: an agent (slug), a person (id) or a team (its name, slugified). Tabs are `?tab=`, Add teammate `?add=`. */
export const Route = createFileRoute('/team/$who')({
  validateSearch: validateTeamSearch,
  loader: ({ context: { queryClient } }) =>
    Promise.all([queryClient.ensureQueryData(directoryQuery), queryClient.ensureQueryData(groupsQuery)]).catch(() => undefined),
  component: TeamWhoRoute,
});

function TeamWhoRoute() {
  const { who } = Route.useParams();
  const search = Route.useSearch();
  return <TeamWho who={who} {...search} />;
}
