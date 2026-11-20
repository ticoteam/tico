import * as z from 'zod/mini';

/* An action's input is a zod/mini schema: a strictObject, so a typo from Tico fails loudly instead of being ignored.
   Here: its readable error ("task: expected a whole number") and the JSON Schema the server checks Tico's
   `show_on_screen` against (contracts/screen-actions.json). */

export type Input<T> = z.ZodMiniType<T, unknown>;
export type JsonSchema = { [key: string]: unknown };

type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

export function parseInput<T>(schema: Input<T>, raw: unknown): Parsed<T> {
  const r = z.safeParse(schema, raw);
  return r.success ? { ok: true, value: r.data } : { ok: false, error: say(schema, raw, r.error.issues) };
}

type Path = readonly PropertyKey[];

const named = (path: Path) =>
  path.reduce<string>((at, k) => (typeof k === 'number' ? `${at || 'input'}[${k}]` : at ? `${at}.${String(k)}` : String(k)), '') ||
  'input';

/** One error, the first a person would fix: an unknown key before the fields, as zod reports it after them. */
function say(schema: Input<unknown>, raw: unknown, issues: readonly z.core.$ZodIssue[]): string {
  const issue = issues.find((i) => i.code === 'unrecognized_keys') ?? issues[0];
  if (!issue) return 'input: is not valid';
  if (issue.code === 'unrecognized_keys') return `${named([...issue.path, issue.keys[0] ?? ''])}: is not a known field`;
  return `${named(issue.path)}: ${what(issue, schemaAt(schema, issue.path), valueAt(raw, issue.path))}`;
}

function what(issue: z.core.$ZodIssue, at: z.core.$ZodType | undefined, value: unknown): string {
  const whole = (issue.code === 'invalid_type' && issue.expected === 'int') || (at?._zod.def.type === 'number' && 'format' in at._zod.def);
  switch (issue.code) {
    case 'invalid_type':
      if (value === undefined && issue.path.length > 0) return 'is required';
      if (whole) return 'expected a whole number';
      return EXPECTED[issue.expected] ?? `expected ${issue.expected}`;
    case 'too_small':
      if (issue.origin === 'string') return issue.minimum === 1 ? 'must not be empty' : `must be at least ${issue.minimum} characters`;
      return issue.origin === 'array' ? `must hold at least ${issue.minimum}` : `must be at least ${issue.minimum}`;
    case 'too_big':
      if (issue.origin === 'string') return `must be at most ${issue.maximum} characters`;
      return issue.origin === 'array' ? `must hold at most ${issue.maximum}` : `must be at most ${issue.maximum}`;
    case 'invalid_value':
      return `expected one of ${issue.values.map((v) => JSON.stringify(v)).join(', ')}`;
    case 'invalid_format': {
      const description = at && z.globalRegistry.get(at)?.description;
      if (description) return `expected ${description}`;
      return issue.pattern ? `must match ${issue.pattern.replace(/^\/|\/[a-z]*$/g, '')}` : `expected ${issue.format}`;
    }
    default:
      return issue.message;
  }
}

const EXPECTED: Record<string, string> = {
  string: 'expected text',
  number: 'expected a number',
  boolean: 'expected true or false',
  array: 'expected a list',
  object: 'expected an object',
  record: 'expected an object',
};

/** The schema that refused, with optional and nullable unwrapped: for its description and whether it wants an int. */
function schemaAt(schema: z.core.$ZodType, path: Path): z.core.$ZodType | undefined {
  let at: z.core.$ZodType | undefined = bare(schema);
  for (const k of path) {
    const def = at._zod.def as z.core.$ZodTypeDef & { shape?: Record<PropertyKey, z.core.$ZodType>; element?: z.core.$ZodType; valueType?: z.core.$ZodType };
    const next = def.type === 'object' ? def.shape?.[k] : def.type === 'array' ? def.element : def.type === 'record' ? def.valueType : undefined;
    if (!next) return undefined;
    at = bare(next);
  }
  return at;
}

function bare(s: z.core.$ZodType): z.core.$ZodType {
  const def = s._zod.def as z.core.$ZodTypeDef & { innerType?: z.core.$ZodType };
  return (def.type === 'optional' || def.type === 'nullable') && def.innerType ? bare(def.innerType) : s;
}

function valueAt(raw: unknown, path: Path): unknown {
  let v = raw;
  for (const k of path) v = typeof v === 'object' && v !== null ? (v as Record<PropertyKey, unknown>)[k] : undefined;
  return v;
}

/* ---------- the JSON Schema for contracts/screen-actions.json ---------- */

/** zod's JSON Schema, kept to what the server's checker reads (clients/commands/_mcp.py `argument_error`): it knows
    `enum` and `anyOf`, not `const` or `$ref`, so a single value is an enum and a `$ref` fails the build. */
export function inputJson(schema: Input<unknown>): JsonSchema {
  const { $schema: _, ...json } = z.toJSONSchema(schema, { io: 'input' });
  return plain(json) as JsonSchema;
}

/* Also drops what zod adds that the server would read differently: an enum's `type` (the checker would say "expected
   string" before "use one of …"), a type list for a nullable (`anyOf`, as Tico's own tools write it), an int's
   safe-integer bounds and a record's string keys. */
function plain(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(plain);
  if (typeof node !== 'object' || node === null) return node;
  const out: JsonSchema = {};
  for (const [k, v] of Object.entries(node)) {
    if (k === '$ref' || k === '$defs' || k === 'definitions') throw new Error(`screen-actions: ${k} is not read by the server`);
    if (k === 'const') out.enum = [v];
    else if (k === 'type' && ('enum' in node || 'const' in node)) continue;
    else if (k === 'type' && Array.isArray(v)) out.anyOf = v.map((type: unknown) => ({ type }));
    else if ((k === 'minimum' && v === Number.MIN_SAFE_INTEGER) || (k === 'maximum' && v === Number.MAX_SAFE_INTEGER)) continue;
    else if (k === 'propertyNames' && JSON.stringify(v) === '{"type":"string"}') continue;
    else out[k] = plain(v);
  }
  return out;
}
