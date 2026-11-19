import { createFileRoute } from '@tanstack/react-router';
import { loadSkills, SkillsPage, skillsSearch } from '@/features/library/exports';

export const Route = createFileRoute('/library/skills')({
  validateSearch: skillsSearch,
  loaderDeps: ({ search }) => ({ skill: search.skill, proposal: search.proposal }),
  loader: ({ deps, context: { queryClient } }) => loadSkills(queryClient, deps),
  component: Skills,
});

function Skills() {
  return <SkillsPage search={Route.useSearch()} />;
}
