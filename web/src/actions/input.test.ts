import * as z from 'zod/mini';
import { describe, expect, it } from 'vitest';
import { inputJson, parseInput } from './input';

const input = z.strictObject({
  task: z.int().check(z.minimum(1)),
  name: z.optional(z.string().check(z.minLength(1), z.maxLength(5))),
  path: z.optional(z.string().check(z.regex(/^\//), z.describe('a path inside the app'))),
  code: z.optional(z.string().check(z.regex(/^[a-z]+$/))),
  view: z.optional(z.enum(['board', 'list'])),
  owner: z.optional(z.nullable(z.string())),
  tags: z.optional(z.array(z.strictObject({ key: z.string() })).check(z.maxLength(2))),
  search: z.optional(z.record(z.string(), z.nullable(z.string()))),
  file: z.optional(z.custom<File>((v) => v instanceof File, 'expected a file')),
});
const error = (v: unknown) => {
  const r = parseInput(input, v);
  return r.ok ? null : r.error;
};

describe('parseInput', () => {
  it('names the field and what was wrong', () => {
    expect(error({ task: 1 })).toBeNull();
    expect(error(null)).toBe('input: expected an object');
    expect(error({})).toBe('task: is required');
    expect(error({ task: 'one' })).toBe('task: expected a whole number');
    expect(error({ task: 1.5 })).toBe('task: expected a whole number');
    expect(error({ task: 0 })).toBe('task: must be at least 1');
    expect(error({ task: 1, name: '' })).toBe('name: must not be empty');
    expect(error({ task: 1, name: 'too long' })).toBe('name: must be at most 5 characters');
    expect(error({ task: 1, name: 3 })).toBe('name: expected text');
    expect(error({ task: 1, path: 'x' })).toBe('path: expected a path inside the app');
    expect(error({ task: 1, code: 'X' })).toBe('code: must match ^[a-z]+$');
    expect(error({ task: 1, view: 'grid' })).toBe('view: expected one of "board", "list"');
    expect(error({ task: 1, owner: 2 })).toBe('owner: expected text');
    expect(error({ task: 1, tags: [{ key: 'a' }, {}] })).toBe('tags[1].key: is required');
    expect(error({ task: 1, tags: [{ key: 'a' }, { key: 'b' }, { key: 'c' }] })).toBe('tags: must hold at most 2');
    expect(error({ task: 1, search: { q: 1 } })).toBe('search.q: expected text');
    expect(error({ task: 1, file: 'a.txt' })).toBe('file: expected a file');
  });

  it('refuses an unknown key first, even beside a bad field', () => {
    expect(error({ task: 'x', who: 1 })).toBe('who: is not a known field');
    expect(error({ task: 1, tags: [{ key: 'a', kind: 'b' }] })).toBe('tags[0].kind: is not a known field');
  });

  it('keeps what it was given, null included', () => {
    expect(parseInput(input, { task: 2, owner: null })).toEqual({ ok: true, value: { task: 2, owner: null } });
  });
});

describe('inputJson', () => {
  it('writes only what the server reads: no $schema, const, type lists, safe-integer bounds or key names', () => {
    const json = inputJson(z.strictObject({ n: z.int(), one: z.literal('x'), m: z.record(z.string(), z.boolean()), off: z.nullable(z.boolean()) }));
    expect(json).toEqual({
      type: 'object',
      properties: {
        n: { type: 'integer' },
        one: { enum: ['x'] },
        m: { type: 'object', additionalProperties: { type: 'boolean' } },
        off: { anyOf: [{ type: 'boolean' }, { type: 'null' }] },
      },
      required: ['n', 'one', 'm', 'off'],
      additionalProperties: false,
    });
  });
});
