import { createFileRoute } from '@tanstack/react-router';
import { SkillsPage } from '@/features/library/exports';
import { loadSkills, skillsSearch } from '@/features/library/routing';
import { pageSearch } from '@/lib';

export const Route = createFileRoute('/library/skills')({
  validateSearch: skillsSearch,
  loaderDeps: ({ search }) => ({ skill: search.skill, proposal: search.proposal }),
  loader: ({ deps, context: { queryClient } }) => loadSkills(queryClient, deps),
  component: Skills,
});

function Skills() {
  return <SkillsPage search={Route.useSearch({ select: (s) => pageSearch(s), structuralSharing: true })} />;
}
