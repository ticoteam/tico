import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';
import { PagesPage } from '@/features/library/exports';
import { libraryPageQuery, pageKinds } from '@/features/library/routing';
import { pageSearch } from '@/lib';

const word = (max: number) => z.string().max(max).optional().catch(undefined);
const version = z.number().int().positive().optional().catch(undefined);
const flag = z.boolean().optional().catch(undefined);
const validateSearch = z.object({
  q: word(500),
  writer: word(200),
  linked: word(200),
  kind: z.enum(pageKinds).optional().catch(undefined),
  stale: flag,
  tag: word(200),
  archived: flag,
  file: word(200),
  v: version,
  vs: version,
});

export const Route = createFileRoute('/library/pages')({
  validateSearch,
  loaderDeps: ({ search }) => ({ file: search.file }),
  loader: ({ deps, context: { queryClient } }) => deps.file ? queryClient.ensureQueryData(libraryPageQuery(deps.file)) : undefined,
  component: Pages,
});

function Pages() {
  return <PagesPage search={Route.useSearch({ select: (s) => pageSearch(s), structuralSharing: true })} />;
}
