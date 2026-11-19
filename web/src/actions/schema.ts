/* A tiny schema helper: validates an action's input at the edge (search, keys, Tico) with a readable error, and emits
   the JSON Schema the server checks Tico's `show_on_screen` against. It also speaks Standard Schema v1, so a route's
   `validateSearch` can take one directly. Only what actions need; nothing more. */

export type JsonSchema = { [key: string]: unknown };

type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

interface StandardProps<T> {
  readonly version: 1;
  readonly vendor: 'tico';
  readonly validate: (
    value: unknown,
  ) => { value: T; issues?: undefined } | { issues: ReadonlyArray<{ message: string }> };
  readonly types?: { readonly input: unknown; readonly output: T };
}

export interface Schema<T> {
  /** `path` names the field in the error ("task: expected a whole number"). */
  parse(value: unknown, path?: string): Parsed<T>;
  json(): JsonSchema;
  readonly optional: boolean;
  readonly '~standard': StandardProps<T>;
}

export type Infer<S> = S extends Schema<infer T> ? T : never;

const at = (path: string | undefined) => path || 'input';

function make<T>(parse: Schema<T>['parse'], json: () => JsonSchema, optional = false): Schema<T> {
  return {
    parse,
    json,
    optional,
    '~standard': {
      version: 1,
      vendor: 'tico',
      validate: (value) => {
        const r = parse(value);
        return r.ok ? { value: r.value } : { issues: [{ message: r.error }] };
      },
    },
  };
}

const fail = (path: string | undefined, what: string): Parsed<never> => ({ ok: false, error: `${at(path)}: ${what}` });

type Shape = Record<string, Schema<unknown>>;
type OptionalKeys<S extends Shape> = { [K in keyof S]: S[K]['optional'] extends true ? K : never }[keyof S];
type RequiredKeys<S extends Shape> = Exclude<keyof S, OptionalKeys<S>>;
type Flat<T> = { [K in keyof T]: T[K] };
export type ObjectOf<S extends Shape> = Flat<
  { [K in RequiredKeys<S>]: Infer<S[K]> } & { [K in OptionalKeys<S>]?: Infer<S[K]> }
>;

type Optional<T> = Schema<T | undefined> & { readonly optional: true };

export const s = {
  string(o: { min?: number; max?: number; pattern?: RegExp; what?: string } = {}): Schema<string> {
    return make(
      (v, p) => {
        if (typeof v !== 'string') return fail(p, 'expected text');
        if (o.min != null && v.length < o.min) return fail(p, o.min === 1 ? 'must not be empty' : `must be at least ${o.min} characters`);
        if (o.max != null && v.length > o.max) return fail(p, `must be at most ${o.max} characters`);
        if (o.pattern && !o.pattern.test(v)) return fail(p, o.what ? `expected ${o.what}` : `must match ${o.pattern.source}`);
        return { ok: true, value: v };
      },
      () => ({
        type: 'string',
        ...(o.min != null && { minLength: o.min }),
        ...(o.max != null && { maxLength: o.max }),
        ...(o.pattern && { pattern: o.pattern.source }),
        ...(o.what && { description: o.what }),
      }),
    );
  },

  number(o: { int?: boolean; min?: number; max?: number } = {}): Schema<number> {
    return make(
      (v, p) => {
        if (typeof v !== 'number' || !Number.isFinite(v)) return fail(p, o.int ? 'expected a whole number' : 'expected a number');
        if (o.int && !Number.isInteger(v)) return fail(p, 'expected a whole number');
        if (o.min != null && v < o.min) return fail(p, `must be at least ${o.min}`);
        if (o.max != null && v > o.max) return fail(p, `must be at most ${o.max}`);
        return { ok: true, value: v };
      },
      () => ({
        type: o.int ? 'integer' : 'number',
        ...(o.min != null && { minimum: o.min }),
        ...(o.max != null && { maximum: o.max }),
      }),
    );
  },

  boolean(): Schema<boolean> {
    return make(
      (v, p) => (typeof v === 'boolean' ? { ok: true, value: v } : fail(p, 'expected true or false')),
      () => ({ type: 'boolean' }),
    );
  },

  oneOf<const V extends readonly (string | number)[]>(values: V): Schema<V[number]> {
    const list = values.map((v) => JSON.stringify(v)).join(', ');
    return make(
      (v, p) =>
        values.includes(v as V[number]) ? { ok: true, value: v as V[number] } : fail(p, `expected one of ${list}`),
      () => ({ enum: [...values] }),
    );
  },

  optional<T>(inner: Schema<T>): Optional<T> {
    return make<T | undefined>(
      (v, p) => (v === undefined ? { ok: true, value: undefined } : inner.parse(v, p)),
      () => inner.json(),
      true,
    ) as Optional<T>;
  },

  nullable<T>(inner: Schema<T>): Schema<T | null> {
    return make<T | null>(
      (v, p) => (v === null ? { ok: true, value: null } : inner.parse(v, p)),
      () => ({ anyOf: [inner.json(), { type: 'null' }] }),
    );
  },

  array<T>(item: Schema<T>, o: { max?: number } = {}): Schema<T[]> {
    return make(
      (v, p) => {
        if (!Array.isArray(v)) return fail(p, 'expected a list');
        if (o.max != null && v.length > o.max) return fail(p, `must hold at most ${o.max}`);
        const out: T[] = [];
        for (const [i, x] of v.entries()) {
          const r = item.parse(x, `${at(p)}[${i}]`);
          if (!r.ok) return r;
          out.push(r.value);
        }
        return { ok: true, value: out };
      },
      () => ({ type: 'array', items: item.json(), ...(o.max != null && { maxItems: o.max }) }),
    );
  },

  /** A plain object with any keys, every value checked by `value` (a route's search, say). */
  record<T>(value: Schema<T>): Schema<Record<string, T>> {
    return make(
      (v, p) => {
        if (!isPlain(v)) return fail(p, 'expected an object');
        const out: Record<string, T> = {};
        for (const [k, x] of Object.entries(v)) {
          const r = value.parse(x, p ? `${p}.${k}` : k);
          if (!r.ok) return r;
          out[k] = r.value;
        }
        return { ok: true, value: out };
      },
      () => ({ type: 'object', additionalProperties: value.json() }),
    );
  },

  /** Unknown keys are refused, so a typo from Tico fails loudly instead of being ignored. */
  object<S extends Shape>(shape: S): Schema<ObjectOf<S>> {
    return make(
      (v, p) => {
        if (!isPlain(v)) return fail(p, 'expected an object');
        for (const k of Object.keys(v)) if (!(k in shape)) return fail(p ? `${p}.${k}` : k, 'is not a known field');
        const out: Record<string, unknown> = {};
        for (const [k, sch] of Object.entries(shape)) {
          const name = p ? `${p}.${k}` : k;
          if (v[k] === undefined && !sch.optional) return fail(name, 'is required');
          const r = sch.parse(v[k], name);
          if (!r.ok) return r;
          if (r.value !== undefined) out[k] = r.value;
        }
        return { ok: true, value: out as ObjectOf<S> };
      },
      () => {
        const required = Object.entries(shape)
          .filter(([, sch]) => !sch.optional)
          .map(([k]) => k);
        return {
          type: 'object',
          properties: Object.fromEntries(Object.entries(shape).map(([k, sch]) => [k, sch.json()])),
          ...(required.length > 0 && { required }),
          additionalProperties: false,
        };
      },
    );
  },
};

function isPlain(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
