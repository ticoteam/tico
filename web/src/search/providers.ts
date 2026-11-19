import { defined, type SearchProvider } from './define';

export { defineSearch, type Lead, type SearchHit, type SearchProvider } from './define';

export function collectProviders(sources: Record<string, Record<string, unknown>>): SearchProvider[] {
  const out: SearchProvider[] = [];
  for (const mod of Object.values(sources))
    for (const v of Object.values(mod)) if (typeof v === 'object' && v !== null && defined.has(v)) out.push(v as SearchProvider);
  return out.sort((a, b) => a.order - b.order);
}

export const providers = collectProviders(
  // features add theirs; people come from the one directory
  import.meta.glob<Record<string, unknown>>(['/src/features/*/search.ts', '/src/search/people.ts'], { eager: true }),
);
