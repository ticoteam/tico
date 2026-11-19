import { defined, type SearchProvider } from './define';

export { defineSearch, type Lead, type SearchHit, type SearchProvider } from './define';

export function collectProviders(sources: Record<string, Record<string, unknown>>): SearchProvider[] {
  const out: SearchProvider[] = [];
  for (const mod of Object.values(sources))
    for (const v of Object.values(mod)) if (typeof v === 'object' && v !== null && defined.has(v)) out.push(v as SearchProvider);
  return out.sort((a, b) => a.order - b.order);
}

// People (the one directory) and the menu's pages are light: ready the moment search opens.
const near = import.meta.glob<Record<string, unknown>>(['/src/search/people.ts', '/src/search/pages.ts'], { eager: true });
// Each feature's search.ts brings its queries along, so they load the first time search opens, not with the app.
const far = import.meta.glob<Record<string, unknown>>('/src/features/*/search.ts');

/** The providers on hand now: people and pages until the features' have loaded, then all of them. */
export let providers: SearchProvider[] = collectProviders(near);

let all: Promise<SearchProvider[]> | undefined;

/** Every provider, the features' loaded once and kept. A feature that fails to load drops out quietly. */
export function loadProviders(): Promise<SearchProvider[]> {
  all ??= Promise.all(
    Object.entries(far).map(([file, load]) =>
      load().then(
        (mod) => [file, mod] as const,
        () => [file, {}] as const,
      ),
    ),
  ).then((mods) => (providers = collectProviders({ ...near, ...Object.fromEntries(mods) })));
  return all;
}
