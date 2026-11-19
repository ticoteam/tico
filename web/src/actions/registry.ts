import * as base from './base';
import type { JsonSchema } from './schema';
import { isAction, type AnyAction } from './types';

/** Every action in the app, by id: the base set, each shared part's `src/<part>/actions.ts` (conversation, …) and each
    feature's `features/<area>/actions.ts`. */
export function buildRegistry(sources: Record<string, Record<string, unknown>>): ReadonlyMap<string, AnyAction> {
  const all = new Map<string, AnyAction>();
  const from = new Map<string, string>();
  for (const [file, mod] of Object.entries(sources)) {
    for (const value of Object.values(mod)) {
      if (!isAction(value)) continue;
      const was = from.get(value.id);
      if (was !== undefined && all.get(value.id) !== value) {
        throw new Error(`Action "${value.id}" is defined twice: in ${was} and in ${file}`);
      }
      all.set(value.id, value);
      from.set(value.id, file);
    }
  }
  return all;
}

/* Every actions file imports `@/actions`, which holds this registry, and the registry imports every actions file: a
   loop. Whichever file loads first is still running when the registry loads, so its exports are not set yet. The
   registry therefore reads its files on first use, never while modules load (cycle.test.ts tries every load order). */
const found = import.meta.glob<Record<string, unknown>>(['/src/*/actions.ts', '/src/features/*/actions.ts'], { eager: true });

let built: ReadonlyMap<string, AnyAction> | null = null;
const all = () => (built ??= buildRegistry({ '/src/actions/base.ts': base, ...found }));

export const registry: ReadonlyMap<string, AnyAction> = {
  get: (id) => all().get(id),
  has: (id) => all().has(id),
  get size() {
    return all().size;
  },
  forEach: (fn, self) => all().forEach(fn, self),
  keys: () => all().keys(),
  values: () => all().values(),
  entries: () => all().entries(),
  [Symbol.iterator]: () => all()[Symbol.iterator](),
};

export interface CatalogEntry {
  id: string;
  title: string;
  kind: 'view' | 'change';
  input: JsonSchema;
}

/** What Tico may ask the screen to do: printed as `contracts/screen-actions.json`, which the server checks against. */
export function actionsCatalog(from: ReadonlyMap<string, AnyAction> = registry): CatalogEntry[] {
  return [...from.values()]
    .filter((a) => a.tico !== false)
    .map((a) => ({ id: a.id, title: a.title, kind: a.kind, input: a.input.json() }))
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}
