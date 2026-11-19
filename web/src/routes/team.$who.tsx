import { createFileRoute } from '@tanstack/react-router';
import { TeamWho } from '@/features/team/exports';
import { loadWho, validateTeamSearch } from '@/features/team/routing';

/* `/team/<who>`: an agent (slug), a person (id) or a team (its name, slugified). Tabs are `?tab=`, Add teammate `?add=`. */
export const Route = createFileRoute('/team/$who')({
  validateSearch: validateTeamSearch,
  loaderDeps: ({ search }) => ({ tab: search.tab }),
  loader: ({ params: { who }, deps: { tab }, context: { queryClient } }) => loadWho(queryClient, who, tab),
  component: TeamWhoRoute,
});

function TeamWhoRoute() {
  const { who } = Route.useParams();
  const search = Route.useSearch();
  return <TeamWho who={who} {...search} />;
}
