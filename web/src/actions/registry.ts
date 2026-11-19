import * as base from './base';
import type { JsonSchema } from './schema';
import { isAction, type AnyAction } from './types';

/** Every action in the app, by id: the base set plus each feature's `features/<area>/actions.ts`. */
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

const features = import.meta.glob<Record<string, unknown>>('/src/features/*/actions.ts', { eager: true });

export const registry = buildRegistry({ '/src/actions/base.ts': base, ...features });

export interface CatalogEntry {
  id: string;
  title: string;
  kind: 'view' | 'change';
  input: JsonSchema;
}

/** What Tico may ask the screen to do: printed as `contracts/screen-actions.json`, which the server checks against. */
export function actionsCatalog(from: ReadonlyMap<string, AnyAction> = registry): CatalogEntry[] {
  return [...from.values()]
    .map((a) => ({ id: a.id, title: a.title, kind: a.kind, input: a.input.json() }))
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}
