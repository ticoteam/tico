import { createFileRoute } from '@tanstack/react-router';
import { TeamWho } from '@/features/team/exports';
import { loadWho, validateTeamSearch } from '@/features/team/routing';

/* `/team/<who>`: an agent (slug) or a person (id); tabs are `?tab=`. A team's old address (its name, slugified) goes home
   with the team shown in the menu (`?team=`), Add teammate (`?add=`) kept. */
export const Route = createFileRoute('/team/$who')({
  validateSearch: validateTeamSearch,
  loaderDeps: ({ search }) => ({ tab: search.tab, add: search.add }),
  loader: ({ params: { who }, deps: { tab, add }, context: { queryClient } }) => loadWho(queryClient, who, tab, add),
  component: TeamWhoRoute,
});

function TeamWhoRoute() {
  const { who } = Route.useParams();
  const search = Route.useSearch();
  return <TeamWho who={who} {...search} />;
}
