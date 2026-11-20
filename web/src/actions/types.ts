import type { QueryClient } from '@tanstack/react-query';
import type { AnyRouter } from '@tanstack/react-router';
import type { Schema } from './schema';

/** Who pressed it. Only 'tico' flies the Dot. */
export type Via = 'click' | 'key' | 'search' | 'tico';

export interface Ctx {
  router: AnyRouter;
  queryClient: QueryClient;
  via: Via;
}

/** What the Dot flies to: the element marked `data-target="<kind>:<id>"`. */
export interface Target {
  kind: string;
  id: string | number;
}

/** What every dispatch returns, and what the app posts back for a screen event. `error` reads as "code: reason". */
export interface Result {
  ok: boolean;
  did?: string;
  error?: string;
  /** On a refusal: the server's own reason as one short sentence, for a toast's second part. Never JSON, a stack or an
      address; absent when the server gave none. */
  why?: string;
  /** On a refusal: the action has said it to the person already (its own toast), so a button says nothing more. */
  told?: boolean;
  /** What the action returned (a token shown once, a sign-in), for the person's own press only: never from Tico, never
      posted to the server. */
  out?: unknown;
}

/* Methods (not function properties) on purpose: their inputs are checked bivariantly, so any Action<I> can sit in the
   one registry as an AnyAction while each feature keeps its input typed. */
export interface Action<I = unknown, O = unknown> {
  /** `area.verb` in camelCase: 'task.open', 'shell.openChat'. */
  readonly id: `${string}.${string}`;
  /** Its name: the search row, the shortcut list, the catalog Tico reads. "Open a task". */
  readonly title: string;
  readonly kind: 'view' | 'change';
  /** Validates input from search, keys and Tico. Typed callers are checked too; it is cheap. */
  readonly input: Schema<I>;
  /** false: the person's own press only. Left out of Tico's catalog, and refused from Tico (run via 'tico', show). */
  readonly tico?: boolean;
  /** Tico's did line and the result: "Opened #412". Defaults to the title. */
  did?(input: I, out: O | undefined): string;
  /** From data already in the cache (my role, a row's can_* fields). The server still decides. */
  can?(ctx: Ctx, input: I): boolean;
  /** Navigate, or write through `@/data`. */
  run(ctx: Ctx, input: I): O | Promise<O>;
  /** For Tico's `done` events: go to where the change can be seen, without writing. */
  show?(ctx: Ctx, input: I): unknown;
  /** The element the Dot flies to after a Tico run. No target, no flight. */
  target?(input: I, out: O | undefined): Target | null;
  /** Shortcuts: combo ('g t', 'mod+k', '[') to the input it runs with, or a function reading the screen (null = skip). */
  readonly keys?: Readonly<Record<string, I | ((ctx: Ctx) => I | null)>>;
  /** Rows this action offers in search, each with its input and, when the title is not enough, its own label. */
  offer?(ctx: Ctx): ReadonlyArray<{ input: I; title?: string }>;
}

export type AnyAction = Action<unknown, unknown>;

const defined = new WeakSet<object>();

/** The one way to make an action; the registry only collects what came through here. */
export function defineAction<I, O = void>(a: Action<I, O>): Action<I, O> {
  if (!/^[a-z][A-Za-z0-9]*\.[a-z][A-Za-z0-9]*$/.test(a.id)) throw new Error(`Action id "${a.id}" must be area.verb in camelCase`);
  defined.add(a);
  return a;
}

export const isAction = (x: unknown): x is AnyAction => typeof x === 'object' && x !== null && defined.has(x);
